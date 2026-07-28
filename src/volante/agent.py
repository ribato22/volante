from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from volante.cost import CostMeter
from volante.providers.base import (
    LLMProvider,
    OnText,
    ProviderError,
    call_provider,
    ensure_complete_response,
)
from volante.tools.base import ToolRegistry
from volante.types import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CapabilityUnavailableError,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

_CONTEXT_MARGIN = 0.85
_CHARS_PER_TOKEN = 4


def _text_of(content: list[ContentBlock]) -> str:
    return "".join(b.text for b in content if isinstance(b, TextBlock))


def _estimate_chars(messages: list[CanonicalMessage]) -> int:
    total = 0
    for m in messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                total += len(b.text)
            elif isinstance(b, ToolUseBlock):
                total += len(str(b.input)) + len(b.name)
            elif isinstance(b, ToolResultBlock):
                total += len(b.content)
    return total


def _estimate_tool_spec_chars(specs: list[ToolSpec]) -> int:
    # Provider wire formats differ, but names/descriptions/schemas all consume
    # context. ``str`` is deliberately conservative and provider-independent.
    return sum(
        len(spec.name) + len(spec.description) + len(str(spec.input_schema))
        for spec in specs
    )


def _trim_tool_result(content: str, char_budget: int) -> str:
    if len(content) <= char_budget:
        return content
    if char_budget <= 0:
        return ""
    marker = "\n…[tool result truncated to fit model context]…\n"
    if char_budget <= len(marker):
        return marker[:char_budget]
    keep = char_budget - len(marker)
    head = keep // 2
    tail = keep - head
    return f"{content[:head]}{marker}{content[-tail:] if tail else ''}"


@dataclass
class TurnRecord:
    index: int
    kind: str  # "tool_use" | "tool_result" | "final"
    payload: str
    usage: Usage | None
    model_id: str


# Two warnings before giving up. One was too few: the nudge measurably rescues runs
# (observed live on csv_stats and json_flatten), and 17 of 24 agentic failures at
# max_iters=16 came from aborting after a single warning rather than from the model
# genuinely being stuck.
_MAX_STALL_NUDGES = 2


@dataclass
class AgenticResult:
    final_text: str
    usage_total: dict[str, Usage]
    turns: list[TurnRecord] = field(default_factory=list)
    tools_used: tuple[str, ...] = ()


class AgenticWorker:
    """Loop model↔tool sampai end_turn / batas. Tak kenal blackboard (Runtime yg menulis)."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        cost_meter: CostMeter,
        max_iters: int = 8,
        max_retries: int = 2,
        char_budget: int = 400_000,
    ) -> None:
        self.providers = providers
        self.cost_meter = cost_meter
        self.max_iters = max_iters
        self.max_retries = max_retries
        self.char_budget = char_budget
        self._before_call: Callable[[str], None] | None = None

    def set_call_gate(self, before_call: Callable[[str], None] | None) -> None:
        """Install a hook invoked immediately before each provider attempt."""

        self._before_call = before_call

    def _effective_char_budget(self, req: CanonicalRequest) -> int:
        if req.context_window is None:
            return self.char_budget
        if req.context_window <= req.max_tokens:
            raise ProviderError(
                "agentic request leaves no input context after output reserve",
                retryable=False,
            )
        model_budget = int(
            (req.context_window - req.max_tokens) * _CONTEXT_MARGIN
        ) * _CHARS_PER_TOKEN
        return min(self.char_budget, model_budget)

    async def _call_with_retry(
        self,
        provider: LLMProvider,
        req: CanonicalRequest,
        model_id: str,
        on_text: OnText | None,
    ) -> CanonicalResponse:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if self._before_call is not None:
                    self._before_call(model_id)
                return await call_provider(provider, req, on_text)
            except (ProviderError, TimeoutError) as err:
                last = err
                # quota_exhausted (kuota/kredit langganan habis) TAK di-backoff:
                # short-circuit -> Runtime me-reroute ke kandidat lain (§6.4),
                # bukan tidur detik-an percuma.
                reroutable = isinstance(err, ProviderError) and (
                    err.quota_exhausted
                    or err.candidate_unavailable
                    or err.provider_unavailable
                )
                retryable = not reroutable and (
                    isinstance(err, TimeoutError)
                    or (isinstance(err, ProviderError) and err.retryable)
                )
                if retryable and attempt < self.max_retries:
                    await asyncio.sleep(0.5 * 2**attempt + random.uniform(0, 0.25))
                    continue
                break
        # Semua kegagalan keluar loop bersifat NON-retryable ke Runtime. Rantai
        # `from last` + status HTTP dipertahankan agar traceback/diagnosa tak hilang.
        # quota_exhausted DIPROPAGASI (re-wrap lama membuangnya) agar Runtime bisa
        # reroute jalur agentic alih-alih menggagalkan task (§6.4).
        raise ProviderError(
            str(last),
            retryable=False,
            status=getattr(last, "status", None),
            quota_exhausted=getattr(last, "quota_exhausted", False),
            candidate_unavailable=getattr(
                last,
                "candidate_unavailable",
                False,
            ),
            provider_unavailable=(
                getattr(last, "provider_unavailable", False)
                or getattr(last, "retryable", False)
                or isinstance(last, TimeoutError)
            ),
        ) from last

    async def run(
        self,
        req: CanonicalRequest,
        model_id: str,
        tools: ToolRegistry,
        on_text: OnText | None = None,
        *,
        required_tools: frozenset[str] | None = None,
    ) -> AgenticResult:
        provider = self.providers[model_id]
        messages = list(req.messages)  # SALINAN — jangan mutasi input
        specs = [t.spec for t in tools.values()]
        local = CostMeter()
        turns: list[TurnRecord] = []
        known_tool_calls = 0
        used_tool_names: set[str] = set()
        # A temperature-0 model that keeps "fixing" a bug the same way re-sends a
        # byte-identical call and gets a byte-identical result back, forever. Track the
        # last turn's (call, result) so the loop can notice it is not moving.
        last_exchange: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        stalls = 0
        effective_budget = self._effective_char_budget(req)
        tool_spec_chars = _estimate_tool_spec_chars(specs)

        for i in range(self.max_iters):
            if _estimate_chars(messages) + tool_spec_chars > effective_budget:
                raise ProviderError(
                    "agentic transcript exceeds model input budget "
                    f"({effective_budget} chars) at iter {i}",
                    retryable=False,
                )
            call = CanonicalRequest(
                messages=messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                tools=specs,
                run_id=req.run_id,
                task_id=req.task_id,
                attempt=i,
                context_window=req.context_window,
            )
            resp = await self._call_with_retry(provider, call, model_id, on_text)
            self.cost_meter.add(
                model_id, resp.usage, cost_usd=resp.cost_usd
            )
            local.add(model_id, resp.usage, cost_usd=resp.cost_usd)

            if resp.stop_reason != "tool_use":
                ensure_complete_response(resp, phase="agentic worker")
                final = _text_of(resp.content)
                missing = (
                    required_tools - used_tool_names
                    if required_tools is not None
                    else frozenset()
                )
                if missing:
                    raise CapabilityUnavailableError(
                        "agentic model completed without invoking required tools: "
                        f"{sorted(missing)}"
                    )
                # Offering tools is not the same as requiring them. A caller that
                # declares its requirements — even as an empty set — has said what it
                # needs, so a model that solves the task in one turn is judged on its
                # answer. Only a caller that declared NOTHING gets the strict reading,
                # where an agentic turn that never touched a tool is unverified work.
                if known_tool_calls == 0 and required_tools is None:
                    raise CapabilityUnavailableError(
                        "agentic model completed without invoking any configured tool"
                    )
                if not final.strip():
                    raise ProviderError(
                        "agentic model returned empty final text",
                        retryable=False,
                    )
                turns.append(TurnRecord(i, "final", final, resp.usage, model_id))
                return AgenticResult(
                    final,
                    local.totals(),
                    turns,
                    tuple(sorted(used_tool_names)),
                )

            tool_uses = [b for b in resp.content if isinstance(b, ToolUseBlock)]
            turns.append(
                TurnRecord(
                    i, "tool_use", str([{b.name: b.input} for b in tool_uses]), resp.usage, model_id
                )
            )
            raw_results: list[tuple[str, str]] = []
            for b in tool_uses:
                if b.name in tools:
                    content = await tools[b.name].run(b.input)
                    known_tool_calls += 1
                    used_tool_names.add(b.name)
                else:
                    content = f"error: unknown tool {b.name!r}"
                raw_results.append((b.id, content))

            # Tool output is untrusted and may be arbitrarily large. Allocate the
            # remaining model-specific transcript budget fairly across results so
            # one verbose tool cannot overflow or starve its siblings.
            assistant_message = CanonicalMessage(
                role="assistant", content=resp.content
            )
            used = (
                _estimate_chars([*messages, assistant_message])
                + tool_spec_chars
            )
            remaining = max(effective_budget - used, 0)
            share = remaining // len(raw_results) if raw_results else 0
            results: list[ContentBlock] = [
                ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=_trim_tool_result(content, share),
                )
                for tool_use_id, content in raw_results
            ]
            for result in results:
                if isinstance(result, ToolResultBlock):
                    turns.append(
                        TurnRecord(
                            i, "tool_result", result.content, None, model_id
                        )
                    )
            messages = messages + [
                assistant_message,
                CanonicalMessage(role="user", content=results),
            ]

            # No-progress guard. Repeating an identical call for an identical result buys
            # nothing and costs a full model call each turn; observed live burning every
            # remaining iteration before failing with an unhelpful "exhausted".
            exchange = (
                tuple(
                    f"{b.name}:{json.dumps(b.input, sort_keys=True, default=str)}"
                    for b in tool_uses
                ),
                tuple(content for _, content in raw_results),
            )
            if last_exchange == exchange:
                stalls += 1
                if stalls > _MAX_STALL_NUDGES:
                    raise ProviderError(
                        "agentic loop made no progress: the same tool call returned the "
                        f"same result {stalls + 1} times, through {_MAX_STALL_NUDGES} "
                        "warning(s). Stopping instead of spending the remaining "
                        f"{self.max_iters - i - 1} iteration(s).",
                        retryable=False,
                    )
                # One chance to recover: name the repetition explicitly, because the model
                # cannot see that its previous turn was identical.
                messages = messages + [
                    CanonicalMessage(
                        role="user",
                        content=[
                            TextBlock(
                                text=(
                                    "You just repeated the exact same tool call and got "
                                    "the exact same result. Repeating it again will not "
                                    "help. Either change your approach, or stop calling "
                                    "tools and give your final answer now."
                                    if stalls == 1
                                    else "This is the second time. Stop calling tools and "
                                    "answer with your best solution as it stands."
                                )
                            )
                        ],
                    )
                ]
            else:
                stalls = 0
            last_exchange = exchange

        raise ProviderError(
            f"agentic loop exhausted after {self.max_iters} iters", retryable=False
        )

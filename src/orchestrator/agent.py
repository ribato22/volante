from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from orchestrator.cost import CostMeter
from orchestrator.providers.base import LLMProvider, ProviderError
from orchestrator.tools.base import ToolRegistry
from orchestrator.types import (
    CanonicalMessage,
    CanonicalRequest,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


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


@dataclass
class TurnRecord:
    index: int
    kind: str  # "tool_use" | "tool_result" | "final"
    payload: str
    usage: Usage | None
    model_id: str


@dataclass
class AgenticResult:
    final_text: str
    usage_total: dict[str, Usage]
    turns: list[TurnRecord] = field(default_factory=list)


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

    async def _complete_with_retry(
        self, provider: LLMProvider, req: CanonicalRequest
    ) -> "object":
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await provider.complete(req)
            except (ProviderError, TimeoutError) as err:
                last = err
                retryable = isinstance(err, TimeoutError) or (
                    isinstance(err, ProviderError) and err.retryable
                )
                if retryable and attempt < self.max_retries:
                    await asyncio.sleep(0.5 * 2**attempt + random.uniform(0, 0.25))
                    continue
                break
        # Semua kegagalan keluar loop bersifat NON-retryable ke Runtime.
        raise ProviderError(str(last), retryable=False)

    async def run(
        self, req: CanonicalRequest, model_id: str, tools: ToolRegistry
    ) -> AgenticResult:
        provider = self.providers[model_id]
        messages = list(req.messages)  # SALINAN — jangan mutasi input
        specs = [t.spec for t in tools.values()]
        local = CostMeter()
        turns: list[TurnRecord] = []

        for i in range(self.max_iters):
            if _estimate_chars(messages) > self.char_budget:
                raise ProviderError(
                    f"agentic transcript exceeds budget at iter {i}", retryable=False
                )
            call = CanonicalRequest(
                messages=messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                tools=specs,
                run_id=req.run_id,
                task_id=req.task_id,
                attempt=i,
            )
            resp = await self._complete_with_retry(provider, call)
            self.cost_meter.add(model_id, resp.usage)  # shared (global cost_usd)
            local.add(model_id, resp.usage)  # per-task tally

            if resp.stop_reason != "tool_use":
                final = _text_of(resp.content)
                turns.append(TurnRecord(i, "final", final, resp.usage, model_id))
                return AgenticResult(final, local.totals(), turns)

            tool_uses = [b for b in resp.content if isinstance(b, ToolUseBlock)]
            turns.append(
                TurnRecord(
                    i, "tool_use", str([{b.name: b.input} for b in tool_uses]), resp.usage, model_id
                )
            )
            results: list[ContentBlock] = []
            for b in tool_uses:
                if b.name in tools:
                    content = await asyncio.to_thread(tools[b.name].run, b.input)
                else:
                    content = f"error: unknown tool {b.name!r}"
                results.append(ToolResultBlock(tool_use_id=b.id, content=content))
                turns.append(TurnRecord(i, "tool_result", content, None, model_id))
            messages = messages + [
                CanonicalMessage(role="assistant", content=resp.content),
                CanonicalMessage(role="user", content=results),
            ]

        raise ProviderError(
            f"agentic loop exhausted after {self.max_iters} iters", retryable=False
        )

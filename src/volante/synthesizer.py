from __future__ import annotations

from collections.abc import Callable

from volante.blackboard import Blackboard
from volante.cost import CostMeter
from volante.projector import model_input_char_budget
from volante.providers.base import (
    LLMProvider,
    ProviderError,
    call_provider,
    ensure_complete_response,
)
from volante.types import CanonicalRequest, TextBlock, text

_DEFAULT_CONTEXT_WINDOW = 32_768
_DEFAULT_MAX_OUTPUT_TOKENS = 2_048


def _trim(content: str, budget: int, label: str) -> str:
    if len(content) <= budget:
        return content
    if budget <= 0:
        return ""
    marker = f"\n…[{label} truncated to fit model context]…\n"
    if budget <= len(marker):
        return marker[:budget]
    keep = budget - len(marker)
    head = keep // 2
    tail = keep - head
    return f"{content[:head]}{marker}{content[-tail:] if tail else ''}"


class Synthesizer:
    """Merangkai current_artifacts dari blackboard menjadi satu output final via provider,
    lalu mencatat usage ke CostMeter (key = model_id) setelah complete() sukses."""

    def __init__(
        self,
        provider: LLMProvider,
        model_id: str,
        cost_meter: CostMeter,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._cost_meter = cost_meter
        # Safe bounded defaults for direct use. Runtime replaces these with the
        # selected model's registry metadata before a run.
        self._context_window = _DEFAULT_CONTEXT_WINDOW
        self._max_output_tokens = _DEFAULT_MAX_OUTPUT_TOKENS
        self._before_call: Callable[[str], None] | None = None

    def set_call_gate(self, before_call: Callable[[str], None] | None) -> None:
        self._before_call = before_call

    def set_model_limits(
        self, *, context_window: int, max_output_tokens: int
    ) -> None:
        """Configure context limits from the runtime's selected model."""

        if context_window <= 1:
            raise ValueError("synthesizer context_window must be greater than one")
        if max_output_tokens <= 0:
            raise ValueError("synthesizer max_output_tokens must be positive")
        self._context_window = context_window
        self._max_output_tokens = max_output_tokens

    def _build_prompt(self, goal: str, bb: Blackboard) -> tuple[str, int]:
        output_tokens = min(
            self._max_output_tokens, max(1, self._context_window // 2)
        )
        char_budget = model_input_char_budget(
            self._context_window, output_tokens
        )
        instruction = (
            "\n\nYou are given the artifacts produced by completed sub-tasks. "
            "Combine them into a single, coherent final answer for the goal. "
            "Write the final answer in the same language as the goal above "
            "(e.g. an English goal gets an English answer).\n\nArtifacts:\n"
        )

        # Reserve most input for evidence, while still bounding an arbitrarily
        # large user goal. The final safety trim below also covers tiny contexts.
        goal_budget = max(char_budget // 5, 1)
        goal_text = _trim(goal, goal_budget, "goal")
        prefix = f"Goal:\n{goal_text}{instruction}"
        if len(prefix) > char_budget:
            prefix = _trim(prefix, char_budget, "synthesis prompt")

        remaining = max(char_budget - len(prefix), 0)
        artifacts = list(bb.current_artifacts().items())
        if not artifacts:
            suffix = _trim("(no artifacts produced)", remaining, "artifacts")
            return f"{prefix}{suffix}", output_tokens

        sections: list[str] = []
        for index, (task_id, payload) in enumerate(artifacts):
            left = len(artifacts) - index
            share = remaining // left if left else 0
            separator = "\n\n" if sections else ""
            label = f"## {task_id}\n"
            payload_budget = max(share - len(separator) - len(label), 0)
            section = (
                f"{separator}{label}"
                f"{_trim(str(payload), payload_budget, f'artifact {task_id}')}"
            )
            if len(section) > remaining:
                section = section[:remaining]
            sections.append(section)
            remaining -= len(section)
            if remaining <= 0:
                break
        return f"{prefix}{''.join(sections)}", output_tokens

    async def synthesize(
        self,
        goal: str,
        bb: Blackboard,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        prompt, output_tokens = self._build_prompt(goal, bb)
        req = CanonicalRequest(
            messages=[text("user", prompt)],
            max_tokens=output_tokens,
            context_window=self._context_window,
        )
        # on_text -> streaming (progres sintesis live); else complete (nol regresi).
        if self._before_call is not None:
            self._before_call(self._model_id)
        resp = await call_provider(self._provider, req, on_text)
        self._cost_meter.add(
            self._model_id, resp.usage, cost_usd=resp.cost_usd
        )
        ensure_complete_response(resp, phase="synthesis")
        parts = [b.text for b in resp.content if isinstance(b, TextBlock)]
        final = "".join(parts)
        if not final.strip():
            raise ProviderError(
                "synthesizer model returned empty text output",
                retryable=False,
            )
        return final

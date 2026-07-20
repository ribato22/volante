from __future__ import annotations

from orchestrator.blackboard import Blackboard
from orchestrator.cost import CostMeter
from orchestrator.providers.base import LLMProvider
from orchestrator.types import CanonicalRequest, TextBlock, text


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

    async def synthesize(self, goal: str, bb: Blackboard) -> str:
        artifacts = bb.current_artifacts()
        sections = [
            f"## {task_id}\n{payload}" for task_id, payload in artifacts.items()
        ]
        body = "\n\n".join(sections) if sections else "(no artifacts produced)"
        prompt = (
            f"Goal:\n{goal}\n\n"
            "You are given the artifacts produced by completed sub-tasks. "
            "Combine them into a single, coherent final answer for the goal.\n\n"
            f"Artifacts:\n{body}"
        )
        req = CanonicalRequest(
            messages=[text("user", prompt)],
            max_tokens=2048,
        )
        resp = await self._provider.complete(req)
        self._cost_meter.add(self._model_id, resp.usage)
        parts = [b.text for b in resp.content if isinstance(b, TextBlock)]
        return "".join(parts)

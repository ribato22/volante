from __future__ import annotations

from baton.registry import Registry
from baton.types import ModelInfo, Task

# task.type -> required model strengths (Fase 0-1 rule table)
_TYPE_STRENGTHS: dict[str, set[str]] = {
    "code": {"coding"},
    "research": {"reasoning"},
    "write": {"reasoning"},
    "analyze": {"reasoning"},
}


class Router:
    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    def route(self, task: Task) -> str:
        strengths = _TYPE_STRENGTHS.get(task.type, {"reasoning"})
        needs_tools = task.mode == "agentic"
        candidates: list[ModelInfo] = self._registry.matching(
            strengths, needs_tools=needs_tools
        )
        if not candidates:
            raise ValueError(
                f"no model matches strengths={strengths} "
                f"needs_tools={needs_tools} for task {task.id!r}"
            )
        best = min(candidates, key=lambda m: m.cost_per_1k_out)
        return best.id

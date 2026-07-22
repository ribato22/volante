from __future__ import annotations

import logging

from baton.registry import Registry
from baton.types import ModelInfo, Task

logger = logging.getLogger(__name__)

# task.type -> required model strengths (Fase 0-1 rule table)
_TYPE_STRENGTHS: dict[str, set[str]] = {
    "code": {"coding"},
    "research": {"reasoning"},
    "write": {"reasoning"},
    "analyze": {"reasoning"},
}

# billing values that draw from a subscription pool (no cash), not a card.
_SUBSCRIPTION_BILLING: set[str] = {"plan_included", "plan_credit"}


def _cash(m: ModelInfo) -> float:
    # Subscription draws no cash (plan-backed pool); card pays the API rate.
    return 0.0 if m.billing in _SUBSCRIPTION_BILLING else m.cost_per_1k_out


def _rank_key(m: ModelInfo) -> tuple[float, int, str]:
    # Cheapest cash first; tiebreak higher tier, then id (deterministic, stable).
    return (_cash(m), -m.tier, m.id)


class Router:
    def __init__(self, registry: Registry, *, prefer: str = "cash_protect_quota") -> None:
        self._registry = registry
        # prefer is one of "cash_protect_quota" | "quality" | "local" | "cheap".
        # This phase implements the cash_protect_quota objective (the default);
        # the other objectives are wired in the bootstrap/wiring phase.
        self._prefer = prefer

    def route_ranked(self, task: Task) -> list[str]:
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
        return [m.id for m in sorted(candidates, key=_rank_key)]

    def route(self, task: Task) -> str:
        return self.route_ranked(task)[0]

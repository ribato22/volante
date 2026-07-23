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

# difficulty -> minimum acceptable model tier (unknown difficulty -> "medium").
_DESIRED_TIER: dict[str, int] = {"trivial": 1, "easy": 2, "medium": 3, "hard": 4}


def _cash(m: ModelInfo) -> float:
    # Subscription draws no cash (plan-backed pool); card pays the API rate.
    return 0.0 if m.billing in _SUBSCRIPTION_BILLING else m.cost_per_1k_out


def _rank_key(m: ModelInfo) -> tuple[float, int, str]:
    # Cheapest cash first; then RIGHT-SIZE: prefer the lowest (adequate) tier so a task
    # uses the weakest sufficient model and reserves stronger/pricier ones for harder
    # tasks. This also distributes work across same-cash subscription providers (e.g. a
    # tier-3 codex handles medium tasks while tier-4 claude-code is reserved for hard).
    return (_cash(m), m.tier, m.id)


class Router:
    def __init__(self, registry: Registry, *, prefer: str = "cash_protect_quota") -> None:
        self._registry = registry
        # prefer is one of "cash_protect_quota" | "quality" | "local" | "cheap".
        # `route_ranked` below currently implements ONLY the cash_protect_quota
        # objective (the default) and never reads `self._prefer`; the other three
        # values are accepted (validated by the CLI) but not yet honored -- they
        # are reserved for a future routing objective implementation.
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
        desired = _DESIRED_TIER.get(task.difficulty, 3)  # unknown -> medium (3)
        adequate = [m for m in candidates if m.tier >= desired]
        if not adequate:
            # No tier-adequate candidate: best-effort over ALL matches (v1 back-compat).
            return [m.id for m in sorted(candidates, key=_rank_key)]
        if task.difficulty == "hard":
            # Hard: allow subscription; rank ALL adequate by cash (subscription ~ $0 wins).
            return [m.id for m in sorted(adequate, key=_rank_key)]
        # Non-hard: rank DIRECT (card) candidates only -> protects subscription quota.
        direct = [m for m in adequate if m.billing not in _SUBSCRIPTION_BILLING]
        if direct:
            return [m.id for m in sorted(direct, key=_rank_key)]
        # No tier-adequate direct option: fall back to subscription (best-effort) + log.
        logger.info(
            "using quota: no adequate direct candidate for task %r (difficulty=%s)",
            task.id,
            task.difficulty,
        )
        subscription = [m for m in adequate if m.billing in _SUBSCRIPTION_BILLING]
        return [m.id for m in sorted(subscription, key=_rank_key)]

    def route(self, task: Task) -> str:
        return self.route_ranked(task)[0]

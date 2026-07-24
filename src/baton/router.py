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


def _quality_key(m: ModelInfo) -> tuple[int, float, str]:
    # QUALITY objective (the default): the STRONGEST model capable of the task first
    # (tier descending) so the best available model answers each task; among equally
    # strong models, prefer the one that costs no cash (subscription $0 before card),
    # then id for determinism.
    return (-m.tier, _cash(m), m.id)


class Router:
    def __init__(self, registry: Registry, *, prefer: str = "quality") -> None:
        self._registry = registry
        # prefer is one of "quality" (default) | "cash_protect_quota" | "local" | "cheap".
        # `route_ranked` implements two objectives: "quality" picks the STRONGEST model
        # capable of each task (best answer), and "cash_protect_quota" right-sizes among
        # tier-adequate models to protect subscription quota. "local"/"cheap" are accepted
        # (validated by the CLI) but currently behave as "quality"; they are reserved for
        # future dedicated objectives.
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
        if self._prefer != "cash_protect_quota":
            # "quality" (default) + reserved objectives: the strongest capable model
            # first (no right-sizing), with the rest as a strongest-to-weakest reroute
            # order. Difficulty is not used to filter here — quality always answers with
            # the best available model, even for an easy task.
            return [m.id for m in sorted(candidates, key=_quality_key)]
        # cash_protect_quota: right-size among tier-adequate models to protect
        # subscription quota; use stronger/subscription models only where unavoidable.
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

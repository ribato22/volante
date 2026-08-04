"""Answer the goal in ONE call, with the router still choosing the model.

Volante has always planned, decomposed, executed and synthesised — and its own eval
says that does not pay. Across the nine-goal suite orchestration ties a single call
(0.997 against 0.993, both at the ceiling) for 7.7x the money; on the goal built to give
decomposition headroom the reproduced difference is -0.021; and in the regime where a
single response physically cannot fit the answer, orchestration ties at best. Meanwhile
the largest measured lever by a factor of three is not decomposition at all — it is
WHICH MODEL answers: +0.462 moving from gpt-4o-mini to gpt-4o on the same goal.

So this keeps the part that pays and drops the part that does not. The router still does
its job: hard capabilities are enforced, the eligible models are ranked on metadata and
evaluation evidence, and the decision is recorded. Then the chosen model answers, once.

It is not a lesser mode for easy goals. It is the shape the measurements support, and
orchestration remains available for the work that genuinely needs a tool loop — which a
single call cannot do at all, and which is the one thing this path gives up.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from volante.blackboard import Blackboard
from volante.cost import CostMeter
from volante.projector import Projector
from volante.providers.base import (
    EmptyOutputError,
    LLMProvider,
    ProviderError,
    call_provider,
    ensure_complete_response,
)
from volante.registry import Registry
from volante.router import Router
from volante.types import RunResult, Task, TextBlock, validate_goal

# The goal becomes one task so the router sees what it always sees. `medium` rather
# than a guess: the planner's own difficulty labels were measured and carry no signal
# — they give a goal where decomposition is worth +0.289 the same profile as one a
# single call already aces — so inventing a difficulty here would be inventing evidence.
_DIRECT_TASK_TYPE = "code"
_DIRECT_DIFFICULTY = "medium"


async def answer_directly(
    goal: str,
    router: Router,
    projector: Projector,
    providers: dict[str, LLMProvider],
    registry: Registry,
    cost_meter: CostMeter,
    *,
    call_timeout: float = 120.0,
    on_text: Any = None,
) -> RunResult:
    """Route the goal, call the chosen model once, return the same RunResult shape.

    The RunResult contract is unchanged on purpose: the CLI, MCP server, Web UI and
    library callers already read it, and a second result type would make the two paths
    diverge exactly where a user compares them.
    """
    validate_goal(goal)
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    task = Task(
        id="direct",
        description=goal,
        type=_DIRECT_TASK_TYPE,
        mode="one_shot",
        difficulty=_DIRECT_DIFFICULTY,
    )
    ranked = router.route_ranked(task)
    trace: dict[str, Any] = {
        "selected_model_id": ranked[0] if ranked else None,
        "executed_model_id": None,
        "fallback_events": [],
    }

    last_error: Exception | None = None
    for model_id in ranked:
        if model_id not in providers:
            continue
        req = projector.project(task, model_id, Blackboard(goal, [task]))
        req.run_id = run_id
        req.task_id = task.id
        try:
            resp = await call_provider(providers[model_id], req, on_text)
        except (ProviderError, TimeoutError) as exc:
            last_error = exc
            trace["fallback_events"].append(
                {"model_id": model_id, "reason": "provider_unavailable",
                 "message": str(exc)}
            )
            continue
        cost_meter.add(model_id, resp.usage, cost_usd=resp.cost_usd)
        ensure_complete_response(resp, phase="direct")
        final = "".join(b.text for b in resp.content if isinstance(b, TextBlock))
        if not final.strip():
            # Same rule as every other phase: a blank completion is a candidate
            # failure, not an answer, so the next ranked model gets a turn.
            last_error = EmptyOutputError(phase="direct model")
            trace["fallback_events"].append(
                {"model_id": model_id, "reason": "invalid_output",
                 "message": str(last_error)}
            )
            continue
        trace["executed_model_id"] = model_id
        billed, credit = cost_meter.costs_usd(registry)
        return RunResult(
            status="success",
            final=final,
            partial_artifacts={"direct": final},
            failed_task=None,
            usage_total=cost_meter.totals(),
            cost_usd=billed + credit,
            duration_ms=int((time.perf_counter() - started) * 1000),
            billed_usd=billed,
            credit_usd=credit,
            routing_decisions={"__direct__": trace},
            cost_estimated=cost_meter.has_estimated(),
        )

    billed, credit = cost_meter.costs_usd(registry)
    return RunResult(
        status="failed",
        final=None,
        partial_artifacts={},
        failed_task="direct",
        usage_total=cost_meter.totals(),
        cost_usd=billed + credit,
        duration_ms=int((time.perf_counter() - started) * 1000),
        billed_usd=billed,
        credit_usd=credit,
        error_code=getattr(last_error, "error_code", "provider_error"),
        error_message=f"{type(last_error).__name__}: {last_error}"
        if last_error
        else "no configured model could answer",
        routing_decisions={"__direct__": trace},
        cost_estimated=cost_meter.has_estimated(),
    )

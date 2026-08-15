"""Simulate the router against measured data: does the depth-aware estimate change anything?

Runs no providers and spends nothing. Two questions, both answered by executing the
real `volante.router.Router` against the measured suite:

1. INVARIANCE on the actual suite. The eval goals do not name files and carry no size
   language, so the depth-aware default estimate must equal the old one for every goal,
   every routing decision must be identical, and the per-goal loss (best measured score
   minus the routed model's score) must not move. This is the honest null result for a
   heuristic the suite cannot see — the number that says the change did not quietly
   disturb existing routing.

2. MECHANISM where the heuristic CAN act. A trivial-difficulty goal that names files
   raises the estimate, flips the `requires_truncation` flag for small-window
   candidates, and drops them behind full-fit ones. The demo table reports selected
   model, truncation flags and context headroom per candidate — the observable behavior
   the feature introduces, not a quality claim (the synthetic small-window models have
   no measured scores, so no loss is computed for them).

    uv run python -m eval.depth_route --measurements eval/artifacts/measurements-k5.json

The inventory mirrors the one the eval measured: the three k5 models at 128k context
(calibrate_models declares exactly that) plus the synthetic window-size arms for the
mechanism section.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.tasks import EVAL_SUITE
from eval.tasks_text import TEXT_SUITE

from volante.registry import ModelInfo, Registry
from volante.router import Router
from volante.types import Task

# Same declared context as eval.calibrate_models._registry_for.
_CONTEXT = 128_000
_OUTPUT = 8_192

_MEASURED_MODELS = (
    "openai-compat/deepseek/deepseek-chat-v3.1",
    "openai-compat/openai/gpt-4o",
    "openai-compat/openai/gpt-4o-mini",
)


def _model(
    model_id: str,
    *,
    tier: int,
    context: int,
    cost: float,
    output: int = _OUTPUT,
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider=model_id.split("/")[0],
        strengths={"coding", "reasoning"},
        context_window=context,
        max_output_tokens=output,
        supports_tools=True,
        cost_per_1k_in=cost,
        cost_per_1k_out=cost,
        tier=tier,
        billing="card",
    )


def _suite_models() -> list[ModelInfo]:
    return [_model(m, tier=4, context=_CONTEXT, cost=0.01) for m in _MEASURED_MODELS]


def _task(goal: str, task_type: str, *, difficulty: str) -> Task:
    return Task(
        id=f"sim-{task_type}-{difficulty}",
        description=goal,
        type=task_type,
        mode="one_shot",
        difficulty=difficulty,
    )


def _per_goal_scores(measurements: dict) -> dict[tuple[str, str], dict[str, float]]:
    """goal-keyed per-model mean scores, keyed by (task_type, goal)."""
    per_goal: dict[tuple[str, str], dict[str, float]] = {}
    for model_id, tasks in measurements.items():
        for task_type, entries in tasks.items():
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("score") is None:
                    continue
                goal = entry["goal"]
                bucket = per_goal.setdefault((task_type, goal), {})
                bucket.setdefault(model_id, []).append(float(entry["score"]))
    return {
        key: {model: sum(v) / len(v) for model, v in models.items()}
        for key, models in per_goal.items()
    }


def _loss(decision, per_goal_scores: dict, key: tuple[str, str]) -> float | None:
    scores = per_goal_scores.get(key)
    if not scores:
        return None
    routed = scores.get(decision.selected_model_id)
    if routed is None:
        return None
    return max(scores.values()) - routed


def _invariance(measurements: dict, per_goal_scores: dict) -> list[dict]:
    """Route every suite goal under the old and the depth-aware engine behavior.

    The OLD behavior is simulated honestly by handing the router the old formula's
    estimate explicitly (that is exactly what the router ran before this change);
    the NEW behavior is the default path. Both use the same inventory and difficulty,
    so any difference in truncation flags, context headroom, ranking or selection is
    attributable to the estimator.
    """
    router = Router(Registry(_suite_models()))
    events: list[dict] = []
    for task in [*EVAL_SUITE, *TEXT_SUITE]:
        key = (task.task_type, task.id)
        if key not in per_goal_scores:
            continue
        goal = task.goal
        t = _task(goal, task.task_type, difficulty="medium")
        old_est = max(256, (len(goal) + 3) // 4 + 128)
        old_decision = router.route_decision(t, estimated_input_tokens=old_est)
        new_decision = router.route_decision(t)
        events.append(
            {
                "goal": task.id,
                "type": task.task_type,
                "old_estimate": old_est,
                "new_estimate": new_decision.estimated_input_tokens,
                "old_selected": old_decision.selected_model_id,
                "new_selected": new_decision.selected_model_id,
                "old_loss": _loss(old_decision, per_goal_scores, key),
                "new_loss": _loss(new_decision, per_goal_scores, key),
            }
        )
    return events


def _mechanism() -> list[dict]:
    """Depth-extended variants of one trivial goal against a mixed-window inventory."""
    variants = {
        "plain": "Write a slugify function.",
        "one file": "Read src/parser.py first, then write a slugify function.",
        "three files + large": (
            "Read src/a.py, src/lib/b.py and src/c.py, all large files, and write a "
            "slugify function."
        ),
    }
    inventory = [
        _model("big/128k", tier=3, context=_CONTEXT, cost=0.05),
        _model("mid/32k", tier=3, context=32_768, cost=0.01),
        _model("small/8k", tier=4, context=8_192, cost=0.001, output=2_048),
    ]
    router = Router(Registry(inventory))
    rows: list[dict] = []
    for label, goal in variants.items():
        decision = router.route_decision(_task(goal, "code", difficulty="trivial"))
        rows.append(
            {
                "variant": label,
                "estimate": decision.estimated_input_tokens,
                "selected": decision.selected_model_id,
                "candidates": [
                    {
                        "id": c.model_id,
                        "rank": c.rank,
                        "truncating": c.requires_truncation,
                        "headroom": next(
                            (x.value for x in c.components if x.name == "context_headroom"),
                            None,
                        ),
                    }
                    for c in decision.candidates
                ],
            }
        )
    return rows


def format_report(events: list[dict], mechanism: list[dict]) -> str:
    est_moved = sum(1 for e in events if e["old_estimate"] != e["new_estimate"])
    changed = [e for e in events if e["old_selected"] != e["new_selected"]]
    losses_old = [e["old_loss"] for e in events if e["old_loss"] is not None]
    losses_new = [e["new_loss"] for e in events if e["new_loss"] is not None]
    mean_old = sum(losses_old) / len(losses_old) if losses_old else None
    mean_new = sum(losses_new) / len(losses_new) if losses_new else None

    lines = [
        f"invariance: {len(events)} measured goals, old vs depth-aware engine behavior:",
        f"  estimates moved: {est_moved}/{len(events)}",
        f"  decisions changed: {len(changed)}/{len(events)}",
        f"  mean loss per goal (best measured minus routed): old={mean_old:.4f} new={mean_new:.4f}",
    ]
    lines.append("")
    lines.append("mechanism, one trivial goal, mixed-window inventory (no measured scores):")
    for row in mechanism:
        cand = ", ".join(
            f"{c['id']}#{c['rank']}{' [truncating]' if c['truncating'] else ''}"
            f"(headroom={c['headroom']:.2f})"
            for c in row["candidates"]
        )
        lines.append(f"  {row['variant']:<22} est={row['estimate']:>6} -> {row['selected']}")
        lines.append(f"  {'':<22} {cand}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--measurements",
        default="eval/artifacts/measurements-k5.json",
        help="goal-labelled measurements file to route against",
    )
    args = parser.parse_args(argv)
    measurements = json.loads(Path(args.measurements).read_text(encoding="utf-8"))
    per_goal_scores = _per_goal_scores(measurements)
    events = _invariance(measurements, per_goal_scores)
    mechanism = _mechanism()
    print(format_report(events, mechanism))


if __name__ == "__main__":
    main()

"""Measure several models on the eval suite and emit a calibration input file.

Volante's router weights a `task_fit` component that comes from a quality profile.
With no profile, every candidate scores the same on it, so the component carries no
information and the router zeroes its weight and says so. That is honest, but it is
not routing: without evidence the ranking reduces to tier and price, and the same
model wins every task type.

`volante --calibrate` turns measurements into a profile. This script produces the
measurements — the step that was missing, and the reason the project shipped no
calibrated evidence of its own.

    uv run python -m eval.calibrate_models --models gpt-4.1-nano,gpt-4o-mini --k 3
    uv run python -m eval.calibrate_models --provider GLM --models glm-4.6,glm-4.5-air --k 3

Measuring a SECOND provider family is the point of --provider. Three models from one
lab tell you almost nothing about whether models have complementary strengths: they
mostly differ in size, so their abilities move together. Routing per task type only
pays off when some cheaper model is genuinely better at one kind of work than a
dearer one — and that is a claim about different labs, not different sizes.

It runs ONLY the baseline arm: one model, one call, no orchestration. That is what a
quality profile is meant to describe — how good a model is at a task type, not how
good a pipeline is. It spends real money, roughly (models x goals x k) calls.

Coverage: the coding suite plus every goal in eval/tasks_text.py, grouped by task
type, so a profile can carry evidence for more than `code`. Whatever type has no
goals yet stays uncalibrated, and the router keeps zeroing task_fit for it —
correctly, because nothing has measured it. Inventing scores would be worse than
admitting the gap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from eval.harness import run_baseline, score_for_calibration
from eval.tasks import EVAL_SUITE
from eval.tasks_text import TEXT_SUITE

from volante.providers.openai_compat import OpenAICompatProvider
from volante.registry import ModelInfo, Registry

# Capability facts for the models this script can drive. Same rule as the runtime
# registry: a number here is either sourced or conservative, never a guess dressed
# up as a fact. These only bound the request (max_tokens) and are not published.
_CONTEXT = 128_000
_MAX_OUTPUT = 8_192


def _registry_for(model_id: str) -> Registry:
    return Registry(
        [
            ModelInfo(
                id=model_id,
                provider="openai_compat",
                strengths={"coding", "reasoning"},
                context_window=_CONTEXT,
                max_output_tokens=_MAX_OUTPUT,
                supports_tools=True,
                cost_per_1k_in=0.0,
                cost_per_1k_out=0.0,
                tier=3,
                billing="card",
            )
        ]
    )


def _summarize(task_type: str, scores: list[float | None]) -> str:
    """Report the mean of the GRADED runs and, separately, how many were unmeasured.

    Averaging `null` as zero would put the conflation this change removes straight
    back into the line a human reads.
    """
    graded = [s for s in scores if s is not None]
    unmeasured = len(scores) - len(graded)
    mean = sum(graded) / len(graded) if graded else 0.0
    tail = f" unmeasured={unmeasured}" if unmeasured else ""
    return f"{task_type}={mean:.3f}(n={len(graded)}){tail}"


async def measure(
    models: list[str],
    k: int,
    only: str | None = None,
    prefix: str = "OPENAI_COMPAT",
    timeout_s: float = 600.0,
) -> dict:
    # Read the endpoint from the environment rather than taking it as an argument:
    # a key belongs in your gitignored .env, not in a command line that lands in
    # shell history and every transcript of this run.
    base_url = os.environ.get(f"{prefix}_BASE_URL")
    api_key = os.environ.get(f"{prefix}_KEY")
    if not base_url or not api_key:
        raise SystemExit(f"{prefix}_BASE_URL and {prefix}_KEY must be set")

    measurements: dict[str, dict[str, list[float]]] = {}
    for wire in models:
        model_id = f"{prefix.lower()}/{wire}"
        registry = _registry_for(model_id)
        # Reasoning models spend a long time before their first token, and a
        # trap-laden analyse goal is exactly the kind of prompt they spend it on.
        # The 120s default times out on them, which would be recorded as a model
        # failure rather than what it is: our own client giving up too early.
        provider = OpenAICompatProvider(
            base_url=base_url, api_key=api_key, model=wire, timeout=timeout_s
        )
        # `float | None`: None is written as JSON null, which volante.calibrate reads
        # as "this run produced nothing usable" and counts toward RELIABILITY instead
        # of grading it. Recording 0.0 here filed our own timeouts and crashed runners
        # as the model being wrong -- the same conflation that once recorded a 120 s
        # client timeout as a failure to analyse.
        by_type: dict[str, list[float | None]] = {}
        goals = [t for t in [*EVAL_SUITE, *TEXT_SUITE] if only in (None, t.task_type)]
        for task in goals:
            bucket = by_type.setdefault(task.task_type, [])
            for run in range(k):
                try:
                    result = await run_baseline(task.goal, provider, model_id, registry)
                    score = score_for_calibration(task, result.output)
                except Exception as exc:  # the run produced nothing: not a zero
                    print(f"  {wire:<14} {task.id:<15} run{run + 1}  ERROR {type(exc).__name__}")
                    bucket.append(None)
                    continue
                bucket.append(score)
                shown = "unmeasured" if score is None else f"{score:.2f}"
                print(f"  {wire:<14} {task.id:<15} run{run + 1}  {shown}")
        measurements[model_id] = by_type
        summary = "  ".join(
            _summarize(t, v) for t, v in sorted(by_type.items())
        )
        print(f"  -> {model_id}: {summary}\n")
    return measurements


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        required=True,
        help="comma-separated wire model names reachable through OPENAI_COMPAT_BASE_URL",
    )
    parser.add_argument("--k", type=int, default=3, help="runs per goal per model")
    parser.add_argument(
        "--provider",
        default="OPENAI_COMPAT",
        help="env prefix of the OpenAI-compatible endpoint to measure, e.g. GLM reads "
        "GLM_BASE_URL and GLM_KEY (default: OPENAI_COMPAT)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="per-call timeout in seconds (default 600; reasoning models need it)",
    )
    parser.add_argument(
        "--only",
        help="measure a single task type — useful to test whether a new goal separates "
        "models before paying to re-measure everything",
    )
    parser.add_argument("--out", default="measurements.json", help="where to write them")
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("--models needs at least one model")
    if len(models) < 2:
        # A one-model file cannot calibrate anything on its own: a component whose
        # value is identical across every candidate is the flat constant this exists
        # to fix. It is still a legitimate thing to produce — measuring one model to
        # compare against, or to merge into, an existing set — so this warns instead
        # of refusing.
        print(
            "note: one model measured. This file cannot calibrate a router by itself; "
            "merge it with measurements for the other models first."
        )
    if args.k < 1:
        raise SystemExit("--k must be at least 1")

    measurements = asyncio.run(measure(models, args.k, args.only, args.provider, args.timeout))
    Path(args.out).write_text(json.dumps(measurements, indent=2, sort_keys=True) + "\n")
    # No spend total on purpose. Pricing a run needs per-model prices, and this
    # script deliberately carries none — inventing them here to print a tidy figure
    # is the same class of error the registry was just fixed for. The provider's own
    # dashboard is the authority on what this cost.
    print(f"wrote {args.out}")
    print(f"next: uv run volante --calibrate {args.out} --profiles quality-profiles.json")


if __name__ == "__main__":
    main()

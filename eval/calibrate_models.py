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

It runs ONLY the baseline arm: one model, one call, no orchestration. That is what a
quality profile is meant to describe — how good a model is at a task type, not how
good a pipeline is. It spends real money, roughly (models x goals x k) calls.

Scope, stated plainly: every goal in the eval suite is a coding task, so this can
only produce `code` evidence. The other three task types stay uncalibrated, and the
router will keep zeroing task_fit for them — correctly, because nothing has measured
them. Graded research/write/analyze goals would be needed for that, and inventing
scores for them would be worse than admitting the gap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from eval.harness import run_baseline, score_code
from eval.tasks import EVAL_SUITE

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


async def measure(models: list[str], k: int) -> dict:
    base_url = os.environ.get("OPENAI_COMPAT_BASE_URL")
    api_key = os.environ.get("OPENAI_COMPAT_KEY")
    if not base_url or not api_key:
        raise SystemExit("OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_KEY must be set")

    measurements: dict[str, dict[str, list[float]]] = {}
    for wire in models:
        model_id = f"openai/{wire}"
        registry = _registry_for(model_id)
        provider = OpenAICompatProvider(base_url=base_url, api_key=api_key, model=wire)
        scores: list[float] = []
        for task in EVAL_SUITE:
            for run in range(k):
                try:
                    result = await run_baseline(task.goal, provider, model_id, registry)
                    score = score_code(result.output, task.reference_test)
                except Exception as exc:  # a failed run is evidence too
                    print(f"  {wire:<14} {task.id:<15} run{run + 1}  ERROR {type(exc).__name__}")
                    scores.append(0.0)
                    continue
                scores.append(score)
                print(f"  {wire:<14} {task.id:<15} run{run + 1}  {score:.2f}")
        measurements[model_id] = {"code": scores}
        mean = sum(scores) / len(scores) if scores else 0.0
        print(f"  -> {model_id}: mean(code)={mean:.3f} over {len(scores)} runs\n")
    return measurements


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        required=True,
        help="comma-separated wire model names reachable through OPENAI_COMPAT_BASE_URL",
    )
    parser.add_argument("--k", type=int, default=3, help="runs per goal per model")
    parser.add_argument("--out", default="measurements.json", help="where to write them")
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        # One model cannot calibrate anything: a component whose value is identical
        # across every candidate is exactly the flat constant this is meant to fix.
        raise SystemExit("--models needs at least two models to be worth measuring")
    if args.k < 1:
        raise SystemExit("--k must be at least 1")

    measurements = asyncio.run(measure(models, args.k))
    Path(args.out).write_text(json.dumps(measurements, indent=2, sort_keys=True) + "\n")
    # No spend total on purpose. Pricing a run needs per-model prices, and this
    # script deliberately carries none — inventing them here to print a tidy figure
    # is the same class of error the registry was just fixed for. The provider's own
    # dashboard is the authority on what this cost.
    print(f"wrote {args.out}")
    print(f"next: uv run volante --calibrate {args.out} --profiles quality-profiles.json")


if __name__ == "__main__":
    main()

"""Turn measured per-model scores into the quality-profile evidence the router uses.

Volante's router treats `task_fit`/`reliability` as evidence-free — and gives them zero
weight — until someone supplies a calibrated `ModelQualityProfile`. This module is the
bridge: it converts a plain measurements file (scores YOU observed, from the eval
harness, a CI job, or manual grading) into a strict quality-profiles JSON that
``VOLANTE_QUALITY_PROFILES_FILE`` accepts.

Deliberately decoupled from `eval/`, which ships only in a source checkout: any harness
that can emit per-model, per-task-type scores can feed this. Volante does not bundle
benchmark numbers of its own — the evidence stays yours, with your provenance on it.

Measurements format (scores are 0..1, one entry per observed run)::

    {
      "anthropic/claude-opus-4-8": {"code": [1.0, 0.8, 1.0], "analyze": [0.9]},
      "ollama/qwen-coder":         {"code": [0.4, 0.0]}
    }
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from volante.inventory import TASK_TYPES

# Confidence never reaches 1.0: a handful of runs is evidence, not certainty. n/(n+3)
# gives 0.25 at n=1, 0.50 at n=3, 0.75 at n=9 — and the router blends a profile toward
# the metadata prior by exactly this factor, so thin evidence moves the score a little
# and thick evidence moves it a lot.
_CONFIDENCE_PRIOR = 3.0
_MAX_CONFIDENCE = 0.9


class CalibrationError(ValueError):
    """The measurements file is not usable as evidence."""


def _scores(raw: Any, *, model_id: str, task_type: str) -> tuple[list[float], int]:
    """Return ``(graded_scores, failed_runs)``.

    ``null`` in a score list means "this run produced nothing usable". Those runs are
    counted separately instead of being graded 0.0, because a refusal/crash is evidence
    about RELIABILITY while a low score is evidence about QUALITY — conflating them makes
    the reliability component a copy of the quality component.
    """
    if not isinstance(raw, list) or not raw:
        raise CalibrationError(
            f"{model_id}.{task_type} must be a non-empty list of 0..1 scores"
        )
    values: list[float] = []
    failures = 0
    for item in raw:
        if item is None:
            failures += 1
            continue
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise CalibrationError(
                f"{model_id}.{task_type} contains a non-numeric score: {item!r}"
            )
        value = float(item)
        if not 0.0 <= value <= 1.0:
            raise CalibrationError(
                f"{model_id}.{task_type} score {value} is outside 0..1"
            )
        values.append(value)
    if not values:
        raise CalibrationError(
            f"{model_id}.{task_type} has no graded runs (only nulls); a task score "
            "needs at least one completed run"
        )
    return values, failures


def build_profiles(
    measurements: Mapping[str, Any], *, source: str
) -> dict[str, dict[str, Any]]:
    """Aggregate raw per-run scores into one quality profile per model.

    ``task_scores`` are means per task type, ``reliability_score`` is the share of runs
    that produced any usable output at all (score > 0), and ``confidence`` grows with the
    number of observations. ``source`` records where the evidence came from.
    """
    if not isinstance(measurements, Mapping) or not measurements:
        raise CalibrationError("measurements must be a non-empty object")
    profiles: dict[str, dict[str, Any]] = {}
    for model_id, raw_tasks in measurements.items():
        if not isinstance(model_id, str) or not model_id.strip():
            raise CalibrationError("measurement keys must be canonical model ids")
        if not isinstance(raw_tasks, Mapping) or not raw_tasks:
            raise CalibrationError(f"{model_id} must map task types to score lists")
        unknown = set(raw_tasks) - TASK_TYPES
        if unknown:
            raise CalibrationError(
                f"{model_id} has unknown task types {sorted(unknown)}; "
                f"expected any of {sorted(TASK_TYPES)}"
            )
        task_scores: dict[str, float] = {}
        per_task_counts: list[int] = []
        graded_total = 0
        failed_total = 0
        for task_type, raw in raw_tasks.items():
            values, failures = _scores(raw, model_id=model_id, task_type=task_type)
            task_scores[task_type] = round(sum(values) / len(values), 4)
            per_task_counts.append(len(values) + failures)
            graded_total += len(values)
            failed_total += failures
        profile: dict[str, Any] = {
            "task_scores": task_scores,
            # Macro-average across task types: pooling raw runs would let an unbalanced
            # sample (30 code runs, 1 write run) make overall_score describe the sampling
            # rather than the model.
            "overall_score": round(sum(task_scores.values()) / len(task_scores), 4),
            "source": source,
            # The router applies ONE confidence to every task type, so it must be the
            # confidence of the WEAKEST-sampled one. Pooling would let 30 unrelated code
            # runs make a single write observation read as near-certain.
            "confidence": round(
                min(
                    _MAX_CONFIDENCE,
                    min(n / (n + _CONFIDENCE_PRIOR) for n in per_task_counts),
                ),
                4,
            ),
        }
        # Only claim reliability when failures were actually recorded (a `null` run).
        # Deriving it from quality scores would double-count the same evidence.
        if failed_total:
            profile["reliability_score"] = round(
                graded_total / (graded_total + failed_total), 4
            )
        profiles[model_id] = profile
    return profiles


def calibrate_file(
    measurements_path: str | Path, output_path: str | Path, *, source: str
) -> dict[str, dict[str, Any]]:
    """Read measurements, write a strict quality-profiles file, return the profiles."""
    if not source.strip():
        raise CalibrationError("source label must not be blank")
    if len(source) > 200:
        raise CalibrationError("source label must be at most 200 characters")
    try:
        raw = Path(measurements_path).expanduser().read_text(encoding="utf-8")
        decoded = json.loads(raw)
    except UnicodeDecodeError as exc:
        raise CalibrationError(
            f"{measurements_path} is not UTF-8 text: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{measurements_path} is not valid JSON: {exc}") from exc
    profiles = build_profiles(decoded, source=source)
    target = Path(output_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profiles, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Round-trip through the strict loader Volante itself uses: writing a file that the
    # engine would later reject — while printing "Wrote N profiles" — is worse than
    # failing here, because the rejection surfaces on an unrelated command.
    from volante.inventory import InventoryConfigError, load_quality_profiles_file

    try:
        load_quality_profiles_file(target)
    except InventoryConfigError as exc:
        raise CalibrationError(
            f"the generated profiles are not loadable by Volante ({exc}); "
            "check the model ids in your measurements file"
        ) from exc
    return profiles

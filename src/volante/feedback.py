"""Turn ``--verify`` runs from real use into calibration evidence.

The router treats `task_fit`/`reliability` as evidence-free until someone supplies a
calibrated `ModelQualityProfile`, and the ONE place Volante already measures real
output is the optional check-derivation detector (``--verify`` /
``runtime.verify_answer``). This module makes that measurement usable: it keeps a
per-run ledger of what the detector saw, and it decides which ledger runs are
evidence at all.

WHICH RUNS ARE EVIDENCE, and why it is only the passing ones. The detector is
deliberately not a verdict: across its own measurements, 45% of the answers it
flags are correct — the derived CHECK is wrong that often — so a flagged run cannot
be graded zero without grading innocent code half the time. A clean run is the rare
verdict-like output (it never approved a wrong answer in 22 measured runs), so it
enters the ledger graded 1.0, and the evaluator's goal-naming rule keeps its breadth
honest: confidence counts DISTINCT GOALS, and a real-world goal that verified clean
is exactly the goal-shaped observation calibration wants.

The ledger itself keeps EVERY ran check, passing or not, because "how often does
verification fire, and what does it see" is answerable from the raw record; the
merge step grades only the passing runs and counts what it set aside and why.

ATTRIBUTION IS SINGLE-MODEL ONLY. Verification examines the FINAL answer, and the
ledger credits only a model it knows produced that answer: the direct path, whose
one executed model and DECLARED task type are both known. A run whose executing
model cannot be identified (e.g. an orchestrated synthesis) is skipped — an
observation with no subject is not a measurement of anything.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from volante.direct import _DIRECT_TASK_TYPE
from volante.inventory import TASK_TYPES
from volante.verify import CheckReport

logger = logging.getLogger(__name__)

FEEDBACK_ENV = "VOLANTE_FEEDBACK_FILE"
_DEFAULT_FEEDBACK_PATH = "~/.volante/feedback.jsonl"

# Real-world observations describe the model as it runs TODAY. A model can be
# replaced or re-tuned under the same slot, so evidence older than the window is
# stale — it no longer describes the candidate the router would pick. Mirrors the
# engine's other product defaults (VOLANTE_MAX_SUBSCRIPTION_CALLS=16,
# VOLANTE_AGENTIC_MAX_ITERS=8): a number the user can change, not a constant only
# the code can. `0` disables the cutoff.
FEEDBACK_MAX_AGE_ENV = "VOLANTE_FEEDBACK_MAX_AGE_DAYS"
_DEFAULT_MAX_AGE_DAYS = 90

# The ledger holds up to this much of a goal: a label for the run, not a transcript,
# and the line must stay well under POSIX PIPE_BUF (4096) so a single O_APPEND write
# stays atomic even when several processes append concurrently.
_MAX_GOAL_CHARS = 240

_LEDGER_DIR_MODE = 0o700
_LEDGER_FILE_MODE = 0o600

# The one serialized routing decision shape whose task type is a DECLARED constant
# rather than a guess: answer_directly emits exactly this key, carrying the executed
# model id, for the single model that produced the final answer.
_DIRECT_DECISION_KEY = "__direct__"


class FeedbackError(ValueError):
    """A feedback ledger cannot be read as the evidence it claims to be."""


def feedback_log_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the feedback-ledger path, or ``None`` if recording is disabled.

    ``VOLANTE_FEEDBACK_FILE`` overrides the default ``~/.volante/feedback.jsonl``;
    setting it to an empty string turns feedback recording off entirely.
    """
    if env is None:
        env = os.environ
    if FEEDBACK_ENV in env:
        configured = env[FEEDBACK_ENV].strip()
        if not configured:
            return None
        return Path(configured).expanduser()
    return Path(_DEFAULT_FEEDBACK_PATH).expanduser()


def feedback_max_age_days(env: Mapping[str, str] | None = None) -> int:
    """How old a verification observation may be before it stops being evidence.

    ``VOLANTE_FEEDBACK_MAX_AGE_DAYS`` overrides the 90-day default; ``0`` disables
    the cutoff entirely (all observations qualify regardless of age).
    """
    if env is None:
        env = os.environ
    raw = env.get(FEEDBACK_MAX_AGE_ENV, "").strip()
    if not raw:
        return _DEFAULT_MAX_AGE_DAYS
    try:
        days = int(raw)
    except ValueError as exc:
        raise FeedbackError(
            f"{FEEDBACK_MAX_AGE_ENV} must be a non-negative integer, got {raw!r}"
        ) from exc
    if days < 0:
        raise FeedbackError(f"{FEEDBACK_MAX_AGE_ENV} must be a non-negative integer, got {raw!r}")
    return days


def _field(result: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
    """Read a field from either RunResult-shaped object or Mapping shapes."""
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _attribution(result: Mapping[str, Any] | Any) -> tuple[str, str] | None:
    """The ``(model_id, task_type)`` this run's final answer can be credited to.

    ``None`` means the subject cannot be identified with certainty: no verified
    checks, a non-successful run, more or fewer than one routing decision, a
    non-direct decision (task types there are the planner's labels), or no
    recorded executing model.
    """
    checks = _field(result, "checks")
    if not isinstance(checks, CheckReport):
        return None
    if not checks.ran:
        return None
    if _field(result, "status") != "success":
        return None
    decisions = _field(result, "routing_decisions", {}) or {}
    if not isinstance(decisions, Mapping) or len(decisions) != 1:
        return None
    entry = next(iter(decisions.values()))
    if not isinstance(entry, Mapping):
        return None
    if next(iter(decisions)) != _DIRECT_DECISION_KEY:
        return None
    model_id = entry.get("executed_model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    return model_id.strip(), _DIRECT_TASK_TYPE


def _restrict_to_owner(path: Path) -> None:
    """Bring the default ledger and its directory down to owner-only.

    Best-effort, mirroring the usage ledger: this is the same kind of private record
    (goals, models, checks) and the same home-directory convention.
    """
    for target, mode in ((path.parent, _LEDGER_DIR_MODE), (path, _LEDGER_FILE_MODE)):
        try:
            if stat.S_IMODE(target.stat().st_mode) & 0o077:
                os.chmod(target, mode)
        except OSError as exc:
            logger.debug("feedback ledger permissions left as found: %s", exc)


def record_observation(
    path: str | os.PathLike[str],
    *,
    result: Mapping[str, Any] | Any,
    goal: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Append one verification observation to the ledger.

    Returns the observation dict when one was recorded, ``None`` when the run was
    not attributable (no verified checks, no single executing model, ...). Never
    raises — a telemetry problem must not fail the run that produced it.
    """
    attributed = _attribution(result)
    if attributed is None:
        return None
    model_id, task_type = attributed
    checks = _field(result, "checks")
    observation = {
        "ts": (now or datetime.now(UTC)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model_id": model_id,
        "task_type": task_type,
        "goal": (goal or "").strip()[:_MAX_GOAL_CHARS],
        "all_passed": bool(checks.all_passed),
        "total": int(checks.total),
        "failed": len(checks.failed),
        "error": checks.error,
    }
    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=_LEDGER_DIR_MODE)
        if env is None:
            env = os.environ
        if FEEDBACK_ENV not in env:
            _restrict_to_owner(target)
        line = json.dumps(observation, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, _LEDGER_FILE_MODE)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        logger.debug("feedback observation not recorded: %s", exc)
        return None
    return observation


def load_ledger(
    path: str | os.PathLike[str],
) -> tuple[list[dict[str, Any]], int]:
    """Read every observation line; ``(observations, skipped_lines)``.

    A blank line is skipped silently; a line that is not a JSON object is skipped
    and counted — the ledger is machine-written, so a broken line is corruption and
    must not abort calibration, but silently re-reading it would hide the problem.
    """
    try:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FeedbackError(f"{path} is not UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise FeedbackError(f"{path} is not a readable feedback ledger: {exc}") from exc
    observations: list[dict[str, Any]] = []
    skipped = 0
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped += 1
            logger.warning(
                "feedback ledger %s line %d is not valid JSON (%s); skipped",
                path,
                line_no,
                exc,
            )
            continue
        if not isinstance(item, dict):
            skipped += 1
            logger.warning("feedback ledger %s line %d is not an object; skipped", path, line_no)
            continue
        observations.append(item)
    return observations, skipped


def _parse_ts(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def measurements_from_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, int]]:
    """Grade the ledger into measurements-shaped evidence.

    Only runs where ``all_passed`` is true become graded entries (score 1.0) — the
    only output of the detector that is verdict-like. Everything else is discarded
    and counted by reason, so a calibration run can say exactly what it set aside
    and why instead of letting the reader guess.

    ``max_age_days`` applies a recency cutoff on the observation's ``ts``: older
    evidence no longer describes the model running today. An observation with no
    parseable timestamp is kept — it cannot be proven stale, and the ledger is
    machine-written with timestamps, so its absence is a shape the writer never
    produces.

    Returns ``(measurements, discarded)`` where ``measurements`` has the shape
    ``build_profiles`` accepts (``{model: {task: [{"goal": ..., "score": 1.0}]}}``)
    and repeated (model, task_type, goal) observations count once.
    """
    cutoff: datetime | None = None
    if max_age_days is not None and max_age_days > 0:
        stamp = now if now is not None else datetime.now(UTC)
        cutoff = stamp - timedelta(days=max_age_days)
    measurements: dict[str, dict[str, list[dict[str, Any]]]] = {}
    discarded: dict[str, int] = {}
    seen: set[tuple[str, str, str]] = set()
    for observation in observations:
        if not isinstance(observation, Mapping):
            discarded["not_an_object"] = discarded.get("not_an_object", 0) + 1
            continue
        model_id = observation.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            discarded["unknown_model_id"] = discarded.get("unknown_model_id", 0) + 1
            continue
        task_type = observation.get("task_type")
        if not isinstance(task_type, str) or task_type not in TASK_TYPES:
            discarded["unknown_task_type"] = discarded.get("unknown_task_type", 0) + 1
            continue
        goal = observation.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            discarded["blank_goal"] = discarded.get("blank_goal", 0) + 1
            continue
        ts = _parse_ts(observation.get("ts"))
        if cutoff is not None and ts is not None and ts < cutoff:
            discarded["too_old"] = discarded.get("too_old", 0) + 1
            continue
        if not observation.get("all_passed"):
            # The ledger stores counts, so the reason is derived from the same fields
            # CheckReport would: error -> no verdict at all; failures -> the detector
            # flagged the answer (45% of the time it is flagging its own wrong check);
            # zero checks -> nothing to run; else too few grounded checks to mean it.
            if observation.get("error") is not None:
                reason = "checks_error"
            elif int(observation.get("failed", 0)) > 0:
                reason = "checks_failed"
            elif int(observation.get("total", 0)) == 0:
                reason = "no_checks_derived"
            else:
                reason = "too_few_checks"
            discarded[reason] = discarded.get(reason, 0) + 1
            continue
        key = (model_id, task_type, goal.strip())
        if key in seen:
            discarded["duplicate_goal"] = discarded.get("duplicate_goal", 0) + 1
            continue
        seen.add(key)
        measurements.setdefault(model_id, {}).setdefault(task_type, []).append(
            {"goal": goal.strip(), "score": 1.0}
        )
    return measurements, discarded


def merge_measurements(
    base: Mapping[str, Any],
    extra: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
) -> tuple[dict[str, Any], int]:
    """Append ``extra`` observations to ``base`` measurements (goal-shaped only).

    An extra entry whose (model, task_type, goal) already exists — in base or in an
    earlier merged ledger — is dropped, because a goal measured twice is the same
    claim twice, and `confidence` counts DISTINCT goals. Returns ``(merged, added)``.
    """
    merged: dict[str, Any] = {}
    seen: set[tuple[str, str, str]] = set()
    for model_id, tasks in base.items():
        merged[model_id] = dict(tasks)
        for task_type, entries in tasks.items():
            for entry in entries:
                if isinstance(entry, Mapping):
                    goal = entry.get("goal")
                    if isinstance(goal, str) and goal.strip():
                        seen.add((model_id, task_type, goal.strip()))
    added = 0
    for model_id, tasks in extra.items():
        merged.setdefault(model_id, {})
        for task_type, entries in tasks.items():
            merged[model_id].setdefault(task_type, [])
            for entry in entries:
                goal = entry.get("goal")
                if not isinstance(goal, str) or not goal.strip():
                    continue
                key = (model_id, task_type, goal.strip())
                if key in seen:
                    continue
                seen.add(key)
                merged[model_id][task_type].append(dict(entry))
                added += 1
    return merged, added

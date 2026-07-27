"""Lightweight, opt-in observability for Volante: a diagnostic log level knob and a
best-effort usage ledger.

Two independent concerns live here because both are "how do I see what Volante did"
questions that the CLI, the MCP server, and the Web UI all need to answer the same way:

* ``configure_logging`` maps the ``VOLANTE_LOG`` environment variable to the standard
  library's root logger so ``VOLANTE_LOG=debug`` surfaces engine diagnostics on stderr
  (e.g. VS Code's MCP "Output" pane). Absent/blank leaves logging untouched — Volante is
  silent by default and never hijacks a host application's logging config.

* ``record_run`` appends one JSON object per orchestrated goal to a usage ledger
  (``~/.volante/usage.jsonl`` by default, overridable with ``VOLANTE_USAGE_LOG``; blank
  disables it). This is the persistent record behind the Web UI "Usage" dashboard and
  ``volante --usage``, so a goal delegated from an IDE via MCP is still auditable later.

Recording is strictly best-effort: a telemetry problem must never fail or slow a real
run, so every filesystem/serialization error is swallowed to a debug log. Each record is
truncated well under the POSIX ``PIPE_BUF`` (4096 bytes) so a single ``O_APPEND`` write
stays atomic even when several processes (MCP server, Web UI, CLI) log concurrently.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("volante.observability")

# Keep the persisted goal short enough that the whole JSON line stays comfortably under
# PIPE_BUF (4096) — that is what makes the append atomic — and to avoid persisting large
# prompts verbatim. The goal is a human label for the run, not a full transcript.
_MAX_GOAL_CHARS = 240
_DEFAULT_USAGE_PATH = "~/.volante/usage.jsonl"


def configure_logging(env: Mapping[str, str] | None = None) -> int | None:
    """Configure root logging from ``VOLANTE_LOG`` and return the level applied.

    ``VOLANTE_LOG`` accepts a level name (``debug``/``info``/``warning``/``error``/
    ``critical``, case-insensitive) or a numeric level. Blank/unset/unrecognized leaves
    the root logger untouched and returns ``None`` — Volante stays silent unless the host
    asks for diagnostics. Output goes to stderr so it never corrupts stdout (the MCP
    stdio channel, or a piped CLI answer).
    """
    if env is None:
        env = os.environ
    raw = env.get("VOLANTE_LOG", "").strip()
    if not raw:
        return None
    level = logging.getLevelName(raw.upper())  # name -> int, or "Level X" for unknowns
    if not isinstance(level, int):
        try:
            level = int(raw)
        except ValueError:
            return None
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(level)  # basicConfig is a no-op if handlers already exist
    return level


def usage_log_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the usage-ledger path, or ``None`` if disabled.

    ``VOLANTE_USAGE_LOG`` overrides the default ``~/.volante/usage.jsonl``; setting it to an
    empty string turns usage recording off entirely.
    """
    if env is None:
        env = os.environ
    if "VOLANTE_USAGE_LOG" in env:
        configured = env["VOLANTE_USAGE_LOG"].strip()
        if not configured:
            return None
        return Path(configured).expanduser()
    return Path(_DEFAULT_USAGE_PATH).expanduser()


def build_record(
    result: Mapping[str, Any] | Any,
    *,
    goal: str,
    prefer: str | None,
    source: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a run outcome into a flat, JSON-serializable ledger record.

    ``result`` may be a mapping (the MCP ``run_goal`` dict) or an object with the same
    attributes (a ``RunResult``); ``_field`` reads either shape so callers don't convert.
    """

    def _field(name: str, default: Any = None) -> Any:
        if isinstance(result, Mapping):
            return result.get(name, default)
        return getattr(result, name, default)

    decisions = _field("routing_decisions", {}) or {}
    models: list[str] = []
    if isinstance(decisions, Mapping):
        for raw in decisions.values():
            if not isinstance(raw, Mapping):
                continue
            model_id = raw.get("executed_model_id") or raw.get("selected_model_id")
            if isinstance(model_id, str) and model_id not in models:
                models.append(model_id)
    stamp = now or datetime.now(UTC)
    goal_text = (goal or "").strip()
    return {
        "ts": stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": source,
        "status": _field("status"),
        "prefer": prefer or "quality",
        "goal": goal_text[:_MAX_GOAL_CHARS],
        "goal_truncated": len(goal_text) > _MAX_GOAL_CHARS,
        "billed_usd": float(_field("billed_usd", 0.0) or 0.0),
        "credit_usd": float(_field("credit_usd", 0.0) or 0.0),
        # Persisted with the amounts so a later audit can tell inferred costs from
        # provider-reported ones instead of reading every figure as authoritative.
        "cost_estimated": bool(_field("cost_estimated", False)),
        "duration_ms": int(_field("duration_ms", 0) or 0),
        "subscription_calls": int(_field("subscription_calls", 0) or 0),
        "failed_task": _field("failed_task"),
        "error_code": _field("error_code"),
        "models": models,
    }


def record_run(
    result: Mapping[str, Any] | Any,
    *,
    goal: str,
    prefer: str | None,
    source: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one usage record for a completed run. Best-effort: returns ``True`` if a
    line was written, ``False`` if recording is disabled or any error was swallowed.

    Never raises — a telemetry failure must not break the run that produced it.
    """
    path = usage_log_path(env)
    if path is None:
        return False
    try:
        record = build_record(result, goal=goal, prefer=prefer, source=source, now=now)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Single O_APPEND write of a sub-PIPE_BUF line -> atomic across processes on POSIX.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        _LOG.debug("usage recording skipped: %s", exc)
        return False


def read_runs(
    env: Mapping[str, str] | None = None,
    *,
    path: Path | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` most-recent usage records, newest first.

    Malformed or partially written lines are skipped rather than raising, so a truncated
    tail (e.g. a concurrent writer mid-append) never breaks the reader.
    """
    if limit < 1:
        return []
    target = path if path is not None else usage_log_path(env)
    if target is None or not target.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with target.open(encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:  # pragma: no cover - defensive
        _LOG.debug("usage read skipped: %s", exc)
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
        if len(records) >= limit:
            break
    return records


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a list of usage records into dashboard summary tiles."""
    total = len(runs)
    successes = sum(1 for r in runs if r.get("status") == "success")
    billed = sum(float(r.get("billed_usd") or 0.0) for r in runs)
    credit = sum(float(r.get("credit_usd") or 0.0) for r in runs)
    subscription_calls = sum(int(r.get("subscription_calls") or 0) for r in runs)
    durations = [int(r.get("duration_ms") or 0) for r in runs]
    avg_duration = round(sum(durations) / total) if total else 0
    # A total is the first number a budgeting user reads, so it must carry the same
    # estimate signal the per-run rows do. Report how much of each total came from
    # estimated runs instead of tagging a mostly-authoritative total wholesale.
    estimated_runs = [r for r in runs if r.get("cost_estimated")]
    return {
        "total_runs": total,
        "successes": successes,
        "success_rate": round(successes / total, 4) if total else 0.0,
        "total_billed_usd": round(billed, 6),
        "total_credit_usd": round(credit, 6),
        "total_subscription_calls": subscription_calls,
        "avg_duration_ms": avg_duration,
        "estimated_runs": len(estimated_runs),
        "estimated_billed_usd": round(
            sum(float(r.get("billed_usd") or 0.0) for r in estimated_runs), 6
        ),
        "estimated_credit_usd": round(
            sum(float(r.get("credit_usd") or 0.0) for r in estimated_runs), 6
        ),
    }

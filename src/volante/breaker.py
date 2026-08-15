"""Circuit breaker: stop a model in an outage from consuming calls again and again.

The engine's failure handling is sound but admission-bound. A model that starts
timing out gets backoff retries on the same task, is rerouted, and then the NEXT
task probes it again — backoff, retries, reroute, repeat. Across a run of many
tasks, one broken model can burn multiple calls per task. The breaker gives the
retry/reroute machinery memory of recent failures and short-circuits it.

What counts as an outage, and why, follows the exception classes the engine
already classifies at the call sites:

- ``TimeoutError`` and ``ProviderError(retryable=True)``: the provider is flaky
  or down right now. This is what backoff exists for, and the breaker gives it
  memory across tasks.
- ``candidate_unavailable`` / ``provider_unavailable``: the deployment or
  endpoint is inaccessible. Rerouting is already fail-fast, but every task still
  probes it once — with the breaker it does not.

What is deliberately NOT an outage (``outage_kind`` returns ``None``):

- ``quota_exhausted``: depleted credit is a billing truth, not a transient; the
  run gate and the no-backoff reroute already handle it, and opening the breaker
  would blur exhaustion with an outage.
- ``EmptyOutputError``, ``IncompleteOutputError``, and other non-retryable
  ``ProviderError``s: the model answered (badly, or not at all); that is
  evidence for calibration and verification, not circuit state.

Only consecutive failures count, and a single success resets the streak. The
breaker opens after ``threshold`` consecutive failures and stays open for
``cooldown`` seconds; after the cooldown the model is probed again (half-open),
and the streak makes one new failure slam it shut at once.

State is per process by design: the breaker protects the wallet within one run
session, and concurrent sessions (CLI + MCP server + Web UI) each see their own
outages rather than fighting over shared mutable state. The same outage events
are appended to a persistent ledger (``~/.volante/reliability.jsonl``) so
calibration and post-mortems can see what the breaker saw.

Both product defaults (threshold 2, cooldown 60s) mirror the engine's other
product defaults: numbers the operator can change, not constants only the code
can.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from volante.providers.base import (
    EmptyOutputError,
    IncompleteOutputError,
    ProviderError,
)

logger = logging.getLogger(__name__)

BREAKER_THRESHOLD_ENV = "VOLANTE_BREAKER_THRESHOLD"
BREAKER_COOLDOWN_ENV = "VOLANTE_BREAKER_COOLDOWN_SECONDS"
RELIABILITY_LOG_ENV = "VOLANTE_RELIABILITY_LOG"
_DEFAULT_THRESHOLD = 2
_DEFAULT_COOLDOWN_SECONDS = 60.0
_DEFAULT_RELIABILITY_PATH = "~/.volante/reliability.jsonl"

_LEDGER_DIR_MODE = 0o700
_LEDGER_FILE_MODE = 0o600


class BreakerConfigError(ValueError):
    """A breaker setting cannot be read as the number it claims to be."""


def breaker_threshold(env: os._Environ | dict[str, str] | None = None) -> int:
    """Consecutive failures that open the breaker (default 2).

    ``VOLANTE_BREAKER_THRESHOLD`` overrides it; the value must be a positive
    integer.
    """
    if env is None:
        env = os.environ
    raw = env.get(BREAKER_THRESHOLD_ENV, "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD
    try:
        value = int(raw)
    except ValueError as exc:
        raise BreakerConfigError(
            f"{BREAKER_THRESHOLD_ENV} must be a positive integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise BreakerConfigError(f"{BREAKER_THRESHOLD_ENV} must be a positive integer, got {raw!r}")
    return value


def breaker_cooldown_seconds(env: os._Environ | dict[str, str] | None = None) -> float:
    """How long an open breaker stays closed before the next probe (default 60).

    ``VOLANTE_BREAKER_COOLDOWN_SECONDS`` overrides it; the value must be a
    non-negative number.
    """
    if env is None:
        env = os.environ
    raw = env.get(BREAKER_COOLDOWN_ENV, "").strip()
    if not raw:
        return _DEFAULT_COOLDOWN_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise BreakerConfigError(
            f"{BREAKER_COOLDOWN_ENV} must be a non-negative number, got {raw!r}"
        ) from exc
    if value < 0:
        raise BreakerConfigError(
            f"{BREAKER_COOLDOWN_ENV} must be a non-negative number, got {raw!r}"
        )
    return value


def reliability_log_path(env: os._Environ | dict[str, str] | None = None) -> Path | None:
    """Resolve the reliability-ledger path, or ``None`` if recording is disabled.

    ``VOLANTE_RELIABILITY_LOG`` overrides the default
    ``~/.volante/reliability.jsonl``; setting it to an empty string turns
    reliability recording off entirely.
    """
    if env is None:
        env = os.environ
    if RELIABILITY_LOG_ENV in env:
        configured = env[RELIABILITY_LOG_ENV].strip()
        if not configured:
            return None
        return Path(configured).expanduser()
    return Path(_DEFAULT_RELIABILITY_PATH).expanduser()


def outage_kind(exc: BaseException) -> str | None:
    """Classify a provider failure as an outage, or ``None`` if it is not one.

    The kinds mirror the engine's reroute reasons ("provider_timeout",
    "provider_unavailable", "candidate_unavailable") so a reliability ledger
    reads like the run trace it came from.
    """
    if isinstance(exc, TimeoutError):
        return "timeout"
    if not isinstance(exc, ProviderError):
        return None
    if isinstance(exc, (EmptyOutputError, IncompleteOutputError)):
        return None
    if exc.quota_exhausted:
        return None
    if exc.retryable or exc.provider_unavailable:
        return "provider_unavailable"
    if exc.candidate_unavailable:
        return "candidate_unavailable"
    return None


@dataclass
class _BreakerState:
    consecutive: int = 0
    opened_at: float | None = None
    last_kind: str | None = None


class CircuitBreaker:
    """Per-model consecutive-failure breaker (in-memory, per process)."""

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        self._threshold = breaker_threshold() if threshold is None else threshold
        self._cooldown = (
            breaker_cooldown_seconds() if cooldown_seconds is None else cooldown_seconds
        )
        if self._threshold < 1:
            raise ValueError("breaker threshold must be a positive integer")
        if self._cooldown < 0:
            raise ValueError("breaker cooldown must be non-negative")
        self._state: dict[str, _BreakerState] = {}

    def record_failure(
        self, model_id: str, *, now: float | None = None, kind: str | None = None
    ) -> None:
        ts = time.time() if now is None else now
        entry = self._state.setdefault(model_id, _BreakerState())
        entry.consecutive += 1
        if kind is not None:
            entry.last_kind = kind
        if entry.consecutive >= self._threshold:
            # Open on the FIRST crossing; keep the original anchor while the
            # breaker is still within its cooldown (so a burst of parallel
            # failures does not extend the lockout). Reopen with a fresh anchor
            # when a post-cooldown probe fails again — a permanently dead model
            # is probed once per cooldown, never once per task.
            if entry.opened_at is None or not self.is_open(model_id, now=ts):
                entry.opened_at = ts

    def record_success(self, model_id: str, *, now: float | None = None) -> None:
        # One success resets the streak and closes the breaker: the model
        # answered, whatever happened before, so the next task should try it.
        self._state.pop(model_id, None)

    def is_open(self, model_id: str, *, now: float | None = None) -> bool:
        ts = time.time() if now is None else now
        entry = self._state.get(model_id)
        if entry is None or entry.opened_at is None:
            return False
        return ts - entry.opened_at < self._cooldown

    def open_models(self, *, now: float | None = None) -> frozenset[str]:
        ts = time.time() if now is None else now
        return frozenset(
            model_id
            for model_id, entry in self._state.items()
            if entry.opened_at is not None and ts - entry.opened_at < self._cooldown
        )

    def status(self, model_id: str, *, now: float | None = None) -> dict[str, Any]:
        """The breaker's view of one model, for trace messages and reports."""
        ts = time.time() if now is None else now
        entry = self._state.get(model_id)
        if entry is None or entry.opened_at is None:
            return {
                "consecutive_failures": entry.consecutive if entry else 0,
                "open": False,
                "cooldown_until": None,
                "last_kind": entry.last_kind if entry else None,
            }
        return {
            "consecutive_failures": entry.consecutive,
            "open": ts - entry.opened_at < self._cooldown,
            "cooldown_until": entry.opened_at + self._cooldown,
            "last_kind": entry.last_kind,
        }

    def reset(self) -> None:
        self._state.clear()


# The process-wide breaker the call sites share.
BREAKER = CircuitBreaker()


def _append_outage(
    path: Path,
    *,
    model_id: str,
    kind: str,
    env: os._Environ | dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Append one outage event to the ledger. Never raises.

    Best-effort, mirroring the feedback and usage ledgers: same home-directory
    convention, owner-only permissions, atomic O_APPEND line writes, and a
    telemetry problem must not fail the run that produced it.
    """
    observation = {
        "ts": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model_id": model_id,
        "kind": kind,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=_LEDGER_DIR_MODE)
        if env is None:
            env = os.environ
        if RELIABILITY_LOG_ENV not in env:
            for target, mode in ((path.parent, _LEDGER_DIR_MODE), (path, _LEDGER_FILE_MODE)):
                try:
                    if stat.S_IMODE(target.stat().st_mode) & 0o077:
                        os.chmod(target, mode)
                except OSError as exc:
                    logger.debug("reliability ledger permissions left as found: %s", exc)
        line = json.dumps(observation, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, _LEDGER_FILE_MODE)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        logger.debug("reliability event not recorded: %s", exc)
        return None
    return observation


def note_failure(model_id: str, exc: BaseException) -> str | None:
    """Classify a failure, record it in the breaker, and persist the outage.

    Returns the outage kind when the failure counted as an outage, ``None``
    when it did not (quota exhaustion, empty output, semantic errors — those
    are handled by their own machinery). Never raises.
    """
    kind = outage_kind(exc)
    if kind is None:
        return None
    BREAKER.record_failure(model_id, kind=kind)
    path = reliability_log_path()
    if path is not None:
        _append_outage(path, model_id=model_id, kind=kind)
    return kind


def note_success(model_id: str) -> None:
    """Record a successful provider call: resets the breaker for that model."""
    BREAKER.record_success(model_id)

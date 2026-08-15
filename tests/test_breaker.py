from __future__ import annotations

import json
from pathlib import Path

import pytest

from volante.breaker import (
    BREAKER,
    BreakerConfigError,
    CircuitBreaker,
    breaker_cooldown_seconds,
    breaker_threshold,
    note_failure,
    note_success,
    outage_kind,
    reliability_log_path,
)
from volante.providers.base import (
    EmptyOutputError,
    IncompleteOutputError,
    ProviderError,
)

T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def _clean_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "")
    BREAKER.reset()
    yield


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOLANTE_RELIABILITY_LOG", raising=False)
    assert breaker_threshold() == 2
    assert breaker_cooldown_seconds() == 60.0
    assert reliability_log_path() == Path("~/.volante/reliability.jsonl").expanduser()


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLANTE_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("VOLANTE_BREAKER_COOLDOWN_SECONDS", "2.5")
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "~/ledger.jsonl")
    assert breaker_threshold() == 3
    assert breaker_cooldown_seconds() == 2.5
    assert reliability_log_path() == Path("~/ledger.jsonl").expanduser()


def test_env_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLANTE_BREAKER_THRESHOLD", "abc")
    with pytest.raises(BreakerConfigError):
        breaker_threshold()
    monkeypatch.setenv("VOLANTE_BREAKER_THRESHOLD", "0")
    with pytest.raises(BreakerConfigError):
        breaker_threshold()
    monkeypatch.setenv("VOLANTE_BREAKER_THRESHOLD", "-1")
    with pytest.raises(BreakerConfigError):
        breaker_threshold()
    monkeypatch.delenv("VOLANTE_BREAKER_THRESHOLD")
    monkeypatch.setenv("VOLANTE_BREAKER_COOLDOWN_SECONDS", "soon")
    with pytest.raises(BreakerConfigError):
        breaker_cooldown_seconds()
    monkeypatch.setenv("VOLANTE_BREAKER_COOLDOWN_SECONDS", "-5")
    with pytest.raises(BreakerConfigError):
        breaker_cooldown_seconds()


def test_reliability_log_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "   ")
    assert reliability_log_path() is None


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=-1.0)


def test_outage_kind_classification() -> None:
    assert outage_kind(TimeoutError("down")) == "timeout"
    assert outage_kind(ProviderError("flaky", retryable=True)) == "provider_unavailable"
    assert (
        outage_kind(ProviderError("gone", retryable=False, provider_unavailable=True))
        == "provider_unavailable"
    )
    assert (
        outage_kind(ProviderError("missing", retryable=False, candidate_unavailable=True))
        == "candidate_unavailable"
    )
    # Deliberately NOT outages: these reroute no-backoff already, and the point
    # of the breaker would blur billing truth with provider health.
    assert outage_kind(ProviderError("quota", retryable=False, quota_exhausted=True)) is None
    assert outage_kind(ProviderError("invalid request", retryable=False)) is None
    assert outage_kind(EmptyOutputError(phase="worker model")) is None
    assert outage_kind(IncompleteOutputError(phase="worker model", stop_reason="pause")) is None
    assert outage_kind(RuntimeError("unexpected")) is None


def test_opens_after_threshold_and_cools_down() -> None:
    c = CircuitBreaker(threshold=2, cooldown_seconds=60.0)
    assert not c.is_open("m", now=T0)
    c.record_failure("m", now=T0)
    assert not c.is_open("m", now=T0 + 1)
    # The anchor is the crossing failure (the second one), not the first.
    c.record_failure("m", now=T0 + 1)
    assert c.is_open("m", now=T0 + 2)
    assert c.is_open("m", now=T0 + 60.9)
    assert not c.is_open("m", now=T0 + 61.1)  # cooldown over: probe allowed


def test_probe_failure_reopens_immediately() -> None:
    c = CircuitBreaker(threshold=2, cooldown_seconds=60.0)
    c.record_failure("m", now=T0)
    c.record_failure("m", now=T0 + 1)
    assert c.is_open("m", now=T0 + 60.5) is True
    assert c.is_open("m", now=T0 + 61.5) is False  # probe allowed after cooldown
    c.record_failure("m", now=T0 + 61.6)  # the post-cooldown probe fails
    assert c.is_open("m", now=T0 + 62.0)  # reopened with a FRESH anchor
    assert not c.is_open("m", now=T0 + 121.7)  # and that cooldown also expires


def test_success_resets_streak_and_closes() -> None:
    c = CircuitBreaker(threshold=2, cooldown_seconds=60.0)
    c.record_failure("m", now=T0)
    c.record_success("m", now=T0 + 1)
    assert not c.is_open("m", now=T0 + 2)
    assert c.status("m", now=T0 + 2)["consecutive_failures"] == 0
    c.record_failure("m", now=T0 + 3)
    c.record_failure("m", now=T0 + 4)
    c.record_success("m", now=T0 + 5)
    assert not c.is_open("m", now=T0 + 6)  # success closes an open breaker


def test_consecutive_vs_gap_on_success() -> None:
    c = CircuitBreaker(threshold=2, cooldown_seconds=60.0)
    c.record_failure("m", now=T0)
    c.record_success("m", now=T0 + 1)
    c.record_failure("m", now=T0 + 2)
    assert not c.is_open("m", now=T0 + 3)


def test_open_models_and_status() -> None:
    c = CircuitBreaker(threshold=1, cooldown_seconds=60.0)
    c.record_failure("a", now=T0, kind="timeout")
    c.record_failure("b", now=T0, kind="timeout")
    c.record_failure("c", now=T0 - 120, kind="timeout")
    c.record_success("b", now=T0 + 1)
    assert c.open_models(now=T0 + 2) == frozenset({"a"})
    assert c.status("c", now=T0 + 2)["open"] is False
    assert c.status("c", now=T0 + 2)["last_kind"] == "timeout"
    assert c.status("never", now=T0 + 2)["consecutive_failures"] == 0


def test_note_failure_records_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "reliability.jsonl"
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", str(ledger))
    assert note_failure("m", TimeoutError("down")) == "timeout"
    assert BREAKER.status("m")["consecutive_failures"] == 1
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["model_id"] == "m"
    assert event["kind"] == "timeout"
    assert event["ts"].endswith("Z")
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_note_failure_non_outage_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "reliability.jsonl"
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", str(ledger))
    assert note_failure("m", ProviderError("quota", retryable=False, quota_exhausted=True)) is None
    assert not ledger.exists()
    assert BREAKER.status("m")["consecutive_failures"] == 0


def test_note_failure_disabled_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "")
    assert note_failure("m", TimeoutError("down")) == "timeout"
    assert not (Path("~/.volante/reliability.jsonl").expanduser()).exists()
    assert BREAKER.status("m")["consecutive_failures"] == 1


def test_note_success_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "reliability.jsonl"
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", str(ledger))
    note_success("m")
    assert not ledger.exists()

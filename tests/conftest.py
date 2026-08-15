from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_usage_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep the whole suite hermetic w.r.t. the usage ledger.

    Any test that drives a real run through the CLI, the MCP ``run_goal``, or the Web UI
    SSE route triggers a best-effort ``record_run`` (volante.observability), which without
    isolation appends to the developer's real ``~/.volante/usage.jsonl``. Point every test
    at a throwaway ledger and silence ``VOLANTE_LOG`` by default; individual tests that
    exercise the ledger explicitly still override ``VOLANTE_USAGE_LOG`` after this runs.
    """
    ledger = tmp_path_factory.mktemp("volante-usage") / "usage.jsonl"
    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(ledger))
    monkeypatch.delenv("VOLANTE_LOG", raising=False)


@pytest.fixture(autouse=True)
def _fresh_breaker() -> None:
    """A clean circuit breaker per test.

    The breaker is process-wide by design (it protects the wallet across the
    runs of one session), so a test that opens it must not leak that state into
    the next test's run. Reset it up front; a test that drives a real outage
    keeps its effects within its own body, just like the usage-ledger fixture.
    """
    from volante.breaker import BREAKER

    BREAKER.reset()


@pytest.fixture(autouse=True)
def _isolate_reliability_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the whole suite hermetic w.r.t. the reliability ledger.

    Any test that drives a failing provider through the Runtime, the agentic
    worker, or the direct path triggers a best-effort outage record
    (volante.breaker.note_failure), which without isolation appends to the
    developer's real ``~/.volante/reliability.jsonl``. Silence recording by
    default; individual tests that exercise the ledger explicitly override
    ``VOLANTE_RELIABILITY_LOG`` after this runs.
    """
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "")


@pytest.fixture(autouse=True)
def _pin_sandbox_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the execution sandbox so the suite does not depend on the developer's Docker.

    Sandbox selection auto-detects a Docker daemon, which would otherwise make results
    differ between a machine with Docker running and one without. Tests drive trusted
    fake providers, so they opt in explicitly to the subprocess sandbox; the tests that
    exercise the selection policy itself override this with their own monkeypatching.
    """
    from volante.tools import sandbox

    monkeypatch.setenv("VOLANTE_SANDBOX", "subprocess")
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox.reset_docker_probe()

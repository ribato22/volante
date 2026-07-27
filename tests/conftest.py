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

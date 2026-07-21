from __future__ import annotations

from pathlib import Path

from orchestrator.tools.docker_sandbox import DockerSandbox
from orchestrator.tools.sandbox import Sandbox, sandbox_for


def test_default_is_subprocess(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    assert isinstance(sandbox_for(tmp_path), Sandbox)


def test_docker_when_env_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIORCH_SANDBOX", "docker")
    assert isinstance(sandbox_for(tmp_path), DockerSandbox)

from __future__ import annotations

from pathlib import Path

from baton.tools.docker_sandbox import DockerSandbox
from baton.tools.sandbox import Sandbox, sandbox_for


def _clear_sandbox_env(monkeypatch) -> None:
    monkeypatch.delenv("BATON_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)


def test_default_is_subprocess(tmp_path: Path, monkeypatch) -> None:
    _clear_sandbox_env(monkeypatch)
    assert isinstance(sandbox_for(tmp_path), Sandbox)


def test_docker_when_env_set(tmp_path: Path, monkeypatch) -> None:
    _clear_sandbox_env(monkeypatch)
    monkeypatch.setenv("BATON_SANDBOX", "docker")
    assert isinstance(sandbox_for(tmp_path), DockerSandbox)


def test_docker_when_legacy_env_set(tmp_path: Path, monkeypatch) -> None:
    # AIORCH_SANDBOX (pre-rename name) is a DEPRECATED fallback: still selects docker
    # when BATON_SANDBOX is unset, so existing configs keep working.
    _clear_sandbox_env(monkeypatch)
    monkeypatch.setenv("AIORCH_SANDBOX", "docker")
    assert isinstance(sandbox_for(tmp_path), DockerSandbox)


def test_new_env_takes_precedence_over_legacy(tmp_path: Path, monkeypatch) -> None:
    # If both are set, BATON_SANDBOX (the current name) wins.
    _clear_sandbox_env(monkeypatch)
    monkeypatch.setenv("BATON_SANDBOX", "subprocess")
    monkeypatch.setenv("AIORCH_SANDBOX", "docker")
    assert isinstance(sandbox_for(tmp_path), Sandbox)

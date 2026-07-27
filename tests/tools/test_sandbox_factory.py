from __future__ import annotations

from pathlib import Path

import pytest

from volante.tools import sandbox as sandbox_mod
from volante.tools.docker_sandbox import DockerSandbox
from volante.tools.sandbox import (
    Sandbox,
    SandboxUnavailableError,
    resolve_sandbox_mode,
    sandbox_for,
)


def _clear_sandbox_env(monkeypatch) -> None:
    monkeypatch.delenv("VOLANTE_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox_mod.reset_docker_probe()


def _docker(monkeypatch, present: bool) -> None:
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda **_: present)


def test_auto_selects_docker_when_a_daemon_answers(tmp_path: Path, monkeypatch) -> None:
    # Secure by default: with container isolation available it is used without asking.
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, True)

    mode, reason = resolve_sandbox_mode()

    assert mode == "docker"
    assert "auto" in reason
    assert isinstance(sandbox_for(tmp_path), DockerSandbox)


def test_fails_closed_when_no_isolation_is_available(tmp_path: Path, monkeypatch) -> None:
    # The P0 this guards: without a sandbox, model-generated code must NOT fall back to
    # running with the caller's filesystem and network access.
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, False)

    mode, reason = resolve_sandbox_mode()

    assert mode == "unavailable"
    assert "VOLANTE_SANDBOX=subprocess" in reason  # tells the user how to opt in
    with pytest.raises(SandboxUnavailableError, match="refusing to execute"):
        sandbox_for(tmp_path)


def test_subprocess_requires_an_explicit_opt_in(tmp_path: Path, monkeypatch) -> None:
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, False)
    monkeypatch.setenv("VOLANTE_SANDBOX", "subprocess")

    mode, reason = resolve_sandbox_mode()

    assert mode == "subprocess"
    assert "host filesystem and network" in reason  # the opt-in states the risk
    assert isinstance(sandbox_for(tmp_path), Sandbox)


def test_explicit_docker_is_honored_when_the_daemon_answers(
    tmp_path: Path, monkeypatch
) -> None:
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, True)
    monkeypatch.setenv("VOLANTE_SANDBOX", "docker")

    assert resolve_sandbox_mode()[0] == "docker"
    assert isinstance(sandbox_for(tmp_path), DockerSandbox)


def test_explicit_docker_with_a_dead_daemon_fails_at_selection(monkeypatch) -> None:
    # Installed-but-stopped Docker Desktop must be caught here, not after the run has
    # paid for planning and every agentic turn has failed inside the loop.
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, False)
    monkeypatch.setenv("VOLANTE_SANDBOX", "docker")

    mode, reason = resolve_sandbox_mode()

    assert mode == "unavailable"
    assert "no Docker daemon answered" in reason
    assert "start Docker" in reason  # actionable, and names the secure fix first


def test_legacy_env_still_selects_docker(monkeypatch) -> None:
    # AIORCH_SANDBOX (pre-rename name) remains a DEPRECATED fallback.
    _clear_sandbox_env(monkeypatch)
    _docker(monkeypatch, True)
    monkeypatch.setenv("AIORCH_SANDBOX", "docker")

    assert resolve_sandbox_mode()[0] == "docker"


def test_new_env_takes_precedence_over_legacy(monkeypatch) -> None:
    _clear_sandbox_env(monkeypatch)
    monkeypatch.setenv("VOLANTE_SANDBOX", "subprocess")
    monkeypatch.setenv("AIORCH_SANDBOX", "docker")

    assert resolve_sandbox_mode()[0] == "subprocess"


def test_unknown_sandbox_value_is_rejected_rather_than_downgraded(monkeypatch) -> None:
    # A typo must not silently land on the weakest sandbox.
    _clear_sandbox_env(monkeypatch)
    monkeypatch.setenv("VOLANTE_SANDBOX", "docker-compose")

    with pytest.raises(ValueError, match="VOLANTE_SANDBOX"):
        resolve_sandbox_mode()


def test_docker_probe_requires_a_responding_daemon(monkeypatch) -> None:
    # A present CLI with a stopped daemon (the common macOS state) is NOT available:
    # treating it as available would downgrade isolation at first execution instead.
    import shutil
    import subprocess

    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/docker")

    class _Failed:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Failed())
    assert sandbox_mod.docker_available() is False

    sandbox_mod.reset_docker_probe()

    class _Ok:
        returncode = 0
        stdout = b"27.0.3\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Ok())
    assert sandbox_mod.docker_available() is True


def test_docker_probe_is_cached(monkeypatch) -> None:
    import shutil
    import subprocess

    sandbox_mod.reset_docker_probe()
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    class _Ok:
        returncode = 0
        stdout = b"27.0.3\n"

    def _run(*a, **k):
        calls.append(a)
        return _Ok()

    monkeypatch.setattr(subprocess, "run", _run)
    assert sandbox_mod.docker_available() is True
    assert sandbox_mod.docker_available() is True
    assert len(calls) == 1  # probed once per process, not per task


def test_missing_docker_cli_is_unavailable(monkeypatch) -> None:
    import shutil

    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert sandbox_mod.docker_available() is False

from __future__ import annotations

from pathlib import Path

import orchestrator.tools.docker_sandbox as ds
from orchestrator.tools.docker_sandbox import DockerSandbox
from orchestrator.tools.sandbox import ExecResult


class _FakeProc:
    def __init__(self, out=b"container-ok\n", err=b"", rc=0) -> None:
        self._out, self._err, self.returncode = out, err, rc

    async def communicate(self):
        return self._out, self._err

    async def wait(self):
        return self.returncode


async def test_run_builds_isolated_argv_and_parses_result(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    async def fake_spawn(*args, **kwargs):
        captured["argv"] = list(args)
        return _FakeProc()

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    res = await DockerSandbox(tmp_path, mem_mb=256, cpus=1.5, pids=64).run("print('x')")

    assert isinstance(res, ExecResult)
    assert res.exit_code == 0
    assert res.timed_out is False
    assert "container-ok" in res.stdout
    argv = captured["argv"]
    assert argv[:2] == ["docker", "run"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "-v" in argv and f"{tmp_path}:/work" in argv
    assert "--memory" in argv and "256m" in argv
    assert "--cpus" in argv and "1.5" in argv
    assert "--pids-limit" in argv and "64" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--read-only" in argv
    assert argv[-2:] == ["python", "_snippet.py"]
    assert (tmp_path / "_snippet.py").read_text() == "print('x')"


async def test_timeout_kills_container(tmp_path: Path, monkeypatch) -> None:
    killed: dict = {}

    class _HangProc:
        returncode = None

        async def communicate(self):
            import asyncio
            await asyncio.sleep(10)
            return b"", b""

        async def wait(self):
            return -9

    async def fake_spawn(*args, **kwargs):
        if args[:2] == ("docker", "kill"):
            killed["name"] = args[2]
            return _FakeProc(rc=0)
        return _HangProc()

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    res = await DockerSandbox(tmp_path, timeout_s=0.2).run("import time; time.sleep(9)")
    assert res.timed_out is True
    assert killed.get("name", "").startswith("aiorch_")

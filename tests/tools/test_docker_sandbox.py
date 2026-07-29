from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import volante.tools.docker_sandbox as ds
from volante.tools.docker_sandbox import DockerSandbox
from volante.tools.sandbox import ExecResult


class _FakeProc:
    def __init__(self, out=b"container-ok\n", err=b"", rc=0) -> None:
        self._out, self._err, self.returncode = out, err, rc
        self.killed = False
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(out)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(err)
        self.stderr.feed_eof()

    async def communicate(self):
        return self._out, self._err

    async def wait(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True


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
    assert "--user" in argv
    assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "--read-only" in argv
    assert argv[-2:] == ["python", "_snippet.py"]
    assert (tmp_path / "_snippet.py").read_text() == "print('x')"


class _HangProc:
    returncode = None

    def __init__(self) -> None:
        self.killed = False
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._done = asyncio.Event()

    async def communicate(self):
        await asyncio.sleep(10)
        return b"", b""

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._done.set()


class _FloodProc(_HangProc):
    def __init__(self) -> None:
        super().__init__()
        self.stdout.feed_data(b"x" * 65_536)


async def test_timeout_kills_container(tmp_path: Path, monkeypatch) -> None:
    killed: dict = {}
    hang = _HangProc()

    async def fake_spawn(*args, **kwargs):
        if args[:2] == ("docker", "kill"):
            killed["name"] = args[2]
            return _FakeProc(rc=0)
        return hang

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    res = await DockerSandbox(tmp_path, timeout_s=0.2).run("import time; time.sleep(9)")
    assert res.timed_out is True
    assert killed.get("name", "").startswith("volante_")
    # A2: klien `docker run` (proc) juga dibunuh, bukan cuma container by-name.
    assert hang.killed is True


async def test_timeout_terminates_even_if_container_kill_is_noop(
    tmp_path: Path, monkeypatch
) -> None:
    # Skenario A2 sesungguhnya: container belum ada (image di-pull) -> `docker kill`
    # no-op. Tanpa proc.kill()+wait berbatas, proc.wait() menggantung selamanya.
    # Di sini `docker kill` "berhasil" tapi tak menghentikan proc; proc.kill() yang
    # harus mengakhiri, dan run() tetap kembali dengan timed_out=True.
    hang = _HangProc()

    async def fake_spawn(*args, **kwargs):
        if args[:2] == ("docker", "kill"):
            return _FakeProc(rc=1)  # "No such container" (non-zero, diabaikan)
        return hang

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    res = await DockerSandbox(tmp_path, timeout_s=0.2).run("while True: pass")
    assert res.timed_out is True
    assert hang.killed is True


class _UnresponsiveKillClient(_FakeProc):
    """A `docker kill` client that is spawned and then does not come back.

    The existing tests give it a fast exit, success or failure. An unresponsive
    daemon gives it neither: the client sits there, and everything that has to
    terminate the sandbox queues behind it.
    """

    def __init__(self, delay: float = 10.0) -> None:
        super().__init__()
        self._delay = delay
        self.killed = False

    async def wait(self):
        await asyncio.sleep(self._delay)
        return 0

    def kill(self) -> None:
        self.killed = True


async def test_termination_is_bounded_when_the_docker_kill_client_hangs(
    tmp_path: Path, monkeypatch
) -> None:
    # proc.wait() was already bounded so "the timeout really bites". The docker kill
    # client awaited just above it was not, so the timeout, output-limit and
    # cancellation paths all stopped there instead and the advertised bound never
    # applied. Cancelling from outside does not help either: it re-enters the same
    # unbounded await.
    hang = _HangProc()
    kill_client = _UnresponsiveKillClient()

    async def fake_spawn(*args, **kwargs):
        return kill_client if args[:2] == ("docker", "kill") else hang

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    # Not raising=False: inlining the deadline back into _kill should fail here.
    monkeypatch.setattr(ds, "_KILL_CLIENT_TIMEOUT_S", 0.2)
    start = asyncio.get_running_loop().time()

    res = await DockerSandbox(tmp_path, timeout_s=0.2).run("while True: pass")

    assert asyncio.get_running_loop().time() - start < 3.0
    assert res.timed_out is True
    assert hang.killed is True, "the docker run client was never reached"
    assert kill_client.killed is True, "the stuck kill client was left running"


async def test_flood_output_is_bounded_and_kills_container(
    tmp_path: Path, monkeypatch
) -> None:
    killed: dict[str, str] = {}
    flood = _FloodProc()

    async def fake_spawn(*args, **kwargs):
        if args[:2] == ("docker", "kill"):
            killed["name"] = args[2]
            return _FakeProc()
        return flood

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    res = await DockerSandbox(
        tmp_path,
        timeout_s=5.0,
        max_output_bytes=512,
    ).run("print('x' * 1_000_000)")

    assert res.output_limited is True
    assert res.timed_out is False
    assert len(res.stdout.encode()) <= 512
    assert "sandbox output truncated" in res.stdout
    assert flood.killed is True
    assert killed.get("name", "").startswith("volante_")


async def test_cancellation_kills_container(tmp_path: Path, monkeypatch) -> None:
    hang = _HangProc()

    async def fake_spawn(*args, **kwargs):
        if args[:2] == ("docker", "kill"):
            return _FakeProc()
        return hang

    monkeypatch.setattr(ds, "_spawn", fake_spawn)
    task = asyncio.create_task(
        DockerSandbox(tmp_path, timeout_s=30.0).run("while True: pass")
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert hang.killed is True

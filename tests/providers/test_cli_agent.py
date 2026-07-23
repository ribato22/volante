# tests/providers/test_cli_agent.py
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from baton.providers.cli_agent import CliRunResult, subprocess_cli_runner


async def test_runner_feeds_stdin_and_captures_stdout():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin="hello",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
    )
    assert isinstance(r, CliRunResult)
    assert r.returncode == 0
    assert r.timed_out is False
    assert r.stdout == "HELLO"


async def test_runner_runs_in_clean_temp_cwd():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.listdir('.'))"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
    )
    lines = r.stdout.splitlines()
    assert "baton-cli-" in lines[0]  # ran in a fresh temp dir, not the repo
    assert lines[1] == "[]"          # temp cwd starts empty


async def test_runner_timeout_kills_process_group():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=1.0,
    )
    assert r.timed_out is True
    assert r.returncode == -9


async def test_runner_cancellation_kills_and_raises():
    task = asyncio.ensure_future(
        subprocess_cli_runner(
            [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_runner_streams_lines_to_on_line():
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line.strip())
        return False  # falsy -> keep reading

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "print('a'); print('b'); print('c')"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
        on_line=_collect,
    )
    assert seen == ["a", "b", "c"]
    assert r.returncode == 0
    assert "a\n" in r.stdout


async def test_runner_early_stop_kills_process_group():
    seen: list[str] = []

    def _stop(line: str) -> bool:
        seen.append(line.strip())
        return True  # truthy on FIRST line -> cooperative early-stop

    start = time.monotonic()
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time; print('0', flush=True); time.sleep(5)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=30.0,
        on_line=_stop,
    )
    elapsed = time.monotonic() - start
    assert seen == ["0"]
    assert elapsed < 2.0        # killed immediately, did NOT wait out sleep(5)
    assert r.timed_out is False
    assert r.stdout == "0\n"


async def test_stream_large_line_is_relayed_not_errored():
    # Default asyncio StreamReader limit is 64KB; claude/codex stream-json emits
    # single lines (e.g. tool_result) that routinely exceed it. Must be relayed,
    # not raise ValueError("chunk is longer than limit").
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line)
        return False

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "print('x' * 200000)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
        on_line=_collect,
    )
    assert len(seen) == 1
    assert len(seen[0].strip()) == 200000
    assert r.returncode == 0


async def test_stream_on_line_raising_kills_and_reraises():
    # B2/B3 adapters will json.loads(line) inside on_line; a bad line raises.
    # The runner must re-raise AND reap the child -- never leave it orphaned.
    pids: list[int] = []

    def _boom(line: str) -> bool:
        pids.append(int(line.strip()))
        raise RuntimeError("boom")

    argv = [
        sys.executable,
        "-c",
        "import os, time; print(os.getpid(), flush=True); time.sleep(30)",
    ]
    with pytest.raises(RuntimeError):
        await subprocess_cli_runner(
            argv,
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
            on_line=_boom,
        )
    assert pids  # child's own pid was captured before on_line raised
    with pytest.raises(ProcessLookupError):
        os.getpgid(pids[0])  # process group is gone -> no orphan


async def test_stream_timeout_kills():
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line.strip())
        return False

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=1.0,
        on_line=_collect,
    )
    assert r.timed_out is True
    assert r.returncode == -9


async def test_stream_cancel_kills_and_reraises():
    task = asyncio.ensure_future(
        subprocess_cli_runner(
            [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
            on_line=lambda line: False,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

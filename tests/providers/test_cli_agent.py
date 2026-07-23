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

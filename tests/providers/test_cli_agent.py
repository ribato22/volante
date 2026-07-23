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

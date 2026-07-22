from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from baton.tools.sandbox import ExecResult, Sandbox


async def test_runs_code_and_captures_stdout(tmp_path: Path) -> None:
    r = await Sandbox(tmp_path).run("print('hello')")
    assert isinstance(r, ExecResult)
    assert r.exit_code == 0
    assert r.timed_out is False
    assert "hello" in r.stdout


async def test_nonzero_exit_code(tmp_path: Path) -> None:
    r = await Sandbox(tmp_path).run("import sys; sys.exit(3)")
    assert r.exit_code == 3
    assert r.timed_out is False


async def test_timeout_sets_flag(tmp_path: Path) -> None:
    r = await Sandbox(tmp_path, timeout_s=1.0).run("while True:\n    pass")
    assert r.timed_out is True


async def test_workspace_persists_across_runs(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path)
    await sb.run("open('note.txt', 'w').write('x')")
    r = await sb.run("print(open('note.txt').read())")
    assert "x" in r.stdout


async def test_clean_env_hides_api_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-should-not-leak")
    r = await Sandbox(tmp_path).run("import os; print('ANTHROPIC_API_KEY' in os.environ)")
    assert "False" in r.stdout


async def test_home_and_tmpdir_point_to_workspace(tmp_path: Path) -> None:
    r = await Sandbox(tmp_path).run("import os; print(os.environ.get('HOME'))")
    assert str(tmp_path) in r.stdout


async def test_cancellation_raises_and_kills(tmp_path):
    sb = Sandbox(tmp_path, timeout_s=30.0)
    task = asyncio.ensure_future(sb.run("import time\nwhile True:\n    time.sleep(0.05)\n"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

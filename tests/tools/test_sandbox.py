from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from volante.tools.sandbox import ExecResult, Sandbox


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


async def test_flood_output_is_bounded_and_process_is_stopped(tmp_path: Path) -> None:
    limit = 8_192
    r = await Sandbox(
        tmp_path,
        timeout_s=5.0,
        max_output_bytes=limit,
    ).run("import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n    sys.stdout.flush()")

    assert r.output_limited is True
    assert r.timed_out is False
    assert r.exit_code == -9
    assert len(r.stdout.encode()) <= limit
    assert "sandbox output truncated" in r.stdout


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


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_rejects_invalid_output_limit(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        Sandbox(tmp_path, max_output_bytes=value)  # type: ignore[arg-type]


# --- the deadline must survive its own cleanup ------------------------------- #
# Same root cause as providers/cli_agent._settle. `Process.wait()` resolves only once
# every pipe transport has disconnected, so model code that leaves a detached process
# holding stdout/stderr keeps it pending after the child is already dead. Two waits
# then run back to back — _terminate_process_group's, then stop_and_settle's — and the
# configured timeout is overrun by their sum, not by anything the code did.
#
# Model code is untrusted by construction; daemonising a helper is one line of it.
_DETACHED_PIPE_HOLDER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
    " start_new_session=True)\n"
    "time.sleep(30)\n"
)


async def test_timeout_holds_when_model_code_leaves_a_detached_pipe_holder(
    tmp_path: Path,
) -> None:
    """A sandbox timeout is a budget an agentic loop plans around, not a suggestion.

    Unfixed this returned at 10.3 s for a 0.3 s timeout — a fixed ~10 s of dead time
    per occurrence, charged against the caller's own deadline, which is how a
    per-task budget silently becomes unenforceable.
    """
    import time as _time

    start = _time.monotonic()
    r = await asyncio.wait_for(
        Sandbox(tmp_path, timeout_s=0.3).run(_DETACHED_PIPE_HOLDER),
        timeout=20.0,  # watchdog: fail the test rather than wedge the suite
    )
    elapsed = _time.monotonic() - start

    assert r.timed_out is True
    assert elapsed < 3.0, f"cleanup overran the deadline it enforces: {elapsed:.1f}s"

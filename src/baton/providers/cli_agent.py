# src/baton/providers/cli_agent.py
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class CliRunResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False  # True bila proses dibunuh karena melewati timeout


# Injected async runner (mock di test → tak pernah spawn claude/codex sungguhan).
# async def runner(argv, *, stdin, env, timeout, on_line=None) -> CliRunResult
CliRunner = Callable[..., Awaitable[CliRunResult]]


def _killpg(proc: asyncio.subprocess.Process) -> None:
    # Bunuh seluruh grup proses (mirror tools/sandbox._killpg) → cucu ikut mati.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def subprocess_cli_runner(
    argv: list[str],
    *,
    stdin: str,
    env: dict[str, str],
    timeout: float,
    on_line: Callable[[str], object] | None = None,
) -> CliRunResult:
    """Real runner: spawn a CLI agent in a FRESH temp cwd + its OWN session, feed the
    prompt via stdin. killpg(SIGKILL) on BOTH TimeoutError AND CancelledError, then
    await proc.wait() (mirror src/baton/tools/sandbox.py). on_line handled in Task 2."""
    workdir = tempfile.mkdtemp(prefix="baton-cli-")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
        env=env,
        start_new_session=True,  # grup proses sendiri → killpg bunuh cucu juga
    )
    try:
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode()), timeout=timeout
            )
        except TimeoutError:
            _killpg(proc)
            await proc.wait()
            return CliRunResult("", "", -9, timed_out=True)
        except asyncio.CancelledError:
            _killpg(proc)
            await proc.wait()
            raise
        return CliRunResult(
            out.decode(errors="replace"),
            err.decode(errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

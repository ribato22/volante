# src/baton/providers/cli_agent.py
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# claude/codex --output-format stream-json emit one JSON object per line; a single
# line (e.g. a tool_result) routinely exceeds asyncio's default 64KB StreamReader
# limit, which would otherwise raise ValueError("chunk is longer than limit") and
# orphan the child. 8 MB is a generous ceiling for that.
_STREAM_LIMIT = 8 * 1024 * 1024


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
    await proc.wait() (mirror src/baton/tools/sandbox.py)."""
    workdir = tempfile.mkdtemp(prefix="baton-cli-")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
        env=env,
        start_new_session=True,  # grup proses sendiri → killpg bunuh cucu juga
        limit=_STREAM_LIMIT,  # allow large stream-json lines without erroring
    )
    try:
        if on_line is None:
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
        return await _stream_lines(proc, stdin, timeout, on_line)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _stream_lines(
    proc: asyncio.subprocess.Process,
    stdin: str,
    timeout: float,
    on_line: Callable[[str], object],
) -> CliRunResult:
    """Feed stdin, then relay stdout lines to on_line (for --output-format stream-json).
    Truthy on_line -> cooperative early-stop: killpg the producer and return the
    accumulated partial. killpg on BOTH TimeoutError AND CancelledError as well.
    Outer finally is a belt-and-suspenders net: ANY other exception from the pump
    (e.g. on_line raising, or a line that still overruns _STREAM_LIMIT) must not
    leave the child orphaned -- reap it before the exception propagates."""
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(stdin.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    parts: list[str] = []
    stopped = False

    async def _pump() -> None:
        nonlocal stopped
        async for raw in proc.stdout:  # type: ignore[union-attr]
            parts.append(raw.decode(errors="replace"))
            if on_line(parts[-1]):  # truthy -> cooperative early-stop
                stopped = True
                return

    try:
        try:
            await asyncio.wait_for(_pump(), timeout=timeout)
        except TimeoutError:
            _killpg(proc)
            await proc.wait()
            return CliRunResult("".join(parts), "", -9, timed_out=True)
        except asyncio.CancelledError:
            _killpg(proc)
            await proc.wait()
            raise
        if stopped:
            _killpg(proc)  # early-stop: kill the remaining producer
            await proc.wait()
            return CliRunResult(
                "".join(parts), "", proc.returncode if proc.returncode is not None else -9
            )
        err = b""
        if proc.stderr is not None:
            err = await proc.stderr.read()
        await proc.wait()
        return CliRunResult(
            "".join(parts),
            err.decode(errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )
    finally:
        if proc.returncode is None:  # any other exception -> still reap, never orphan
            _killpg(proc)
            await proc.wait()

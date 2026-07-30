from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import stat
import sys
from asyncio import create_subprocess_exec as _spawn
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from volante.types import CapabilityUnavailableError


def write_model_code(path: os.PathLike[str] | str, code: str) -> None:
    """Write model-authored code to ``path`` without following a symlink there.

    ``Path.write_text`` follows symlinks, and the workspace PERSISTS across agentic
    iterations — so one tool call could plant ``_snippet.py`` as a symlink and the
    next would write model-authored bytes straight through it to any file the host
    process can reach. That escape goes through the bind mount, not the container,
    so `--network none`, `--cap-drop ALL` and `--read-only` do not touch it: a
    relative link to `.git/hooks/pre-commit` is enough, and needs no knowledge of
    host paths.

    Two defences, because either alone leaves a window. The unlink removes a planted
    link WITHOUT following it, and O_NOFOLLOW then refuses to open one that appears
    in the gap between the two. O_EXCL makes a regular file planted in that same gap
    an error rather than a silent overwrite — fail closed, not fail quiet.
    """
    target = os.fspath(path)
    try:
        os.unlink(target)
    except FileNotFoundError:
        pass
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, code.encode("utf-8"))
    finally:
        os.close(fd)


def read_model_file(
    path: os.PathLike[str] | str, *, max_bytes: int
) -> str | None:
    """Read a model-authored workspace file, or ``None`` if it is not one.

    The read side of ``write_model_code``. Guarding only the write reverses the
    direction of the same escape rather than closing it: the workspace is a bind
    mount the model owns, so it can plant ``solution.py`` as a symlink and a
    trusted host process calling ``read_text()`` resolves it and pulls host
    content into its own output.

    Three refusals, each for a distinct failure:
    * ``O_NOFOLLOW`` — a symlink is not read, in place or through a parent name.
    * ``O_NONBLOCK`` + a regular-file check — a FIFO would otherwise BLOCK the
      opener until something writes to it, with no timeout anywhere near this
      call; a device or directory would return something that is not source.
    * ``max_bytes`` — an unbounded ``read_text()`` on a path the model chooses is
      an allocation the model chooses.

    ``None`` means "no model-authored file here", which callers already handle:
    absent and refused deserve the same fallback, and raising would turn a
    hostile workspace into a crashed run.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError:
        # Missing, a symlink (ELOOP/EMLINK — BSD and Linux disagree on which),
        # a directory, or unreadable. All mean the same thing to the caller.
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, max_bytes)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data.decode("utf-8", errors="ignore")

# The wrapper runs INSIDE the child process: it sets RLIMIT_CPU before running the
# snippet. This avoids preexec_fn (fork-unsafe & not safe inside an asyncio event
# loop). RLIMIT_AS (memory) is deliberately NOT set — in practice it is not
# enforced on macOS; the timeout is the real backstop.
_WRAPPER = (
    "import resource,runpy,sys;"
    "c=int(sys.argv[1]);"
    "resource.setrlimit(resource.RLIMIT_CPU,(c,c));"
    "runpy.run_path(sys.argv[2], run_name='__main__')"
)

_DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
_OUTPUT_LIMIT_MARKER = b"\n...[sandbox output truncated: byte limit reached]...\n"


def _clean_env(workspace: Path) -> dict[str, str]:
    # Only PATH + HOME/TMPDIR (pointed at the workspace). Every secret (*_API_KEY,
    # etc.) is dropped: the code being evaluated cannot read credential env vars.
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
    }


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    output_limited: bool = False


class _BoundedCapture:
    """Retain at most ``limit`` bytes and signal as soon as a producer exceeds it."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.exceeded = False

    def add(self, chunk: bytes) -> bool:
        """Store a bounded prefix and return True only on the first overflow."""

        remaining = self.limit - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            first_overflow = not self.exceeded
            self.exceeded = True
            return first_overflow
        return False

    def text(self) -> str:
        if not self.exceeded:
            return bytes(self.data).decode(errors="replace")
        keep = max(self.limit - len(_OUTPUT_LIMIT_MARKER), 0)
        bounded = bytes(self.data[:keep]) + _OUTPUT_LIMIT_MARKER[: self.limit - keep]
        return bounded.decode(errors="replace")


async def _drain_bounded(
    reader: asyncio.StreamReader | None,
    capture: _BoundedCapture,
    output_limit_reached: asyncio.Event,
) -> None:
    if reader is None:
        return
    while chunk := await reader.read(64 * 1024):
        if capture.add(chunk):
            output_limit_reached.set()


async def _collect_bounded_output(
    proc: asyncio.subprocess.Process,
    *,
    timeout_s: float,
    max_output_bytes: int,
    terminate: Callable[[], Awaitable[None]],
) -> ExecResult:
    """Drain both pipes concurrently while bounding memory and process lifetime."""

    stdout = _BoundedCapture(max_output_bytes)
    stderr = _BoundedCapture(max_output_bytes)
    output_limit_reached = asyncio.Event()
    tasks = [
        asyncio.create_task(
            _drain_bounded(proc.stdout, stdout, output_limit_reached)
        ),
        asyncio.create_task(
            _drain_bounded(proc.stderr, stderr, output_limit_reached)
        ),
        asyncio.create_task(proc.wait()),
    ]
    completion = asyncio.gather(*tasks)
    limit_waiter = asyncio.create_task(output_limit_reached.wait())

    async def stop_and_settle() -> None:
        await terminate()
        try:
            await asyncio.wait_for(completion, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        race = {
            cast(asyncio.Future[Any], completion),
            cast(asyncio.Future[Any], limit_waiter),
        }
        done, _ = await asyncio.wait(
            race,
            timeout=timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if limit_waiter in done and output_limit_reached.is_set():
            await stop_and_settle()
            return ExecResult(
                stdout=stdout.text(),
                stderr=stderr.text(),
                exit_code=proc.returncode if proc.returncode is not None else -9,
                timed_out=False,
                output_limited=True,
            )
        if completion in done:
            await completion
        else:
            await stop_and_settle()
            return ExecResult(
                stdout=stdout.text(),
                stderr=stderr.text(),
                exit_code=-9,
                timed_out=True,
            )
    except asyncio.CancelledError:
        await stop_and_settle()
        raise
    except Exception:
        await stop_and_settle()
        raise
    finally:
        if not limit_waiter.done():
            limit_waiter.cancel()
        await asyncio.gather(limit_waiter, return_exceptions=True)

    return ExecResult(
        stdout=stdout.text(),
        stderr=stderr.text(),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=False,
    )


def _killpg(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# A dead process is reaped in milliseconds. This bound is only ever reached when
# `wait()` is being held open by something we cannot kill, and in that case waiting
# longer yields nothing: the drains have already read every buffered byte, because
# their EOF depends on the same write ends that are being held.
_KILL_SETTLE_S = 1.0


def _release_pipes(proc: asyncio.subprocess.Process) -> None:
    """Drop our ends of the child's pipes (twin of providers/cli_agent._release_pipes).

    asyncio resolves `Process.wait()` only once every pipe transport has reported
    connection_lost, so a process outside our group holding the inherited stdout or
    stderr keeps it pending indefinitely — killpg cannot reach that holder, which is
    the whole point of the session it created. Closing the transport ends the wait
    regardless of who else holds the write ends.

    `_transport` is private because asyncio exposes no public accessor: StreamReader
    has no close(), and only the transport owns the read ends.
    """
    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    with contextlib.suppress(Exception):
        transport.close()


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    _killpg(proc)
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_SETTLE_S)
        return
    except TimeoutError:
        pass
    # The child is dead and still not reaped: its pipes are held from outside. Let go
    # of our ends, or this wait and stop_and_settle's run back to back and the caller's
    # timeout is overrun by their sum — 0.3 s configured, 10.3 s observed.
    _release_pipes(proc)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=_KILL_SETTLE_S)


class Sandbox:
    """Best-effort Python execution in a single workspace (POSIX). One Sandbox per
    agentic task; files persist across `run` calls."""

    def __init__(
        self,
        workspace: Path,
        timeout_s: float = 10.0,
        cpu_s: int = 10,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < 1
        ):
            raise ValueError("max_output_bytes must be a positive integer")
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.cpu_s = cpu_s
        self.max_output_bytes = max_output_bytes

    async def run(self, code: str) -> ExecResult:
        script = self.workspace / "_snippet.py"
        write_model_code(script, code)
        proc = await _spawn(
            sys.executable, "-c", _WRAPPER, str(self.cpu_s), str(script),
            cwd=str(self.workspace),
            env=_clean_env(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group → killpg kills grandchildren too
        )
        return await _collect_bounded_output(
            proc,
            timeout_s=self.timeout_s,
            max_output_bytes=self.max_output_bytes,
            terminate=lambda: _terminate_process_group(proc),
        )


class SandboxUnavailableError(CapabilityUnavailableError):
    """Raised when code execution is requested but no isolating sandbox is available.

    Volante fails closed here on purpose: running model-generated code in the plain
    subprocess sandbox exposes the host filesystem and network, so it must be an
    explicit, informed choice (``VOLANTE_SANDBOX=subprocess``) rather than a silent
    default. Subclassing ``CapabilityUnavailableError`` makes the runtime report it as
    ``capability_unavailable`` — a missing capability, which is what it is — instead of a
    generic task error indistinguishable from a model or tool bug.
    """


# Probing the daemon costs a subprocess round-trip, so remember the answer for the
# process. Tests reset it through ``reset_docker_probe``.
_docker_probe: bool | None = None


def reset_docker_probe() -> None:
    """Forget the cached Docker probe (tests, or after starting/stopping a daemon)."""
    global _docker_probe
    _docker_probe = None


def docker_available(*, timeout_s: float = 3.0) -> bool:
    """True only if the Docker CLI exists AND its daemon answers.

    ``docker version`` is queried for the SERVER version specifically: the client
    binary alone (a very common macOS state — Docker Desktop installed but stopped)
    cannot run a container, and treating it as available would silently downgrade
    isolation at the first execution instead of at selection time.
    """
    global _docker_probe
    if _docker_probe is not None:
        return _docker_probe
    import shutil
    import subprocess

    available = False
    if shutil.which("docker") is not None:
        try:
            probe = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            available = probe.returncode == 0 and bool(probe.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            available = False
    _docker_probe = available
    return available


def resolve_sandbox_mode(env: Any = None) -> tuple[str, str]:
    """Decide how (or whether) model-generated code may run.

    Returns ``(mode, reason)`` where mode is ``"docker"``, ``"subprocess"``, or
    ``"unavailable"``. ``VOLANTE_SANDBOX`` (deprecated fallback: ``AIORCH_SANDBOX``)
    forces a choice; unset means auto-select: container isolation when a Docker daemon
    answers, otherwise ``"unavailable"`` so the caller withholds code execution rather
    than quietly running it with full host access.
    """
    if env is None:
        env = os.environ
    raw = (env.get("VOLANTE_SANDBOX") or env.get("AIORCH_SANDBOX") or "").strip().lower()
    if raw == "docker":
        # Probe even though the choice is explicit: an installed-but-stopped Docker
        # Desktop would otherwise pass selection, and every tool call would fail with
        # "Cannot connect to the Docker daemon" — text only the model sees — after the
        # run has already paid for planning and the agentic turns. Fail at selection.
        if not docker_available():
            return (
                "unavailable",
                "VOLANTE_SANDBOX=docker but no Docker daemon answered; start Docker, or "
                "set VOLANTE_SANDBOX=subprocess to accept the unisolated subprocess "
                "sandbox (model-generated code could read your files and reach the "
                "network)",
            )
        return "docker", "VOLANTE_SANDBOX=docker"
    if raw == "subprocess":
        return (
            "subprocess",
            "VOLANTE_SANDBOX=subprocess (host filesystem and network are reachable)",
        )
    if raw:
        raise ValueError(
            f"VOLANTE_SANDBOX must be 'docker' or 'subprocess', got {raw!r}"
        )
    if docker_available():
        return "docker", "auto-selected: a Docker daemon is available"
    return (
        "unavailable",
        "no Docker daemon detected; set VOLANTE_SANDBOX=subprocess to accept the "
        "unisolated subprocess sandbox (model-generated code could read your files "
        "and reach the network)",
    )


def sandbox_for(workspace, *, mode: str | None = None):
    """Build the sandbox for ``workspace`` (``mode`` defaults to resolve_sandbox_mode).

    Raises ``SandboxUnavailableError`` when no isolation is available, so an
    unisolated execution can never happen by accident.
    """
    if mode is None:
        mode, reason = resolve_sandbox_mode()
    else:
        reason = f"mode={mode}"
    if mode == "docker":
        from volante.tools.docker_sandbox import DockerSandbox

        return DockerSandbox(workspace)
    if mode == "subprocess":
        return Sandbox(workspace)
    raise SandboxUnavailableError(
        f"refusing to execute model-generated code: {reason}"
    )

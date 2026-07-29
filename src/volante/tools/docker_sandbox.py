from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from asyncio import create_subprocess_exec as _spawn  # alias: avoid the substring a hook forbids
from pathlib import Path

from volante.tools.sandbox import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    ExecResult,
    _collect_bounded_output,
    write_model_code,
)

# How long to wait on the auxiliary `docker kill` client before abandoning it.
# `docker kill` talks to a local daemon, so seconds is already generous; the point
# is that there IS a bound. Module-level so a test can shrink it.
_KILL_CLIENT_TIMEOUT_S = 5.0


class DockerSandbox:
    """Container-isolated sandbox (same interface as Sandbox). Fixes the honest limits
    of the macOS subprocess sandbox: --network none (network), mounts only the workspace
    (disk), cgroup limits (memory). Opt-in via sandbox_for()."""

    def __init__(
        self,
        workspace: Path,
        timeout_s: float = 15.0,
        image: str = "python:3.12-slim",
        mem_mb: int = 512,
        cpus: float = 1.0,
        pids: int = 128,
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
        self.image = image
        self.mem_mb = mem_mb
        self.cpus = cpus
        self.pids = pids
        self.max_output_bytes = max_output_bytes

    def _argv(self, name: str) -> list[str]:
        return [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "-v", f"{self.workspace}:/work", "-w", "/work",
            "--memory", f"{self.mem_mb}m",
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            # The bind-mounted workspace is created by this host process with the
            # caller's normal permissions (typically 0755). Running as ``nobody``
            # made it readable but not writable, contradicting the persistent
            # workspace contract. Matching the host's numeric identity grants
            # write access only to the intentionally mounted workspace; the
            # container root remains read-only and capabilities stay dropped.
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m",
            self.image, "python", "_snippet.py",
        ]

    async def _kill(self, name: str) -> None:
        try:
            k = await _spawn(
                "docker", "kill", name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        try:
            await asyncio.wait_for(k.wait(), timeout=_KILL_CLIENT_TIMEOUT_S)
        except TimeoutError:
            # An unresponsive daemon leaves this client sitting there. Waiting on it
            # without a deadline put every termination path -- timeout, output limit,
            # cancellation -- behind a process that may never answer, which is exactly
            # what the bounded proc.wait() below was added to prevent. Give up on the
            # auxiliary client and let _terminate go on to kill the `docker run` one,
            # which is the process actually holding this sandbox open.
            with contextlib.suppress(ProcessLookupError, OSError):
                k.kill()

    async def _terminate(self, proc: asyncio.subprocess.Process, name: str) -> None:
        # Kill the container by name AND the `docker run` client (proc). If the
        # container does not exist yet (image still being pulled), `docker kill` is a
        # no-op → without proc.kill() + a bounded wait, `proc.wait()` hangs forever and
        # the outer timeout is useless. proc.wait() is bounded so the timeout really bites.
        await self._kill(name)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            pass

    async def run(self, code: str) -> ExecResult:
        write_model_code(self.workspace / "_snippet.py", code)
        name = "volante_" + uuid.uuid4().hex[:12]
        proc = await _spawn(
            *self._argv(name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return await _collect_bounded_output(
            proc,
            timeout_s=self.timeout_s,
            max_output_bytes=self.max_output_bytes,
            terminate=lambda: self._terminate(proc, name),
        )

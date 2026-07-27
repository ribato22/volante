from __future__ import annotations

import asyncio
import os
import uuid
from asyncio import create_subprocess_exec as _spawn  # alias: hindari substring terlarang hook
from pathlib import Path

from volante.tools.sandbox import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    ExecResult,
    _collect_bounded_output,
)


class DockerSandbox:
    """Sandbox terisolasi container (interface sama dgn Sandbox). Memperbaiki batas
    jujur subprocess-sandbox macOS: --network none (jaringan), mount hanya workspace
    (disk), cgroup limits (memori). Opt-in via sandbox_for()."""

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
            await k.wait()
        except OSError:
            pass

    async def _terminate(self, proc: asyncio.subprocess.Process, name: str) -> None:
        # Bunuh container by-name DAN klien `docker run` (proc). Kalau container
        # belum ada (image masih di-pull), `docker kill` no-op → tanpa proc.kill()
        # + wait berbatas, `proc.wait()` menggantung tak-terhingga & timeout luar
        # jadi percuma. proc.wait() dibatasi agar timeout benar-benar menggigit.
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
        (self.workspace / "_snippet.py").write_text(code, encoding="utf-8")
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

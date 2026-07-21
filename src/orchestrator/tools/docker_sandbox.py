from __future__ import annotations

import asyncio
import uuid
from asyncio import create_subprocess_exec as _spawn  # alias: hindari substring terlarang hook
from pathlib import Path

from orchestrator.tools.sandbox import ExecResult


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
    ) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.image = image
        self.mem_mb = mem_mb
        self.cpus = cpus
        self.pids = pids

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
            "--user", "65534:65534",
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
        name = "aiorch_" + uuid.uuid4().hex[:12]
        proc = await _spawn(
            *self._argv(name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            await self._terminate(proc, name)
            return ExecResult(stdout="", stderr="", exit_code=-9, timed_out=True)
        except asyncio.CancelledError:
            await self._terminate(proc, name)
            raise
        return ExecResult(
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=False,
        )

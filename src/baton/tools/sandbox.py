from __future__ import annotations

import asyncio
import os
import signal
import sys
from asyncio import create_subprocess_exec as _spawn
from dataclasses import dataclass
from pathlib import Path

# Wrapper dijalankan DI DALAM proses anak: set RLIMIT_CPU sebelum menjalankan
# snippet. Ini menghindari preexec_fn (fork-unsafe & tak aman di event loop
# asyncio). RLIMIT_AS (memori) sengaja TIDAK diset — praktis tak
# ditegakkan di macOS; timeout adalah backstop nyata.
_WRAPPER = (
    "import resource,runpy,sys;"
    "c=int(sys.argv[1]);"
    "resource.setrlimit(resource.RLIMIT_CPU,(c,c));"
    "runpy.run_path(sys.argv[2], run_name='__main__')"
)


def _clean_env(workspace: Path) -> dict[str, str]:
    # Hanya PATH + HOME/TMPDIR (diarahkan ke workspace). Semua secret (*_API_KEY,
    # dll.) dibuang: kode yang dinilai tak bisa membaca env kredensial.
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


def _killpg(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class Sandbox:
    """Eksekusi Python best-effort di satu workspace (POSIX). Satu Sandbox per
    agentic-task; file bertahan antar pemanggilan `run`."""

    def __init__(self, workspace: Path, timeout_s: float = 10.0, cpu_s: int = 10) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.cpu_s = cpu_s

    async def run(self, code: str) -> ExecResult:
        script = self.workspace / "_snippet.py"
        script.write_text(code, encoding="utf-8")
        proc = await _spawn(
            sys.executable, "-c", _WRAPPER, str(self.cpu_s), str(script),
            cwd=str(self.workspace),
            env=_clean_env(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # grup proses sendiri → killpg bunuh cucu juga
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            _killpg(proc)
            await proc.wait()
            return ExecResult(stdout="", stderr="", exit_code=-9, timed_out=True)
        except asyncio.CancelledError:
            _killpg(proc)
            await proc.wait()
            raise
        return ExecResult(
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=False,
        )


def sandbox_for(workspace):
    """Pilih impl sandbox via env AIORCH_SANDBOX ("subprocess" default | "docker")."""
    import os
    if os.environ.get("AIORCH_SANDBOX", "subprocess").lower() == "docker":
        from baton.tools.docker_sandbox import DockerSandbox
        return DockerSandbox(workspace)
    return Sandbox(workspace)

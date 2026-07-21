from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Wrapper dijalankan DI DALAM proses anak: set RLIMIT_CPU sebelum menjalankan
# snippet. Ini menghindari preexec_fn (fork-unsafe & tak thread-safe di bawah
# asyncio.to_thread). RLIMIT_AS (memori) sengaja TIDAK diset — praktis tak
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


class Sandbox:
    """Eksekusi Python best-effort di satu workspace (POSIX). Satu Sandbox per
    agentic-task; file bertahan antar pemanggilan `run`."""

    def __init__(self, workspace: Path, timeout_s: float = 10.0, cpu_s: int = 10) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.cpu_s = cpu_s

    def run(self, code: str) -> ExecResult:
        script = self.workspace / "_snippet.py"
        script.write_text(code, encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-c", _WRAPPER, str(self.cpu_s), str(script)],
            cwd=str(self.workspace),
            env=_clean_env(self.workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # grup proses sendiri → killpg bunuh cucu juga
        )
        try:
            out, err = proc.communicate(timeout=self.timeout_s)
            return ExecResult(stdout=out, stderr=err, exit_code=proc.returncode, timed_out=False)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            out, err = proc.communicate()
            return ExecResult(stdout=out or "", stderr=err or "", exit_code=-9, timed_out=True)

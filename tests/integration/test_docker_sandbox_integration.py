from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from orchestrator.tools.docker_sandbox import DockerSandbox

pytestmark = pytest.mark.integration

# Workspace di BAWAH repo (bukan /var/folders) agar Docker Desktop bisa mount.
_ROOT = Path(__file__).resolve().parents[2]


def _ws() -> Path:
    p = _ROOT / ".docker_it_ws" / uuid.uuid4().hex[:8]
    p.mkdir(parents=True, exist_ok=True)
    return p


async def test_runs_code_no_network_isolated_fs() -> None:
    ws = _ws()
    try:
        sb = DockerSandbox(ws, timeout_s=30.0)
        r = await sb.run(
            "import socket\n"
            "print('hello')\n"
            "try:\n"
            "    socket.create_connection(('8.8.8.8', 53), timeout=3); print('NET-OPEN')\n"
            "except OSError:\n"
            "    print('NET-BLOCKED')\n"
        )
        assert r.exit_code == 0, r.stderr
        assert "hello" in r.stdout
        assert "NET-BLOCKED" in r.stdout and "NET-OPEN" not in r.stdout
        await sb.run("open('made.txt','w').write('hi')")
        assert (ws / "made.txt").read_text() == "hi"
        r2 = await sb.run("import os; print('HOST' if os.path.exists('/Users') else 'NO-HOST')")
        assert "NO-HOST" in r2.stdout
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def test_timeout_kills_container() -> None:
    ws = _ws()
    try:
        r = await DockerSandbox(ws, timeout_s=3.0).run(
            "import time\nwhile True:\n    time.sleep(0.1)\n"
        )
        assert r.timed_out is True
    finally:
        shutil.rmtree(ws, ignore_errors=True)

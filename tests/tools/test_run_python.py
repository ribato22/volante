from __future__ import annotations

from pathlib import Path

from baton.tools.run_python import RunPythonTool
from baton.tools.sandbox import Sandbox


def _tool(tmp_path: Path, **kw) -> RunPythonTool:
    return RunPythonTool(Sandbox(tmp_path), **kw)


def test_spec_shape(tmp_path: Path) -> None:
    t = _tool(tmp_path)
    assert t.name == "run_python"
    assert t.spec.name == "run_python"
    assert t.spec.input_schema["required"] == ["code"]


async def test_runs_and_reports_stdout(tmp_path: Path) -> None:
    out = await _tool(tmp_path).run({"code": "print('hi')"})
    assert "exit=0" in out
    assert "hi" in out


async def test_missing_code_returns_error_string_not_exception(tmp_path: Path) -> None:
    out = await _tool(tmp_path).run({})
    assert "error" in out.lower()


async def test_large_output_is_capped(tmp_path: Path) -> None:
    out = await _tool(tmp_path, max_result_chars=200).run({"code": "print('A' * 5000)"})
    assert len(out) <= 200
    assert "[dipotong]" in out

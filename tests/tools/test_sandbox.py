from __future__ import annotations

from pathlib import Path

from orchestrator.tools.sandbox import ExecResult, Sandbox


def test_runs_code_and_captures_stdout(tmp_path: Path) -> None:
    r = Sandbox(tmp_path).run("print('hello')")
    assert isinstance(r, ExecResult)
    assert r.exit_code == 0
    assert r.timed_out is False
    assert "hello" in r.stdout


def test_nonzero_exit_code(tmp_path: Path) -> None:
    r = Sandbox(tmp_path).run("import sys; sys.exit(3)")
    assert r.exit_code == 3
    assert r.timed_out is False


def test_timeout_sets_flag(tmp_path: Path) -> None:
    r = Sandbox(tmp_path, timeout_s=1.0).run("while True:\n    pass")
    assert r.timed_out is True


def test_workspace_persists_across_runs(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path)
    sb.run("open('note.txt', 'w').write('x')")
    r = sb.run("print(open('note.txt').read())")
    assert "x" in r.stdout


def test_clean_env_hides_api_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-should-not-leak")
    r = Sandbox(tmp_path).run("import os; print('ANTHROPIC_API_KEY' in os.environ)")
    assert "False" in r.stdout


def test_home_and_tmpdir_point_to_workspace(tmp_path: Path) -> None:
    r = Sandbox(tmp_path).run("import os; print(os.environ.get('HOME'))")
    assert str(tmp_path) in r.stdout

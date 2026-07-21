from __future__ import annotations

from pathlib import Path

from orchestrator.tools.read_file import ReadFileTool


async def test_reads_file_under_root(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content-a", encoding="utf-8")
    out = await ReadFileTool(tmp_path).run({"path": "a.txt"})
    assert out == "content-a"


async def test_blocks_dotdot_traversal(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = await ReadFileTool(tmp_path / "sub").run({"path": "../secret.txt"})
    assert "escapes allowed root" in out


async def test_blocks_absolute_outside_root(tmp_path: Path) -> None:
    out = await ReadFileTool(tmp_path).run({"path": "/etc/hosts"})
    assert "escapes allowed root" in out


async def test_not_a_file(tmp_path: Path) -> None:
    out = await ReadFileTool(tmp_path).run({"path": "nope.txt"})
    assert "not a file" in out


async def test_missing_path_errors(tmp_path: Path) -> None:
    out = await ReadFileTool(tmp_path).run({})
    assert "error" in out.lower()


async def test_caps_size(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("B" * 5000, encoding="utf-8")
    out = await ReadFileTool(tmp_path, max_bytes=100).run({"path": "big.txt"})
    assert len(out) == 100

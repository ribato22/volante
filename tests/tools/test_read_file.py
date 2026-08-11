from __future__ import annotations

from pathlib import Path

from volante.tools.read_file import ReadFileTool


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


async def test_missing_file_is_a_clean_error(tmp_path: Path) -> None:
    out = await ReadFileTool(tmp_path).run({"path": "nope.txt"})
    assert "cannot open" in out


async def test_a_fifo_cannot_block_the_opener(tmp_path: Path) -> None:
    # A FIFO planted at the path must be refused, not opened (O_NONBLOCK + a
    # regular-file check): an open() on a pipe blocks until a writer shows up,
    # with no timeout anywhere near the tool.
    import os

    os.mkfifo(tmp_path / "pipe")
    out = await ReadFileTool(tmp_path).run({"path": "pipe"})
    assert "not a regular file" in out


async def test_a_planted_symlink_outside_the_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"  # sibling of the root: genuinely outside
    outside.write_text("host", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "link.txt").symlink_to(outside)
    out = await ReadFileTool(tmp_path).run({"path": "sub/link.txt"})
    assert "escapes allowed root" in out


async def test_a_symlink_inside_the_root_reads_through_its_target(tmp_path: Path) -> None:
    # resolve() follows the link BEFORE the boundary check, so a link whose target
    # stays inside the root is not an escape: the content read is the content under
    # the allowed root. O_NOFOLLOW's job is the RACE — a link planted at the
    # resolved path between the check and the open — which a static test cannot
    # hold still; the FIFO test above pins the same guard.
    (tmp_path / "real.txt").write_text("data", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    out = await ReadFileTool(tmp_path).run({"path": "link.txt"})
    assert out == "data"


async def test_missing_path_errors(tmp_path: Path) -> None:
    out = await ReadFileTool(tmp_path).run({})
    assert "error" in out.lower()


async def test_caps_size(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("B" * 5000, encoding="utf-8")
    out = await ReadFileTool(tmp_path, max_bytes=100).run({"path": "big.txt"})
    assert len(out) == 100


def test_rejects_non_positive_size_cap(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="max_bytes"):
        ReadFileTool(tmp_path, max_bytes=0)

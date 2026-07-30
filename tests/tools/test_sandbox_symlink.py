"""The workspace is attacker-influenced storage, so treat it that way.

A sandbox workspace persists across agentic iterations and holds files the MODEL
wrote. `Path.write_text` follows symlinks, so one tool call could plant
`_snippet.py` as a symlink and the next would write model-authored bytes through it
to any file the host process can reach. The escape travels through the bind mount
rather than the container, so `--network none`, `--cap-drop ALL` and `--read-only`
never see it.

None of the 1027 tests that existed when this was found touched a symlink. Every
one of them exercised the sandbox as a cooperative directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from volante.tools.sandbox import read_model_file, write_model_code


def test_a_planted_symlink_does_not_redirect_the_write(tmp_path: Path) -> None:
    victim = tmp_path / "precious"
    victim.write_text("original")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Relative on purpose: reaching `.git/hooks/pre-commit` from a run directory
    # needs no knowledge of host paths at all.
    (workspace / "_snippet.py").symlink_to("../precious")

    write_model_code(workspace / "_snippet.py", "print('owned')")

    assert victim.read_text() == "original", "model code was written through the link"
    assert (workspace / "_snippet.py").read_text() == "print('owned')"
    assert not (workspace / "_snippet.py").is_symlink()


def test_an_absolute_symlink_is_equally_refused(tmp_path: Path) -> None:
    victim = tmp_path / "outside"
    victim.write_text("keep me")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "_snippet.py").symlink_to(victim.resolve())

    write_model_code(workspace / "_snippet.py", "x = 1")

    assert victim.read_text() == "keep me"


def test_a_symlink_to_a_directory_fails_instead_of_writing_somewhere_odd(
    tmp_path: Path,
) -> None:
    target = tmp_path / "adir"
    target.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "_snippet.py").symlink_to(target)

    # Unlinking removes the link, so the write lands on a fresh regular file.
    write_model_code(workspace / "_snippet.py", "y = 2")

    assert (workspace / "_snippet.py").read_text() == "y = 2"
    assert target.is_dir()


def test_repeated_writes_replace_rather_than_append(tmp_path: Path) -> None:
    # Each agentic iteration rewrites the snippet; leftovers from the previous one
    # would silently change what runs.
    path = tmp_path / "_snippet.py"

    write_model_code(path, "first")
    write_model_code(path, "second")

    assert path.read_text() == "second"


def test_the_snippet_is_not_world_readable(tmp_path: Path) -> None:
    path = tmp_path / "_snippet.py"

    write_model_code(path, "secret = 1")

    assert oct(path.stat().st_mode)[-3:] == "600"


def test_both_sandboxes_write_through_the_guarded_helper() -> None:
    # A future sandbox that reaches for write_text directly would reopen the hole,
    # and no behavioural test would notice until someone attacked it again.
    import inspect

    from volante.tools import docker_sandbox, sandbox

    for module in (sandbox, docker_sandbox):
        source = inspect.getsource(module)
        assert "write_text(code" not in source, f"{module.__name__} writes model code unguarded"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="POSIX only")
def test_a_link_planted_between_the_unlink_and_the_open_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """Drive the race the flags exist for, instead of reading the source for them.

    This replaces `assert "O_NOFOLLOW" in inspect.getsource(write_model_code)`, which
    the docstring satisfied on its own: the word appears once in the prose and once in
    the code, so deleting the flag from the `os.open` call left the assertion — and
    the whole suite — green while the defence was gone. Verified by mutation: with
    the flags cut back to O_WRONLY|O_CREAT|O_TRUNC the old test still passed.

    The unlink closes the common case. This is the gap after it, which is the only
    part the flags are responsible for: something re-plants the link before the open.
    """
    victim = tmp_path / "precious"
    victim.write_text("do not overwrite me", encoding="utf-8")
    target = tmp_path / "_snippet.py"

    real_unlink = os.unlink
    planted = False

    def _unlink_then_plant(path, *args, **kwargs):
        nonlocal planted
        try:
            real_unlink(path, *args, **kwargs)
        except FileNotFoundError:
            pass
        if not planted and os.fspath(path) == os.fspath(target):
            planted = True
            os.symlink(victim, target)  # the attacker wins the race
        raise FileNotFoundError(path)

    monkeypatch.setattr(os, "unlink", _unlink_then_plant)

    with pytest.raises(OSError):
        write_model_code(target, "secret = 1")

    assert planted, "the race was never actually staged"
    assert victim.read_text(encoding="utf-8") == "do not overwrite me"


# --- the read side ---------------------------------------------------------- #
# Guarding the WRITE and then reading the same workspace with `read_text()` only
# reverses the direction of the same escape: the host process resolves the link and
# pulls its contents back into trusted output.


def test_reading_a_planted_symlink_returns_nothing(tmp_path: Path) -> None:
    victim = tmp_path / "outside"
    victim.write_text("private host content")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "solution.py").symlink_to(victim.resolve())

    assert read_model_file(workspace / "solution.py", max_bytes=1000) is None


def test_a_relative_link_is_refused_too(tmp_path: Path) -> None:
    (tmp_path / "outside").write_text("private host content")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "solution.py").symlink_to("../outside")

    assert read_model_file(workspace / "solution.py", max_bytes=1000) is None


def test_an_ordinary_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "solution.py"
    path.write_text("def slugify(t):\n    return t\n")

    assert read_model_file(path, max_bytes=1000) == "def slugify(t):\n    return t\n"


def test_a_missing_file_is_absent_not_an_error(tmp_path: Path) -> None:
    assert read_model_file(tmp_path / "nope.py", max_bytes=1000) is None


def test_reading_is_capped(tmp_path: Path) -> None:
    path = tmp_path / "solution.py"
    path.write_text("x" * 5000)

    assert read_model_file(path, max_bytes=100) == "x" * 100


def test_a_fifo_is_refused_rather_than_blocking_forever(tmp_path: Path) -> None:
    # The dangerous case is not just the wrong CONTENT: opening a model-created
    # FIFO for reading blocks until someone writes to it, hanging the evaluator
    # with no timeout anywhere around the read.
    fifo = tmp_path / "solution.py"
    os.mkfifo(fifo)

    assert read_model_file(fifo, max_bytes=1000) is None


def test_a_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "solution.py").mkdir()

    assert read_model_file(tmp_path / "solution.py", max_bytes=1000) is None

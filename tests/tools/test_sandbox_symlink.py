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

from volante.tools.sandbox import write_model_code


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
def test_helper_uses_nofollow() -> None:
    # The unlink closes the common case; O_NOFOLLOW closes the race where a link
    # reappears between the unlink and the open.
    import inspect

    assert "O_NOFOLLOW" in inspect.getsource(write_model_code)

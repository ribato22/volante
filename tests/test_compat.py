from __future__ import annotations

import pytest

from volante.compat import LEGACY_SETTINGS, apply_legacy_env


def test_legacy_names_fill_unset_current_names() -> None:
    env = {"BATON_SANDBOX": "docker", "BATON_LOG": "debug"}

    honored = apply_legacy_env(env, warn=False)

    assert env["VOLANTE_SANDBOX"] == "docker"
    assert env["VOLANTE_LOG"] == "debug"
    assert honored == ["BATON_LOG", "BATON_SANDBOX"]


def test_a_current_name_always_wins_over_the_legacy_one() -> None:
    env = {"BATON_SANDBOX": "docker", "VOLANTE_SANDBOX": "subprocess"}

    assert apply_legacy_env(env, warn=False) == []
    assert env["VOLANTE_SANDBOX"] == "subprocess"  # never overwritten


def test_unrelated_baton_variables_are_not_absorbed() -> None:
    # Some other tool's BATON_* must not become Volante configuration.
    env = {"BATON_SOMETHING_ELSE": "x"}

    assert apply_legacy_env(env, warn=False) == []
    assert "VOLANTE_SOMETHING_ELSE" not in env


def test_migration_is_idempotent() -> None:
    env = {"BATON_LOG": "info"}

    assert apply_legacy_env(env, warn=False) == ["BATON_LOG"]
    assert apply_legacy_env(env, warn=False) == []  # nothing left to migrate


def test_deprecation_is_reported_once_with_the_new_name(capsys) -> None:
    apply_legacy_env({"BATON_USAGE_LOG": "/tmp/u.jsonl"})

    err = capsys.readouterr().err
    assert "deprecated BATON_USAGE_LOG" in err
    assert "VOLANTE_USAGE_LOG" in err


@pytest.mark.parametrize("setting", sorted(LEGACY_SETTINGS))
def test_every_documented_setting_migrates(setting: str) -> None:
    env = {f"BATON_{setting}": "value"}

    apply_legacy_env(env, warn=False)

    assert env[f"VOLANTE_{setting}"] == "value"


def test_allowlist_matches_the_settings_the_code_actually_reads() -> None:
    # Guards the shim against drift: a new VOLANTE_* setting that forgets its legacy
    # entry would silently ignore an existing user's configuration.
    import pathlib
    import re

    read = set()
    for path in pathlib.Path("src/volante").rglob("*.py"):
        read.update(re.findall(r'"VOLANTE_([A-Z_]+)"', path.read_text()))
    for path in (pathlib.Path("webui"), pathlib.Path("volante_mcp")):
        for file in path.rglob("*.py"):
            read.update(re.findall(r'"VOLANTE_([A-Z_]+)"', file.read_text()))
    missing = sorted(read - LEGACY_SETTINGS)
    assert not missing, f"not covered by the compat shim: {missing}"

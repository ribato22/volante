from __future__ import annotations

import pytest

import baton.cli as cli


def test_parse_args_defaults() -> None:
    args = cli._parse_args(["build a thing"])
    assert args.goal == "build a thing"
    assert args.prefer == "cash_protect_quota"  # §7.2 CLI default objective
    assert args.provider is None
    assert args.model is None
    assert args.json is False
    assert args.no_stream is False


def test_parse_args_all_flags() -> None:
    args = cli._parse_args(
        ["g", "--prefer", "local", "-P", "ollama", "--model", "m1", "--json", "--no-stream"]
    )
    assert args.prefer == "local"
    assert args.provider == "ollama"
    assert args.model == "m1"
    assert args.json is True
    assert args.no_stream is True


def test_parse_args_rejects_unknown_prefer() -> None:
    with pytest.raises(SystemExit):  # argparse choices guard
        cli._parse_args(["g", "--prefer", "bogus"])

# tests/providers/test_codex.py
from __future__ import annotations

import json
import subprocess

import pytest

from baton.providers.base import ProviderError
from baton.providers.codex import CodexAdapter
from baton.types import CanonicalRequest, text


def _req(prompt: str = "hi") -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", prompt)], max_tokens=256)


def test_name_is_codex() -> None:
    assert CodexAdapter().name == "codex"


def test_argv_has_exec_json_skip_git_and_model() -> None:
    argv = CodexAdapter().argv(
        _req(), model="gpt-5-codex", max_output=4096,
        system_prompt_mode="append", stream=False,
    )
    assert argv == [
        "codex", "exec", "--json", "--skip-git-repo-check",
        "--config", "model=gpt-5-codex",
    ]


def test_argv_identical_when_stream_true() -> None:
    common = dict(model="gpt-5-codex", max_output=4096, system_prompt_mode="append")
    assert CodexAdapter().argv(_req(), stream=False, **common) == CodexAdapter().argv(
        _req(), stream=True, **common
    )


def test_child_env_scrubs_openai_and_codex_keys() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "OPENAI_API_KEY": "sk-leak",
        "CODEX_API_KEY": "cdx-leak",
    }
    env = CodexAdapter().child_env(base, depth=0)
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"           # unrelated keys preserved
    assert env["HOME"] == "/home/u"            # needed to reach ~/.codex/auth.json
    assert base["OPENAI_API_KEY"] == "sk-leak"  # caller dict NOT mutated


def test_child_env_sets_depth_plus_one() -> None:
    assert CodexAdapter().child_env({}, depth=0)["BATON_CLI_AGENT_DEPTH"] == "1"
    assert CodexAdapter().child_env({}, depth=1)["BATON_CLI_AGENT_DEPTH"] == "2"


def test_stdin_is_prompt_text_system_then_user() -> None:
    req = CanonicalRequest(
        messages=[text("system", "be terse"), text("user", "add two numbers")],
        max_tokens=64,
    )
    assert CodexAdapter().stdin(req) == "be terse\nadd two numbers"

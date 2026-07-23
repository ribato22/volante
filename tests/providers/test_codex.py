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


_JSONL = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "th_1"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "agent_message", "message": "Hello from Codex"}),
    json.dumps({"type": "turn.completed",
                "usage": {"input_tokens": 120, "output_tokens": 34}}),
])


def _run_result(stdout: str, *, stderr: str = "", returncode: int = 0,
                timed_out: bool = False):
    from baton.providers.cli_agent import CliRunResult
    return CliRunResult(
        stdout=stdout, stderr=stderr, returncode=returncode, timed_out=timed_out
    )


def test_parse_final_text_and_usage_from_turn_completed() -> None:
    resp = CodexAdapter().parse(_run_result(_JSONL), _req("write a function"))
    assert resp.content[0].text == "Hello from Codex"
    assert resp.usage.prompt_tokens == 120
    assert resp.usage.completion_tokens == 34
    assert resp.usage.estimated is False
    assert resp.model == "codex"
    assert resp.cost_usd is None  # this JSONL carried no total_cost_usd


def test_parse_usage_estimated_when_turn_completed_lacks_usage() -> None:
    jsonl = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "th_2"}),
        json.dumps({"type": "agent_message", "message": "abcdefgh"}),
        json.dumps({"type": "turn.completed"}),  # no usage key
    ])
    resp = CodexAdapter().parse(_run_result(jsonl), _req("0123456789012345"))
    assert resp.content[0].text == "abcdefgh"
    assert resp.usage.estimated is True
    assert resp.usage.prompt_tokens == 4       # len("0123456789012345") // 4
    assert resp.usage.completion_tokens == 2   # len("abcdefgh") // 4


def test_parse_sets_cost_usd_when_total_cost_present() -> None:
    jsonl = "\n".join([
        json.dumps({"type": "agent_message", "message": "done"}),
        json.dumps({"type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "total_cost_usd": 0.0123}),
    ])
    resp = CodexAdapter().parse(_run_result(jsonl), _req())
    assert resp.cost_usd == 0.0123

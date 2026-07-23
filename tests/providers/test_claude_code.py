# tests/providers/test_claude_code.py
from __future__ import annotations

import json
import re
from pathlib import Path

from baton.providers.claude_code import ClaudeCodeAdapter
from baton.providers.cli_agent import CliAgentProvider, CliRunResult
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage, text


def _req(sys_prompt: str | None, user: str) -> CanonicalRequest:
    msgs = []
    if sys_prompt is not None:
        msgs.append(text("system", sys_prompt))
    msgs.append(text("user", user))
    return CanonicalRequest(messages=msgs, max_tokens=64)


def test_name_is_claude_code() -> None:
    assert ClaudeCodeAdapter().name == "claude_code"


def test_argv_json_append_is_canonical_no_bare() -> None:
    a = ClaudeCodeAdapter()
    argv = a.argv(
        _req("SYS", "hello"),
        model="opus",
        max_output=4096,
        system_prompt_mode="append",
        stream=False,
    )
    assert argv == [
        "claude", "-p",
        "--input-format", "text",
        "--output-format", "json",
        "--model", "opus",
        "--tools", "",
        "--strict-mcp-config",
        "--append-system-prompt", "SYS",
    ]
    # OAuth langganan HARUS hidup + tak boleh skip permission (§8.1).
    assert "--bare" not in argv
    assert "--dangerously-skip-permissions" not in argv


def test_argv_stream_switches_output_format() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req("SYS", "hi"), model="opus", max_output=4096,
        system_prompt_mode="append", stream=True,
    )
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_argv_replace_mode_uses_system_prompt_flag() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req("SYS", "hi"), model="opus", max_output=4096,
        system_prompt_mode="replace", stream=False,
    )
    assert "--system-prompt" in argv
    assert "--append-system-prompt" not in argv


def test_argv_omits_system_flag_when_no_system_message() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req(None, "hi"), model="opus", max_output=4096,
        system_prompt_mode="append", stream=False,
    )
    assert "--append-system-prompt" not in argv
    assert "--system-prompt" not in argv
    assert argv[-1] == "--strict-mcp-config"  # trailing flag when no sys prompt


def test_stdin_is_user_text_only() -> None:
    # --input-format text: prompt user lewat stdin; sistem lewat argv (Task 1).
    a = ClaudeCodeAdapter()
    assert a.stdin(_req("SYS", "hello world")) == "hello world"
    assert a.stdin(_req(None, "just user")) == "just user"


def test_child_env_bumps_depth_and_preserves_oauth() -> None:
    from baton.providers.claude_code import DEPTH_ENV

    a = ClaudeCodeAdapter()
    base = {"PATH": "/bin", "ANTHROPIC_API_KEY": "sk-should-survive", DEPTH_ENV: "0"}
    env = a.child_env(base, depth=0)
    assert env[DEPTH_ENV] == "1"                        # anak +1 (§8.2)
    assert env["PATH"] == "/bin"                        # base diteruskan
    # OAuth langganan dipertahankan: key TIDAK disuntik/dihapus di sini (kontras Codex).
    assert env["ANTHROPIC_API_KEY"] == "sk-should-survive"
    assert base[DEPTH_ENV] == "0"                       # tak mutasi input


def test_parse_json_result_sets_usage_cost_and_latency() -> None:
    payload = {
        "type": "result", "subtype": "success", "is_error": False,
        "duration_ms": 4213, "result": "Paris.",
        "total_cost_usd": 0.0123, "usage": {"input_tokens": 812, "output_tokens": 37},
    }
    res = CliRunResult(stdout=json.dumps(payload), stderr="", returncode=0)
    resp = ClaudeCodeAdapter().parse(res, _req("SYS", "capital of France?"))
    assert resp.content == [TextBlock(text="Paris.")]
    assert resp.usage == Usage(prompt_tokens=812, completion_tokens=37)
    assert resp.usage.estimated is False        # token nyata dari JSON
    assert resp.cost_usd == 0.0123              # total_cost_usd → carrier kredit (§5.3)
    assert resp.latency_ms == 4213             # duration_ms
    assert resp.stop_reason == "end_turn"      # subtype "success" -> end_turn


def test_parse_unparseable_json_falls_back_to_estimated_usage() -> None:
    # JSON gagal parse -> estimasi bertanda, cost otoritatif tak tersedia (§5.3).
    res = CliRunResult(stdout="not json at all", stderr="", returncode=0)
    resp = ClaudeCodeAdapter().parse(res, _req(None, "0123456789012345"))
    assert resp.content == [TextBlock(text="not json at all")]
    assert resp.usage.estimated is True
    assert resp.usage.prompt_tokens == 4       # _est("0123456789012345") == 16 // 4
    assert resp.cost_usd is None


def test_parse_json_without_usage_estimates_but_keeps_cost() -> None:
    payload = {"type": "result", "subtype": "success", "result": "hi", "total_cost_usd": 0.5}
    res = CliRunResult(stdout=json.dumps(payload), stderr="", returncode=0)
    resp = ClaudeCodeAdapter().parse(res, _req(None, "q"))
    assert resp.usage.estimated is True         # usage hilang -> estimasi
    assert resp.cost_usd == 0.5                 # tapi cost otoritatif tetap dipakai


def test_parse_delta_extracts_assistant_text() -> None:
    a = ClaudeCodeAdapter()
    line = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Paris"}]}}
    )
    assert a.parse_delta(line) == "Paris"


def test_parse_delta_ignores_control_and_non_text_lines() -> None:
    a = ClaudeCodeAdapter()
    assert a.parse_delta(json.dumps({"type": "system", "subtype": "init"})) is None
    assert a.parse_delta(json.dumps({"type": "result", "result": "x"})) is None
    assert a.parse_delta("not json") is None
    # blok non-text (mis. tool_use bocor) -> tak diperlakukan sebagai teks.
    tu = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t"}]}}
    )
    assert a.parse_delta(tu) is None


def test_classify_error_not_logged_in_is_quota_exhausted() -> None:
    a = ClaudeCodeAdapter()
    res = CliRunResult(stdout="", stderr="Not logged in. Please run /login", returncode=1)
    err = a.classify_error(res)
    assert err.quota_exhausted is True     # pragmatis: reroute tanpa backoff (Fase 5)
    assert err.retryable is False          # quota_exhausted -> WAJIB non-retryable (Fase 4)


def test_classify_error_usage_limit_is_quota_exhausted() -> None:
    a = ClaudeCodeAdapter()
    payload = {"type": "result", "is_error": True, "subtype": "error",
               "result": "Claude usage limit reached. Try again later."}
    res = CliRunResult(stdout=json.dumps(payload), stderr="", returncode=0)
    err = a.classify_error(res)
    assert err.quota_exhausted is True
    assert err.retryable is False


def test_classify_error_generic_failure_fails_task() -> None:
    # Galat lain (max_turns/400/parse) -> GAGALKAN task, JANGAN reroute kandidat lain.
    a = ClaudeCodeAdapter()
    res = CliRunResult(stdout="", stderr="unexpected boom", returncode=2)
    err = a.classify_error(res)
    assert err.quota_exhausted is False
    assert err.retryable is False
    assert "boom" in str(err)


def test_is_error_true_when_json_envelope_flags_it() -> None:
    # claude -p can exit 0 while the JSON envelope carries is_error=true (max-turns /
    # mid-run execution error) -- CliAgentProvider consults this hook even on exit 0.
    a = ClaudeCodeAdapter()
    payload = {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}
    res = CliRunResult(stdout=json.dumps(payload), stderr="", returncode=0)
    assert a.is_error(res) is True


def test_is_error_false_for_success_envelope() -> None:
    a = ClaudeCodeAdapter()
    payload = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
    res = CliRunResult(stdout=json.dumps(payload), stderr="", returncode=0)
    assert a.is_error(res) is False


def test_is_error_false_when_unparseable() -> None:
    # Non-zero exit / non-JSON stdout is already caught by returncode -- is_error
    # only needs to default safely (False) when it cannot read the envelope.
    a = ClaudeCodeAdapter()
    res = CliRunResult(stdout="not json", stderr="boom", returncode=1)
    assert a.is_error(res) is False


_FIXTURES = Path(__file__).parent / "fixtures"


def _runner_returning(result: CliRunResult):
    async def runner(argv, *, stdin, env, timeout, on_line=None):
        return result
    return runner


def test_result_fixture_is_dated() -> None:
    # §8.3: fixture output CLI WAJIB bertanggal (permukaan billing/skema volatil).
    files = list(_FIXTURES.glob("claude_code_result.*.json"))
    assert files, "fixture claude -p bertanggal wajib ada"
    assert all(re.search(r"\.\d{4}-\d{2}-\d{2}\.json$", f.name) for f in files)


async def test_complete_through_provider_carries_cost_usd() -> None:
    fixture = (_FIXTURES / "claude_code_result.2026-07-22.json").read_text()
    provider = CliAgentProvider(
        ClaudeCodeAdapter(),
        "opus",
        runner=_runner_returning(CliRunResult(stdout=fixture, stderr="", returncode=0)),
    )
    req = CanonicalRequest(messages=[text("user", "capital of France?")], max_tokens=64)
    resp = await provider.complete(req)
    assert isinstance(resp, CanonicalResponse)
    assert resp.content[0].text == "The capital of France is Paris."
    assert resp.usage == Usage(prompt_tokens=812, completion_tokens=37)
    assert resp.usage.estimated is False
    assert resp.cost_usd == 0.0123          # total_cost_usd -> carrier kredit (§5.3)
    assert resp.latency_ms == 4213


_STREAM_LINES = [
    json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Paris"}]}}),
    json.dumps(
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": " is the capital."}]}}
    ),
    json.dumps(
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "Paris is the capital.", "total_cost_usd": 0.004,
         "usage": {"input_tokens": 5, "output_tokens": 6}, "duration_ms": 900}
    ),
]


def _stream_runner(lines: list[str]):
    final = CliRunResult(stdout="\n".join(lines), stderr="", returncode=0)

    async def runner(argv, *, stdin, env, timeout, on_line=None):
        for ln in lines:
            if on_line is not None and on_line(ln):  # truthy -> provider minta stop
                break
        return final
    return runner


async def test_stream_forwards_assistant_text_deltas() -> None:
    provider = CliAgentProvider(
        ClaudeCodeAdapter(), "opus", runner=_stream_runner(_STREAM_LINES)
    )
    req = CanonicalRequest(messages=[text("user", "capital?")], max_tokens=64)
    got: list[str] = []
    resp = await provider.stream(req, got.append)
    assert got == ["Paris", " is the capital."]   # init/result -> parse_delta None
    assert isinstance(resp, CanonicalResponse)


async def test_stream_early_stop_on_truthy_callback() -> None:
    provider = CliAgentProvider(
        ClaudeCodeAdapter(), "opus", runner=_stream_runner(_STREAM_LINES)
    )
    req = CanonicalRequest(messages=[text("user", "capital?")], max_tokens=64)
    got: list[str] = []

    def cb(s: str) -> bool:
        got.append(s)
        return True  # stop setelah delta pertama

    await provider.stream(req, cb)
    assert got == ["Paris"]  # delta kedua tak diteruskan (early-stop)


def test_stream_result_line_returns_last_result_type_line() -> None:
    a = ClaudeCodeAdapter()
    assert a.stream_result_line(_STREAM_LINES) == _STREAM_LINES[-1]


def test_stream_result_line_none_when_no_result_line() -> None:
    a = ClaudeCodeAdapter()
    assert a.stream_result_line(_STREAM_LINES[:-1]) is None
    assert a.stream_result_line([]) is None


async def test_stream_through_provider_surfaces_usage_and_cost() -> None:
    # §5.3: cost_usd is the primary credit source -- a STREAMED subscription call
    # must surface REAL usage/cost from the terminal result line, not Usage(0, 0).
    provider = CliAgentProvider(
        ClaudeCodeAdapter(), "opus", runner=_stream_runner(_STREAM_LINES)
    )
    req = CanonicalRequest(messages=[text("user", "capital?")], max_tokens=64)
    resp = await provider.stream(req, lambda _d: False)
    assert resp.content[0].text == "Paris is the capital."
    assert resp.usage == Usage(prompt_tokens=5, completion_tokens=6)
    assert resp.usage.estimated is False
    assert resp.cost_usd == 0.004
    assert resp.latency_ms == 900

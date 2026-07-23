# tests/providers/test_codex.py
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from baton.providers.base import ProviderError
from baton.providers.codex import CodexAdapter, build_codex_model, codex_detected
from baton.types import CanonicalRequest, Usage, text


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


def test_argv_omits_config_model_pair_when_model_empty() -> None:
    # Empty model (CODEX_MODEL unset) -> codex exec must fall back to the user's OWN
    # configured default, not an explicit `--config model=` (empty value breaks a real spawn).
    argv = CodexAdapter().argv(
        _req(), model="", max_output=4096, system_prompt_mode="append", stream=False,
    )
    assert argv == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert "--config" not in argv


def test_argv_includes_config_model_pair_when_model_set() -> None:
    argv = CodexAdapter().argv(
        _req(), model="gpt-5-codex", max_output=4096,
        system_prompt_mode="append", stream=False,
    )
    assert "--config" in argv
    assert "model=gpt-5-codex" in argv


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


def test_child_env_sets_depth_verbatim() -> None:
    # `depth` passed in is already the CHILD's intended depth (CliAgentProvider
    # does the +1 before calling child_env); the adapter writes it through
    # unchanged -- no double-bump (§8.2 fix).
    assert CodexAdapter().child_env({}, depth=0)["BATON_CLI_AGENT_DEPTH"] == "0"
    assert CodexAdapter().child_env({}, depth=1)["BATON_CLI_AGENT_DEPTH"] == "1"


def test_child_env_depth_matches_claude_code_adapter() -> None:
    # Provably consistent with ClaudeCodeAdapter (mirrors its own depth test,
    # `test_child_env_sets_depth_and_preserves_oauth` -- child env is "0" from a
    # depth=0 call): the two CliAgentAdapter implementations must never diverge on
    # how the shared CliAgentProvider recursion-depth env travels.
    from baton.providers.claude_code import DEPTH_ENV, ClaudeCodeAdapter

    for depth in (0, 1, 3):
        codex_env = CodexAdapter().child_env({}, depth=depth)
        claude_env = ClaudeCodeAdapter().child_env({}, depth=depth)
        assert codex_env["BATON_CLI_AGENT_DEPTH"] == claude_env[DEPTH_ENV] == str(depth)


async def test_depth_guard_refuses_recursion_for_codex(monkeypatch) -> None:
    # End-to-end (through the real CliAgentProvider, not just the adapter in
    # isolation): the anti-recursion guard must still block at the first hop for
    # Codex, exactly like it does for ClaudeCode (test_cli_agent.py's
    # test_depth_guard_refuses_recursion). Guard fires BEFORE spawn.
    from baton.providers.cli_agent import CliAgentProvider

    monkeypatch.setenv("BATON_CLI_AGENT_DEPTH", "1")
    runner = _CaptureRunner(_JSONL)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner, max_depth=1)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is False
    assert runner.argv is None  # guard fires BEFORE spawn -- runner never invoked


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


def test_parse_delta_returns_text_for_agent_message() -> None:
    line = json.dumps({"type": "agent_message", "message": "chunk-1"})
    assert CodexAdapter().parse_delta(line) == "chunk-1"


def test_parse_delta_none_for_lifecycle_and_garbage() -> None:
    a = CodexAdapter()
    assert a.parse_delta(json.dumps({"type": "turn.started"})) is None
    assert a.parse_delta(json.dumps({"type": "turn.completed", "usage": {}})) is None
    assert a.parse_delta("not json at all") is None
    assert a.parse_delta("") is None


def test_classify_not_logged_in_is_quota_exhausted_non_retryable() -> None:
    err = CodexAdapter().classify_error(
        _run_result("", stderr="Not logged in. Please run `codex login`.", returncode=1)
    )
    assert isinstance(err, ProviderError)
    assert err.quota_exhausted is True
    assert err.retryable is False


def test_classify_usage_limit_is_quota_exhausted() -> None:
    err = CodexAdapter().classify_error(
        _run_result("", stderr="You've hit your usage limit. Try again in 3h 12m.",
                    returncode=1)
    )
    assert err.quota_exhausted is True
    assert err.retryable is False


def test_classify_timeout_is_retryable_not_quota() -> None:
    err = CodexAdapter().classify_error(
        _run_result("", returncode=-9, timed_out=True)
    )
    assert err.retryable is True
    assert err.quota_exhausted is False


def test_classify_generic_failure_is_non_retryable_non_quota() -> None:
    err = CodexAdapter().classify_error(
        _run_result("", stderr="some unexpected internal error", returncode=2)
    )
    assert err.retryable is False
    assert err.quota_exhausted is False


# --- is_error / stream_result_line ------------------------------------------
# The CliAgentAdapter Protocol grew these two hooks after this plan section was
# drafted (B1/B2 review): CodexAdapter MUST implement them or it fails
# `isinstance(_, CliAgentAdapter)` and CliAgentProvider's error/stream guards
# silently no-op. PROVISIONAL: codex exit-0-but-failed-turn signalling is not yet
# live-verified (§14); we key off an explicit `{"type": "error"}` event OR a
# truthy `error` field on `turn.completed` -- reconfirm both at the live gate.


def test_is_error_true_for_explicit_error_event() -> None:
    jsonl = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "th_3"}),
        json.dumps({"type": "error", "message": "codex: sandbox denied"}),
    ])
    assert CodexAdapter().is_error(_run_result(jsonl)) is True


def test_is_error_true_when_turn_completed_carries_error_field() -> None:
    jsonl = "\n".join([
        json.dumps({"type": "agent_message", "message": "partial"}),
        json.dumps({"type": "turn.completed", "error": {"message": "turn failed"}}),
    ])
    assert CodexAdapter().is_error(_run_result(jsonl)) is True


def test_is_error_false_for_clean_turn_completed() -> None:
    assert CodexAdapter().is_error(_run_result(_JSONL)) is False


def test_is_error_false_when_unparseable() -> None:
    res = _run_result("not json", stderr="boom", returncode=1)
    assert CodexAdapter().is_error(res) is False


def test_stream_result_line_returns_synthesized_turn_completed_line() -> None:
    # Codex's real `turn.completed` carries usage but NOT the final text (that
    # lives on the earlier `agent_message` event) -- unlike Claude's self-contained
    # terminal `result` envelope. Because CliAgentProvider.stream() feeds ONLY this
    # ONE returned line into `parse()`, stream_result_line folds the accumulated
    # agent_message text into a Baton-namespaced SENTINEL key (`_baton_stream_message`,
    # NOT the plausible-real-wire-key `message`) so parse() still recovers it
    # (PROVISIONAL bridging shape, §14). The sentinel is Baton-internal -- the real
    # Codex CLI cannot emit it -- so a genuine live `turn.completed` shape can never
    # collide with it (see test_parse_ignores_real_message_key_on_turn_completed).
    lines = _JSONL.splitlines()
    result_line = CodexAdapter().stream_result_line(lines)
    assert result_line is not None
    parsed = json.loads(result_line)
    assert parsed["type"] == "turn.completed"
    assert parsed["usage"] == {"input_tokens": 120, "output_tokens": 34}
    assert parsed["_baton_stream_message"] == "Hello from Codex"
    assert "message" not in parsed  # never write the plausible-real-wire key


def test_parse_reads_baton_stream_message_sentinel_on_turn_completed() -> None:
    # This is the shape stream_result_line() synthesizes: a single turn.completed
    # line carrying the sentinel. parse() must recover the text from it exactly
    # like it would from an earlier agent_message event.
    jsonl = json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 7, "output_tokens": 3},
        "_baton_stream_message": "bridged text",
    })
    resp = CodexAdapter().parse(_run_result(jsonl), _req())
    assert resp.content[0].text == "bridged text"


def test_parse_ignores_real_message_key_on_turn_completed() -> None:
    # Hardening: `message` is a PLAUSIBLE real Codex wire key. If the installed
    # CLI ever emits a `message`/status field directly on a real turn.completed,
    # parse()'s non-synthetic complete() path must NOT treat it as agent text --
    # only the Baton-internal `_baton_stream_message` sentinel is honored.
    jsonl = "\n".join([
        json.dumps({"type": "agent_message", "message": "real answer"}),
        json.dumps({
            "type": "turn.completed",
            "message": "some unrelated real-CLI status string",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }),
    ])
    resp = CodexAdapter().parse(_run_result(jsonl), _req())
    assert resp.content[0].text == "real answer"  # NOT duplicated/corrupted


def test_stream_result_line_none_when_no_turn_completed() -> None:
    a = CodexAdapter()
    lines = _JSONL.splitlines()[:-1]  # drop the turn.completed line
    assert a.stream_result_line(lines) is None
    assert a.stream_result_line([]) is None


def test_codex_detected_true_on_exit_zero() -> None:
    def fake_run(argv, **kwargs):
        assert argv == ["codex", "login", "status"]
        return subprocess.CompletedProcess(argv, 0, stdout="Logged in", stderr="")
    assert codex_detected(run=fake_run) is True


def test_codex_detected_false_on_nonzero() -> None:
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Not logged in")
    assert codex_detected(run=fake_run) is False


def test_codex_detected_false_when_binary_missing() -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("codex")
    assert codex_detected(run=fake_run) is False


def test_build_codex_model_reads_env_tier_and_plan_included() -> None:
    mi = build_codex_model({
        "CODEX_MODEL": "gpt-5-codex",
        "CODEX_TIER": "4",
        "CODEX_CONTEXT": "400000",
        "CODEX_MAX_OUTPUT": "8192",
        "CODEX_TOOLS": "shell",
    })
    assert mi.id == "codex/gpt-5-codex"
    assert mi.provider == "codex"
    assert mi.tier == 4
    assert mi.billing == "plan_included"
    assert mi.context_window == 400000
    assert mi.max_output_tokens == 8192
    assert mi.supports_tools is True


def test_build_codex_model_requires_explicit_tier_no_sniff() -> None:
    # tier must be explicit — never sniffed from a "-mini" model name.
    with pytest.raises(ValueError):
        build_codex_model({"CODEX_MODEL": "gpt-5-codex-mini"})


def test_build_codex_model_default_id_when_model_unset() -> None:
    # No CODEX_MODEL -> a sensible "codex/default" id, consistent with argv omitting
    # `--config model=` (empty) so codex exec falls back to the user's own config.
    mi = build_codex_model({"CODEX_TIER": "3"})
    assert mi.id == "codex/default"


def test_build_codex_model_tools_absent_means_no_tools() -> None:
    mi = build_codex_model({"CODEX_MODEL": "gpt-5-codex", "CODEX_TIER": "3"})
    assert mi.supports_tools is False
    assert mi.tier == 3
    assert mi.billing == "plan_included"


class _CaptureRunner:
    """Mock CliRunner: records argv/env/stdin, returns canned JSONL stdout."""

    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self._stdout = stdout
        self._returncode = returncode
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None
        self.stdin: str | None = None
        self.lines: list[str] = []

    async def __call__(self, argv, *, stdin, env, timeout, on_line=None):
        from baton.providers.cli_agent import CliRunResult
        self.argv = argv
        self.env = env
        self.stdin = stdin
        if on_line is not None:
            for line in self._stdout.splitlines():
                on_line(line)
                self.lines.append(line)
        return CliRunResult(stdout=self._stdout, stderr="", returncode=self._returncode)


def test_codex_adapter_conforms_to_protocol() -> None:
    from baton.providers.base import LLMProvider
    from baton.providers.cli_agent import CliAgentAdapter, CliAgentProvider
    adapter = CodexAdapter()
    assert isinstance(adapter, CliAgentAdapter)
    provider = CliAgentProvider(adapter, "gpt-5-codex", runner=_CaptureRunner(_JSONL))
    assert isinstance(provider, LLMProvider)


async def test_complete_through_provider_nets_depth_one_at_top_level(monkeypatch) -> None:
    # End-to-end through the real CliAgentProvider + real CodexAdapter: a
    # top-level run (no BATON_CLI_AGENT_DEPTH in the parent env, i.e. depth 0) must
    # net the CHILD a depth of "1", not "2" -- regression guard for the adapter
    # double-increment bug (provider already adds +1; adapter must not add another).
    from baton.providers.cli_agent import CliAgentProvider

    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    runner = _CaptureRunner(_JSONL)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner)
    await provider.complete(_req("write a function"))
    assert runner.env["BATON_CLI_AGENT_DEPTH"] == "1"


async def test_complete_through_cli_agent_provider_scrubs_env(monkeypatch) -> None:
    from baton.providers.cli_agent import CliAgentProvider
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-scrubbed")
    monkeypatch.setenv("CODEX_API_KEY", "cdx-should-be-scrubbed")
    runner = _CaptureRunner(_JSONL)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner, tier=3)
    resp = await provider.complete(_req("write a function"))
    # argv wired correctly
    assert runner.argv[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert "model=gpt-5-codex" in runner.argv
    # child env scrubbed end-to-end (openai/codex #2000)
    assert "OPENAI_API_KEY" not in runner.env
    assert "CODEX_API_KEY" not in runner.env
    # parse produced final text + authoritative usage
    assert resp.content[0].text == "Hello from Codex"
    assert resp.usage.prompt_tokens == 120
    assert resp.usage.completion_tokens == 34


_FIXTURES = Path(__file__).parent / "fixtures"


def test_result_fixture_is_dated() -> None:
    # §8.3: provisional CLI output fixture MUST be dated (billing/schema is volatile
    # and NOT yet live-verified for Codex, §14).
    files = list(_FIXTURES.glob("codex_result.*.jsonl"))
    assert files, "dated provisional codex exec --json fixture wajib ada"
    assert all(re.search(r"\.\d{4}-\d{2}-\d{2}\.jsonl$", f.name) for f in files)


async def test_complete_through_provider_reads_dated_fixture() -> None:
    from baton.providers.cli_agent import CliAgentProvider

    fixture = (_FIXTURES / "codex_result.2026-07-23.jsonl").read_text()
    runner = _CaptureRunner(fixture)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner)
    resp = await provider.complete(_req("capital of France?"))
    assert resp.content[0].text == "The capital of France is Paris."
    assert resp.usage == Usage(prompt_tokens=512, completion_tokens=21)
    assert resp.usage.estimated is False
    assert resp.cost_usd == 0.0  # subscription leg: $0 cash (provisional, §8.3)


async def test_stream_forwards_agent_message_delta() -> None:
    from baton.providers.cli_agent import CliAgentProvider
    runner = _CaptureRunner(_JSONL)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner)
    chunks: list[str] = []
    resp = await provider.stream(_req(), chunks.append)
    # only the agent_message line yields a delta; lifecycle lines yield None
    assert chunks == ["Hello from Codex"]
    assert "Hello from Codex" in resp.content[0].text


_JSONL_TERMINAL_ERROR = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "th_4"}),
    json.dumps({"type": "agent_message", "message": "Working..."}),
    json.dumps({"type": "turn.completed", "error": {"message": "sandbox denied"}}),
])


async def test_stream_through_provider_surfaces_usage_and_cost() -> None:
    # §5.3: cost_usd is the primary credit source -- a STREAMED subscription call
    # must surface REAL usage from the synthesized terminal turn.completed line,
    # not a blind Usage(0, 0).
    from baton.providers.cli_agent import CliAgentProvider

    runner = _CaptureRunner(_JSONL)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner)
    resp = await provider.stream(_req(), lambda _d: False)
    assert resp.content[0].text == "Hello from Codex"
    assert resp.usage == Usage(prompt_tokens=120, completion_tokens=34)
    assert resp.usage.estimated is False


async def test_stream_terminal_is_error_reroutes() -> None:
    # Mirror ClaudeCodeAdapter's terminal-is_error regression test: on a REAL spawn
    # result.stdout is concatenated JSONL (multi-line), so is_error(aggregate) can't
    # see a mid-stream failure -- CliAgentProvider checks is_error on the TERMINAL
    # line itself (§ Protocol addendum). A codex turn that completes with an `error`
    # field must reroute (quota_exhausted=True), not return a bogus success.
    from baton.providers.cli_agent import CliAgentProvider

    runner = _CaptureRunner(_JSONL_TERMINAL_ERROR)
    provider = CliAgentProvider(CodexAdapter(), "gpt-5-codex", runner=runner)
    with pytest.raises(ProviderError) as ei:
        await provider.stream(_req(), lambda _d: False)
    assert ei.value.quota_exhausted is False
    assert ei.value.retryable is False

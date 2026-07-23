# tests/providers/test_cli_agent.py
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

from baton.providers.base import LLMProvider, ProviderError
from baton.providers.cli_agent import (
    CliAgentAdapter,
    CliAgentProvider,
    CliRunResult,
    subprocess_cli_runner,
)
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage, text


async def test_runner_feeds_stdin_and_captures_stdout():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin="hello",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
    )
    assert isinstance(r, CliRunResult)
    assert r.returncode == 0
    assert r.timed_out is False
    assert r.stdout == "HELLO"


async def test_runner_runs_in_clean_temp_cwd():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.listdir('.'))"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
    )
    lines = r.stdout.splitlines()
    assert "baton-cli-" in lines[0]  # ran in a fresh temp dir, not the repo
    assert lines[1] == "[]"          # temp cwd starts empty


async def test_runner_timeout_kills_process_group():
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=1.0,
    )
    assert r.timed_out is True
    assert r.returncode == -9


async def test_runner_cancellation_kills_and_raises():
    task = asyncio.ensure_future(
        subprocess_cli_runner(
            [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_runner_streams_lines_to_on_line():
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line.strip())
        return False  # falsy -> keep reading

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "print('a'); print('b'); print('c')"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
        on_line=_collect,
    )
    assert seen == ["a", "b", "c"]
    assert r.returncode == 0
    assert "a\n" in r.stdout


async def test_runner_early_stop_kills_process_group():
    seen: list[str] = []

    def _stop(line: str) -> bool:
        seen.append(line.strip())
        return True  # truthy on FIRST line -> cooperative early-stop

    start = time.monotonic()
    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time; print('0', flush=True); time.sleep(5)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=30.0,
        on_line=_stop,
    )
    elapsed = time.monotonic() - start
    assert seen == ["0"]
    assert elapsed < 2.0        # killed immediately, did NOT wait out sleep(5)
    assert r.timed_out is False
    assert r.stdout == "0\n"


async def test_stream_large_line_is_relayed_not_errored():
    # Default asyncio StreamReader limit is 64KB; claude/codex stream-json emits
    # single lines (e.g. tool_result) that routinely exceed it. Must be relayed,
    # not raise ValueError("chunk is longer than limit").
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line)
        return False

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "print('x' * 200000)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=10.0,
        on_line=_collect,
    )
    assert len(seen) == 1
    assert len(seen[0].strip()) == 200000
    assert r.returncode == 0


async def test_stream_on_line_raising_kills_and_reraises():
    # B2/B3 adapters will json.loads(line) inside on_line; a bad line raises.
    # The runner must re-raise AND reap the child -- never leave it orphaned.
    pids: list[int] = []

    def _boom(line: str) -> bool:
        pids.append(int(line.strip()))
        raise RuntimeError("boom")

    argv = [
        sys.executable,
        "-c",
        "import os, time; print(os.getpid(), flush=True); time.sleep(30)",
    ]
    with pytest.raises(RuntimeError):
        await subprocess_cli_runner(
            argv,
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
            on_line=_boom,
        )
    assert pids  # child's own pid was captured before on_line raised
    with pytest.raises(ProcessLookupError):
        os.getpgid(pids[0])  # process group is gone -> no orphan


async def test_stream_timeout_kills():
    seen: list[str] = []

    def _collect(line: str) -> bool:
        seen.append(line.strip())
        return False

    r = await subprocess_cli_runner(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
        stdin="",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=1.0,
        on_line=_collect,
    )
    assert r.timed_out is True
    assert r.returncode == -9


async def test_stream_cancel_kills_and_reraises():
    task = asyncio.ensure_future(
        subprocess_cli_runner(
            [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.05)"],
            stdin="",
            env={"PATH": os.environ.get("PATH", "")},
            timeout=30.0,
            on_line=lambda line: False,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _FakeAdapter:
    """In-test CliAgentAdapter: parses a tiny JSON shape, scrubs OPENAI_API_KEY in
    child_env, and maps not-logged-in / limit / quota to quota_exhausted."""

    name = "fake_cli"

    def __init__(
        self,
        *,
        is_error_result: bool = False,
        stream_result_line_value: str | None = None,
    ) -> None:
        self.argv_calls: list[dict] = []
        self._is_error_result = is_error_result
        self._stream_result_line_value = stream_result_line_value

    def argv(self, req, *, model, max_output, system_prompt_mode, stream):
        self.argv_calls.append(
            {
                "model": model,
                "max_output": max_output,
                "system_prompt_mode": system_prompt_mode,
                "stream": stream,
            }
        )
        return ["fake-cli", "--model", model]

    def child_env(self, base, *, depth):
        env = dict(base)
        env.pop("OPENAI_API_KEY", None)  # scrub hook per adapter (§8.1)
        env["BATON_CLI_AGENT_DEPTH"] = str(depth)
        return env

    def stdin(self, req):
        return "".join(
            b.text for m in req.messages for b in m.content if isinstance(b, TextBlock)
        )

    def parse(self, result, req):
        data = json.loads(result.stdout)
        u = data["usage"]
        return CanonicalResponse(
            content=[TextBlock(text=data["result"])],
            usage=Usage(prompt_tokens=u["input_tokens"], completion_tokens=u["output_tokens"]),
            model=self.name,
            stop_reason="end_turn",
            latency_ms=0,
            cost_usd=data.get("total_cost_usd"),  # -> credit source (§5.3)
        )

    def parse_delta(self, line):
        line = line.strip()
        if not line:
            return None
        data = json.loads(line)
        return data["text"] if data.get("type") == "text" else None

    def classify_error(self, result):
        blob = (result.stdout + result.stderr).lower()
        if "not logged in" in blob or "limit reached" in blob or "quota" in blob:
            return ProviderError(
                "subscription quota exhausted", retryable=False, quota_exhausted=True
            )
        return ProviderError(f"fake-cli exited {result.returncode}", retryable=False)

    def is_error(self, result):
        return self._is_error_result

    def stream_result_line(self, lines):
        # Real adapters (e.g. ClaudeCodeAdapter) return the terminal `type:"result"`
        # line so the provider can surface real usage/cost from a streamed run;
        # this fake defaults to None (unchanged behavior) unless a test configures it.
        return self._stream_result_line_value


class _RecordingRunner:
    """Injected fake runner: records argv/stdin/env, replays a fixed CliRunResult, and
    (for stream) feeds `lines` to on_line honoring truthy early-stop. No real spawn."""

    def __init__(self, result, lines=None):
        self.result = result
        self.lines = lines or []
        self.argv = None
        self.stdin = None
        self.env = None

    async def __call__(self, argv, *, stdin, env, timeout, on_line=None):
        self.argv = argv
        self.stdin = stdin
        self.env = env
        if on_line is not None:
            for line in self.lines:
                if on_line(line):  # truthy -> early stop
                    break
        return self.result


def _req(text_in="explain recursion"):
    return CanonicalRequest(
        messages=[text("user", text_in)], max_tokens=999999, temperature=0.9
    )


def _ok_result(**extra):
    payload = {"result": "done", "usage": {"input_tokens": 12, "output_tokens": 5}}
    payload.update(extra)
    return CliRunResult(json.dumps(payload), "", 0)


def test_fake_adapter_conforms_to_protocol():
    assert isinstance(_FakeAdapter(), CliAgentAdapter)


def test_provider_is_llmprovider_with_adapter_name():
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=_RecordingRunner(_ok_result()))
    assert isinstance(provider, LLMProvider)
    assert provider.name == "fake_cli"


async def test_complete_parses_usage_and_cost(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    runner = _RecordingRunner(_ok_result(total_cost_usd=0.01))
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    resp = await provider.complete(_req())
    assert resp.content[0].text == "done"
    assert (resp.usage.prompt_tokens, resp.usage.completion_tokens) == (12, 5)
    assert resp.cost_usd == 0.01  # JSON total_cost_usd -> CanonicalResponse.cost_usd


async def test_complete_ignores_req_temperature_and_max_tokens(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    adapter = _FakeAdapter()
    provider = CliAgentProvider(
        adapter, "opus", runner=_RecordingRunner(_ok_result()), max_output=4096
    )
    await provider.complete(_req())  # req.max_tokens=999999, temperature=0.9
    call = adapter.argv_calls[-1]
    assert call["max_output"] == 4096  # conservative provider cap, NOT req.max_tokens (§8.3)
    assert call["stream"] is False


async def test_child_env_increments_depth(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    runner = _RecordingRunner(_ok_result())
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    await provider.complete(_req())
    assert runner.env["BATON_CLI_AGENT_DEPTH"] == "1"  # depth 0 -> child gets 1


async def test_child_env_scrubs_openai_api_key(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    runner = _RecordingRunner(_ok_result())
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    await provider.complete(_req())
    assert "OPENAI_API_KEY" not in runner.env  # adapter scrub hook honored


async def test_depth_guard_refuses_recursion(monkeypatch):
    monkeypatch.setenv("BATON_CLI_AGENT_DEPTH", "1")
    runner = _RecordingRunner(_ok_result())
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner, max_depth=1)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is False  # depth cap -> fail-fast, no reroute-backoff
    assert runner.argv is None  # guard fires BEFORE spawn -- runner never invoked


async def test_complete_maps_not_logged_in_to_quota_exhausted(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    runner = _RecordingRunner(CliRunResult(stdout="", stderr="Error: not logged in", returncode=1))
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.quota_exhausted is True
    assert ei.value.retryable is False  # quota_exhausted => never backoff, reroute instead


async def test_complete_timed_out_result_is_classified(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    runner = _RecordingRunner(CliRunResult("", "hard limit reached", -9, timed_out=True))
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.quota_exhausted is True


async def test_complete_is_error_payload_with_zero_exit_is_classified(monkeypatch):
    # claude -p can exit 0 while the JSON envelope carries is_error=true (max-turns /
    # mid-run execution error) -- the base provider can't parse JSON to detect this,
    # so the adapter's is_error hook must be consulted even when returncode == 0.
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    adapter = _FakeAdapter(is_error_result=True)
    runner = _RecordingRunner(_ok_result())  # returncode=0, well-formed JSON success shape
    provider = CliAgentProvider(adapter, "opus", runner=runner)
    with pytest.raises(ProviderError):
        await provider.complete(_req())


async def test_stream_relays_deltas_and_early_stop(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    lines = [
        json.dumps({"type": "text", "text": "Hel"}) + "\n",
        json.dumps({"type": "text", "text": "lo"}) + "\n",
        json.dumps({"type": "text", "text": " world"}) + "\n",
    ]
    runner = _RecordingRunner(CliRunResult("", "", 0), lines=lines)
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner)
    seen: list[str] = []

    def _on_text(delta: str) -> bool:
        seen.append(delta)
        return len(seen) >= 2  # truthy after 2 deltas -> cooperative early-stop

    resp = await provider.stream(_req(), _on_text)
    assert seen == ["Hel", "lo"]           # 3rd delta never delivered (stopped)
    assert resp.content[0].text == "Hello"  # accumulated partial
    assert resp.usage.estimated is True


async def test_stream_surfaces_usage_and_cost_from_terminal_result_line(monkeypatch):
    # §5.3: cost_usd is the primary credit source -- a STREAMED subscription call
    # must not report Usage(0, 0) / no cost just because it went through .stream().
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    lines = [json.dumps({"type": "text", "text": "Hello"}) + "\n"]
    result_line = json.dumps(
        {
            "result": "Hello",
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "total_cost_usd": 0.02,
        }
    )
    adapter = _FakeAdapter(stream_result_line_value=result_line)
    runner = _RecordingRunner(CliRunResult("", "", 0), lines=lines)
    provider = CliAgentProvider(adapter, "opus", runner=runner)
    resp = await provider.stream(_req(), lambda _d: False)
    assert resp.content[0].text == "Hello"
    assert (resp.usage.prompt_tokens, resp.usage.completion_tokens) == (3, 2)
    assert resp.usage.estimated is False
    assert resp.cost_usd == 0.02  # terminal result line -> real usage/cost, not Usage(0,0)


async def test_stream_early_stop_keeps_partial_even_with_result_line(monkeypatch):
    # Early-stop (cooperative cancel) must NOT be overridden by a terminal result
    # line -- the accumulated partial is the correct answer when the caller asked
    # to stop early, even if the runner happens to hand back a result line.
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    lines = [
        json.dumps({"type": "text", "text": "Hel"}) + "\n",
        json.dumps({"type": "text", "text": "lo world"}) + "\n",
    ]
    result_line = json.dumps(
        {"result": "Hello world", "usage": {"input_tokens": 3, "output_tokens": 2}}
    )
    adapter = _FakeAdapter(stream_result_line_value=result_line)
    runner = _RecordingRunner(CliRunResult("", "", 0), lines=lines)
    provider = CliAgentProvider(adapter, "opus", runner=runner)
    resp = await provider.stream(_req(), lambda _d: True)  # stop after first delta
    assert resp.content[0].text == "Hel"       # partial, NOT the full result-line text
    assert resp.usage.estimated is True         # unchanged early-stop contract


async def test_stream_sets_stream_flag_true(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    adapter = _FakeAdapter()
    provider = CliAgentProvider(adapter, "opus", runner=_RecordingRunner(CliRunResult("", "", 0)))
    await provider.stream(_req(), lambda _d: False)
    assert adapter.argv_calls[-1]["stream"] is True


async def test_concurrency_cap_serializes_spawns(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    inflight = 0
    max_seen = 0

    async def _runner(argv, *, stdin, env, timeout, on_line=None):
        nonlocal inflight, max_seen
        inflight += 1
        max_seen = max(max_seen, inflight)
        await asyncio.sleep(0.03)
        inflight -= 1
        return _ok_result()

    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=_runner, concurrency=1)
    await asyncio.gather(*(provider.complete(_req()) for _ in range(4)))
    assert max_seen == 1  # per-provider cap nested in fan-out semaphore (§8.2)


async def test_stream_depth_guard_refuses_recursion(monkeypatch):
    monkeypatch.setenv("BATON_CLI_AGENT_DEPTH", "1")
    runner = _RecordingRunner(CliRunResult("", "", 0))
    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=runner, max_depth=1)
    with pytest.raises(ProviderError) as ei:
        await provider.stream(_req(), lambda _d: False)
    assert ei.value.retryable is False  # depth cap -> fail-fast, no reroute-backoff
    assert runner.argv is None  # guard fires BEFORE spawn -- runner never invoked


async def test_stream_concurrency_cap_serializes_spawns(monkeypatch):
    monkeypatch.delenv("BATON_CLI_AGENT_DEPTH", raising=False)
    inflight = 0
    max_seen = 0

    async def _runner(argv, *, stdin, env, timeout, on_line=None):
        nonlocal inflight, max_seen
        inflight += 1
        max_seen = max(max_seen, inflight)
        await asyncio.sleep(0.03)
        inflight -= 1
        return CliRunResult("", "", 0)

    provider = CliAgentProvider(_FakeAdapter(), "opus", runner=_runner, concurrency=1)
    await asyncio.gather(*(provider.stream(_req(), lambda _d: False) for _ in range(4)))
    assert max_seen == 1  # per-provider cap nested in fan-out semaphore (§8.2)

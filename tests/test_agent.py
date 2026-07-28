from __future__ import annotations

import pytest

from volante.agent import AgenticResult, AgenticWorker
from volante.cost import CostMeter
from volante.providers.base import IncompleteOutputError
from volante.providers.base import ProviderError as _PE
from volante.providers.fake import FakeProvider
from volante.tools.base import ToolRegistry
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    CapabilityUnavailableError,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
    text,
)


class _RecordingTool:
    name = "run_python"
    spec = ToolSpec(name="run_python", description="x", input_schema={"type": "object"})

    def __init__(self, name: str = "run_python") -> None:
        self.name = name
        self.spec = ToolSpec(
            name=name,
            description="x",
            input_schema={"type": "object"},
        )
        self.calls: list[dict] = []

    async def run(self, args: dict) -> str:
        self.calls.append(args)
        return "exit=0\nstdout:\nOK\n"


def _resp(
    content: list,
    stop: str,
    usage=(3, 2),
    *,
    cost_usd: float | None = None,
) -> CanonicalResponse:
    return CanonicalResponse(
        content=content,
        usage=Usage(prompt_tokens=usage[0], completion_tokens=usage[1]),
        model="m1",
        stop_reason=stop,
        latency_ms=1,
        cost_usd=cost_usd,
    )


def _req() -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", "fix the bug")], max_tokens=256, task_id="t1")


@pytest.mark.asyncio
async def test_loop_runs_tool_then_finishes() -> None:
    tool = _RecordingTool()
    tools: ToolRegistry = {"run_python": tool}
    provider = FakeProvider(
        responses=[
            _resp(
                [ToolUseBlock(id="u1", name="run_python", input={"code": "print(1)"})],
                "tool_use",
            ),
            _resp([TextBlock(text="done, tests pass")], "end_turn"),
        ]
    )
    meter = CostMeter()
    worker = AgenticWorker({"m1": provider}, meter, max_iters=8)
    req = _req()

    res = await worker.run(req, "m1", tools)

    assert isinstance(res, AgenticResult)
    assert res.final_text == "done, tests pass"
    assert tool.calls == [{"code": "print(1)"}]                 # tool dieksekusi
    assert res.usage_total["m1"].completion_tokens == 4         # 2 turn x 2
    assert meter.totals()["m1"].prompt_tokens == 6              # shared meter jg terisi
    assert any(t.kind == "tool_use" for t in res.turns)         # jejak terekam
    assert any(t.kind == "tool_result" for t in res.turns)


@pytest.mark.parametrize("stop_reason", ["max_tokens", "content_filter"])
async def test_agentic_rejects_incomplete_terminal_output_after_tool_use(
    stop_reason: str,
) -> None:
    provider = FakeProvider(
        responses=[
            _resp(
                [ToolUseBlock(id="u1", name="run_python", input={"code": "1"})],
                "tool_use",
            ),
            _resp([TextBlock(text="partial")], stop_reason),
        ]
    )
    meter = CostMeter()
    worker = AgenticWorker({"m1": provider}, meter)

    with pytest.raises(IncompleteOutputError) as exc_info:
        await worker.run(
            _req(),
            "m1",
            {"run_python": _RecordingTool()},
        )

    assert exc_info.value.phase == "agentic worker"
    assert exc_info.value.stop_reason == stop_reason
    assert meter.totals()["m1"] == Usage(6, 4)


@pytest.mark.asyncio
async def test_agentic_rejects_final_when_a_required_tool_was_not_invoked() -> None:
    provider = FakeProvider(
        responses=[
            _resp(
                [
                    ToolUseBlock(
                        id="u1",
                        name="run_python",
                        input={"code": "print(1)"},
                    )
                ],
                "tool_use",
            ),
            _resp([TextBlock(text="done")], "end_turn"),
        ]
    )
    worker = AgenticWorker({"m1": provider}, CostMeter())

    with pytest.raises(CapabilityUnavailableError, match="fetch_url"):
        await worker.run(
            _req(),
            "m1",
            {"run_python": _RecordingTool()},
            required_tools=frozenset({"run_python", "fetch_url"}),
        )


@pytest.mark.asyncio
async def test_agentic_reports_every_required_tool_it_actually_invoked() -> None:
    run_python = _RecordingTool()
    fetch_url = _RecordingTool("fetch_url")
    provider = FakeProvider(
        responses=[
            _resp(
                [
                    ToolUseBlock(
                        id="u1",
                        name="run_python",
                        input={"code": "print(1)"},
                    )
                ],
                "tool_use",
            ),
            _resp(
                [
                    ToolUseBlock(
                        id="u2",
                        name="fetch_url",
                        input={"url": "https://example.com"},
                    )
                ],
                "tool_use",
            ),
            _resp([TextBlock(text="verified")], "end_turn"),
        ]
    )

    result = await AgenticWorker({"m1": provider}, CostMeter()).run(
        _req(),
        "m1",
        {"run_python": run_python, "fetch_url": fetch_url},
        required_tools=frozenset({"run_python", "fetch_url"}),
    )

    assert result.final_text == "verified"
    assert result.tools_used == ("fetch_url", "run_python")
    assert len(run_python.calls) == 1
    assert len(fetch_url.calls) == 1


@pytest.mark.asyncio
async def test_agentic_forwards_provider_authoritative_cost_each_turn() -> None:
    provider = FakeProvider(
        responses=[
            _resp(
                [ToolUseBlock(id="u1", name="run_python", input={"code": "1"})],
                "tool_use",
                cost_usd=0.1,
            ),
            _resp([TextBlock(text="done")], "end_turn", cost_usd=0.2),
        ]
    )
    meter = CostMeter()
    worker = AgenticWorker({"m1": provider}, meter)

    await worker.run(_req(), "m1", {"run_python": _RecordingTool()})

    assert meter._direct["m1"]["usd"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_input_messages_not_mutated() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    provider = FakeProvider(
        responses=[
            _resp([ToolUseBlock(id="u1", name="run_python", input={"code": "x"})], "tool_use"),
            _resp([TextBlock(text="ok")], "end_turn"),
        ]
    )
    worker = AgenticWorker({"m1": provider}, CostMeter())
    req = _req()
    before = len(req.messages)

    await worker.run(req, "m1", tools)

    assert len(req.messages) == before  # bekerja pada salinan; input utuh


class _AlwaysToolUse:
    """Provider yang selalu minta tool → memaksa loop mentok max_iters.

    Tiap panggilan BERBEDA supaya yang diuji benar-benar batas iterasi, bukan guard
    no-progress (yang menangkap pengulangan identik lebih dulu — lihat
    test_repeating_the_same_tool_call_is_nudged_once_then_abandoned)."""

    name = "loopy"

    def __init__(self) -> None:
        self.n = 0

    async def complete(self, req):
        self.n += 1
        return _resp(
            [ToolUseBlock(id=f"u{self.n}", name="run_python", input={"code": str(self.n)})],
            "tool_use",
        )


class _Flaky:
    """Fail once, then use a configured tool before returning a final answer."""

    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        if self.calls == 1:
            raise _PE("429 rate limit", retryable=True, status=429)
        if self.calls == 2:
            return _resp(
                [ToolUseBlock(id="u", name="run_python", input={"code": "1"})],
                "tool_use",
            )
        return _resp([TextBlock(text="recovered")], "end_turn")


@pytest.mark.asyncio
async def test_max_iters_exhausted_raises_non_retryable() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    worker = AgenticWorker({"m1": _AlwaysToolUse()}, CostMeter(), max_iters=3)
    with pytest.raises(_PE) as ei:
        await worker.run(_req(), "m1", tools)
    assert ei.value.retryable is False
    assert "exhausted" in str(ei.value)


@pytest.mark.asyncio
async def test_retryable_error_handled_in_loop() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    provider = _Flaky()
    res = await AgenticWorker({"m1": provider}, CostMeter(), max_retries=2).run(_req(), "m1", tools)
    assert res.final_text == "recovered"
    assert provider.calls == 3  # failed retry + tool turn + final turn


@pytest.mark.asyncio
async def test_transcript_budget_guard_fails_early() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    worker = AgenticWorker({"m1": _AlwaysToolUse()}, CostMeter(), max_iters=8, char_budget=1)
    with pytest.raises(_PE) as ei:
        await worker.run(_req(), "m1", tools)
    assert ei.value.retryable is False
    assert "budget" in str(ei.value)


@pytest.mark.asyncio
async def test_run_streams_when_on_text_given() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    provider = FakeProvider(
        responses=[
            _resp([ToolUseBlock(id="u1", name="run_python", input={"code": "x"})], "tool_use"),
            _resp([TextBlock(text="done")], "end_turn"),
        ]
    )
    got: list[str] = []
    res = await AgenticWorker({"m1": provider}, CostMeter()).run(
        _req(), "m1", tools, on_text=got.append
    )
    assert res.final_text == "done"
    assert "done" in "".join(got)  # teks final ter-stream


class _QuotaThenNever:
    """Provider yang langsung quota_exhausted (retryable False)."""

    name = "quota"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        raise _PE("plan quota exhausted", retryable=False, quota_exhausted=True)


class _UnavailableThenNever:
    name = "unavailable"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        raise _PE(
            "model_not_found",
            retryable=False,
            status=404,
            candidate_unavailable=True,
        )


class _TransientThenNever:
    name = "transient"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        raise _PE("upstream unavailable", retryable=True, status=503)


@pytest.mark.asyncio
async def test_quota_exhausted_propagates_without_backoff(monkeypatch) -> None:
    slept: list[float] = []

    async def _no_sleep(delay: float) -> None:
        slept.append(delay)  # rekam backoff tanpa tidur nyata

    monkeypatch.setattr("volante.agent.asyncio.sleep", _no_sleep)

    tools: ToolRegistry = {"run_python": _RecordingTool()}
    provider = _QuotaThenNever()
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_retries=2)

    with pytest.raises(_PE) as ei:
        await worker.run(_req(), "m1", tools)

    assert ei.value.quota_exhausted is True   # dipropagasi ke Runtime (bukan dibuang)
    assert ei.value.retryable is False
    assert provider.calls == 1                # short-circuit: TAK ada retry
    assert slept == []                        # TAK ada backoff/sleep


@pytest.mark.asyncio
async def test_candidate_unavailable_propagates_without_backoff(monkeypatch) -> None:
    slept: list[float] = []

    async def _no_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("volante.agent.asyncio.sleep", _no_sleep)
    provider = _UnavailableThenNever()
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_retries=2)

    with pytest.raises(_PE) as raised:
        await worker.run(
            _req(),
            "m1",
            {"run_python": _RecordingTool()},
        )

    assert raised.value.candidate_unavailable is True
    assert raised.value.retryable is False
    assert provider.calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_exhausted_transient_agentic_failure_becomes_provider_unavailable(
    monkeypatch,
) -> None:
    slept: list[float] = []

    async def _no_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("volante.agent.asyncio.sleep", _no_sleep)
    provider = _TransientThenNever()
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_retries=2)

    with pytest.raises(_PE) as raised:
        await worker.run(
            _req(),
            "m1",
            {"run_python": _RecordingTool()},
        )

    assert raised.value.provider_unavailable is True
    assert raised.value.retryable is False
    assert provider.calls == 3
    assert len(slept) == 2


@pytest.mark.asyncio
async def test_call_gate_counts_every_agentic_retry_attempt() -> None:
    provider = _Flaky()
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_retries=2)
    gated: list[str] = []
    worker.set_call_gate(gated.append)

    result = await worker.run(
        _req(), "m1", {"run_python": _RecordingTool()}
    )

    assert result.final_text == "recovered"
    assert gated == ["m1", "m1", "m1"]


class _HugeResultTool(_RecordingTool):
    async def run(self, args: dict) -> str:
        return "HEAD-" + ("X" * 10_000) + "-TAIL"


class _CaptureSecondTurn:
    name = "capture"

    def __init__(self) -> None:
        self.requests: list[CanonicalRequest] = []

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.requests.append(req)
        if len(self.requests) == 1:
            return _resp(
                [
                    ToolUseBlock(
                        id="u1", name="run_python", input={"code": "print(1)"}
                    )
                ],
                "tool_use",
            )
        return _resp([TextBlock(text="bounded")], "end_turn")


@pytest.mark.asyncio
async def test_agentic_tool_results_are_trimmed_to_selected_model_context() -> None:
    provider = _CaptureSecondTurn()
    req = CanonicalRequest(
        messages=[text("user", "inspect")],
        max_tokens=20,
        task_id="t1",
        context_window=100,
    )
    worker = AgenticWorker({"m1": provider}, CostMeter(), char_budget=400_000)

    result = await worker.run(
        req, "m1", {"run_python": _HugeResultTool()}
    )

    assert result.final_text == "bounded"
    second = provider.requests[1]
    tool_result = second.messages[-1].content[0]
    assert isinstance(tool_result, ToolResultBlock)
    assert len(tool_result.content) < 10_000
    assert "truncated" in tool_result.content
    # (100 - 20) * 0.85 * 4 = 272 conservative input characters.
    total = sum(
        len(getattr(block, "text", getattr(block, "content", "")))
        for message in second.messages
        for block in message.content
    )
    assert total <= 272


class _StuckTool:
    """Always returns the same failure — the shape that triggers a deterministic livelock."""

    name = "run_python"
    spec = ToolSpec(name="run_python", description="x", input_schema={"type": "object"})

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, args: dict) -> str:
        self.calls.append(args)
        return "exit=1\nstdout:\nstderr:\nAssertionError"


def _same_tool_call() -> CanonicalResponse:
    # Byte-identical every turn: what a temperature-0 model does when it keeps
    # "fixing" a bug the same way and getting the same error back.
    return _resp(
        [ToolUseBlock(id="u1", name="run_python", input={"code": "assert broken()"})],
        "tool_use",
    )


@pytest.mark.asyncio
async def test_repeating_the_same_tool_call_is_nudged_once_then_abandoned() -> None:
    # Observed live with gpt-4o-mini: from turn 3 on it re-sent a byte-identical call and
    # got a byte-identical error, burning every remaining iteration before failing with a
    # generic "exhausted". The loop must notice it is making no progress.
    tool = _StuckTool()
    provider = FakeProvider(responses=[_same_tool_call() for _ in range(12)])
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_iters=12)

    with pytest.raises(_PE, match="no progress"):
        await worker.run(_req(), "m1", {"run_python": tool})

    # Stops well before max_iters instead of paying for identical calls.
    assert len(tool.calls) <= 4, f"burned {len(tool.calls)} identical calls"


@pytest.mark.asyncio
async def test_a_nudged_model_that_changes_course_still_succeeds() -> None:
    # The nudge is a chance to recover, not just a kill switch.
    tool = _StuckTool()
    provider = FakeProvider(
        responses=[
            _same_tool_call(),
            _same_tool_call(),  # repeat -> nudge
            _resp([TextBlock(text="different approach, done")], "end_turn"),
        ]
    )
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_iters=8)

    result = await worker.run(_req(), "m1", {"run_python": tool})

    assert result.final_text == "different approach, done"


@pytest.mark.asyncio
async def test_genuine_progress_is_never_mistaken_for_a_stall() -> None:
    tool = _RecordingTool()
    provider = FakeProvider(
        responses=[
            _resp([ToolUseBlock(id="u1", name="run_python", input={"code": "step1"})], "tool_use"),
            _resp([ToolUseBlock(id="u2", name="run_python", input={"code": "step2"})], "tool_use"),
            _resp([ToolUseBlock(id="u3", name="run_python", input={"code": "step3"})], "tool_use"),
            _resp([TextBlock(text="all good")], "end_turn"),
        ]
    )
    worker = AgenticWorker({"m1": provider}, CostMeter(), max_iters=8)

    result = await worker.run(_req(), "m1", {"run_python": tool})

    assert result.final_text == "all good"
    assert len(tool.calls) == 3

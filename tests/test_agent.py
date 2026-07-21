from __future__ import annotations

import pytest

from orchestrator.agent import AgenticResult, AgenticWorker
from orchestrator.cost import CostMeter
from orchestrator.providers.base import ProviderError as _PE
from orchestrator.providers.fake import FakeProvider
from orchestrator.tools.base import ToolRegistry
from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
    text,
)


class _RecordingTool:
    name = "run_python"
    spec = ToolSpec(name="run_python", description="x", input_schema={"type": "object"})

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, args: dict) -> str:
        self.calls.append(args)
        return "exit=0\nstdout:\nOK\n"


def _resp(content: list, stop: str, usage=(3, 2)) -> CanonicalResponse:
    return CanonicalResponse(
        content=content,
        usage=Usage(prompt_tokens=usage[0], completion_tokens=usage[1]),
        model="m1",
        stop_reason=stop,
        latency_ms=1,
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
    """Provider yang selalu minta tool → memaksa loop mentok max_iters."""

    name = "loopy"

    async def complete(self, req):
        return _resp([ToolUseBlock(id="u", name="run_python", input={"code": "1"})], "tool_use")


class _Flaky:
    """Gagal retryable sekali, lalu end_turn."""

    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        if self.calls == 1:
            raise _PE("429 rate limit", retryable=True, status=429)
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
    assert provider.calls == 2  # gagal sekali (retryable), sukses di percobaan kedua


@pytest.mark.asyncio
async def test_transcript_budget_guard_fails_early() -> None:
    tools: ToolRegistry = {"run_python": _RecordingTool()}
    worker = AgenticWorker({"m1": _AlwaysToolUse()}, CostMeter(), max_iters=8, char_budget=1)
    with pytest.raises(_PE) as ei:
        await worker.run(_req(), "m1", tools)
    assert ei.value.retryable is False
    assert "budget" in str(ei.value)

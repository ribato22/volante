"""Unit tests for the MCP server's engine-facing logic (no `mcp` dep needed)."""

from __future__ import annotations

import pytest
from baton_mcp.server import format_result, run_goal


class _FakeResult:
    def __init__(self, **kw):
        self.status = kw.get("status", "success")
        self.final = kw.get("final", "hello world")
        self.failed_task = kw.get("failed_task")
        self.cost_usd = kw.get("cost_usd", 0.25)
        self.billed_usd = kw.get("billed_usd", 0.0)
        self.credit_usd = kw.get("credit_usd", 0.25)
        self.duration_ms = kw.get("duration_ms", 61569)


class _FakeRuntime:
    def __init__(self, res: _FakeResult) -> None:
        self._res = res
        self.seen_goal: str | None = None

    async def aexecute(self, goal, on_text=None, on_worker_text=None):
        self.seen_goal = goal
        return self._res


async def test_run_goal_maps_runresult_to_dict() -> None:
    res = _FakeResult()
    out = await run_goal("Summarize X", runtime_factory=lambda: _FakeRuntime(res))
    assert out["status"] == "success"
    assert out["final"] == "hello world"
    assert out["billed_usd"] == 0.0
    assert out["credit_usd"] == 0.25
    assert out["duration_ms"] == 61569


async def test_run_goal_trims_and_forwards_goal() -> None:
    rt = _FakeRuntime(_FakeResult())
    await run_goal("  do the thing  ", runtime_factory=lambda: rt)
    assert rt.seen_goal == "do the thing"


async def test_run_goal_rejects_empty_goal() -> None:
    with pytest.raises(ValueError):
        await run_goal("   ", runtime_factory=lambda: _FakeRuntime(_FakeResult()))


def test_format_result_success_has_answer_and_ledger() -> None:
    s = format_result(
        {
            "status": "success",
            "final": "the answer",
            "billed_usd": 0.0,
            "credit_usd": 0.25,
            "duration_ms": 61569,
        }
    )
    assert "the answer" in s
    assert "cash $0.000000" in s
    assert "plan credit $0.250000" in s
    assert "61.6s" in s


def test_format_result_failure_notes_failed_task() -> None:
    s = format_result(
        {
            "status": "failed",
            "final": None,
            "failed_task": "research",
            "billed_usd": 0.0,
            "credit_usd": 0.0,
            "duration_ms": 10,
        }
    )
    assert "failed" in s.lower()
    assert "research" in s

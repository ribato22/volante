from __future__ import annotations

from pathlib import Path

import pytest

from baton.agent import AgenticWorker
from baton.cost import CostMeter
from baton.providers.base import ProviderError
from baton.registry import Registry
from baton.runtime import Runtime
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
    text,
)
from baton.worker import Worker


def _model(mid: str, ctx: int) -> ModelInfo:
    return ModelInfo(
        id=mid, provider="fake", strengths={"coding"}, context_window=ctx,
        max_output_tokens=4096, supports_tools=True,
        cost_per_1k_in=0.001, cost_per_1k_out=0.002,
    )


class _RecordingTool:
    name = "run_python"
    spec = ToolSpec(name="run_python", description="x", input_schema={"type": "object"})

    async def run(self, args: dict) -> str:
        return "exit=0\nstdout:\nOK\n"


class _ToolThenQuota:
    """Kandidat A: satu turn tool_use (kerja parsial ter-meter) LALU quota_exhausted."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        if self.calls == 1:
            return CanonicalResponse(
                content=[ToolUseBlock(id="a1", name="run_python", input={"code": "x"})],
                usage=Usage(prompt_tokens=5, completion_tokens=3),
                model=self.name, stop_reason="tool_use", latency_ms=1,
            )
        raise ProviderError("plan quota exhausted", retryable=False, quota_exhausted=True)

    async def stream(self, req, on_text) -> CanonicalResponse:
        return await self.complete(req)


class _ToolThenDone:
    """Kandidat B: tool_use lalu end_turn; catat max_tokens req (bukti re-proyeksi)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen_max_tokens: list[int] = []
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.seen_max_tokens.append(req.max_tokens)
        self.calls += 1
        if self.calls == 1:
            return CanonicalResponse(
                content=[ToolUseBlock(id="b1", name="run_python", input={"code": "y"})],
                usage=Usage(prompt_tokens=3, completion_tokens=2),
                model=self.name, stop_reason="tool_use", latency_ms=1,
            )
        return CanonicalResponse(
            content=[TextBlock(text="done via B")],
            usage=Usage(prompt_tokens=3, completion_tokens=2),
            model=self.name, stop_reason="end_turn", latency_ms=1,
        )

    async def stream(self, req, on_text) -> CanonicalResponse:
        return await self.complete(req)


class _Sup:
    def __init__(self, plan) -> None:
        self._plan = plan

    async def plan(self, goal, on_text=None):
        return list(self._plan)


class _RankRouter:
    def __init__(self, ranked: list[str]) -> None:
        self._ranked = ranked

    def route_ranked(self, task) -> list[str]:
        return list(self._ranked)

    def route(self, task) -> str:  # back-compat, tak dipakai jalur ini
        return self._ranked[0]


class _Projector:
    """max_tokens per model -> buktikan RE-PROYEKSI per kandidat; tangkap bb."""

    def __init__(self, max_tokens_by_model: dict[str, int]) -> None:
        self._mt = max_tokens_by_model
        self.last_bb = None

    def project(self, task, model_id, bb) -> CanonicalRequest:
        self.last_bb = bb
        return CanonicalRequest(
            messages=[text("user", task.description)],
            max_tokens=self._mt[model_id], task_id=task.id,
        )


class _Synth:
    async def synthesize(self, goal, bb, on_text=None) -> str:
        return "synth"


@pytest.mark.asyncio
async def test_agentic_reroutes_on_quota_to_next_candidate_reprojected(
    tmp_path: Path, monkeypatch
) -> None:
    slept: list[float] = []

    async def _no_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("baton.agent.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("baton.runtime.asyncio.sleep", _no_sleep)

    cm = CostMeter()
    mA = _ToolThenQuota("mA")
    mB = _ToolThenDone("mB")
    agentic = AgenticWorker({"mA": mA, "mB": mB}, cm, max_iters=8)
    plan = [Task(id="t1", description="fix", type="code", mode="agentic")]
    projector = _Projector({"mA": 999, "mB": 64})
    rt = Runtime(
        _Sup(plan),
        _RankRouter(["mA", "mB"]),
        projector,
        Worker(providers={"mA": mA, "mB": mB}, cost_meter=cm),
        _Synth(),
        Registry([_model("mA", 200_000), _model("mB", 8_000)]),
        cm,
        agentic_worker=agentic,
        tools_factory=lambda ws: {"run_python": _RecordingTool()},
        runs_dir=tmp_path / "runs",
    )

    res = await rt.aexecute("goal")

    assert res.status == "success"
    assert res.partial_artifacts["t1"] == "done via B"       # kandidat B menyelesaikan
    assert mB.seen_max_tokens[0] == 64                        # RE-PROYEKSI ke budget B (bukan A)
    assert slept == []                                        # reroute TANPA backoff/sleep
    assert mA.calls == 2                                      # A: tool_use lalu quota_exhausted
    # Kerja PARSIAL kandidat A ter-meter (turn tool_use sebelum quota) — bukti
    # "restart from scratch": TurnRecord A TAK dipersist, tapi cost sudah accrue.
    assert "mA" in cm.totals()
    # Jejak reroute tertulis satu status per kandidat yang ditinggalkan.
    statuses = [e for e in projector.last_bb.entries() if e.kind == "status"]
    assert any("quota_exhausted" in e.payload for e in statuses)


@pytest.mark.asyncio
async def test_agentic_all_candidates_quota_exhausted_fails(
    tmp_path: Path, monkeypatch
) -> None:
    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("baton.agent.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("baton.runtime.asyncio.sleep", _no_sleep)

    cm = CostMeter()
    mA = _ToolThenQuota("mA")
    mB = _ToolThenQuota("mB")
    agentic = AgenticWorker({"mA": mA, "mB": mB}, cm, max_iters=8)
    plan = [Task(id="t1", description="fix", type="code", mode="agentic")]
    rt = Runtime(
        _Sup(plan),
        _RankRouter(["mA", "mB"]),
        _Projector({"mA": 999, "mB": 64}),
        Worker(providers={"mA": mA, "mB": mB}, cost_meter=cm),
        _Synth(),
        Registry([_model("mA", 200_000), _model("mB", 8_000)]),
        cm,
        agentic_worker=agentic,
        tools_factory=lambda ws: {"run_python": _RecordingTool()},
        runs_dir=tmp_path / "runs",
    )

    res = await rt.aexecute("goal")

    assert res.status == "failed"       # semua kandidat quota_exhausted -> gagal
    assert res.failed_task == "t1"
    assert mB.calls == 2                # B benar-benar dicoba (bukan berhenti di A)

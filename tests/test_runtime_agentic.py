from __future__ import annotations

from pathlib import Path

from orchestrator.agent import AgenticResult, TurnRecord
from orchestrator.cost import CostMeter
from orchestrator.providers.fake import FakeProvider
from orchestrator.registry import Registry
from orchestrator.runtime import Runtime
from orchestrator.types import CanonicalRequest, ModelInfo, Task, Usage, text
from orchestrator.worker import Worker


def _model(mid: str) -> ModelInfo:
    return ModelInfo(
        id=mid, provider="fake", strengths={"coding"}, context_window=100_000,
        max_output_tokens=4096, supports_tools=True,
        cost_per_1k_in=0.001, cost_per_1k_out=0.002,
    )


class _Sup:
    def __init__(self, plan) -> None:
        self._plan = plan

    async def plan(self, goal: str):
        return list(self._plan)


class _Router:
    def route(self, task) -> str:
        return "m1"


class _Projector:
    def project(self, task, model_id, bb) -> CanonicalRequest:
        return CanonicalRequest(messages=[text("user", task.description)], max_tokens=64, task_id=task.id)


class _Synth:
    async def synthesize(self, goal, bb) -> str:
        return "synth"


class _FakeAgentic:
    """Ganti AgenticWorker: catat pemanggilan, kembalikan hasil skrip."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, list[str]]] = []

    async def run(self, req, model_id, tools):
        self.seen.append((req.task_id, sorted(tools.keys())))
        return AgenticResult(
            final_text=f"done:{req.task_id}",
            usage_total={model_id: Usage(prompt_tokens=10, completion_tokens=4)},
            turns=[
                TurnRecord(0, "tool_use", "run_python(...)", Usage(10, 4), model_id),
                TurnRecord(0, "tool_result", "exit=0", None, model_id),
                TurnRecord(1, "final", f"done:{req.task_id}", Usage(3, 2), model_id),
            ],
        )


def _build(plan, tmp_path: Path, sandbox_factory=None) -> tuple[Runtime, _FakeAgentic]:
    cm = CostMeter()
    agentic = _FakeAgentic()
    rt = Runtime(
        _Sup(plan),
        _Router(),
        _Projector(),
        Worker(providers={"m1": FakeProvider(name="m1")}, cost_meter=cm),
        _Synth(),
        Registry([_model("m1")]),
        cm,
        agentic_worker=agentic,
        sandbox_factory=sandbox_factory or (lambda ws: ws),  # _FakeAgentic tak pakai sandbox
        runs_dir=tmp_path / "runs",
    )
    return rt, agentic


def test_agentic_task_routed_to_agentic_worker(tmp_path: Path) -> None:
    plan = [Task(id="t1", description="fix", type="code", mode="agentic")]
    rt, agentic = _build(plan, tmp_path)
    res = rt.execute("goal")  # execute() = wrapper blocking (asyncio.run)

    assert res.status == "success"
    assert agentic.seen == [("t1", ["run_python"])]     # dialihkan + tools per-task
    assert res.partial_artifacts["t1"] == "done:t1"     # final_text jadi artifact


def test_two_agentic_tasks_get_isolated_workspaces(tmp_path: Path) -> None:
    plan = [
        Task(id="t1", description="a", type="code", mode="agentic"),
        Task(id="t2", description="b", type="code", mode="agentic"),
    ]
    captured: list[Path] = []
    rt, _ = _build(plan, tmp_path, sandbox_factory=lambda ws: captured.append(Path(ws)) or ws)
    rt.execute("goal")

    assert len(captured) == 2
    assert captured[0] != captured[1]                    # workspace per-task, tak bertabrakan (satu run_id, id task beda)
    assert all("runs" in str(p) for p in captured)

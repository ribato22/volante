from __future__ import annotations

import asyncio

import pytest

from orchestrator.cost import CostMeter
from orchestrator.providers.fake import FakeProvider
from orchestrator.registry import Registry
from orchestrator.runtime import Runtime
from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    Usage,
    text,
)
from orchestrator.worker import Worker


def _resp(txt: str, model: str, *, prompt: int = 1000, completion: int = 1000) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=txt)],
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion),
        model=model,
        stop_reason="end_turn",
        latency_ms=1,
    )


def _model(model_id: str) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths={"coding"},
        context_window=100_000,
        max_output_tokens=4_096,
        supports_tools=False,
        cost_per_1k_in=0.001,
        cost_per_1k_out=0.002,
    )


def _registry(*ids: str) -> Registry:
    return Registry([_model(i) for i in ids])


def _plan_diamond() -> list[Task]:
    # T1, T2 tanpa dependensi (satu wave, konkuren); T3 bergantung pada keduanya.
    return [
        Task(id="T1", description="do one", type="research", mode="one_shot"),
        Task(id="T2", description="do two", type="analyze", mode="one_shot"),
        Task(
            id="T3",
            description="do three",
            type="write",
            mode="one_shot",
            depends_on=["T1", "T2"],
        ),
    ]


class _StubSupervisor:
    def __init__(self, plan: list[Task]) -> None:
        self._plan = plan
        self.calls = 0

    async def plan(self, goal: str) -> list[Task]:
        self.calls += 1
        return list(self._plan)


class _StubRouter:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def route(self, task: Task) -> str:
        return self._mapping[task.id]


class _StubProjector:
    """Menangkap Blackboard yang dibangun Runtime supaya test bisa memeriksa entry."""

    def __init__(self) -> None:
        self.last_bb: object | None = None

    def project(self, task: Task, model_id: str, bb: object) -> CanonicalRequest:
        self.last_bb = bb
        return CanonicalRequest(messages=[text("user", task.description)], max_tokens=64)


class _StubSynthesizer:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, goal: str, bb: object) -> str:
        self.calls += 1
        keys = ",".join(sorted(bb.current_artifacts().keys()))
        return f"synth[{keys}]"


class _ConcurrencyProbe:
    """Provider tunggal yang dipetakan ke semua model_id; mencatat puncak konkurensi."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.current = 0
        self.max_concurrent = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.current += 1
        self.max_concurrent = max(self.max_concurrent, self.current)
        try:
            await asyncio.sleep(0.02)  # jendela nyata agar sibling bisa tumpang-tindih
        finally:
            self.current -= 1
        return _resp("art", self.name)


def test_execute_happy_path_dag_populates_cost_fields() -> None:
    cm = CostMeter()
    plan = _plan_diamond()
    supervisor = _StubSupervisor(plan)
    router = _StubRouter({"T1": "m1", "T2": "m2", "T3": "m3"})
    projector = _StubProjector()
    worker = Worker(
        providers={
            "m1": FakeProvider(responses=[_resp("art-1", "m1")], name="m1"),
            "m2": FakeProvider(responses=[_resp("art-2", "m2")], name="m2"),
            "m3": FakeProvider(responses=[_resp("art-3", "m3")], name="m3"),
        },
        cost_meter=cm,
    )
    synthesizer = _StubSynthesizer()
    runtime = Runtime(
        supervisor,
        router,
        projector,
        worker,
        synthesizer,
        _registry("m1", "m2", "m3"),
        cm,
    )

    result = runtime.execute("build the thing")

    assert result.status == "success"
    assert result.failed_task is None
    assert result.final == "synth[T1,T2,T3]"
    assert result.partial_artifacts == {"T1": "art-1", "T2": "art-2", "T3": "art-3"}
    assert supervisor.calls == 1  # non-re-entrant: plan() dipanggil SEKALI
    # PATCH v2.1 close-out fields di jalur SUKSES.
    assert set(result.usage_total) == {"m1", "m2", "m3"}
    assert result.usage_total == cm.totals()
    assert result.usage_total["m1"].prompt_tokens == 1000
    assert result.usage_total["m1"].completion_tokens == 1000
    # 3 model x (1000/1000*0.001 + 1000/1000*0.002) = 3 x 0.003 = 0.009
    assert result.cost_usd == pytest.approx(0.009)
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


def test_execute_fan_out_caps_concurrency() -> None:
    cm = CostMeter()
    # 3 task independen dalam SATU wave; fan_out=2 harus membatasi in-flight ke 2.
    plan = [
        Task(id="A", description="a", type="code", mode="one_shot"),
        Task(id="B", description="b", type="code", mode="one_shot"),
        Task(id="C", description="c", type="code", mode="one_shot"),
    ]
    supervisor = _StubSupervisor(plan)
    router = _StubRouter({"A": "m1", "B": "m2", "C": "m3"})
    projector = _StubProjector()
    probe = _ConcurrencyProbe("probe")
    worker = Worker(providers={"m1": probe, "m2": probe, "m3": probe}, cost_meter=cm)
    synthesizer = _StubSynthesizer()
    runtime = Runtime(
        supervisor,
        router,
        projector,
        worker,
        synthesizer,
        _registry("m1", "m2", "m3"),
        cm,
        fan_out=2,
    )

    result = runtime.execute("build")

    assert result.status == "success"
    assert probe.max_concurrent == 2  # Semaphore(fan_out) menahan yang ke-3

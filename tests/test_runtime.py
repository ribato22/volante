from __future__ import annotations

import asyncio

import pytest

from baton.cost import CostMeter
from baton.providers.base import ProviderError
from baton.providers.fake import FakeProvider
from baton.registry import Registry
from baton.runtime import Runtime
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    Usage,
    text,
)
from baton.worker import Worker


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

    async def plan(self, goal: str, on_text=None) -> list[Task]:
        self.calls += 1
        if on_text is not None:
            on_text("[plan]")  # buktikan Runtime meneruskan callback ke plan
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

    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        self.calls += 1
        if on_text is not None:
            on_text("[synth]")  # buktikan Runtime meneruskan callback ke sintesis
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


def test_execute_routes_on_text_to_plan_and_synth_not_workers() -> None:
    # Streaming: aexecute(on_text) meneruskan callback ke supervisor.plan +
    # synthesizer.synthesize (fase sekuensial). Worker paralel TIDAK di-stream
    # (Runtime tak meneruskan on_text ke worker) -> hanya marker plan/synth muncul.
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
        supervisor, router, projector, worker, synthesizer,
        _registry("m1", "m2", "m3"), cm,
    )
    chunks: list[str] = []

    result = runtime.execute("build", on_text=chunks.append)

    assert result.status == "success"
    # Hanya marker fase sekuensial; tak ada teks worker (art-1/2/3) yang ter-stream.
    assert chunks == ["[plan]", "[synth]"]
    assert not any("art-" in c for c in chunks)


def test_execute_streams_workers_labeled_by_task_id() -> None:
    # on_worker_text(task_id, delta): tiap worker one-shot stream teksnya ter-label
    # task_id -> output PARALEL terurai per-task (bukan bercampur). Urutan bisa
    # bervariasi (paralel) -> banding sebagai set.
    cm = CostMeter()
    plan = _plan_diamond()
    supervisor = _StubSupervisor(plan)
    router = _StubRouter({"T1": "m1", "T2": "m2", "T3": "m3"})
    worker = Worker(
        providers={
            "m1": FakeProvider(responses=[_resp("art-1", "m1")], name="m1"),
            "m2": FakeProvider(responses=[_resp("art-2", "m2")], name="m2"),
            "m3": FakeProvider(responses=[_resp("art-3", "m3")], name="m3"),
        },
        cost_meter=cm,
    )
    runtime = Runtime(
        supervisor, router, _StubProjector(), worker, _StubSynthesizer(),
        _registry("m1", "m2", "m3"), cm,
    )
    events: list[tuple[str, str]] = []

    result = runtime.execute("build", on_worker_text=lambda tid, d: events.append((tid, d)))

    assert result.status == "success"
    assert set(events) == {("T1", "art-1"), ("T2", "art-2"), ("T3", "art-3")}


def test_execute_without_on_text_streams_nothing() -> None:
    # Nol regresi: tanpa on_text, plan/synth pakai complete (stub tak memancarkan
    # marker). Perilaku default tak berubah.
    cm = CostMeter()
    supervisor = _StubSupervisor(_plan_diamond())
    router = _StubRouter({"T1": "m1", "T2": "m2", "T3": "m3"})
    worker = Worker(
        providers={
            "m1": FakeProvider(responses=[_resp("a", "m1")], name="m1"),
            "m2": FakeProvider(responses=[_resp("a", "m2")], name="m2"),
            "m3": FakeProvider(responses=[_resp("a", "m3")], name="m3"),
        },
        cost_meter=cm,
    )
    runtime = Runtime(
        supervisor, router, _StubProjector(), worker, _StubSynthesizer(),
        _registry("m1", "m2", "m3"), cm,
    )

    result = runtime.execute("build")  # tanpa on_text
    assert result.status == "success"


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


class _RaisingProvider:
    def __init__(self, name: str, err: Exception) -> None:
        self.name = name
        self._err = err
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        raise self._err


class _HangingProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        await asyncio.Event().wait()  # menggantung sampai dibatalkan oleh wait_for
        raise AssertionError("unreachable")  # pragma: no cover


def _single_task_runtime(
    cm: CostMeter,
    provider: object,
    *,
    model_id: str,
    projector: _StubProjector,
    max_retries: int = 2,
    call_timeout: float = 120.0,
) -> tuple[Runtime, _StubSynthesizer]:
    plan = [Task(id="T1", description="only", type="code", mode="one_shot")]
    supervisor = _StubSupervisor(plan)
    router = _StubRouter({"T1": model_id})
    worker = Worker(providers={model_id: provider}, cost_meter=cm)
    synthesizer = _StubSynthesizer()
    runtime = Runtime(
        supervisor,
        router,
        projector,
        worker,
        synthesizer,
        Registry([]),  # totals kosong pada kegagalan tunggal -> cost_usd == 0.0
        cm,
        max_retries=max_retries,
        call_timeout=call_timeout,
    )
    return runtime, synthesizer


def test_non_retryable_error_fails_without_retry() -> None:
    cm = CostMeter()
    projector = _StubProjector()
    failing = _RaisingProvider(
        "m_fail", ProviderError("bad request", retryable=False, status=400)
    )
    runtime, synthesizer = _single_task_runtime(
        cm, failing, model_id="m_fail", projector=projector
    )

    result = runtime.execute("do it")

    assert result.status == "failed"
    assert result.failed_task == "T1"
    assert result.final is None
    # Non-retryable => TEPAT satu panggilan provider (bukan 3x retry).
    assert failing.calls == 1
    assert synthesizer.calls == 0
    # Close-out tetap terisi di jalur GAGAL.
    assert result.usage_total == {}
    assert result.cost_usd == 0.0
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


def test_retryable_error_retries_then_fails_and_records_str_err(monkeypatch) -> None:
    slept: list[float] = []

    async def _fast_sleep(delay: float) -> None:
        slept.append(delay)  # rekam backoff, tanpa tidur nyata (test cepat)

    monkeypatch.setattr("baton.runtime.asyncio.sleep", _fast_sleep)

    cm = CostMeter()
    projector = _StubProjector()
    err = ProviderError("rate limited", retryable=True, status=429)
    failing = _RaisingProvider("m_flaky", err)
    runtime, _ = _single_task_runtime(
        cm, failing, model_id="m_flaky", projector=projector, max_retries=2
    )

    result = runtime.execute("do it")

    assert result.status == "failed"
    assert result.failed_task == "T1"
    # attempt 0..max_retries inklusif => max_retries + 1 = 3 percobaan.
    assert failing.calls == 3
    # Backoff antar percobaan (bukan setelah percobaan terakhir), eksponensial.
    assert len(slept) == 2
    assert slept[0] < slept[1]
    # str(err) tersimpan di entry status gagal.
    entries = projector.last_bb.entries()
    status_entries = [e for e in entries if e.kind == "status"]
    assert len(status_entries) == 1  # tak ada status "success"
    assert "rate limited" in status_entries[0].payload


def test_timeout_is_retryable_then_fails(monkeypatch) -> None:
    async def _fast_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("baton.runtime.asyncio.sleep", _fast_sleep)

    cm = CostMeter()
    projector = _StubProjector()
    hanging = _HangingProvider("m_slow")
    runtime, _ = _single_task_runtime(
        cm, hanging, model_id="m_slow", projector=projector, call_timeout=0.01
    )

    result = runtime.execute("do it")

    assert result.status == "failed"
    assert result.failed_task == "T1"
    # asyncio.wait_for -> TimeoutError diperlakukan retryable => 3 percobaan time-out.
    assert hanging.calls == 3


def test_unexpected_exception_is_recorded_not_raised() -> None:
    # Regresi A1: eksepsi TAK-terduga (bukan ProviderError/TimeoutError) dari sebuah
    # task TIDAK boleh lolos ke asyncio.gather (crash + sibling orphan). Harus jadi
    # status gagal tercatat + RunResult(status="failed"), bukan exception mentah.
    cm = CostMeter()
    projector = _StubProjector()
    boom = _RaisingProvider("m_boom", ValueError("kaboom"))
    runtime, synthesizer = _single_task_runtime(
        cm, boom, model_id="m_boom", projector=projector
    )

    result = runtime.execute("do it")  # tidak melempar

    assert result.status == "failed"
    assert result.failed_task == "T1"
    assert synthesizer.calls == 0
    # ValueError non-retryable -> satu panggilan (bukan retry).
    assert boom.calls == 1
    status_entries = [e for e in projector.last_bb.entries() if e.kind == "status"]
    assert len(status_entries) == 1
    assert "ValueError" in status_entries[0].payload
    assert "kaboom" in status_entries[0].payload


def test_unexpected_exception_in_wave_stops_and_preserves_sibling() -> None:
    # A1 dalam konteks wave: T2 melempar KeyError tak-terduga, T1 (sibling) sukses.
    # Run harus fail-fast dengan sibling tersimpan, tanpa exception lolos.
    cm = CostMeter()
    plan = _plan_diamond()
    supervisor = _StubSupervisor(plan)
    router = _StubRouter({"T1": "m1", "T2": "m_boom", "T3": "m3"})
    projector = _StubProjector()
    boom = _RaisingProvider("m_boom", KeyError("missing"))
    worker = Worker(
        providers={
            "m1": FakeProvider(responses=[_resp("art-1", "m1")], name="m1"),
            "m_boom": boom,
            "m3": FakeProvider(responses=[_resp("art-3", "m3")], name="m3"),
        },
        cost_meter=cm,
    )
    synthesizer = _StubSynthesizer()
    runtime = Runtime(
        supervisor, router, projector, worker, synthesizer,
        _registry("m1", "m2", "m3"), cm,
    )

    result = runtime.execute("build")

    assert result.status == "failed"
    assert result.failed_task == "T2"
    assert result.partial_artifacts == {"T1": "art-1"}
    assert synthesizer.calls == 0


def test_fail_fast_keeps_partial_artifacts_and_stops() -> None:
    cm = CostMeter()
    plan = _plan_diamond()
    supervisor = _StubSupervisor(plan)
    # T2 dirutekan ke provider gagal (non-retryable); T1 sukses di wave yang sama.
    router = _StubRouter({"T1": "m1", "T2": "m_fail", "T3": "m3"})
    projector = _StubProjector()
    failing = _RaisingProvider("m_fail", ProviderError("nope", retryable=False, status=400))
    worker = Worker(
        providers={
            "m1": FakeProvider(responses=[_resp("art-1", "m1")], name="m1"),
            "m_fail": failing,
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
        max_retries=2,
    )

    result = runtime.execute("build the thing")

    assert result.status == "failed"
    assert result.failed_task == "T2"
    assert result.final is None
    # T1 (sibling sukses) tersimpan; T2 gagal tanpa artifact; T3 (dependen) tak jalan.
    assert result.partial_artifacts == {"T1": "art-1"}
    assert "T3" not in result.partial_artifacts
    assert failing.calls == 1  # non-retryable, tanpa retry
    assert synthesizer.calls == 0
    # Biaya T1 tetap ter-meter; close-out jalur gagal merefleksikannya.
    assert set(result.usage_total) == {"m1"}
    assert result.cost_usd == pytest.approx(0.003)

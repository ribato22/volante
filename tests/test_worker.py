from __future__ import annotations

import pytest

from orchestrator.cost import CostMeter
from orchestrator.providers.fake import FakeProvider
from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    Usage,
    text,
)
from orchestrator.worker import Worker


def _resp(
    s: str,
    *,
    prompt: int = 1,
    completion: int = 1,
    estimated: bool = False,
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=s)],
        usage=Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            estimated=estimated,
        ),
        model="m",
        stop_reason="end_turn",
        latency_ms=1,
    )


def _req() -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", "hi")], max_tokens=64)


class _BoomProvider:
    """Provider yang meledak di complete() — untuk membuktikan add() TIDAK dipanggil."""

    name = "boom"

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        raise RuntimeError("provider exploded")


# --- Kontrak dasar dipertahankan (happy path) ---


async def test_run_one_shot_dispatches_to_named_provider() -> None:
    meter = CostMeter()
    fake_a = FakeProvider(responses=[_resp("from-a")], name="a")
    fake_b = FakeProvider(responses=[_resp("from-b")], name="b")
    worker = Worker(providers={"model-a": fake_a, "model-b": fake_b}, cost_meter=meter)

    out = await worker.run_one_shot(_req(), "model-b")

    assert isinstance(out, CanonicalResponse)
    assert out.content[0].text == "from-b"


async def test_run_one_shot_returns_response_from_selected_provider_only() -> None:
    meter = CostMeter()
    fake_a = FakeProvider(responses=[_resp("from-a")], name="a")
    fake_b = FakeProvider(responses=[_resp("from-b")], name="b")
    worker = Worker(providers={"model-a": fake_a, "model-b": fake_b}, cost_meter=meter)

    out = await worker.run_one_shot(_req(), "model-a")

    assert out.content[0].text == "from-a"


# --- PATCH v2.1: injeksi CostMeter ---


async def test_run_one_shot_records_usage_keyed_by_model_id() -> None:
    # KEY = model_id ("model-b"), BUKAN provider.name ("b").
    meter = CostMeter()
    fake_b = FakeProvider(responses=[_resp("from-b", prompt=7, completion=4)], name="b")
    worker = Worker(providers={"model-b": fake_b}, cost_meter=meter)

    await worker.run_one_shot(_req(), "model-b")

    totals = meter.totals()
    assert set(totals) == {"model-b"}
    assert totals["model-b"].prompt_tokens == 7
    assert totals["model-b"].completion_tokens == 4


async def test_run_one_shot_accumulates_usage_per_model_id() -> None:
    meter = CostMeter()
    fake_b = FakeProvider(
        responses=[
            _resp("call-1", prompt=2, completion=3),
            _resp("call-2", prompt=5, completion=1),
        ],
        name="b",
    )
    worker = Worker(providers={"model-b": fake_b}, cost_meter=meter)

    await worker.run_one_shot(_req(), "model-b")
    await worker.run_one_shot(_req(), "model-b")

    totals = meter.totals()
    assert totals["model-b"].prompt_tokens == 7  # 2 + 5
    assert totals["model-b"].completion_tokens == 4  # 3 + 1


async def test_run_one_shot_propagates_estimated_flag() -> None:
    meter = CostMeter()
    fake = FakeProvider(responses=[_resp("est", estimated=True)], name="b")
    worker = Worker(providers={"model-b": fake}, cost_meter=meter)

    assert meter.has_estimated() is False
    await worker.run_one_shot(_req(), "model-b")
    assert meter.has_estimated() is True


async def test_run_one_shot_exact_usage_stays_not_estimated() -> None:
    meter = CostMeter()
    fake = FakeProvider(responses=[_resp("exact", estimated=False)], name="b")
    worker = Worker(providers={"model-b": fake}, cost_meter=meter)

    await worker.run_one_shot(_req(), "model-b")
    assert meter.has_estimated() is False


async def test_run_one_shot_unknown_model_raises_and_records_nothing() -> None:
    meter = CostMeter()
    worker = Worker(providers={"model-a": FakeProvider(name="a")}, cost_meter=meter)

    with pytest.raises(ValueError):
        await worker.run_one_shot(_req(), "missing-model")

    # Gagal SEBELUM complete() -> tak ada usage hantu tercatat.
    assert meter.totals() == {}
    assert meter.has_estimated() is False


async def test_run_one_shot_provider_error_records_nothing() -> None:
    # add() harus dipanggil SETELAH complete() sukses: bila complete() meledak,
    # error diteruskan dan CostMeter tetap kosong.
    meter = CostMeter()
    worker = Worker(providers={"model-x": _BoomProvider()}, cost_meter=meter)

    with pytest.raises(RuntimeError):
        await worker.run_one_shot(_req(), "model-x")

    assert meter.totals() == {}

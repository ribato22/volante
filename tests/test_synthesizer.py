from __future__ import annotations

from baton.blackboard import Blackboard
from baton.cost import CostMeter
from baton.providers.fake import FakeProvider
from baton.synthesizer import Synthesizer
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    Entry,
    Task,
    TextBlock,
    Usage,
)


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


def _bb_with_artifacts() -> Blackboard:
    plan = [
        Task(id="t1", description="research topic", type="research", mode="one_shot"),
        Task(id="t2", description="draft section", type="write", mode="one_shot"),
    ]
    bb = Blackboard(goal="Write a report", plan=plan)
    bb.append(
        Entry(
            run_id="r1",
            task_id="t1",
            attempt=0,
            kind="artifact",
            payload="FACT-ONE",
            model_id="m",
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            timestamp=0.0,
        )
    )
    bb.append(
        Entry(
            run_id="r1",
            task_id="t2",
            attempt=0,
            kind="artifact",
            payload="DRAFT-TWO",
            model_id="m",
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            timestamp=1.0,
        )
    )
    return bb


class _CapturingProvider:
    """Test double lokal yang merekam request terakhir (memenuhi LLMProvider Protocol)."""

    name = "capture"

    def __init__(self, resp: CanonicalResponse) -> None:
        self._resp = resp
        self.last_req: CanonicalRequest | None = None

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.last_req = req
        return self._resp


# --- Kontrak dasar dipertahankan ---


async def test_synthesize_returns_provider_text() -> None:
    meter = CostMeter()
    provider = FakeProvider(responses=[_resp("FINAL-REPORT")])
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = _bb_with_artifacts()

    out = await synth.synthesize("Write a report", bb)

    assert out == "FINAL-REPORT"


async def test_synthesize_prompt_includes_goal_and_artifacts() -> None:
    meter = CostMeter()
    provider = _CapturingProvider(_resp("ok"))
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = _bb_with_artifacts()

    await synth.synthesize("Write a report", bb)

    assert provider.last_req is not None
    prompt = provider.last_req.messages[-1].content[0].text
    assert "Write a report" in prompt
    assert "FACT-ONE" in prompt
    assert "DRAFT-TWO" in prompt


async def test_synthesize_handles_empty_artifacts() -> None:
    meter = CostMeter()
    provider = FakeProvider(responses=[_resp("EMPTY-FINAL")])
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = Blackboard(goal="Nothing done", plan=[])

    out = await synth.synthesize("Nothing done", bb)

    assert out == "EMPTY-FINAL"


# --- PATCH v2.1: injeksi CostMeter ---


async def test_synthesize_records_usage_keyed_by_model_id() -> None:
    meter = CostMeter()
    provider = FakeProvider(responses=[_resp("FINAL", prompt=9, completion=6)])
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = _bb_with_artifacts()

    await synth.synthesize("Write a report", bb)

    totals = meter.totals()
    assert set(totals) == {"synth-model"}
    assert totals["synth-model"].prompt_tokens == 9
    assert totals["synth-model"].completion_tokens == 6


async def test_synthesize_streams_when_on_text_given() -> None:
    meter = CostMeter()
    provider = FakeProvider(responses=[_resp("FINAL-REPORT", completion=6)])
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = _bb_with_artifacts()
    chunks: list[str] = []

    out = await synth.synthesize("Write a report", bb, on_text=chunks.append)

    assert out == "FINAL-REPORT"
    assert "".join(chunks) == "FINAL-REPORT"  # teks sintesis ter-stream
    assert meter.totals()["synth-model"].completion_tokens == 6  # cost tetap tercatat


async def test_synthesize_propagates_estimated_flag() -> None:
    meter = CostMeter()
    provider = FakeProvider(responses=[_resp("FINAL", estimated=True)])
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    bb = _bb_with_artifacts()

    assert meter.has_estimated() is False
    await synth.synthesize("Write a report", bb)
    assert meter.has_estimated() is True

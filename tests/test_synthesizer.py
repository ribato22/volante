from __future__ import annotations

import pytest

from volante.blackboard import Blackboard
from volante.cost import CostMeter
from volante.providers.base import IncompleteOutputError
from volante.providers.fake import FakeProvider
from volante.synthesizer import Synthesizer
from volante.types import (
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
    cost_usd: float | None = None,
    stop_reason: str = "end_turn",
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=s)],
        usage=Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            estimated=estimated,
        ),
        model="m",
        stop_reason=stop_reason,
        latency_ms=1,
        cost_usd=cost_usd,
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


@pytest.mark.parametrize("stop_reason", ["max_tokens", "content_filter"])
async def test_synthesize_rejects_incomplete_output_after_metering(
    stop_reason: str,
) -> None:
    meter = CostMeter()
    provider = FakeProvider(
        responses=[
            _resp(
                "PARTIAL",
                prompt=5,
                completion=13,
                stop_reason=stop_reason,
            )
        ]
    )
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)

    with pytest.raises(IncompleteOutputError) as exc_info:
        await synth.synthesize("Write a report", _bb_with_artifacts())

    assert exc_info.value.phase == "synthesis"
    assert exc_info.value.stop_reason == stop_reason
    assert meter.totals()["synth-model"] == Usage(5, 13)


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


async def test_the_instruction_does_not_sit_beside_the_artifacts_it_governs() -> None:
    # Artifacts are worker output, and a worker's text can come from a fetched page,
    # a read file or a tool result. Concatenating "combine these results" and that
    # content into ONE user turn gives an injected instruction the same standing as
    # the real one. Synthesis holds no tools, so the worst case is a corrupted final
    # answer rather than an action — worth separating anyway, since it costs nothing.
    provider = _CapturingProvider(_resp("ok"))
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=CostMeter())

    await synth.synthesize("Write a report", _bb_with_artifacts())

    assert provider.last_req is not None
    roles = [m.role for m in provider.last_req.messages]
    assert roles == ["system", "user"]
    system = provider.last_req.messages[0].content[0].text
    user = provider.last_req.messages[1].content[0].text
    assert "Combine them" in system
    assert "FACT-ONE" not in system, "artifact content leaked into the instruction turn"
    assert "FACT-ONE" in user


async def test_the_instruction_says_the_artifacts_are_data() -> None:
    # Naming the boundary is the only mitigation available at this layer: there is no
    # unforgeable delimiter, and a determined injection can still persuade a model.
    # Saying nothing at all leaves the model no reason to treat the two differently.
    provider = _CapturingProvider(_resp("ok"))
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=CostMeter())

    await synth.synthesize("Write a report", _bb_with_artifacts())

    assert provider.last_req is not None
    system = provider.last_req.messages[0].content[0].text.lower()
    assert "data" in system
    assert "instruction" in system


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


async def test_synthesize_forwards_provider_authoritative_cost() -> None:
    meter = CostMeter()
    provider = FakeProvider(
        responses=[_resp("FINAL", cost_usd=0.456)]
    )
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)

    await synth.synthesize("Write a report", _bb_with_artifacts())

    assert meter._direct["synth-model"]["usd"] == 0.456


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


async def test_synthesis_prompt_is_bounded_by_selected_model_context() -> None:
    meter = CostMeter()
    provider = _CapturingProvider(_resp("bounded"))
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    synth.set_model_limits(context_window=200, max_output_tokens=40)
    bb = _bb_with_artifacts()
    bb.append(
        Entry(
            run_id="r1",
            task_id="t1",
            attempt=1,
            kind="artifact",
            payload="HEAD-" + ("X" * 20_000) + "-TAIL",
            model_id="m",
            usage=None,
            timestamp=2.0,
        )
    )

    await synth.synthesize("G" * 10_000, bb)

    assert provider.last_req is not None
    # The instruction now travels as a system message, but it is still context: the
    # budget covers what is SENT, not what sits in one particular turn.
    sent = sum(len(m.content[0].text) for m in provider.last_req.messages)
    # (200 - 40) * 0.85 * 4 = 544 conservative input characters.
    assert sent <= 544
    assert provider.last_req.max_tokens == 40
    assert provider.last_req.context_window == 200


async def test_synthesizer_call_gate_runs_immediately_before_provider() -> None:
    meter = CostMeter()
    provider = _CapturingProvider(_resp("ok"))
    synth = Synthesizer(provider=provider, model_id="synth-model", cost_meter=meter)
    calls: list[str] = []
    synth.set_call_gate(calls.append)

    await synth.synthesize("Write a report", _bb_with_artifacts())

    assert calls == ["synth-model"]

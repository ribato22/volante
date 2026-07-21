from __future__ import annotations

import pytest
from eval.harness import run_suite
from eval.tasks import EVAL_SUITE

from orchestrator.providers.fake import FakeProvider
from orchestrator.registry import Registry
from orchestrator.types import (
    CanonicalResponse,
    ModelInfo,
    RunResult,
    TextBlock,
    Usage,
)

FENCE = "`" * 3

# Output "kuat": code (mungkin skor 0 di runner goal ini) + tanda test + README,
# sehingga composite >= 0.30 apa pun reference_test-nya. Cukup untuk MENGALAHKAN
# output kosong (composite 0.0) secara deterministik, tanpa bergantung skor kode.
STRONG = (
    f"{FENCE}python\ndef solution():\n    return 1\n{FENCE}\n"
    "def test_it():\n    assert True\n"
    "# README\nrun the tests with pytest\n"
)
WEAK = ""

COMPARE_KEYS = {
    "orch_cost",
    "base_cost",
    "orch_ms",
    "base_ms",
    "orch_composite",
    "base_composite",
    "orch_estimated",
    "base_estimated",
    "winner",
}
AGG_KEYS = {
    "orch_wins",
    "base_wins",
    "ties",
    "orch_cost_total",
    "base_cost_total",
    "any_estimated",
    "verdict",
}


def _model(model_id: str = "strong-model") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="anthropic",
        strengths={"coding"},
        context_window=200_000,
        max_output_tokens=4_096,
        supports_tools=True,
        cost_per_1k_in=0.003,
        cost_per_1k_out=0.015,
    )


def _registry() -> Registry:
    return Registry([_model()])


def _resp(text_val: str, *, pt: int = 10, ct: int = 20, estimated: bool = False):
    return CanonicalResponse(
        content=[TextBlock(text=text_val)],
        usage=Usage(pt, ct, estimated=estimated),
        model="strong-model",
        stop_reason="end_turn",
        latency_ms=5,
    )


# Biaya baseline per-respons di atas: 10/1000*0.003 + 20/1000*0.015 = 0.00033.
BASE_COST_EACH = 10 / 1000 * 0.003 + 20 / 1000 * 0.015
ORCH_COST_EACH = 0.01


class _StubRuntime:
    """Runtime stand-in: aexecute() mengembalikan RunResult dengan final yang
    ditentukan per-goal (final_for(goal)). Tanpa jaringan."""

    def __init__(self, final_for, *, estimated: bool = False) -> None:
        self._final_for = final_for
        self._estimated = estimated

    async def aexecute(self, goal: str) -> RunResult:
        return RunResult(
            status="success",
            final=self._final_for(goal),
            partial_artifacts={},
            failed_task=None,
            usage_total={"orch": Usage(50, 80, estimated=self._estimated)},
            cost_usd=ORCH_COST_EACH,
            duration_ms=100,
        )


def _factory(final_for, *, estimated: bool = False):
    calls: list[str] = []

    def make_runtime() -> _StubRuntime:
        calls.append("x")
        return _StubRuntime(final_for, estimated=estimated)

    return make_runtime, calls


async def test_run_suite_shape_and_orchestration_verdict():
    make_runtime, _ = _factory(lambda goal: STRONG)
    provider = FakeProvider(responses=[_resp(WEAK), _resp(WEAK)])
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    assert set(result) == {"per_goal", "aggregate"}
    per_goal = result["per_goal"]
    assert len(per_goal) == 2
    assert [g["id"] for g in per_goal] == ["slugify", "roman"]
    for g in per_goal:
        assert COMPARE_KEYS <= set(g)
        assert g["winner"] == "orchestration"
    agg = result["aggregate"]
    assert set(agg) == AGG_KEYS
    assert agg["orch_wins"] == 2
    assert agg["base_wins"] == 0
    assert agg["ties"] == 0
    assert agg["verdict"] == "orchestration"
    assert agg["orch_cost_total"] == pytest.approx(2 * ORCH_COST_EACH)
    assert agg["base_cost_total"] == pytest.approx(2 * BASE_COST_EACH)
    assert agg["any_estimated"] is False


async def test_run_suite_baseline_majority_verdict():
    make_runtime, _ = _factory(lambda goal: WEAK)
    provider = FakeProvider(responses=[_resp(STRONG), _resp(STRONG)])
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    agg = result["aggregate"]
    assert agg["base_wins"] == 2
    assert agg["orch_wins"] == 0
    assert agg["verdict"] == "baseline"


async def test_run_suite_tie_verdict_on_split():
    # slugify: orch kuat -> orch menang; roman: orch lemah, baseline kuat -> baseline.
    make_runtime, _ = _factory(lambda goal: STRONG if "slugify" in goal else WEAK)
    provider = FakeProvider(responses=[_resp(WEAK), _resp(STRONG)])
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    agg = result["aggregate"]
    assert agg["orch_wins"] == 1
    assert agg["base_wins"] == 1
    assert agg["verdict"] == "tie"


async def test_run_suite_respects_k():
    make_runtime, calls = _factory(lambda goal: STRONG)
    provider = FakeProvider(responses=[_resp(WEAK) for _ in range(4)])
    await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=2
    )
    # make_runtime dipanggil k kali per goal (2 goal * k=2).
    assert len(calls) == 4


async def test_run_suite_any_estimated_flag():
    make_runtime, _ = _factory(lambda goal: STRONG, estimated=True)
    provider = FakeProvider(responses=[_resp(WEAK), _resp(WEAK)])
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    assert result["aggregate"]["any_estimated"] is True

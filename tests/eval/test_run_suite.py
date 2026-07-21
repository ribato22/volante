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
    ToolUseBlock,
    Usage,
)

FENCE = "`" * 3

# Output teks "kuat": code (mungkin skor 0 di runner goal ini) + tanda test + README,
# sehingga composite = 0.30 apa pun reference_test-nya. Mengalahkan output kosong
# (composite 0.0) secara deterministik untuk arm baseline & orchestration.
STRONG = (
    f"{FENCE}python\ndef solution():\n    return 1\n{FENCE}\n"
    "def test_it():\n    assert True\n"
    "# README\nrun the tests with pytest\n"
)
WEAK = ""

SCORE_KEYS = {"code", "has_tests", "has_readme", "composite"}
ARM_NAMES = {"baseline", "orchestration", "agentic"}
AGG_KEYS = {"wins", "ties", "cost_total", "any_estimated", "verdict"}

# Kode tool agentic: HANYA menulis solution.py rusak (tanpa test/readme) -> composite 0.
_A_WEAK_CODE = "open('solution.py','w').write('def f():\\n    return 1\\n')\n"
# Kode tool agentic: solution rusak + test_*.py + README -> composite 0.30 (test+readme).
_A_MID_CODE = (
    "open('solution.py','w').write('def f():\\n    return 1\\n')\n"
    "open('test_x.py','w').write('def test_ok():\\n    assert True\\n')\n"
    "open('README.md','w').write('# readme\\n')\n"
)


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


def _text_resp(text_val: str, *, estimated: bool = False) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=text_val)],
        usage=Usage(10, 20, estimated=estimated),
        model="strong-model",
        stop_reason="end_turn",
        latency_ms=5,
    )


def _tool_resp(code: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[ToolUseBlock(id="u1", name="run_python", input={"code": code})],
        usage=Usage(10, 20),
        model="strong-model",
        stop_reason="tool_use",
        latency_ms=5,
    )


def _agentic(code: str) -> list[CanonicalResponse]:
    # Loop AgenticWorker mengonsumsi: tool_use -> eksekusi -> end_turn.
    return [_tool_resp(code), _text_resp("done")]


# Biaya baseline per-respons: 10/1000*0.003 + 20/1000*0.015 = 0.00033.
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
    provider = FakeProvider(responses=(
        [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
        + [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
    ))
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    assert set(result) == {"per_goal", "aggregate"}
    per_goal = result["per_goal"]
    assert len(per_goal) == 2
    assert [g["id"] for g in per_goal] == ["slugify", "roman"]
    for g in per_goal:
        assert {"id", "winner", "scores"} <= set(g)
        assert set(g["scores"]) == ARM_NAMES
        for arm in ARM_NAMES:
            assert SCORE_KEYS <= set(g["scores"][arm])
        assert g["winner"] == "orchestration"
    agg = result["aggregate"]
    assert AGG_KEYS <= set(agg)
    assert agg["wins"] == {"baseline": 0, "orchestration": 2, "agentic": 0}
    assert agg["ties"] == 0
    assert set(agg["cost_total"]) == ARM_NAMES
    assert agg["cost_total"]["orchestration"] == pytest.approx(2 * ORCH_COST_EACH)
    assert agg["cost_total"]["baseline"] == pytest.approx(2 * BASE_COST_EACH)
    assert agg["verdict"] == "orchestration"
    assert agg["any_estimated"] is False


async def test_run_suite_agentic_majority_verdict():
    # baseline & orchestration lemah; agentic menulis test+readme -> agentic menang.
    make_runtime, _ = _factory(lambda goal: WEAK)
    provider = FakeProvider(responses=(
        [_text_resp(WEAK), *_agentic(_A_MID_CODE)]
        + [_text_resp(WEAK), *_agentic(_A_MID_CODE)]
    ))
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    agg = result["aggregate"]
    assert agg["wins"]["agentic"] == 2
    assert agg["wins"]["orchestration"] == 0
    assert agg["wins"]["baseline"] == 0
    assert agg["verdict"] == "agentic"


async def test_run_suite_tie_verdict_on_split():
    # slugify: orch STRONG -> orch menang; roman: orch lemah, agentic MID -> agentic.
    make_runtime, _ = _factory(lambda goal: STRONG if "slugify" in goal else WEAK)
    provider = FakeProvider(responses=(
        [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
        + [_text_resp(WEAK), *_agentic(_A_MID_CODE)]
    ))
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    agg = result["aggregate"]
    assert agg["wins"]["orchestration"] == 1
    assert agg["wins"]["agentic"] == 1
    assert agg["wins"]["baseline"] == 0
    assert agg["verdict"] == "tie"


async def test_run_suite_respects_k():
    make_runtime, calls = _factory(lambda goal: STRONG)
    per_iter = [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
    provider = FakeProvider(responses=per_iter * 4)
    await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=2
    )
    # make_runtime dipanggil k kali per goal (2 goal * k=2).
    assert len(calls) == 4


async def test_run_suite_any_estimated_flag():
    make_runtime, _ = _factory(lambda goal: STRONG, estimated=True)
    provider = FakeProvider(responses=(
        [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
        + [_text_resp(WEAK), *_agentic(_A_WEAK_CODE)]
    ))
    result = await run_suite(
        EVAL_SUITE[:2], make_runtime, provider, "strong-model", _registry(), k=1
    )
    assert result["aggregate"]["any_estimated"] is True

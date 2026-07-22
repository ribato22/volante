from __future__ import annotations

from dataclasses import dataclass

import pytest

from baton.cost import CostMeter
from baton.types import ModelInfo, Usage


@dataclass
class _FakeRegistry:
    """Registry palsu: cukup .get(model_id) -> ModelInfo (unit test tanpa jaringan)."""

    models: dict[str, ModelInfo]

    def get(self, model_id: str) -> ModelInfo:
        return self.models[model_id]


def _mi(model_id: str, cost_in: float, cost_out: float, billing: str = "card") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths=set(),
        context_window=8_000,
        max_output_tokens=1_000,
        supports_tools=False,
        cost_per_1k_in=cost_in,
        cost_per_1k_out=cost_out,
        billing=billing,
    )


def test_empty_meter() -> None:
    m = CostMeter()
    assert m.totals() == {}
    assert m.has_estimated() is False
    assert m.cost_usd(_FakeRegistry(models={})) == 0.0


def test_add_accumulates_per_model_id() -> None:
    m = CostMeter()
    m.add("gpt", Usage(prompt_tokens=100, completion_tokens=50))
    m.add("gpt", Usage(prompt_tokens=200, completion_tokens=25))
    totals = m.totals()
    assert set(totals) == {"gpt"}
    assert totals["gpt"].prompt_tokens == 300
    assert totals["gpt"].completion_tokens == 75


def test_add_keeps_models_separate() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=10, completion_tokens=1))
    m.add("b", Usage(prompt_tokens=20, completion_tokens=2))
    totals = m.totals()
    assert (totals["a"].prompt_tokens, totals["a"].completion_tokens) == (10, 1)
    assert (totals["b"].prompt_tokens, totals["b"].completion_tokens) == (20, 2)


def test_has_estimated_false_when_all_exact() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=10, completion_tokens=1))
    m.add("a", Usage(prompt_tokens=5, completion_tokens=1))
    assert m.has_estimated() is False


def test_has_estimated_true_if_any_estimated() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=10, completion_tokens=1))
    m.add("a", Usage(prompt_tokens=5, completion_tokens=1, estimated=True))
    assert m.has_estimated() is True


def test_per_model_total_marks_estimated() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=10, completion_tokens=1))
    m.add("a", Usage(prompt_tokens=5, completion_tokens=1, estimated=True))
    # total per-model juga menandai estimasi bila salah satu kontribusinya estimasi
    assert m.totals()["a"].estimated is True


def test_cost_usd_single_model() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=1000, completion_tokens=2000))
    reg = _FakeRegistry(models={"a": _mi("a", cost_in=0.5, cost_out=1.5)})
    # 1000/1000*0.5 + 2000/1000*1.5 = 0.5 + 3.0 = 3.5
    assert m.cost_usd(reg) == 3.5


def test_cost_usd_sums_across_models() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=1000, completion_tokens=1000))
    m.add("b", Usage(prompt_tokens=2000, completion_tokens=0))
    reg = _FakeRegistry(
        models={
            "a": _mi("a", cost_in=1.0, cost_out=2.0),
            "b": _mi("b", cost_in=0.1, cost_out=0.2),
        }
    )
    # a: 1.0 + 2.0 = 3.0 ; b: 2000/1000*0.1 + 0 = 0.2 ; total 3.2
    assert m.cost_usd(reg) == pytest.approx(3.2)


def test_cost_usd_fractional_tokens() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=123, completion_tokens=456))
    reg = _FakeRegistry(models={"a": _mi("a", cost_in=3.0, cost_out=6.0)})
    expected = 123 / 1000 * 3.0 + 456 / 1000 * 6.0
    assert m.cost_usd(reg) == pytest.approx(expected)


def test_totals_returns_copy_not_internal_dict() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=10, completion_tokens=5))
    snapshot = m.totals()
    snapshot["a"] = Usage(prompt_tokens=999, completion_tokens=999)
    snapshot["b"] = Usage(prompt_tokens=1, completion_tokens=1)
    # mutasi hasil snapshot TIDAK boleh mengubah state internal
    fresh = m.totals()
    assert set(fresh) == {"a"}
    assert (fresh["a"].prompt_tokens, fresh["a"].completion_tokens) == (10, 5)


def test_add_does_not_mutate_caller_usage() -> None:
    m = CostMeter()
    first = Usage(prompt_tokens=10, completion_tokens=5)
    m.add("a", first)
    m.add("a", Usage(prompt_tokens=7, completion_tokens=3))
    # objek Usage milik pemanggil tak boleh ikut ter-akumulasi
    assert (first.prompt_tokens, first.completion_tokens) == (10, 5)
    assert (m.totals()["a"].prompt_tokens, m.totals()["a"].completion_tokens) == (17, 8)


def test_costs_usd_card_model_goes_to_billed() -> None:
    m = CostMeter()
    m.add("a", Usage(prompt_tokens=1000, completion_tokens=2000))
    reg = _FakeRegistry(models={"a": _mi("a", cost_in=0.5, cost_out=1.5)})  # billing=card
    billed, credit = m.costs_usd(reg)
    # 1000/1000*0.5 + 2000/1000*1.5 = 0.5 + 3.0 = 3.5 -> semuanya ke billed (cash).
    assert billed == pytest.approx(3.5)
    assert credit == 0.0


def test_costs_usd_plan_included_goes_to_credit() -> None:
    m = CostMeter()
    m.add("p", Usage(prompt_tokens=1000, completion_tokens=1000))
    reg = _FakeRegistry(
        models={"p": _mi("p", cost_in=1.0, cost_out=2.0, billing="plan_included")}
    )
    billed, credit = m.costs_usd(reg)
    # Nilai konsumsi 3.0 -> credit (bukan cash); billed 0.0 (kejujuran §5.3).
    assert billed == 0.0
    assert credit == pytest.approx(3.0)


def test_costs_usd_plan_credit_also_goes_to_credit() -> None:
    m = CostMeter()
    m.add("p", Usage(prompt_tokens=1000, completion_tokens=0))
    reg = _FakeRegistry(
        models={"p": _mi("p", cost_in=2.0, cost_out=9.0, billing="plan_credit")}
    )
    billed, credit = m.costs_usd(reg)
    # plan_credit (dorman) juga -> credit untuk iterasi ini.
    assert billed == 0.0
    assert credit == pytest.approx(2.0)


def test_costs_usd_mixes_card_billed_and_plan_credit() -> None:
    m = CostMeter()
    m.add("card", Usage(prompt_tokens=1000, completion_tokens=1000))
    m.add("plan", Usage(prompt_tokens=1000, completion_tokens=1000))
    reg = _FakeRegistry(
        models={
            "card": _mi("card", cost_in=1.0, cost_out=2.0),  # card -> billed
            "plan": _mi("plan", cost_in=1.0, cost_out=2.0, billing="plan_included"),
        }
    )
    billed, credit = m.costs_usd(reg)
    assert billed == pytest.approx(3.0)
    assert credit == pytest.approx(3.0)


def test_cost_usd_equals_sum_of_billed_and_credit() -> None:
    m = CostMeter()
    m.add("card", Usage(prompt_tokens=1000, completion_tokens=1000))
    m.add("plan", Usage(prompt_tokens=1000, completion_tokens=1000))
    reg = _FakeRegistry(
        models={
            "card": _mi("card", cost_in=1.0, cost_out=2.0),
            "plan": _mi("plan", cost_in=1.0, cost_out=2.0, billing="plan_included"),
        }
    )
    billed, credit = m.costs_usd(reg)
    assert m.cost_usd(reg) == pytest.approx(billed + credit)
    assert m.cost_usd(reg) == pytest.approx(6.0)

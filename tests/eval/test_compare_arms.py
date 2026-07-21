from __future__ import annotations

from eval.harness import compare_arms


def _arm(comp, cost, ms=1, est=False):
    return {"composite": comp, "cost": cost, "ms": ms, "estimated": est}


def test_highest_composite_wins():
    out = compare_arms({"baseline": _arm(0.5, 0.01), "orchestration": _arm(0.9, 0.05),
                        "agentic": _arm(0.7, 0.03)})
    assert out["winner"] == "orchestration"
    assert set(out["arms"]) == {"baseline", "orchestration", "agentic"}


def test_composite_tie_cheapest_wins():
    out = compare_arms({"baseline": _arm(0.8, 0.01), "agentic": _arm(0.8, 0.05)})
    assert out["winner"] == "baseline"


def test_full_tie():
    out = compare_arms({"a": _arm(0.8, 0.02), "b": _arm(0.8, 0.02)})
    assert out["winner"] == "tie"

from __future__ import annotations

from eval.run import format_report

_ARM_NAMES = ("baseline", "orchestration", "agentic")


def _scores(comp: float) -> dict:
    return {"code": comp, "has_tests": 0.0, "has_readme": 0.0, "composite": comp}


def _arm(comp: float, cost: float, ms: int = 1, est: bool = False) -> dict:
    return {"composite": comp, "cost": cost, "ms": ms, "estimated": est}


def _per_goal(
    gid: str,
    winner: str = "orchestration",
    *,
    b: float = 0.3,
    o: float = 0.7,
    a: float = 0.5,
    b_cost: float = 0.002,
    o_cost: float = 0.01,
    a_cost: float = 0.005,
    est: bool = False,
) -> dict:
    return {
        "id": gid,
        "winner": winner,
        "arms": {
            "baseline": _arm(b, b_cost, est=est),
            "orchestration": _arm(o, o_cost),
            "agentic": _arm(a, a_cost),
        },
        "scores": {
            "baseline": _scores(b),
            "orchestration": _scores(o),
            "agentic": _scores(a),
        },
    }


def _result(
    per_goal: list[dict], *, verdict: str = "orchestration", any_estimated: bool = False
) -> dict:
    wins = {n: sum(1 for g in per_goal if g["winner"] == n) for n in _ARM_NAMES}
    ties = sum(1 for g in per_goal if g["winner"] == "tie")
    cost_total = {n: sum(g["arms"][n]["cost"] for g in per_goal) for n in _ARM_NAMES}
    return {
        "per_goal": per_goal,
        "aggregate": {
            "wins": wins,
            "ties": ties,
            "cost_total": cost_total,
            "any_estimated": any_estimated,
            "verdict": verdict,
        },
    }


def test_format_report_lists_every_goal_id():
    per = [_per_goal("slugify"), _per_goal("roman", winner="baseline")]
    report = format_report(_result(per, verdict="tie"))
    assert "slugify" in report
    assert "roman" in report


def test_format_report_names_all_three_arms():
    report = format_report(_result([_per_goal("slugify")]))
    for name in _ARM_NAMES:
        assert name in report


def test_format_report_returns_str():
    report = format_report(_result([_per_goal("slugify")]))
    assert isinstance(report, str)
    assert report.strip() != ""


def test_format_report_has_verdict_line_with_value():
    report = format_report(_result([_per_goal("slugify")], verdict="agentic"))
    verdict_lines = [ln for ln in report.splitlines() if "VERDICT:" in ln]
    assert len(verdict_lines) == 1
    assert "AGENTIC" in verdict_lines[0].upper()


def test_format_report_mentions_estimated_when_flagged():
    per = [_per_goal("slugify", est=True)]
    report = format_report(_result(per, any_estimated=True))
    assert "estimated" in report.lower()


def test_format_report_omits_estimated_when_all_measured():
    report = format_report(_result([_per_goal("slugify")], any_estimated=False))
    assert "estimated" not in report.lower()

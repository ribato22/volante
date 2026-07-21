from __future__ import annotations

from eval.run import format_report


def _per_goal(gid: str, winner: str = "orchestration", **kw) -> dict:
    base = dict(
        id=gid,
        winner=winner,
        orch_cost=0.01,
        base_cost=0.002,
        orch_ms=100,
        base_ms=50,
        orch_composite=0.7,
        base_composite=0.3,
        orch_estimated=False,
        base_estimated=False,
    )
    base.update(kw)
    return base


def _result(per_goal: list[dict], *, verdict: str = "orchestration",
            any_estimated: bool = False, **agg) -> dict:
    orch_wins = sum(1 for g in per_goal if g["winner"] == "orchestration")
    base_wins = sum(1 for g in per_goal if g["winner"] == "baseline")
    ties = sum(1 for g in per_goal if g["winner"] == "tie")
    aggregate = dict(
        orch_wins=orch_wins,
        base_wins=base_wins,
        ties=ties,
        orch_cost_total=sum(g["orch_cost"] for g in per_goal),
        base_cost_total=sum(g["base_cost"] for g in per_goal),
        any_estimated=any_estimated,
        verdict=verdict,
    )
    aggregate.update(agg)
    return {"per_goal": per_goal, "aggregate": aggregate}


def test_format_report_lists_every_goal_id():
    per = [_per_goal("slugify"), _per_goal("roman", winner="baseline")]
    report = format_report(_result(per, verdict="tie"))
    assert "slugify" in report
    assert "roman" in report


def test_format_report_returns_str():
    report = format_report(_result([_per_goal("slugify")]))
    assert isinstance(report, str)
    assert report.strip() != ""


def test_format_report_has_verdict_line_with_value():
    report = format_report(_result([_per_goal("slugify")], verdict="orchestration"))
    verdict_lines = [ln for ln in report.splitlines() if "VERDICT:" in ln]
    assert len(verdict_lines) == 1
    assert "ORCHESTRATION" in verdict_lines[0].upper()


def test_format_report_mentions_estimated_when_flagged():
    per = [_per_goal("slugify", orch_estimated=True)]
    report = format_report(_result(per, any_estimated=True))
    assert "estimated" in report.lower()


def test_format_report_omits_estimated_when_all_measured():
    report = format_report(_result([_per_goal("slugify")], any_estimated=False))
    assert "estimated" not in report.lower()

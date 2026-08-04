"""What these tests protect: a number that cannot be trusted must not read as a finding.

Three published results in this project were corrected downward after re-measurement.
None was a bug — the eval reported point estimates from a design that could not resolve
the effect it claimed. So the interval and the detectable effect are not decoration, and
the cases below are the ones where a careless implementation would say "significant".
"""

from __future__ import annotations

from eval.stats import paired_delta, pairs_needed


def test_drift_that_moves_both_arms_together_cancels() -> None:
    """The exact failure that produced +0.289 and then -0.021.

    Both arms swing wildly between runs; the difference is a steady +0.10. Comparing
    means would drown in the drift, and pairing removes it.
    """
    baseline = [0.40, 0.70, 0.45, 0.65]
    orchestration = [0.50, 0.80, 0.55, 0.75]

    delta = paired_delta(baseline, orchestration)

    assert abs(delta.mean - 0.10) < 1e-9
    assert delta.significant, "a perfectly consistent gap must survive the drift"


def test_a_gap_smaller_than_the_noise_is_reported_as_not_significant() -> None:
    """+0.159 against this much spread is what was published at p<0.005. It should
    never have read as a finding."""
    baseline = [0.41, 0.49, 0.71, 0.50]
    orchestration = [0.74, 0.72, 0.66, 0.48]

    delta = paired_delta(baseline, orchestration)

    assert delta.mean > 0.0
    assert not delta.significant
    assert delta.ci_low < 0.0 < delta.ci_high


def test_the_detectable_effect_is_reported_so_a_null_cannot_be_overread() -> None:
    """A run that found nothing must say what it could have found. Otherwise 'no
    difference' and 'we could not have seen one' look identical."""
    delta = paired_delta([0.5, 0.5, 0.5, 0.5], [0.5, 0.6, 0.4, 0.5])

    assert not delta.significant
    assert delta.detectable > 0.1, "this design cannot see a small effect; say so"
    assert "smallest effect" in delta.describe("baseline", "orchestration")


def test_small_n_uses_the_t_distribution_not_a_normal_approximation() -> None:
    """At k=3 the df is 2, where 1.96 understates the interval by more than half.

    Using the normal value there is how an underpowered run reports significance.
    """
    scores_a = [0.50, 0.50, 0.50]
    scores_b = [0.60, 0.62, 0.58]

    delta = paired_delta(scores_a, scores_b)
    half_width = delta.ci_high - delta.mean
    sd = (((0.10 - 0.10) ** 2 + (0.12 - 0.10) ** 2 + (0.08 - 0.10) ** 2) / 2) ** 0.5
    normal_half = 1.96 * sd / (3**0.5)

    assert half_width > normal_half * 1.5


def test_a_single_pair_reports_no_interval_rather_than_a_fake_one() -> None:
    delta = paired_delta([0.5], [0.9])

    assert delta.n == 1
    assert not delta.significant
    assert "no interval" in delta.describe("baseline", "orchestration")


def test_no_pairs_is_not_a_result() -> None:
    delta = paired_delta([], [])

    assert delta.n == 0
    assert not delta.significant


def test_pairs_needed_matches_the_observed_spread() -> None:
    """The number that explains the three corrections: at the drift actually measured
    (sd 0.12), seeing +0.15 needs about eleven batches, and four were run."""
    # Paired sd measured across the four real batches, not either arm's own spread.
    assert pairs_needed(0.187, 0.30) == 4
    assert pairs_needed(0.187, 0.16) == 11
    assert pairs_needed(0.187, 0.10) == 28


# --- the report must carry the uncertainty, not just the verdict ------------- #


def _fake_result(base: list[float], orch: list[float]) -> dict:
    arm = lambda v: {"composite": sum(v) / len(v), "cost": 0.0, "ms": 1,  # noqa: E731
                     "estimated": False, "measured": True}
    return {
        "per_goal": [
            {
                "id": "g",
                "winner": "baseline",
                "arms": {"baseline": arm(base), "orchestration": arm(orch),
                         "agentic": arm(base)},
                "scores": {"baseline": arm(base), "orchestration": arm(orch),
                           "agentic": arm(base)},
                "series": {"baseline": base, "orchestration": orch, "agentic": base},
            }
        ],
        "aggregate": {
            "wins": {"baseline": 1, "orchestration": 0, "agentic": 0},
            "ties": 0,
            "cost_total": {"baseline": 0.0, "orchestration": 0.0, "agentic": 0.0},
            "any_estimated": False,
            "verdict": "baseline",
        },
    }


def test_the_report_refuses_to_call_a_noisy_gap_evidence() -> None:
    """The exact data that was published as p<0.005 and then reversed."""
    from eval.run import format_report

    report = format_report(
        _fake_result([0.414, 0.492, 0.708, 0.497], [0.742, 0.721, 0.656, 0.476])
    )

    assert "INCLUDES zero" in report
    assert "not evidence of a difference" in report
    assert "Smallest effect this run could have detected" in report


def test_the_report_states_a_real_difference_plainly() -> None:
    from eval.run import format_report

    report = format_report(
        _fake_result([0.40, 0.70, 0.45, 0.65], [0.50, 0.80, 0.55, 0.75])
    )

    assert "excludes zero" in report
    assert "INCLUDES zero" not in report


def test_a_report_without_the_paired_series_omits_the_claim_entirely() -> None:
    """An older artifact has no series. Better to print nothing than to invent one."""
    from eval.run import format_report

    stale = _fake_result([0.4], [0.5])
    del stale["per_goal"][0]["series"]

    assert "paired over every run" not in format_report(stale)

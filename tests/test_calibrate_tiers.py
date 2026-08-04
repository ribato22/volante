"""Tier suggestions have to match what the measurements say — per task type.

Two measured facts force this shape. A profile does not replace the declared tier:
the router blends them by confidence (n/(n+3)), so at k=5 the tier still carries
37.5% and decides — the routing loss with a real profile was identical to using none.
And one tier cannot express the map: gpt-4o-mini measured 0.998 at code against
gpt-4o's 0.956, and 0.167 at analyze against 0.367. Averaging those put mini BELOW
gpt-4o and reproduced the guessed tiers exactly.
"""

from __future__ import annotations

from volante.calibrate import suggest_tiers

MEASURED = {
    "fast": {"task_scores": {"code": 0.998, "analyze": 0.167}, "overall_score": 0.58},
    "big": {"task_scores": {"code": 0.956, "analyze": 0.367}, "overall_score": 0.66},
}


def test_the_ranking_flips_between_task_types() -> None:
    """The whole reason a single number was not enough."""
    tiers = suggest_tiers(MEASURED)

    assert tiers["code"]["fast"] > tiers["code"]["big"]
    assert tiers["analyze"]["big"] > tiers["analyze"]["fast"]


def test_models_too_close_to_separate_share_a_tier() -> None:
    """Below the separation threshold they are the same model as far as this evidence
    can tell, and inventing a gap hands the router a distinction nothing measured."""
    tiers = suggest_tiers(
        {
            "a": {"task_scores": {"code": 0.900}},
            "b": {"task_scores": {"code": 0.995}},
            "c": {"task_scores": {"code": 1.000}},
        }
    )["code"]

    assert tiers["c"] == tiers["b"], "0.005 apart is not a tier's worth of difference"
    assert tiers["a"] < tiers["b"]


def test_the_best_measured_model_tops_the_scale() -> None:
    tiers = suggest_tiers(MEASURED)

    assert tiers["code"]["fast"] == 4
    assert tiers["analyze"]["big"] == 4


def test_tiers_stay_inside_the_declared_range() -> None:
    """`tier` is documented 1..4; a long tail of models must not walk off the bottom."""
    many = {
        f"m{i}": {"task_scores": {"code": 1.0 - i * 0.1}} for i in range(8)
    }

    tiers = suggest_tiers(many)["code"]

    assert min(tiers.values()) >= 1
    assert max(tiers.values()) == 4


def test_a_task_type_nobody_was_measured_on_is_absent() -> None:
    """Silence is the honest output for a type nothing measured — inventing a tier
    there is the same laundering `--calibrate` withholds overall_score to prevent."""
    tiers = suggest_tiers({"a": {"task_scores": {"code": 0.9}}})

    assert set(tiers) == {"code"}


def test_no_profiles_yields_no_suggestions() -> None:
    assert suggest_tiers({}) == {}

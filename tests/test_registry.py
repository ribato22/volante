# tests/test_registry.py
from __future__ import annotations

import logging

import pytest

from volante.registry import ModelQualityProfile, Registry
from volante.types import ModelInfo


def _model(
    model_id: str,
    *,
    strengths: set[str],
    supports_tools: bool,
    cost_in: float = 0.001,
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="anthropic",
        strengths=strengths,
        context_window=100_000,
        max_output_tokens=4_096,
        supports_tools=supports_tools,
        cost_per_1k_in=cost_in,
        cost_per_1k_out=cost_in,
    )


def test_all_returns_models_in_insertion_order() -> None:
    a = _model("a", strengths={"coding"}, supports_tools=True)
    b = _model("b", strengths={"reasoning"}, supports_tools=False)
    reg = Registry([a, b])
    assert reg.all() == [a, b]


def test_all_returns_a_copy_not_internal_list() -> None:
    a = _model("a", strengths={"coding"}, supports_tools=True)
    reg = Registry([a])
    got = reg.all()
    got.clear()
    assert reg.all() == [a]


def test_get_returns_model_by_id() -> None:
    a = _model("a", strengths={"coding"}, supports_tools=True)
    reg = Registry([a])
    assert reg.get("a") is a


def test_get_unknown_id_raises_value_error() -> None:
    reg = Registry([_model("a", strengths={"coding"}, supports_tools=True)])
    with pytest.raises(ValueError):
        reg.get("missing")


def test_matching_requires_strengths_subset() -> None:
    a = _model("a", strengths={"coding", "reasoning"}, supports_tools=True)
    b = _model("b", strengths={"coding"}, supports_tools=True)
    c = _model("c", strengths={"cheap_fast"}, supports_tools=True)
    reg = Registry([a, b, c])
    assert reg.matching({"coding"}) == [a, b]
    assert reg.matching({"coding", "reasoning"}) == [a]
    assert reg.matching({"long_context"}) == []


def test_matching_needs_tools_filters_out_non_tool_models() -> None:
    a = _model("a", strengths={"coding"}, supports_tools=True)
    b = _model("b", strengths={"coding"}, supports_tools=False)
    reg = Registry([a, b])
    assert reg.matching({"coding"}) == [a, b]
    assert reg.matching({"coding"}, needs_tools=True) == [a]


def test_duplicate_model_ids_fail_instead_of_hiding_inventory() -> None:
    first = _model("same", strengths={"coding"}, supports_tools=True)
    second = _model("same", strengths={"reasoning"}, supports_tools=False)
    with pytest.raises(ValueError, match="duplicate model ids"):
        Registry([first, second])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "", "model id"),
        ("provider", "", "provider"),
        ("strengths", set(), "strengths"),
        ("context_window", 0, "context_window"),
        ("max_output_tokens", 0, "max_output_tokens"),
        ("tier", 0, "tier"),
        ("tier", 5, "tier"),
        ("billing", "typo", "billing"),
        ("supports_tools", "yes", "supports_tools"),
        ("cost_per_1k_in", -1.0, "cost_per_1k_in"),
        ("cost_per_1k_out", float("nan"), "cost_per_1k_out"),
    ],
)
def test_invalid_model_inventory_fails_fast(field: str, value, message: str) -> None:
    model = _model("a", strengths={"coding"}, supports_tools=True)
    setattr(model, field, value)
    with pytest.raises(ValueError, match=message):
        Registry([model])


def test_output_limit_must_leave_input_context() -> None:
    model = _model("a", strengths={"coding"}, supports_tools=True)
    model.max_output_tokens = model.context_window
    with pytest.raises(ValueError, match="smaller than context_window"):
        Registry([model])


def test_quality_profile_is_optional_and_model_scoped() -> None:
    model = _model("a", strengths={"coding"}, supports_tools=True)
    profile = ModelQualityProfile(
        task_scores={"code": 0.9},
        overall_score=0.8,
        source="local-eval",
    )
    registry = Registry([model], quality_profiles={"a": profile})

    assert registry.quality_profile("a") == profile


def test_quality_profile_for_unknown_model_fails_fast() -> None:
    with pytest.raises(ValueError, match="reference unknown models"):
        Registry([], quality_profiles={"typo": ModelQualityProfile()})


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_quality_profile_scores_are_normalized(score: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        ModelQualityProfile(task_scores={"code": score})


def test_a_shared_profile_may_name_models_you_do_not_have(caplog) -> None:
    """A calibration describes the inventory it MEASURED.

    Rejecting the whole file for mentioning one model this process does not configure
    made such a file unusable anywhere but the machine that produced it — which is
    every shared profile, and every profile from a run that measured more models than
    are configured now. Entries that match nothing cannot affect routing.
    """
    model = ModelInfo(
        id="have-it", provider="fake", strengths={"coding"}, context_window=8000,
        max_output_tokens=1000, supports_tools=False,
        cost_per_1k_in=0.0, cost_per_1k_out=0.0,
    )
    profiles = {
        "have-it": ModelQualityProfile(overall_score=0.9, source="measured"),
        "not-configured-here": ModelQualityProfile(overall_score=0.5),
    }

    with caplog.at_level(logging.WARNING):
        registry = Registry([model], quality_profiles=profiles)

    assert registry.quality_profile("have-it") is not None
    assert "not-configured-here" in caplog.text, "a dropped entry must be named"


def test_a_profile_matching_nothing_still_fails_fast() -> None:
    """The typo protection this check exists for. A file where NOTHING matches
    describes some other inventory, or every id in it is wrong."""
    model = ModelInfo(
        id="have-it", provider="fake", strengths={"coding"}, context_window=8000,
        max_output_tokens=1000, supports_tools=False,
        cost_per_1k_in=0.0, cost_per_1k_out=0.0,
    )

    with pytest.raises(ValueError, match="reference unknown models"):
        Registry([model], quality_profiles={"typo": ModelQualityProfile()})

# tests/test_registry.py
from __future__ import annotations

import pytest

from orchestrator.registry import Registry
from orchestrator.types import ModelInfo


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

from __future__ import annotations

import pytest

from orchestrator.registry import Registry
from orchestrator.router import Router
from orchestrator.types import ModelInfo, Task


def _models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id="anthropic/claude-opus-4-8",
            provider="anthropic",
            strengths={"coding", "reasoning", "long_context"},
            context_window=200_000,
            max_output_tokens=8_192,
            supports_tools=True,
            cost_per_1k_in=15.0,
            cost_per_1k_out=75.0,
        ),
        ModelInfo(
            id="anthropic/claude-haiku",
            provider="anthropic",
            strengths={"coding", "cheap_fast"},
            context_window=200_000,
            max_output_tokens=8_192,
            supports_tools=True,
            cost_per_1k_in=0.8,
            cost_per_1k_out=4.0,
        ),
        ModelInfo(
            id="openai_compat/local-coder",
            provider="openai_compat",
            strengths={"coding"},
            context_window=32_000,
            max_output_tokens=4_096,
            supports_tools=False,
            cost_per_1k_in=0.1,
            cost_per_1k_out=0.2,
        ),
        ModelInfo(
            id="openai_compat/reasoner",
            provider="openai_compat",
            strengths={"reasoning"},
            context_window=128_000,
            max_output_tokens=4_096,
            supports_tools=True,
            cost_per_1k_in=2.0,
            cost_per_1k_out=6.0,
        ),
    ]


def _task(type_: str, mode: str = "one_shot") -> Task:
    return Task(id="t1", description="do the thing", type=type_, mode=mode)


def test_route_code_picks_cheapest_coding_model():
    router = Router(Registry(_models()))
    # coding candidates: opus (out=75), haiku (out=4), local-coder (out=0.2)
    assert router.route(_task("code")) == "openai_compat/local-coder"


def test_route_research_picks_cheapest_reasoning_model():
    router = Router(Registry(_models()))
    # reasoning candidates: opus (out=75), reasoner (out=6)
    assert router.route(_task("research")) == "openai_compat/reasoner"


def test_route_write_and_analyze_use_reasoning_strength():
    router = Router(Registry(_models()))
    assert router.route(_task("write")) == "openai_compat/reasoner"
    assert router.route(_task("analyze")) == "openai_compat/reasoner"


def test_route_agentic_requires_tool_capable_model():
    router = Router(Registry(_models()))
    # coding + needs_tools: local-coder excluded (no tools);
    # remaining haiku (out=4) vs opus (out=75) -> haiku
    assert router.route(_task("code", mode="agentic")) == "anthropic/claude-haiku"


def test_route_raises_valueerror_when_no_model_matches():
    only_reasoner_no_tools = [
        ModelInfo(
            id="openai_compat/reasoner",
            provider="openai_compat",
            strengths={"reasoning"},
            context_window=128_000,
            max_output_tokens=4_096,
            supports_tools=False,
            cost_per_1k_in=2.0,
            cost_per_1k_out=6.0,
        ),
    ]
    router = Router(Registry(only_reasoner_no_tools))
    with pytest.raises(ValueError):
        router.route(_task("code"))

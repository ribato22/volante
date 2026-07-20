# tests/test_registry_seed.py
from __future__ import annotations

from orchestrator.registry import Registry, default_models, default_registry


def test_default_registry_is_a_registry() -> None:
    assert isinstance(default_registry(), Registry)


def test_default_models_has_three_expected_seeds() -> None:
    models = default_models()
    assert len(models) == 3
    ids = {m.id for m in models}
    assert ids == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k2",
        "ollama/llama3.2",
    }


def test_anthropic_seed_coding_reasoning_tools_large_window() -> None:
    opus = default_registry().get("anthropic/claude-opus-4-8")
    assert opus.provider == "anthropic"
    assert {"coding", "reasoning"}.issubset(opus.strengths)
    assert opus.supports_tools is True
    assert opus.context_window >= 100_000


def test_kimi_seed_is_coding_and_cheaper_than_opus() -> None:
    reg = default_registry()
    kimi = reg.get("kimi/kimi-k2")
    opus = reg.get("anthropic/claude-opus-4-8")
    assert kimi.provider == "openai_compat"
    assert "coding" in kimi.strengths
    assert kimi.cost_per_1k_in < opus.cost_per_1k_in
    assert kimi.cost_per_1k_out < opus.cost_per_1k_out


def test_ollama_seed_cheap_fast_no_tools_small_window() -> None:
    ollama = default_registry().get("ollama/llama3.2")
    assert ollama.provider == "openai_compat"
    assert ollama.strengths == {"cheap_fast"}
    assert ollama.supports_tools is False
    assert ollama.context_window <= 32_000


def test_matching_over_default_registry_picks_exact_models() -> None:
    reg = default_registry()

    coders = reg.matching({"coding"})
    assert {m.id for m in coders} == {"anthropic/claude-opus-4-8", "kimi/kimi-k2"}

    reasoning_with_tools = reg.matching({"reasoning"}, needs_tools=True)
    assert [m.id for m in reasoning_with_tools] == ["anthropic/claude-opus-4-8"]

    fast = reg.matching({"cheap_fast"})
    assert [m.id for m in fast] == ["ollama/llama3.2"]

    fast_with_tools = reg.matching({"cheap_fast"}, needs_tools=True)
    assert fast_with_tools == []

from __future__ import annotations

import logging

import pytest

from baton.registry import Registry
from baton.router import Router
from baton.types import ModelInfo, Task


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


def _task(type_: str, mode: str = "one_shot", difficulty: str = "medium") -> Task:
    return Task(
        id="t1", description="do the thing", type=type_, mode=mode, difficulty=difficulty
    )


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


def test_single_default_seed_routes_every_one_shot_task_type() -> None:
    # Regresi (audit-important): dengan HANYA satu provider default (mis. Ollama/Kimi
    # gratis), router harus bisa mengarahkan setiap jenis task one-shot -> orkestrasi
    # penuh (demo.py orchestrate) tak gagal routing untuk konfigurasi tunggal.
    from baton.registry import default_models

    for seed in default_models():
        router = Router(Registry([seed]))
        for ttype in ("code", "research", "write", "analyze"):
            task = Task(id="t", description="d", type=ttype, mode="one_shot")
            assert router.route(task) == seed.id


def test_route_ranked_returns_list_and_route_matches_first():
    router = Router(Registry(_models()), prefer="cash_protect_quota")
    ranked = router.route_ranked(_task("code"))
    assert isinstance(ranked, list)
    assert ranked  # never empty when a candidate matches
    assert router.route(_task("code")) == ranked[0]
    # tier-uniform default models (tier=2) at difficulty "medium" (desired 3):
    # no tier-adequate candidate -> best-effort cash ranking == old min(cost_out).
    assert ranked[0] == "openai_compat/local-coder"


def _tiered_models() -> list[ModelInfo]:
    # Subscription opus (plan_included, tier 4) vs API opus (card, tier 4) vs
    # mid card (kimi, tier 3) vs free local (ollama, card $0, tier 1, no tools).
    return [
        ModelInfo(
            id="claude-code/opus",
            provider="claude_code",
            strengths={"coding", "reasoning"},
            context_window=200_000,
            max_output_tokens=4_096,
            supports_tools=True,
            cost_per_1k_in=0.015,
            cost_per_1k_out=0.075,
            tier=4,
            billing="plan_included",
        ),
        ModelInfo(
            id="anthropic/opus",
            provider="anthropic",
            strengths={"coding", "reasoning"},
            context_window=200_000,
            max_output_tokens=8_192,
            supports_tools=True,
            cost_per_1k_in=0.015,
            cost_per_1k_out=0.075,
            tier=4,
            billing="card",
        ),
        ModelInfo(
            id="kimi/kimi-k2",
            provider="openai_compat",
            strengths={"coding", "reasoning"},
            context_window=128_000,
            max_output_tokens=4_096,
            supports_tools=True,
            cost_per_1k_in=0.0012,
            cost_per_1k_out=0.0012,
            tier=3,
            billing="card",
        ),
        ModelInfo(
            id="ollama/llama3.2",
            provider="openai_compat",
            strengths={"coding", "reasoning"},
            context_window=8_192,
            max_output_tokens=2_048,
            supports_tools=False,
            cost_per_1k_in=0.0,
            cost_per_1k_out=0.0,
            tier=1,
            billing="card",
        ),
    ]


def test_hard_task_allows_subscription_ranked_by_cash():
    router = Router(Registry(_tiered_models()), prefer="cash_protect_quota")
    # difficulty hard -> desired tier 4: only the two opus (tier 4) qualify;
    # subscription cash $0 beats card $0.075 -> subscription first.
    assert router.route_ranked(_task("code", difficulty="hard")) == [
        "claude-code/opus",
        "anthropic/opus",
    ]


def test_difficulty_filters_out_low_tier_models():
    router = Router(Registry(_tiered_models()), prefer="cash_protect_quota")
    ranked = router.route_ranked(_task("code", difficulty="hard"))
    assert "kimi/kimi-k2" not in ranked  # tier 3 < desired 4
    assert "ollama/llama3.2" not in ranked  # tier 1 < desired 4


def test_non_hard_uses_direct_only_protecting_quota():
    router = Router(Registry(_tiered_models()), prefer="cash_protect_quota")
    # medium -> desired 3: adequate = claude-code(4,sub), anthropic(4,card), kimi(3,card).
    # non-hard -> DIRECT only (subscription excluded); cash: kimi 0.0012 < anthropic 0.075.
    ranked = router.route_ranked(_task("code", difficulty="medium"))
    assert "claude-code/opus" not in ranked
    assert ranked == ["kimi/kimi-k2", "anthropic/opus"]


def test_trivial_task_picks_free_local_direct():
    router = Router(Registry(_tiered_models()), prefer="cash_protect_quota")
    # trivial -> desired 1: all card qualify; free ollama (cash $0) wins, sub excluded.
    ranked = router.route_ranked(_task("code", difficulty="trivial"))
    assert ranked[0] == "ollama/llama3.2"
    assert "claude-code/opus" not in ranked


def test_non_hard_falls_back_to_subscription_when_no_direct(caplog):
    sub_only = [
        ModelInfo(
            id="claude-code/opus",
            provider="claude_code",
            strengths={"coding", "reasoning"},
            context_window=200_000,
            max_output_tokens=4_096,
            supports_tools=True,
            cost_per_1k_in=0.015,
            cost_per_1k_out=0.075,
            tier=4,
            billing="plan_included",
        ),
        ModelInfo(
            id="ollama/llama3.2",
            provider="openai_compat",
            strengths={"coding", "reasoning"},
            context_window=8_192,
            max_output_tokens=2_048,
            supports_tools=False,
            cost_per_1k_in=0.0,
            cost_per_1k_out=0.0,
            tier=1,
            billing="card",
        ),
    ]
    router = Router(Registry(sub_only), prefer="cash_protect_quota")
    with caplog.at_level(logging.INFO, logger="baton.router"):
        ranked = router.route_ranked(_task("code", difficulty="medium"))
    # medium desired 3: ollama(1) too low -> only subscription is adequate ->
    # best-effort fallback + honest log that quota is being used.
    assert ranked == ["claude-code/opus"]
    assert "using quota" in caplog.text


def test_no_tier_adequate_falls_back_to_best_effort():
    # Regression guard (post-A3.2-review): an Ollama-only registry (tier 1) with a
    # "medium" task (desired tier 3) has NO tier-adequate candidate at all. The
    # non-hard direct/subscription partition must not swallow this case: it must
    # still fall back to the v1 best-effort ranking over ALL matches, returning the
    # single candidate rather than raising or returning an empty list.
    ollama_only = [
        ModelInfo(
            id="ollama/llama3.2",
            provider="openai_compat",
            strengths={"coding", "reasoning"},
            context_window=8_192,
            max_output_tokens=2_048,
            supports_tools=False,
            cost_per_1k_in=0.0,
            cost_per_1k_out=0.0,
            tier=1,
            billing="card",
        ),
    ]
    router = Router(Registry(ollama_only), prefer="cash_protect_quota")
    ranked = router.route_ranked(_task("code", difficulty="medium"))
    assert ranked == ["ollama/llama3.2"]

# tests/test_registry_seed.py
from __future__ import annotations

from baton.registry import Registry, default_models, default_registry


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


def test_kimi_seed_is_catch_all_and_cheaper_than_opus() -> None:
    reg = default_registry()
    kimi = reg.get("kimi/kimi-k2")
    opus = reg.get("anthropic/claude-opus-4-8")
    assert kimi.provider == "openai_compat"
    # Catch-all strengths -> routable untuk semua task type (Kimi-saja bisa orkestrasi).
    assert {"coding", "reasoning"}.issubset(kimi.strengths)
    assert kimi.cost_per_1k_in < opus.cost_per_1k_in
    assert kimi.cost_per_1k_out < opus.cost_per_1k_out


def test_ollama_seed_catch_all_no_tools_small_window() -> None:
    ollama = default_registry().get("ollama/llama3.2")
    assert ollama.provider == "openai_compat"
    # Catch-all agar Ollama-saja (gratis) bisa menjalankan orkestrasi penuh...
    assert {"coding", "reasoning"}.issubset(ollama.strengths)
    # ...tapi tak tool-capable -> task agentic tak dirutekan ke sini.
    assert ollama.supports_tools is False
    assert ollama.context_window <= 32_000


def test_every_seed_routes_all_one_shot_task_types() -> None:
    # Regresi (audit-important): tiap model default (termasuk Ollama-saja / Kimi-saja)
    # cocok untuk strengths yang diperlukan SEMUA task type one-shot -> tak ada
    # konfigurasi tunggal yang membuat orkestrasi gagal routing.
    for m in default_models():
        assert {"coding"}.issubset(m.strengths)
        assert {"reasoning"}.issubset(m.strengths)


def test_matching_over_default_registry() -> None:
    reg = default_registry()

    # Semua seed punya coding + reasoning -> matching mengembalikan ketiganya.
    coders = reg.matching({"coding"})
    assert {m.id for m in coders} == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k2",
        "ollama/llama3.2",
    }
    assert {m.id for m in reg.matching({"reasoning"})} == {m.id for m in coders}

    # needs_tools menyaring Ollama (supports_tools=False).
    reasoning_with_tools = reg.matching({"reasoning"}, needs_tools=True)
    assert {m.id for m in reasoning_with_tools} == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k2",
    }

    # `cheap_fast` tak dipakai model mana pun lagi -> tak cocok apa pun.
    assert reg.matching({"cheap_fast"}) == []


def test_seed_tiers_are_opus4_kimi3_ollama1() -> None:
    reg = default_registry()
    assert reg.get("anthropic/claude-opus-4-8").tier == 4
    assert reg.get("kimi/kimi-k2").tier == 3
    assert reg.get("ollama/llama3.2").tier == 1


def test_seed_billing_all_card_today() -> None:
    # Realitas hari ini (§5.1): semua seed = card (Ollama rate 0.0 -> genuinely gratis).
    for m in default_models():
        assert m.billing == "card"

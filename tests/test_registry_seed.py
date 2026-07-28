# tests/test_registry_seed.py
"""Seed-inventory tests.

Split deliberately in two, because the old version of this file could not fail on
the bug it was supposed to catch. Every numeric assertion here used to be a
RELATION or a BOUND — `context_window >= 100_000`, `kimi.cost < opus.cost` — and a
relation is satisfied by infinitely many wrong numbers. The Anthropic row sat at a
fifth of its real context window and three times its real price, and this suite
stayed green through all of it.

So: vendor FACTS are pinned to exact values in one table that carries its sources,
and OUR POLICY (tier, strengths, billing, tool support) is asserted separately.
Changing a fact now means editing a line that names the page it came from.
"""

from __future__ import annotations

import pytest

from volante.registry import Registry, default_models, default_registry

# Numbers the vendor publishes. Read on 2026-07-28 from the URL beside each row.
# A model whose numbers are NOT vendor-published (anything run locally) does not
# belong here — see DEPLOYMENT_FLOORS.
VENDOR_FACTS: dict[str, dict[str, object]] = {
    "anthropic/claude-opus-4-8": {
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "cost_per_1k_in": 0.005,
        "cost_per_1k_out": 0.025,
        "published_per_mtok_in": 5.00,
        "published_per_mtok_out": 25.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "kimi/kimi-k3": {
        "context_window": 1_048_576,
        "max_output_tokens": 131_072,
        # Cache-MISS input rate: the schema holds one input price and assuming
        # cache hits would understate cold traffic by ~10x.
        "cost_per_1k_in": 0.003,
        "cost_per_1k_out": 0.015,
        "published_per_mtok_in": 3.00,
        "published_per_mtok_out": 15.00,
        "source": "https://platform.kimi.ai/docs/pricing/chat-k3",
    },
}

# Models we run on the user's own hardware. Their numbers are a floor WE picked to
# match what the runtime actually grants — not something a vendor published — so
# they are asserted separately and must never be cited as vendor facts.
DEPLOYMENT_FLOORS: dict[str, dict[str, int]] = {
    "ollama/llama3.2": {"context_window": 4_096, "max_output_tokens": 1_024},
}


def test_default_registry_is_a_registry() -> None:
    assert isinstance(default_registry(), Registry)


def test_default_models_has_three_expected_seeds() -> None:
    models = default_models()
    assert len(models) == 3
    assert {m.id for m in models} == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k3",
        "ollama/llama3.2",
    }


def test_every_seed_records_where_its_numbers_came_from() -> None:
    # The drift guard. A new seed added without recording its facts fails here
    # rather than shipping numbers nobody can trace.
    documented = set(VENDOR_FACTS) | set(DEPLOYMENT_FLOORS)
    undocumented = sorted({m.id for m in default_models()} - documented)
    assert not undocumented, f"seeded with no recorded source: {undocumented}"


@pytest.mark.parametrize("model_id", sorted(VENDOR_FACTS))
def test_vendor_facts_match_exactly(model_id: str) -> None:
    facts = VENDOR_FACTS[model_id]
    model = default_registry().get(model_id)

    for field in ("context_window", "max_output_tokens", "cost_per_1k_in", "cost_per_1k_out"):
        assert getattr(model, field) == facts[field], (
            f"{model_id}.{field} disagrees with {facts['source']} — if the vendor "
            "changed it, update this table and the citation in registry.py together"
        )


@pytest.mark.parametrize("model_id", sorted(VENDOR_FACTS))
def test_prices_are_per_thousand_tokens_not_per_million(model_id: str) -> None:
    # The exact arithmetic that produced the 3x overcharge. Vendors publish $/MTok;
    # this field is $/1k, so the divisor is 1000. Pinning the published figure next
    # to the stored one makes a unit slip fail instead of silently tripling a bill.
    facts = VENDOR_FACTS[model_id]
    model = default_registry().get(model_id)

    assert model.cost_per_1k_in * 1000 == pytest.approx(facts["published_per_mtok_in"])
    assert model.cost_per_1k_out * 1000 == pytest.approx(facts["published_per_mtok_out"])


@pytest.mark.parametrize("model_id", sorted(DEPLOYMENT_FLOORS))
def test_locally_run_models_use_our_floor_and_are_free(model_id: str) -> None:
    floor = DEPLOYMENT_FLOORS[model_id]
    model = default_registry().get(model_id)

    assert model.context_window == floor["context_window"]
    assert model.max_output_tokens == floor["max_output_tokens"]
    # Inference on the user's hardware costs the user's electricity, not a vendor
    # rate — 0.0 here is correct, not a missing number.
    assert model.cost_per_1k_in == 0.0
    assert model.cost_per_1k_out == 0.0


def test_output_budget_always_fits_inside_the_context_window() -> None:
    # Cheap invariant that would have caught a context/output pair drifting apart:
    # the projector budgets (context_window - max_tokens), and agentic runs raise
    # outright when the window is not strictly larger than the output cap.
    for model in default_models():
        assert model.max_output_tokens < model.context_window, model.id


# --- OUR POLICY (argue with these; they cite nothing because they are choices) ---


def test_anthropic_seed_is_the_tool_capable_top_tier() -> None:
    opus = default_registry().get("anthropic/claude-opus-4-8")
    assert opus.provider == "anthropic"
    assert {"coding", "reasoning"}.issubset(opus.strengths)
    assert opus.supports_tools is True


def test_kimi_seed_is_a_catch_all_mid_tier() -> None:
    kimi = default_registry().get("kimi/kimi-k3")
    assert kimi.provider == "openai_compat"
    # Catch-all strengths -> routable for every task type (a Kimi-only setup can
    # still orchestrate).
    assert {"coding", "reasoning"}.issubset(kimi.strengths)


def test_ollama_seed_is_catch_all_but_not_tool_capable() -> None:
    ollama = default_registry().get("ollama/llama3.2")
    assert ollama.provider == "openai_compat"
    assert {"coding", "reasoning"}.issubset(ollama.strengths)
    # ...but not tool-capable -> agentic tasks are never routed here.
    assert ollama.supports_tools is False


def test_every_seed_routes_all_one_shot_task_types() -> None:
    # Regression (audit-important): every default model — including an Ollama-only
    # or Kimi-only setup — matches the strengths EVERY one-shot task type needs, so
    # no single-provider configuration can fail to route.
    for m in default_models():
        assert {"coding"}.issubset(m.strengths)
        assert {"reasoning"}.issubset(m.strengths)


def test_matching_over_default_registry() -> None:
    reg = default_registry()

    # Every seed has coding + reasoning -> matching returns all three.
    coders = reg.matching({"coding"})
    assert {m.id for m in coders} == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k3",
        "ollama/llama3.2",
    }
    assert {m.id for m in reg.matching({"reasoning"})} == {m.id for m in coders}

    # needs_tools filters out Ollama (supports_tools=False).
    reasoning_with_tools = reg.matching({"reasoning"}, needs_tools=True)
    assert {m.id for m in reasoning_with_tools} == {
        "anthropic/claude-opus-4-8",
        "kimi/kimi-k3",
    }

    # `cheap_fast` is no longer claimed by any model -> matches nothing.
    assert reg.matching({"cheap_fast"}) == []


def test_seed_tiers_are_opus4_kimi3_ollama1() -> None:
    reg = default_registry()
    assert reg.get("anthropic/claude-opus-4-8").tier == 4
    assert reg.get("kimi/kimi-k3").tier == 3
    assert reg.get("ollama/llama3.2").tier == 1


def test_seed_billing_all_card_today() -> None:
    # Today's reality (§5.1): every seed is card-billed (Ollama's rate is 0.0, so
    # it is genuinely free rather than plan-funded).
    for m in default_models():
        assert m.billing == "card"


# --- THE LIVE PATH ------------------------------------------------------------
# Everything above tests default_models(), which is a demo seed. What actually
# charges a user is bootstrap.py's env defaults, and they carried a byte-identical
# copy of the same wrong numbers. A review proved the point: reverting bootstrap.py
# alone to its pre-fix values left the whole suite green. Facts are only guarded
# where they are asserted, so the same table is bound to both paths here.


def _live_model(env: dict[str, str]):
    """Build the inventory the way a real user's environment would."""
    import os

    from volante.bootstrap import build_providers_from_env

    previous = dict(os.environ)
    try:
        os.environ.update(env)
        registry, _providers, _baseline = build_providers_from_env()
        return registry
    finally:
        os.environ.clear()
        os.environ.update(previous)


def test_live_anthropic_defaults_match_the_same_vendor_facts() -> None:
    facts = VENDOR_FACTS["anthropic/claude-opus-4-8"]
    registry = _live_model(
        {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "claude-opus-4-8"}
    )

    model = registry.get("anthropic/claude-opus-4-8")
    assert model.context_window == facts["context_window"]
    assert model.max_output_tokens == facts["max_output_tokens"]
    assert model.cost_per_1k_in == facts["cost_per_1k_in"]
    assert model.cost_per_1k_out == facts["cost_per_1k_out"]


def test_live_anthropic_defaults_are_per_model_not_per_family() -> None:
    # A single shared default over-claims a 200k model's window by 5x, and
    # over-claiming context fails silently rather than raising.
    registry = _live_model(
        {
            "ANTHROPIC_API_KEY": "test",
            "ANTHROPIC_MODELS": "claude-opus-4-8,claude-haiku-4-5",
            "ANTHROPIC_NAMES": "anthropic/opus,anthropic/haiku",
        }
    )

    assert registry.get("anthropic/opus").context_window == 1_000_000
    assert registry.get("anthropic/haiku").context_window == 200_000
    assert registry.get("anthropic/haiku").cost_per_1k_out == 0.005


def test_an_unknown_anthropic_model_is_wrong_in_the_safe_direction() -> None:
    # We have no sourced numbers for it, so under-claim capability (never silently
    # over-pack a prompt) and over-claim price (never under-report a bill).
    registry = _live_model(
        {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "claude-not-released-yet"}
    )

    model = registry.get("anthropic/claude-not-released-yet")
    smallest_known = min(f["context_window"] for f in VENDOR_FACTS.values())
    dearest_known = max(f["cost_per_1k_out"] for f in VENDOR_FACTS.values())
    assert model.context_window <= smallest_known
    assert model.cost_per_1k_out >= dearest_known


def test_live_moonshot_defaults_match_the_same_vendor_facts() -> None:
    facts = VENDOR_FACTS["kimi/kimi-k3"]
    registry = _live_model({"MOONSHOT_API_KEY": "test"})

    model = registry.get("kimi/kimi-k3")
    assert model.context_window == facts["context_window"]
    assert model.max_output_tokens == facts["max_output_tokens"]
    assert model.cost_per_1k_in == facts["cost_per_1k_in"]
    assert model.cost_per_1k_out == facts["cost_per_1k_out"]


def test_live_ollama_defaults_match_the_same_deployment_floor() -> None:
    floor = DEPLOYMENT_FLOORS["ollama/llama3.2"]
    registry = _live_model({"OLLAMA_BASE_URL": "http://localhost:11434/v1"})

    model = registry.get("ollama/llama3.2")
    assert model.context_window == floor["context_window"]
    assert model.max_output_tokens == floor["max_output_tokens"]


def test_no_distribution_surface_ships_a_retired_model_id() -> None:
    # smithery.yaml and mcpb/manifest.json each pinned kimi-k2-0711-preview as an
    # always-set default, so fixing bootstrap.py left the dead id live in two
    # install paths that a user never sees the source of.
    import pathlib

    retired = ("kimi-k2-0711-preview", "kimi-k2-turbo-preview", "kimi-k2-thinking")
    root = pathlib.Path(__file__).resolve().parents[1]
    surfaces = [
        root / "smithery.yaml",
        root / "mcpb" / "manifest.json",
        root / ".env.example",
        root / "README.md",
    ]
    offenders = [
        f"{path.name}:{n}"
        for path in surfaces
        if path.exists()
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if any(dead in line for dead in retired)
    ]
    assert not offenders, f"retired Moonshot model id still shipped in: {offenders}"

from __future__ import annotations

import pytest

from volante.bootstrap import (
    _all_openai_compat_from_env,
    _openai_compat_from_env,
    build_providers_from_env,
    make_runtime_factory,
)


def test_bootstrap_exposes_moved_symbols():
    # The four provider-wiring helpers now live in volante.bootstrap (package),
    # not eval.run. Pure helpers must behave exactly as before the move.
    assert _openai_compat_from_env({}) is None
    assert _all_openai_compat_from_env({}) == []
    assert callable(build_providers_from_env)
    assert callable(make_runtime_factory)


def test_build_providers_accepts_prefer_and_include_subscription(monkeypatch):
    # New defaulted params (prefer, include_subscription) must be accepted and, at
    # their defaults, preserve today's behavior (same registry/providers/baseline).
    for k in ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for n in ("", "_2", "_3", "_4"):
        for suf in ("_BASE_URL", "_MODEL", "_KEY", "_NAME"):
            monkeypatch.delenv(f"OPENAI_COMPAT{n}{suf}", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://x/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("OPENAI_COMPAT_NAME", "google/gemini-flash")

    reg_default, prov_default, base_default = build_providers_from_env()
    reg, providers, baseline = build_providers_from_env(
        prefer="quality", include_subscription=False
    )
    assert baseline == base_default == "google/gemini-flash"
    assert set(providers) == set(prov_default)
    assert {m.id for m in reg.all()} == {m.id for m in reg_default.all()}


def test_openai_compat_from_env_tier_defaults_to_3():
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://x/v1",
        "OPENAI_COMPAT_MODEL": "gemini-2.5-flash",
    }
    info, _base_url, _api_key, _wire = _openai_compat_from_env(env)
    assert info.tier == 3
    assert info.billing == "card"


def test_openai_compat_from_env_tier_reads_env_override():
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://x/v1",
        "OPENAI_COMPAT_MODEL": "gemini-2.5-flash",
        "OPENAI_COMPAT_TIER": "4",
    }
    info, _base_url, _api_key, _wire = _openai_compat_from_env(env)
    assert info.tier == 4


def test_empty_optional_compat_metadata_uses_engine_defaults():
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://x/v1",
        "OPENAI_COMPAT_MODEL": "model",
        "OPENAI_COMPAT_STRENGTHS": "",
        "OPENAI_COMPAT_CONTEXT": "",
        "OPENAI_COMPAT_MAX_OUTPUT": "",
        "OPENAI_COMPAT_TOOLS": "",
        "OPENAI_COMPAT_COST_IN": "",
        "OPENAI_COMPAT_COST_OUT": "",
        "OPENAI_COMPAT_TIER": "",
    }
    info, _base_url, _api_key, _wire = _openai_compat_from_env(env)
    assert info.strengths == {"coding", "reasoning"}
    assert info.context_window == 128_000
    assert info.max_output_tokens == 8_192
    assert info.supports_tools is False
    assert info.cost_per_1k_in == 0
    assert info.cost_per_1k_out == 0
    assert info.tier == 3


def test_empty_required_compat_model_remains_invalid():
    with pytest.raises(RuntimeError, match="MODEL is missing"):
        _openai_compat_from_env(
            {
                "OPENAI_COMPAT_BASE_URL": "https://x/v1",
                "OPENAI_COMPAT_MODEL": "",
            }
        )

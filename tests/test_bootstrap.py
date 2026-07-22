from __future__ import annotations

from baton.bootstrap import (
    _all_openai_compat_from_env,
    _openai_compat_from_env,
    build_providers_from_env,
    make_runtime_factory,
)


def test_bootstrap_exposes_moved_symbols():
    # The four provider-wiring helpers now live in baton.bootstrap (package),
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

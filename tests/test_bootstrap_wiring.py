from __future__ import annotations

import pytest

import baton.bootstrap as bootstrap
from baton.bootstrap import build_providers_from_env


def _clear_all_provider_env(monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OLLAMA_BASE_URL",
        "CLAUDE_CODE_ENABLED", "CLAUDE_CODE_MODEL", "CLAUDE_CODE_TIER",
        "CLAUDE_CODE_TIMEOUT", "CLAUDE_CODE_MAX_OUTPUT", "CLAUDE_CODE_SYSTEM_PROMPT_MODE",
        "CODEX_ENABLED", "CODEX_MODEL", "CODEX_TIER", "CODEX_CONTEXT", "CODEX_MAX_OUTPUT",
    ):
        monkeypatch.delenv(k, raising=False)
    for n in ("", "_2", "_3", "_4"):
        for suf in ("_BASE_URL", "_MODEL", "_KEY", "_NAME", "_COST_OUT"):
            monkeypatch.delenv(f"OPENAI_COMPAT{n}{suf}", raising=False)


def test_subscription_absent_without_opt_in(monkeypatch):
    # CLI present but *_ENABLED unset -> no subscription provider registered.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert not any(mid.startswith("claude-code/") for mid in providers)
    assert not any(mid.startswith("codex/") for mid in providers)


def test_subscription_absent_when_cli_missing(monkeypatch):
    # Opted in but the binary is not on PATH -> not registered.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: False)
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert not any(mid.startswith("claude-code/") for mid in providers)


def test_claude_code_registered_when_enabled_and_detected(monkeypatch, capsys):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: binary == "claude")
    registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert "claude-code/opus" in providers
    info = registry.get("claude-code/opus")
    assert info.tier == 4
    assert info.billing == "plan_included"
    # §9 honesty warning printed to stderr on registration.
    err = capsys.readouterr().err.lower()
    assert "interactive" in err and "quota" in err
    # No duplicate registry entry even if default_models() also seeds claude-code.
    assert [m.id for m in registry.all()].count("claude-code/opus") == 1


def test_include_subscription_false_excludes_even_when_enabled(monkeypatch):
    # Eval fence (§9): the DEFAULT never registers subscription, even opted-in + detected.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)
    _registry, providers, _baseline = build_providers_from_env()  # include_subscription=False
    assert not any(mid.startswith("claude-code/") for mid in providers)


def test_codex_requires_explicit_tier(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5-codex")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)
    with pytest.raises(RuntimeError, match="CODEX_TIER"):
        build_providers_from_env(include_subscription=True)


def test_subscription_never_displaces_card_baseline(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)
    _registry, providers, baseline = build_providers_from_env(include_subscription=True)
    assert baseline == "anthropic/claude-opus-4-8"  # card model stays baseline
    assert "claude-code/opus" in providers  # registered, just not baseline

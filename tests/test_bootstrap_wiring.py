from __future__ import annotations

import pytest

import baton.bootstrap as bootstrap
from baton.bootstrap import _planner_model_id, build_providers_from_env, make_runtime_factory
from baton.providers.fake import FakeProvider
from baton.registry import Registry
from baton.types import ModelInfo


def _model(mid: str, *, billing: str = "card", tier: int = 2) -> ModelInfo:
    return ModelInfo(
        id=mid,
        provider="fake",
        strengths={"coding", "reasoning"},
        context_window=128_000,
        max_output_tokens=4_096,
        supports_tools=True,
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        tier=tier,
        billing=billing,
    )


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


def test_planner_prefers_card_over_subscription():
    # Even when the baseline handed in is a subscription model, planning must land on a
    # temperature-controllable (card) model (§7.1: claude -p ignores temperature).
    registry = Registry([
        _model("sub/x", billing="plan_included", tier=4),
        _model("api/y", billing="card", tier=3),
    ])
    providers = {"sub/x": FakeProvider(), "api/y": FakeProvider()}
    assert _planner_model_id(registry, providers, "sub/x") == "api/y"


def test_planner_keeps_card_baseline_unchanged():
    # Back-compat: a card baseline (today's only case) is returned as-is.
    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": FakeProvider()}
    assert _planner_model_id(registry, providers, "api/y") == "api/y"


def test_planner_picks_highest_tier_card_deterministically():
    registry = Registry([
        _model("sub/x", billing="plan_included", tier=4),
        _model("card/lo", billing="card", tier=2),
        _model("card/hi", billing="card", tier=4),
    ])
    providers = {"sub/x": FakeProvider(), "card/lo": FakeProvider(), "card/hi": FakeProvider()}
    assert _planner_model_id(registry, providers, "sub/x") == "card/hi"


def test_planner_falls_back_to_subscription_when_only_option():
    # Subscription-only setup: nothing card exists -> the baseline (subscription) is returned;
    # the CLI runs verify_claude_plan_gate before trusting it to plan.
    registry = Registry([_model("sub/x", billing="plan_included", tier=4)])
    providers = {"sub/x": FakeProvider()}
    assert _planner_model_id(registry, providers, "sub/x") == "sub/x"


def test_make_runtime_factory_wires_card_planner_and_synth():
    registry = Registry([
        _model("sub/x", billing="plan_included", tier=4),
        _model("api/y", billing="card", tier=3),
    ])
    providers = {"sub/x": FakeProvider(), "api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "sub/x", prefer="cash_protect_quota")()
    assert runtime.supervisor._model_id == "api/y"
    assert runtime.synthesizer._model_id == "api/y"


def test_make_runtime_factory_threads_prefer_into_router():
    # Router(registry) alone silently ignores the objective; make_runtime_factory must
    # forward `prefer` so runtime.router actually routes on it (not the Router default).
    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "api/y", prefer="cash_protect_quota")()
    assert runtime.router._prefer == "cash_protect_quota"


def test_make_runtime_factory_default_prefer_is_quality_back_compat():
    # Existing callers (demo.py, webui/server.py, tests/eval) call
    # make_runtime_factory(registry, providers, model_id) with no `prefer` -> unchanged.
    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "api/y")()
    assert runtime.router._prefer == "quality"

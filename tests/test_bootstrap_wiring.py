from __future__ import annotations

import pytest

import volante.bootstrap as bootstrap
from volante.bootstrap import (
    _phase_model_ids,
    _planner_model_id,
    build_providers_from_env,
    make_runtime_factory,
    make_verified_runtime_factory,
    verify_claude_plan_gate,
)
from volante.providers.base import ProviderError
from volante.providers.fake import FakeProvider
from volante.registry import Registry
from volante.types import CanonicalResponse, ModelInfo, TextBlock, Usage


def _model(
    mid: str,
    *,
    billing: str = "card",
    tier: int = 2,
    cost_in: float = 0.0,
    cost_out: float = 0.0,
) -> ModelInfo:
    return ModelInfo(
        id=mid,
        provider="fake",
        strengths={"coding", "reasoning"},
        context_window=128_000,
        max_output_tokens=4_096,
        supports_tools=True,
        cost_per_1k_in=cost_in,
        cost_per_1k_out=cost_out,
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
    # Live-verified default is "replace" (append breaks planning — opus answers the goal
    # instead of emitting the strict plan JSON). Override via CLAUDE_CODE_SYSTEM_PROMPT_MODE.
    assert providers["claude-code/opus"].system_prompt_mode == "replace"


def test_claude_code_system_prompt_mode_append_override(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SYSTEM_PROMPT_MODE", "append")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: binary == "claude")
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert providers["claude-code/opus"].system_prompt_mode == "append"


def test_include_subscription_false_excludes_even_when_enabled(monkeypatch):
    # Eval fence (§9): the DEFAULT never registers subscription, even opted-in + detected.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)
    _registry, providers, _baseline = build_providers_from_env()  # include_subscription=False
    assert not any(mid.startswith("claude-code/") for mid in providers)


def test_no_providers_message_mentions_subscription_when_included(monkeypatch):
    # CLI path (include_subscription=True): actionable, mentions CLAUDE_CODE_ENABLED,
    # and drops the stale "before running the eval" wording (this error also fires
    # from the `volante` CLI, not just the eval suite).
    _clear_all_provider_env(monkeypatch)
    with pytest.raises(RuntimeError) as exc_info:
        build_providers_from_env(include_subscription=True)
    message = str(exc_info.value)
    assert "CLAUDE_CODE_ENABLED" in message
    assert "eval" not in message


def test_no_providers_message_omits_subscription_when_excluded(monkeypatch):
    # eval path (include_subscription=False, default): still actionable, but does not
    # dangle subscription options that build_providers_from_env would never register here.
    _clear_all_provider_env(monkeypatch)
    with pytest.raises(RuntimeError) as exc_info:
        build_providers_from_env()
    message = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "CLAUDE_CODE_ENABLED" not in message
    assert "eval" not in message


def test_codex_requires_explicit_tier(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5-codex")
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: True)
    with pytest.raises(RuntimeError, match="CODEX_TIER"):
        build_providers_from_env(include_subscription=True)


def test_codex_gate_uses_codex_detected_not_bare_path_lookup(monkeypatch):
    # A `codex` binary can sit on PATH without being logged in. The gate MUST be
    # `codex_detected()` (`codex login status` exit 0), not a bare shutil.which — a
    # PATH-only check would register a live-looking (~$0 cash) provider that the
    # router ranks first for every hard task, wasting a candidate attempt before reroute.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_TIER", "3")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: True)  # PATH says "found"
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: False)  # not logged in
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert not any(mid.startswith("codex/") for mid in providers)


def test_codex_registered_when_enabled_and_login_ok(monkeypatch, capsys):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_TIER", "3")
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: True)
    registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert "codex/default" in providers  # unset CODEX_MODEL -> sensible default id
    info = registry.get("codex/default")
    assert info.tier == 3
    assert info.billing == "plan_included"
    err = capsys.readouterr().err.lower()
    assert "interactive" in err and "quota" in err


def test_codex_id_follows_configured_model(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_TIER", "3")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5-codex")
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: True)
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert "codex/gpt-5-codex" in providers
    assert "codex/default" not in providers


def test_claude_code_id_follows_configured_model(monkeypatch):
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_MODEL", "sonnet")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: binary == "claude")
    _registry, providers, _baseline = build_providers_from_env(include_subscription=True)
    assert "claude-code/sonnet" in providers
    assert "claude-code/opus" not in providers


def test_subscription_model_info_comes_from_shared_seed_helpers(monkeypatch):
    # #3: no inline-duplicated ModelInfo — both leg's registered ModelInfo must match
    # what the existing single-source-of-truth seed helpers produce (drift regression
    # guard: claude_code_model_info's strengths include long_context; build_codex_model's
    # default context_window is 256_000, not a bootstrap-local 128_000).
    from volante.providers.claude_code import claude_code_model_info
    from volante.providers.codex import build_codex_model

    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_TIER", "3")
    monkeypatch.setattr(bootstrap, "_detect_cli", lambda binary: binary == "claude")
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: True)
    registry, _providers, _baseline = build_providers_from_env(include_subscription=True)

    cc = registry.get("claude-code/opus")
    expected_cc = claude_code_model_info("opus", tier=4, max_output_tokens=4096)
    assert cc.strengths == expected_cc.strengths
    assert "long_context" in cc.strengths
    assert cc.context_window == expected_cc.context_window

    cx = registry.get("codex/default")
    expected_cx = build_codex_model({"CODEX_TIER": "3"})
    assert cx.context_window == expected_cx.context_window == 256_000
    assert cx.strengths == expected_cx.strengths


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


def test_phase_fallbacks_use_quality_order_and_exclude_unverified_subscription() -> None:
    registry = Registry(
        [
            _model("api/primary", billing="card", tier=2),
            _model("api/frontier", billing="card", tier=4),
            _model("api/small", billing="card", tier=1),
            _model("sub/other", billing="plan_included", tier=4),
        ]
    )
    providers = {
        model_id: FakeProvider()
        for model_id in (
            "api/primary",
            "api/frontier",
            "api/small",
            "sub/other",
        )
    }

    assert _phase_model_ids(
        registry,
        providers,
        "api/primary",
        phase="planning",
    ) == ["api/primary", "api/frontier", "api/small"]
    assert _phase_model_ids(
        registry,
        providers,
        "api/primary",
        phase="synthesis",
    ) == ["api/primary", "api/frontier", "api/small"]


def test_make_runtime_factory_wires_card_planner_and_synth():
    registry = Registry([
        _model("sub/x", billing="plan_included", tier=4),
        _model("api/y", billing="card", tier=3),
    ])
    providers = {"sub/x": FakeProvider(), "api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "sub/x", prefer="cash_protect_quota")()
    assert runtime.supervisor._model_id == "api/y"
    assert runtime.synthesizer._model_id == "api/y"
    assert runtime._planning_model_ids == ("api/y",)
    assert runtime._synthesis_model_ids == ("api/y",)


def test_make_runtime_factory_threads_prefer_into_router():
    # Router(registry) alone silently ignores the objective; make_runtime_factory must
    # forward `prefer` so runtime.router actually routes on it (not the Router default).
    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "api/y", prefer="cash_protect_quota")()
    assert runtime.router._prefer == "cash_protect_quota"


def test_make_runtime_factory_default_prefer_is_quality():
    # The default objective is "quality" (strongest capable model per task), matching
    # Router's own default. Callers that pass no `prefer` (demo.py, webui/server.py,
    # tests/eval) land on quality; cash_protect_quota is opt-in.
    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": FakeProvider()}
    runtime = make_runtime_factory(registry, providers, "api/y")()
    assert runtime.router._prefer == "quality"


def test_sync_factory_rejects_ungated_subscription_only_planner():
    registry = Registry([_model("sub/x", billing="plan_included", tier=4)])
    providers = {"sub/x": FakeProvider()}
    with pytest.raises(RuntimeError, match="make_verified_runtime_factory"):
        make_runtime_factory(registry, providers, "sub/x")


def _plan_resp(
    payload: str, *, prompt: int = 0, completion: int = 0
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=payload)],
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion),
        model="claude-code/opus",
        stop_reason="end_turn",
        latency_ms=0,
    )


async def test_verify_claude_plan_gate_true_on_valid_plan():
    valid = '[{"id":"t1","description":"d","type":"code","mode":"one_shot","depends_on":[]}]'
    provider = FakeProvider([_plan_resp(valid)])
    assert await verify_claude_plan_gate(provider, "claude-code/opus") is True


async def test_verify_claude_plan_gate_false_on_garbage():
    provider = FakeProvider([_plan_resp("sorry, I can't emit JSON")])
    assert await verify_claude_plan_gate(provider, "claude-code/opus") is False


async def test_verify_claude_plan_gate_false_on_empty_plan():
    # An empty array parses as JSON but fails supervisor _validate ("plan is empty").
    provider = FakeProvider([_plan_resp("[]")])
    assert await verify_claude_plan_gate(provider, "claude-code/opus") is False


async def test_verified_factory_skips_live_probe_for_card_planner():
    class _MustNotRun:
        async def complete(self, req):
            raise ProviderError("must not be called", retryable=False)

    registry = Registry([_model("api/y", billing="card", tier=3)])
    providers = {"api/y": _MustNotRun()}
    factory = await make_verified_runtime_factory(registry, providers, "api/y")
    assert factory().supervisor._model_id == "api/y"


async def test_verified_factory_accepts_subscription_planner_after_live_gate():
    valid = '[{"id":"t1","description":"d","type":"code","mode":"one_shot","depends_on":[]}]'
    registry = Registry([_model("sub/x", billing="plan_included", tier=4)])
    providers = {
        "sub/x": FakeProvider([_plan_resp(valid, prompt=11, completion=7)])
    }
    factory = await make_verified_runtime_factory(registry, providers, "sub/x")
    first = factory()
    second = factory()
    assert first.supervisor._model_id == "sub/x"
    assert first._initial_subscription_calls == 1
    assert first.cost_meter.totals()["sub/x"] == Usage(
        prompt_tokens=11, completion_tokens=7
    )
    assert second._initial_subscription_calls == 0
    assert second.cost_meter.totals() == {}


async def test_verified_factory_fails_closed_for_incompatible_subscription_planner():
    registry = Registry([_model("sub/x", billing="plan_included", tier=4)])
    providers = {"sub/x": FakeProvider([_plan_resp("not json")])}
    with pytest.raises(RuntimeError, match="parse-plan gate"):
        await make_verified_runtime_factory(registry, providers, "sub/x")


async def test_verified_factory_preflight_obeys_subscription_cap(
    monkeypatch,
) -> None:
    class _Counting(FakeProvider):
        def __init__(self) -> None:
            super().__init__([_plan_resp("not json")])
            self.calls = 0

        async def complete(self, req):
            self.calls += 1
            return await super().complete(req)

    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "0")
    registry = Registry([_model("sub/x", billing="plan_included", tier=4)])
    provider = _Counting()
    with pytest.raises(RuntimeError, match="preflight is disabled"):
        await make_verified_runtime_factory(
            registry, {"sub/x": provider}, "sub/x"
        )
    assert provider.calls == 0


async def test_preflight_is_in_first_run_result_and_not_double_charged() -> None:
    valid = '[{"id":"t1","description":"d","type":"code","mode":"one_shot","depends_on":[]}]'
    registry = Registry(
        [
            _model(
                "sub/x",
                billing="plan_included",
                tier=4,
                cost_in=0.01,
                cost_out=0.02,
            )
        ]
    )
    provider = FakeProvider(
        [
            _plan_resp(valid, prompt=10, completion=5),  # one-time preflight
            _plan_resp(valid, prompt=20, completion=10),  # first run plan
            _plan_resp("artifact", prompt=30, completion=15),
            _plan_resp("final", prompt=40, completion=20),
            _plan_resp(valid, prompt=1, completion=1),  # second run plan
            _plan_resp("artifact 2", prompt=1, completion=1),
            _plan_resp("final 2", prompt=1, completion=1),
        ]
    )
    factory = await make_verified_runtime_factory(
        registry, {"sub/x": provider}, "sub/x"
    )

    first = await factory().aexecute("first goal")
    second = await factory().aexecute("second goal")

    assert first.status == "success"
    assert first.subscription_calls == 4
    assert first.usage_total["sub/x"] == Usage(100, 50)
    assert first.credit_usd == pytest.approx(0.002)
    assert second.status == "success"
    assert second.subscription_calls == 3
    assert second.usage_total["sub/x"] == Usage(3, 3)


async def test_runtime_cap_includes_successful_preflight(monkeypatch) -> None:
    valid = '[{"id":"t1","description":"d","type":"code","mode":"one_shot","depends_on":[]}]'
    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "1")
    registry = Registry(
        [_model("sub/x", billing="plan_included", tier=4, cost_in=0.01)]
    )
    provider = FakeProvider(
        [
            _plan_resp(valid, prompt=10, completion=0),
            _plan_resp(valid, prompt=999, completion=999),
        ]
    )
    factory = await make_verified_runtime_factory(
        registry, {"sub/x": provider}, "sub/x"
    )

    result = await factory().aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "__planning__"
    assert result.error_code == "quota_exhausted"
    assert result.subscription_calls == 1
    assert result.usage_total["sub/x"] == Usage(10, 0)
    assert provider._index == 1


async def test_no_sandbox_withholds_code_execution_from_the_planner(
    monkeypatch, capsys
) -> None:
    # Fail-closed end to end: with no isolating sandbox the planner must not be told it
    # can execute code, so it cannot plan a task that would run unisolated.
    from volante.tools import sandbox as sandbox_mod

    monkeypatch.delenv("VOLANTE_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda **_: False)

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    runtime = factory()

    assert "run_python" not in runtime.available_tool_names
    assert "disabled" in capsys.readouterr().err.lower()  # says so out loud


async def test_available_docker_enables_code_execution(monkeypatch) -> None:
    from volante.tools import sandbox as sandbox_mod

    monkeypatch.delenv("VOLANTE_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda **_: True)

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    assert "run_python" in factory().available_tool_names


async def test_explicit_subprocess_opt_in_warns_loudly(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOLANTE_SANDBOX", "subprocess")

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    runtime = factory()

    assert "run_python" in runtime.available_tool_names
    assert "UNISOLATED" in capsys.readouterr().err


async def test_withheld_code_execution_is_reported_in_band(monkeypatch) -> None:
    # stderr is invisible to an IDE MCP client and to the Web UI, so the degraded
    # capability must ride on the RunResult itself.
    from volante.tools import sandbox as sandbox_mod

    monkeypatch.delenv("VOLANTE_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda **_: False)

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    notice = factory().capability_notice
    assert notice and "disabled" in notice.lower()
    assert "VOLANTE_SANDBOX=subprocess" in notice  # actionable


async def test_unisolated_opt_in_is_reported_in_band(monkeypatch) -> None:
    monkeypatch.setenv("VOLANTE_SANDBOX", "subprocess")

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    notice = factory().capability_notice
    assert notice and "UNISOLATED" in notice


async def test_docker_sandbox_leaves_no_capability_notice(monkeypatch) -> None:
    from volante.tools import sandbox as sandbox_mod

    monkeypatch.delenv("VOLANTE_SANDBOX", raising=False)
    monkeypatch.delenv("AIORCH_SANDBOX", raising=False)
    sandbox_mod.reset_docker_probe()
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda **_: True)

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    assert factory().capability_notice is None


async def test_agentic_iteration_cap_is_configurable(monkeypatch) -> None:
    # Three eval goals hit the old cap of 8 while still making genuinely different calls
    # each turn. The cap has to be tunable, because it bounds real work — a loop that is
    # merely repeating itself is stopped by the no-progress guard, not by this number.
    monkeypatch.setenv("VOLANTE_AGENTIC_MAX_ITERS", "20")

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    assert factory().agentic_worker.max_iters == 20


async def test_agentic_iteration_cap_default_is_the_measured_one(monkeypatch) -> None:
    monkeypatch.delenv("VOLANTE_AGENTIC_MAX_ITERS", raising=False)

    registry = Registry([_model("api/x")])
    factory = await make_verified_runtime_factory(
        registry, {"api/x": FakeProvider([])}, "api/x"
    )

    assert factory().agentic_worker.max_iters == 8


@pytest.mark.parametrize("bad", ["0", "-3", "many"])
async def test_an_unusable_iteration_cap_fails_fast(monkeypatch, bad: str) -> None:
    monkeypatch.setenv("VOLANTE_AGENTIC_MAX_ITERS", bad)

    registry = Registry([_model("api/x")])
    with pytest.raises(ValueError, match="VOLANTE_AGENTIC_MAX_ITERS"):
        await make_verified_runtime_factory(
            registry, {"api/x": FakeProvider([])}, "api/x"
        )

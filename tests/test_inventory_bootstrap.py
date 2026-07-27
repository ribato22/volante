from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from volante.bootstrap import NoProvidersConfiguredError, build_providers_from_env
from volante.router import Router
from volante.types import Task

_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_COMPAT",
    "MOONSHOT_",
    "OLLAMA_",
    "CLAUDE_CODE_",
    "CODEX_",
    "VOLANTE_QUALITY_PROFILES",
    "VOLANTE_MODEL_OVERRIDES",
)


@pytest.fixture(autouse=True)
def _clean_provider_inventory_env(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(_PREFIXES):
            monkeypatch.delenv(key, raising=False)


def test_anthropic_repeatable_models_all_enter_inventory(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODELS", "claude-opus,claude-sonnet")

    registry, providers, baseline = build_providers_from_env()

    assert [model.id for model in registry.all()] == [
        "anthropic/claude-opus",
        "anthropic/claude-sonnet",
    ]
    assert list(providers) == [
        "anthropic/claude-opus",
        "anthropic/claude-sonnet",
    ]
    assert providers["anthropic/claude-opus"].model == "claude-opus"
    assert providers["anthropic/claude-sonnet"].model == "claude-sonnet"
    assert baseline == "anthropic/claude-opus"


def test_pristine_env_example_does_not_activate_a_provider(monkeypatch) -> None:
    values: dict[str, str] = {}
    for raw_line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
        monkeypatch.setenv(key, value)

    assert values["ANTHROPIC_API_KEY"] == ""
    assert values["OPENAI_COMPAT_BASE_URL"] == ""
    with pytest.raises(NoProvidersConfiguredError):
        build_providers_from_env()


def test_anthropic_singular_wire_override_has_matching_canonical_id(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet")

    registry, providers, baseline = build_providers_from_env()

    assert [model.id for model in registry.all()] == ["anthropic/claude-sonnet"]
    assert list(providers) == ["anthropic/claude-sonnet"]
    assert baseline == "anthropic/claude-sonnet"


def test_quality_profiles_can_calibrate_selection_across_local_models(
    monkeypatch, tmp_path: Path
) -> None:
    profiles = tmp_path / "quality.json"
    profiles.write_text(
        json.dumps(
            {
                "ollama/llama3.2": {
                    "task_scores": {"code": 0.40},
                    "source": "user-eval",
                    "confidence": 1.0,
                },
                "ollama/qwen2.5-coder:14b": {
                    "task_scores": {"code": 0.95},
                    "source": "user-eval",
                    "confidence": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OLLAMA_MODELS", "llama3.2,qwen2.5-coder:14b")
    monkeypatch.setenv("VOLANTE_QUALITY_PROFILES_FILE", str(profiles))

    registry, providers, _ = build_providers_from_env()
    decision = Router(registry).route_decision(
        Task(id="code", description="implement it", type="code", mode="one_shot")
    )

    assert set(providers) == {
        "ollama/llama3.2",
        "ollama/qwen2.5-coder:14b",
    }
    assert decision.selected_model_id == "ollama/qwen2.5-coder:14b"
    assert len(decision.candidates) == 2
    assert all(candidate.profile_source == "user-eval" for candidate in decision.candidates)


def test_moonshot_repeatable_models_support_parallel_canonical_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "test")
    monkeypatch.setenv("MOONSHOT_MODELS", "kimi-a,kimi-b")
    monkeypatch.setenv("MOONSHOT_NAMES", "moonshot/fast,moonshot/quality")

    registry, providers, baseline = build_providers_from_env()

    assert [model.id for model in registry.all()] == [
        "moonshot/fast",
        "moonshot/quality",
    ]
    assert set(providers) == {"moonshot/fast", "moonshot/quality"}
    assert baseline == "moonshot/fast"


def test_claude_code_repeatable_models_use_same_detected_subscription(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_MODELS", "opus,sonnet")
    monkeypatch.setattr("volante.bootstrap._detect_cli", lambda binary: True)

    registry, providers, baseline = build_providers_from_env(
        include_subscription=True
    )

    assert [model.id for model in registry.all()] == [
        "claude-code/opus",
        "claude-code/sonnet",
    ]
    assert set(providers) == {"claude-code/opus", "claude-code/sonnet"}
    assert baseline == "claude-code/opus"


def test_codex_repeatable_models_use_one_authenticated_cli(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_ENABLED", "1")
    monkeypatch.setenv("CODEX_TIER", "4")
    monkeypatch.setenv("CODEX_MODELS", "gpt-5-codex,gpt-5-codex-mini")
    monkeypatch.setattr("volante.providers.codex.codex_detected", lambda: True)

    registry, providers, baseline = build_providers_from_env(
        include_subscription=True
    )

    assert [model.id for model in registry.all()] == [
        "codex/gpt-5-codex",
        "codex/gpt-5-codex-mini",
    ]
    assert set(providers) == {
        "codex/gpt-5-codex",
        "codex/gpt-5-codex-mini",
    }
    assert baseline == "codex/gpt-5-codex"


def test_parallel_canonical_names_must_match_model_count(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODELS", "one,two")
    monkeypatch.setenv("ANTHROPIC_NAMES", "anthropic/only-one")

    with pytest.raises(RuntimeError, match="exactly 2"):
        build_providers_from_env()


def test_profile_for_inaccessible_model_fails_closed(
    monkeypatch, tmp_path: Path
) -> None:
    profiles = tmp_path / "quality.json"
    profiles.write_text(
        json.dumps({"unconfigured/model": {"overall_score": 0.9}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("VOLANTE_QUALITY_PROFILES_FILE", str(profiles))

    with pytest.raises(ValueError, match="unknown models"):
        build_providers_from_env()


def test_plural_anthropic_models_use_independent_hard_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    overrides = tmp_path / "overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "anthropic/claude-haiku": {
                    "tier": 2,
                    "cost_per_1k_in": 0.0008,
                    "cost_per_1k_out": 0.004,
                    "strengths": ["reasoning"],
                },
                "anthropic/claude-opus": {
                    "tier": 4,
                    "cost_per_1k_in": 0.015,
                    "cost_per_1k_out": 0.075,
                    "strengths": ["coding", "reasoning", "long_context"],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODELS", "claude-haiku,claude-opus")
    monkeypatch.setenv("VOLANTE_MODEL_OVERRIDES_FILE", str(overrides))

    registry, _, _ = build_providers_from_env()
    decision = Router(registry).route_decision(
        Task(
            id="analysis",
            description="deep analysis",
            type="analyze",
            mode="one_shot",
            difficulty="hard",
        )
    )

    assert registry.get("anthropic/claude-haiku").tier == 2
    assert registry.get("anthropic/claude-opus").tier == 4
    assert decision.selected_model_id == "anthropic/claude-opus"

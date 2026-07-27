from __future__ import annotations

import json
from pathlib import Path

import pytest

from volante.inventory import (
    InventoryConfigError,
    apply_model_overrides,
    load_model_overrides,
    load_model_overrides_file,
    load_quality_profiles,
    load_quality_profiles_file,
    model_list_from_env,
    parse_model_list,
    summarize_inventory,
)
from volante.registry import ModelQualityProfile
from volante.types import ModelInfo


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_parse_model_list_accepts_repeatable_comma_separated_values() -> None:
    assert parse_model_list(
        [" opus, sonnet ", "haiku,openrouter/meta-llama/llama-3.3:free"],
        setting_name="MODELS",
    ) == [
        "opus",
        "sonnet",
        "haiku",
        "openrouter/meta-llama/llama-3.3:free",
    ]


def test_parse_model_list_none_is_empty() -> None:
    assert parse_model_list(None) == []


@pytest.mark.parametrize("value", ["", " ", "opus,", ",opus", "opus,,sonnet"])
def test_parse_model_list_rejects_empty_entries(value: str) -> None:
    with pytest.raises(InventoryConfigError, match="empty model entry"):
        parse_model_list(value, setting_name="MODELS")


@pytest.mark.parametrize(
    "value",
    [
        "opus, opus",
        ["opus", "opus"],
        ["opus,sonnet", "sonnet"],
    ],
)
def test_parse_model_list_rejects_duplicates_after_trimming(
    value: str | list[str],
) -> None:
    with pytest.raises(InventoryConfigError, match="duplicate"):
        parse_model_list(value, setting_name="MODELS")


@pytest.mark.parametrize(
    "value",
    [
        "../opus",
        "/absolute",
        "model name",
        "model$secret",
        "model;command",
        "model\\name",
        "model\nname",
        "provider//model",
    ],
)
def test_parse_model_list_rejects_unsafe_entries(value: str) -> None:
    with pytest.raises(InventoryConfigError, match="unsafe"):
        parse_model_list(value, setting_name="MODELS")


def test_parse_model_list_rejects_non_string_repeated_entry() -> None:
    with pytest.raises(InventoryConfigError, match="must be strings"):
        parse_model_list(["opus", 1], setting_name="MODELS")  # type: ignore[list-item]


def test_model_list_from_env_prefers_plural_setting() -> None:
    env = {
        "ANTHROPIC_MODELS": "claude-opus,claude-sonnet",
        "ANTHROPIC_MODEL": "legacy",
    }
    assert model_list_from_env(env, "ANTHROPIC_MODELS", "ANTHROPIC_MODEL") == [
        "claude-opus",
        "claude-sonnet",
    ]


def test_model_list_from_env_uses_singular_fallback() -> None:
    assert model_list_from_env(
        {"OLLAMA_MODEL": "qwen2.5-coder:14b"},
        "OLLAMA_MODELS",
        "OLLAMA_MODEL",
    ) == ["qwen2.5-coder:14b"]


def test_model_list_from_env_uses_default_only_when_both_are_absent() -> None:
    assert model_list_from_env({}, "CODEX_MODELS", "CODEX_MODEL", default="gpt-5.3-codex") == [
        "gpt-5.3-codex"
    ]


def test_present_empty_plural_does_not_silently_use_singular() -> None:
    env = {"CLAUDE_CODE_MODELS": "", "CLAUDE_CODE_MODEL": "opus"}
    with pytest.raises(InventoryConfigError, match="empty model entry"):
        model_list_from_env(env, "CLAUDE_CODE_MODELS", "CLAUDE_CODE_MODEL")


def test_load_quality_profiles_is_optional() -> None:
    assert load_quality_profiles({}) == {}


def test_load_quality_profiles_reads_all_supported_metadata(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "profiles.json",
        {
            "anthropic/claude-opus": {
                "task_scores": {
                    "research": 0.91,
                    "code": 0.88,
                    "write": 0.94,
                    "analyze": 0.93,
                },
                "overall_score": 0.92,
                "reliability_score": 0.97,
                "is_local": False,
                "source": "user-eval-2026-07",
                "confidence": 0.85,
            },
            "ollama/qwen2.5-coder:14b": {
                "task_scores": {"code": 0.73},
                "is_local": True,
            },
        },
    )

    profiles = load_quality_profiles({"VOLANTE_QUALITY_PROFILES_FILE": str(path)})

    assert profiles == {
        "anthropic/claude-opus": ModelQualityProfile(
            task_scores={
                "research": 0.91,
                "code": 0.88,
                "write": 0.94,
                "analyze": 0.93,
            },
            overall_score=0.92,
            reliability_score=0.97,
            is_local=False,
            source="user-eval-2026-07",
            confidence=0.85,
        ),
        "ollama/qwen2.5-coder:14b": ModelQualityProfile(
            task_scores={"code": 0.73},
            is_local=True,
        ),
    }


def test_empty_optional_inventory_file_paths_are_treated_as_unset() -> None:
    assert load_quality_profiles({"VOLANTE_QUALITY_PROFILES_FILE": " "}) == {}
    assert load_model_overrides({"VOLANTE_MODEL_OVERRIDES_FILE": ""}) == {}


def test_load_quality_profiles_fails_clearly_when_file_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(InventoryConfigError, match="does not exist"):
        load_quality_profiles_file(tmp_path / "missing.json")


@pytest.mark.parametrize("contents", ["{", "[]", '"not an object"', "NaN"])
def test_load_quality_profiles_rejects_malformed_or_wrong_root(
    tmp_path: Path,
    contents: str,
) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(InventoryConfigError):
        load_quality_profiles_file(path)


def test_load_quality_profiles_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(
        '{"provider/model":{"source":"a","source":"b"}}',
        encoding="utf-8",
    )
    with pytest.raises(InventoryConfigError, match="strict JSON"):
        load_quality_profiles_file(path)


@pytest.mark.parametrize(
    "profile",
    [
        [],
        {"task_scores": []},
        {"task_scores": {"unknown": 0.8}},
        {"overall_score": True},
        {"overall_score": 1.1},
        {"reliability_score": -0.1},
        {"is_local": "yes"},
        {"source": ""},
        {"confidence": 2},
    ],
)
def test_load_quality_profiles_rejects_invalid_profile_schema(
    tmp_path: Path,
    profile: object,
) -> None:
    path = _write_json(tmp_path / "profiles.json", {"provider/model": profile})
    with pytest.raises(InventoryConfigError):
        load_quality_profiles_file(path)


@pytest.mark.parametrize(
    "secret_field",
    ["api_key", "token", "password", "client_secret", "authorization"],
)
def test_quality_profiles_never_accept_secret_fields(
    tmp_path: Path,
    secret_field: str,
) -> None:
    path = _write_json(
        tmp_path / "profiles.json",
        {"provider/model": {"overall_score": 0.8, secret_field: "do-not-store"}},
    )
    with pytest.raises(InventoryConfigError, match="credentials must never"):
        load_quality_profiles_file(path)


def test_load_quality_profiles_rejects_unsafe_model_id(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "profiles.json",
        {"../unsafe": {"overall_score": 0.8}},
    )
    with pytest.raises(InventoryConfigError, match="unsafe"):
        load_quality_profiles_file(path)


def _inventory_model(model_id: str) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths={"reasoning"},
        context_window=8_192,
        max_output_tokens=1_024,
        supports_tools=False,
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        tier=2,
    )


def test_model_overrides_refine_each_plural_model_independently(
    tmp_path: Path,
) -> None:
    path = _write_json(
        tmp_path / "overrides.json",
        {
            "anthropic/haiku": {
                "strengths": ["reasoning", "cheap_fast"],
                "context_window": 200_000,
                "max_output_tokens": 4_096,
                "supports_tools": True,
                "cost_per_1k_in": 0.0008,
                "cost_per_1k_out": 0.004,
                "tier": 2,
            },
            "anthropic/opus": {
                "strengths": ["coding", "reasoning", "long_context"],
                "cost_per_1k_in": 0.015,
                "cost_per_1k_out": 0.075,
                "tier": 4,
            },
        },
    )
    overrides = load_model_overrides({"VOLANTE_MODEL_OVERRIDES_FILE": str(path)})
    models = apply_model_overrides(
        [_inventory_model("anthropic/haiku"), _inventory_model("anthropic/opus")],
        overrides,
    )

    by_id = {model.id: model for model in models}
    assert by_id["anthropic/haiku"].tier == 2
    assert by_id["anthropic/haiku"].cost_per_1k_out == 0.004
    assert by_id["anthropic/opus"].tier == 4
    assert by_id["anthropic/opus"].strengths == {
        "coding",
        "reasoning",
        "long_context",
    }


def test_model_overrides_reject_unknown_models_and_secret_fields(
    tmp_path: Path,
) -> None:
    secret_path = _write_json(
        tmp_path / "secret.json",
        {"provider/model": {"api_key": "must-not-be-accepted"}},
    )
    with pytest.raises(InventoryConfigError, match="cannot be overridden"):
        load_model_overrides_file(secret_path)

    with pytest.raises(InventoryConfigError, match="unknown models"):
        apply_model_overrides(
            [_inventory_model("configured/model")],
            {"typo/model": {"tier": 4}},
        )


@pytest.mark.parametrize(
    "override",
    [
        {"strengths": []},
        {"context_window": 0},
        {"max_output_tokens": -1},
        {"supports_tools": "yes"},
        {"cost_per_1k_in": -0.01},
        {"cost_per_1k_out": float("inf")},
        {"tier": 5},
    ],
)
def test_model_overrides_reject_invalid_hard_or_economic_metadata(
    tmp_path: Path,
    override: object,
) -> None:
    path = _write_json(
        tmp_path / "invalid-overrides.json",
        {"provider/model": override},
    )

    with pytest.raises(InventoryConfigError):
        load_model_overrides_file(path)


def test_model_overrides_reject_invalid_cross_field_limits() -> None:
    with pytest.raises(InventoryConfigError, match="max_output_tokens"):
        apply_model_overrides(
            [_inventory_model("provider/model")],
            {
                "provider/model": {
                    "context_window": 512,
                    "max_output_tokens": 1_024,
                }
            },
        )


def test_summary_defines_available_as_configured_or_locally_detected() -> None:
    summary = summarize_inventory(
        configured=["anthropic/opus,ollama/qwen"],
        detected=["codex/default", "anthropic/opus"],
    )

    assert summary.configured_model_ids == ("anthropic/opus", "ollama/qwen")
    assert summary.detected_model_ids == ("codex/default", "anthropic/opus")
    assert summary.available_model_ids == (
        "anthropic/opus",
        "ollama/qwen",
        "codex/default",
    )
    assert "configured or locally detected" in summary.availability_note
    assert "did not contact providers" in summary.availability_note
    assert summary.network_checked is False
    assert summary.entitlements_checked is False

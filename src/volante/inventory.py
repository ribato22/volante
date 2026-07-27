"""Offline helpers for declaring Volante's user-owned model inventory.

This module deliberately does not import provider SDKs, contact provider APIs, or
infer account entitlements.  It only parses explicit configuration, loads optional
quality evidence, and reports what was configured or detected locally.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from volante.registry import ModelQualityProfile, Registry
from volante.types import TASK_TYPES, ModelInfo

QUALITY_PROFILES_ENV = "VOLANTE_QUALITY_PROFILES_FILE"
MODEL_OVERRIDES_ENV = "VOLANTE_MODEL_OVERRIDES_FILE"
MAX_MODEL_NAME_LENGTH = 256
MAX_PROFILE_FILE_BYTES = 1_048_576

_MODEL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]*$")
_ALLOWED_PROFILE_FIELDS = frozenset(
    {
        "task_scores",
        "overall_score",
        "reliability_score",
        "is_local",
        "source",
        "confidence",
    }
)
_ALLOWED_OVERRIDE_FIELDS = frozenset(
    {
        "strengths",
        "context_window",
        "max_output_tokens",
        "supports_tools",
        "cost_per_1k_in",
        "cost_per_1k_out",
        "tier",
    }
)
_AVAILABILITY_NOTE = (
    "available means explicitly configured or locally detected; Volante did not contact "
    "providers or verify account access, model entitlement, quota, or service availability"
)


class InventoryConfigError(ValueError):
    """A local inventory setting or quality-profile file is invalid."""


def _validate_model_name(value: str, *, setting_name: str) -> str:
    """Validate a wire model name or canonical model id without shell/path ambiguity."""

    if not value:
        raise InventoryConfigError(f"{setting_name} contains an empty model entry")
    if len(value) > MAX_MODEL_NAME_LENGTH:
        raise InventoryConfigError(
            f"{setting_name} contains a model entry longer than {MAX_MODEL_NAME_LENGTH} characters"
        )

    # Slash-separated names cover canonical ids and provider wire names such as
    # ``openrouter/meta-llama/llama-3.3-70b:free``.  Each segment must start with
    # an alphanumeric character, ruling out absolute paths and traversal segments.
    segments = value.split("/")
    if any(_MODEL_SEGMENT.fullmatch(segment) is None for segment in segments):
        raise InventoryConfigError(
            f"{setting_name} contains an unsafe model entry; use only slash-separated "
            "alphanumeric names with '.', '_', ':', '@', '+', or '-'"
        )
    return value


def parse_model_list(
    values: str | Iterable[str] | None,
    *,
    setting_name: str = "model list",
) -> list[str]:
    """Parse repeatable, comma-separated model settings.

    ``values`` may be one environment-style string or repeated strings from a CLI
    option. Whitespace around entries is removed. Empty or duplicate entries are
    rejected instead of being silently repaired, so configuration mistakes cannot
    hide part of the user's inventory.
    """

    if values is None:
        return []
    raw_values: Iterable[str]
    if isinstance(values, str):
        raw_values = (values,)
    else:
        raw_values = values

    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            raise InventoryConfigError(
                f"{setting_name} entries must be strings, got {type(raw_value).__name__}"
            )
        for raw_entry in raw_value.split(","):
            entry = raw_entry.strip()
            _validate_model_name(entry, setting_name=setting_name)
            if entry in seen:
                raise InventoryConfigError(
                    f"{setting_name} contains a duplicate model entry after trimming"
                )
            seen.add(entry)
            result.append(entry)
    return result


def model_list_from_env(
    env: Mapping[str, str],
    plural_key: str,
    singular_key: str,
    *,
    default: str | Iterable[str] | None = None,
) -> list[str]:
    """Read a plural model list with a backward-compatible singular fallback.

    The plural setting wins whenever it is present. A present-but-empty plural
    setting is invalid; it does not silently fall through to the singular setting.
    This contract is suitable for ``ANTHROPIC_MODELS``/``ANTHROPIC_MODEL`` and the
    equivalent Moonshot, Ollama, Claude Code, and Codex settings.
    """

    if plural_key in env:
        return parse_model_list(env[plural_key], setting_name=plural_key)
    if singular_key in env:
        return parse_model_list(env[singular_key], setting_name=singular_key)
    return parse_model_list(default, setting_name=f"default for {plural_key}")


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # Do not echo an arbitrary key: a quality file must never cause an
            # accidentally pasted credential to be copied into logs.
            raise _DuplicateJsonKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite_json(_constant: str) -> None:
    raise ValueError("non-finite numbers are not valid quality scores")


def _read_json_object(path: Path, *, document_name: str = "quality profiles") -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise InventoryConfigError(f"{document_name} file does not exist: {path}") from exc
    except OSError as exc:
        raise InventoryConfigError(f"cannot inspect {document_name} file: {path}") from exc
    if not path.is_file():
        raise InventoryConfigError(f"{document_name} path is not a regular file: {path}")
    if size > MAX_PROFILE_FILE_BYTES:
        raise InventoryConfigError(
            f"{document_name} file exceeds the {MAX_PROFILE_FILE_BYTES}-byte limit"
        )

    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InventoryConfigError(f"cannot read {document_name} file as UTF-8: {path}") from exc
    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_non_finite_json,
        )
    except (_DuplicateJsonKeyError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise InventoryConfigError(
            f"{document_name} file is not valid strict JSON: {path}"
        ) from exc
    if not isinstance(decoded, dict):
        raise InventoryConfigError(
            "quality profiles JSON root must be an object keyed by canonical model_id"
        )
    return cast(dict[str, Any], decoded)


def _profile_from_json(raw: object, *, index: int) -> ModelQualityProfile:
    if not isinstance(raw, dict):
        raise InventoryConfigError(f"quality profile #{index} must be a JSON object")
    fields = cast(dict[str, object], raw)
    if set(fields) - _ALLOWED_PROFILE_FIELDS:
        raise InventoryConfigError(
            f"quality profile #{index} contains unsupported fields; only quality "
            "metadata is accepted and credentials must never be stored in this file"
        )

    task_scores_raw = fields.get("task_scores", {})
    if not isinstance(task_scores_raw, dict):
        raise InventoryConfigError(f"quality profile #{index} task_scores must be a JSON object")
    task_scores = cast(dict[str, object], task_scores_raw)
    if set(task_scores) - TASK_TYPES:
        raise InventoryConfigError(
            f"quality profile #{index} has an unsupported task_scores key; "
            f"expected a subset of {sorted(TASK_TYPES)}"
        )

    is_local = fields.get("is_local")
    if is_local is not None and not isinstance(is_local, bool):
        raise InventoryConfigError(
            f"quality profile #{index} is_local must be true, false, or null"
        )
    source = fields.get("source", "user-configured")
    if not isinstance(source, str):
        raise InventoryConfigError(f"quality profile #{index} source must be a string")
    if len(source) > 256 or not source.isprintable():
        raise InventoryConfigError(
            f"quality profile #{index} source must be printable and at most 256 characters"
        )

    try:
        return ModelQualityProfile(
            task_scores=cast(dict[str, float], task_scores),
            overall_score=cast(float | None, fields.get("overall_score")),
            reliability_score=cast(float | None, fields.get("reliability_score")),
            is_local=is_local,
            source=source,
            confidence=cast(float, fields.get("confidence", 1.0)),
        )
    except (TypeError, ValueError) as exc:
        # ModelQualityProfile owns the numeric 0..1 invariants. Its error values
        # are scores or schema labels, never arbitrary JSON field values.
        raise InventoryConfigError(f"quality profile #{index} is invalid: {exc}") from exc


def load_quality_profiles_file(path: str | os.PathLike[str]) -> dict[str, ModelQualityProfile]:
    """Load strict quality evidence keyed by canonical model id from ``path``.

    Unknown fields are rejected. Consequently this file cannot double as provider
    configuration and cannot accept API keys, tokens, passwords, or other secrets.
    """

    decoded = _read_json_object(Path(path).expanduser())
    profiles: dict[str, ModelQualityProfile] = {}
    for index, (model_id, raw_profile) in enumerate(decoded.items(), start=1):
        _validate_model_name(model_id, setting_name="quality profile model_id")
        profiles[model_id] = _profile_from_json(raw_profile, index=index)
    return profiles


def load_quality_profiles(
    env: Mapping[str, str] | None = None,
) -> dict[str, ModelQualityProfile]:
    """Load ``VOLANTE_QUALITY_PROFILES_FILE`` when configured, otherwise return ``{}``."""

    source = os.environ if env is None else env
    if QUALITY_PROFILES_ENV not in source:
        return {}
    raw_path = source[QUALITY_PROFILES_ENV].strip()
    if not raw_path:
        return {}
    return load_quality_profiles_file(raw_path)


def load_model_overrides_file(
    path: str | os.PathLike[str],
) -> dict[str, dict[str, object]]:
    """Load strict per-model hard capability and economic metadata overrides.

    Provider identity, wire model, billing mode, and credentials are intentionally
    not accepted: this file can refine a configured backend but cannot create or
    redirect one.
    """

    decoded = _read_json_object(Path(path).expanduser(), document_name="model overrides")
    overrides: dict[str, dict[str, object]] = {}
    for index, (model_id, raw) in enumerate(decoded.items(), start=1):
        _validate_model_name(model_id, setting_name="model override model_id")
        if not isinstance(raw, dict):
            raise InventoryConfigError(f"model override #{index} must be a JSON object")
        fields = cast(dict[str, object], raw)
        unknown = set(fields) - _ALLOWED_OVERRIDE_FIELDS
        if unknown:
            raise InventoryConfigError(
                f"model override #{index} contains unsupported fields; provider, "
                "billing, wire configuration, and credentials cannot be overridden"
            )
        normalized: dict[str, object] = {}
        if "strengths" in fields:
            strengths = fields["strengths"]
            if (
                not isinstance(strengths, list)
                or not strengths
                or any(
                    not isinstance(value, str) or not value.strip() or value != value.strip()
                    for value in strengths
                )
            ):
                raise InventoryConfigError(
                    f"model override #{index} strengths must be a non-empty string array"
                )
            if len(set(strengths)) != len(strengths):
                raise InventoryConfigError(f"model override #{index} strengths contains duplicates")
            normalized["strengths"] = set(strengths)
        for field in ("context_window", "max_output_tokens", "tier"):
            if field not in fields:
                continue
            value = fields[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise InventoryConfigError(f"model override #{index} {field} must be an integer")
            if field in {"context_window", "max_output_tokens"} and value < 1:
                raise InventoryConfigError(f"model override #{index} {field} must be positive")
            if field == "tier" and not 1 <= value <= 4:
                raise InventoryConfigError(f"model override #{index} tier must be between 1 and 4")
            normalized[field] = value
        if "supports_tools" in fields:
            value = fields["supports_tools"]
            if not isinstance(value, bool):
                raise InventoryConfigError(
                    f"model override #{index} supports_tools must be boolean"
                )
            normalized["supports_tools"] = value
        for field in ("cost_per_1k_in", "cost_per_1k_out"):
            if field not in fields:
                continue
            value = fields[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise InventoryConfigError(
                    f"model override #{index} {field} must be a finite number"
                )
            normalized[field] = float(value)
        overrides[model_id] = normalized
    return overrides


def load_model_overrides(
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    source = os.environ if env is None else env
    if MODEL_OVERRIDES_ENV not in source:
        return {}
    raw_path = source[MODEL_OVERRIDES_ENV].strip()
    if not raw_path:
        return {}
    return load_model_overrides_file(raw_path)


def apply_model_overrides(
    models: Iterable[ModelInfo],
    overrides: Mapping[str, Mapping[str, object]],
) -> list[ModelInfo]:
    """Apply validated overrides and reject inaccessible/typoed model ids."""

    materialized = list(models)
    known = {model.id for model in materialized}
    unknown = set(overrides) - known
    if unknown:
        raise InventoryConfigError(f"model overrides reference unknown models: {sorted(unknown)}")
    result = [
        replace(
            model,
            strengths=cast(
                set[str],
                overrides.get(model.id, {}).get("strengths", model.strengths),
            ),
            context_window=cast(
                int,
                overrides.get(model.id, {}).get("context_window", model.context_window),
            ),
            max_output_tokens=cast(
                int,
                overrides.get(model.id, {}).get("max_output_tokens", model.max_output_tokens),
            ),
            supports_tools=cast(
                bool,
                overrides.get(model.id, {}).get("supports_tools", model.supports_tools),
            ),
            cost_per_1k_in=cast(
                float,
                overrides.get(model.id, {}).get("cost_per_1k_in", model.cost_per_1k_in),
            ),
            cost_per_1k_out=cast(
                float,
                overrides.get(model.id, {}).get("cost_per_1k_out", model.cost_per_1k_out),
            ),
            tier=cast(
                int,
                overrides.get(model.id, {}).get("tier", model.tier),
            ),
        )
        for model in materialized
    ]
    try:
        return Registry(result).all()
    except ValueError as exc:
        raise InventoryConfigError(f"model overrides produce invalid metadata: {exc}") from exc


@dataclass(frozen=True)
class InventorySummary:
    """An offline snapshot of configured and locally detected model identifiers."""

    configured_model_ids: tuple[str, ...]
    detected_model_ids: tuple[str, ...]
    available_model_ids: tuple[str, ...]
    availability_note: str = _AVAILABILITY_NOTE
    network_checked: bool = False
    entitlements_checked: bool = False


def summarize_inventory(
    *,
    configured: str | Iterable[str] | None = None,
    detected: str | Iterable[str] | None = None,
) -> InventorySummary:
    """Build an honest offline inventory summary.

    ``detected`` means a local executable or model record was observed by the
    caller. It does not imply login state, remote access, quota, or entitlement.
    """

    configured_ids = parse_model_list(configured, setting_name="configured models")
    detected_ids = parse_model_list(detected, setting_name="detected models")
    available = list(configured_ids)
    seen = set(configured_ids)
    for model_id in detected_ids:
        if model_id not in seen:
            available.append(model_id)
            seen.add(model_id)
    return InventorySummary(
        configured_model_ids=tuple(configured_ids),
        detected_model_ids=tuple(detected_ids),
        available_model_ids=tuple(available),
    )

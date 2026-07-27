# src/volante/registry.py
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from volante.types import TASK_TYPES, ModelInfo

_BILLING_MODES = frozenset({"card", "plan_credit", "plan_included"})


def _validate_model(model: ModelInfo) -> None:
    """Reject invalid inventory records before they can influence routing."""
    if not isinstance(model.id, str) or not model.id.strip():
        raise ValueError("model id must be a non-empty string")
    if not isinstance(model.provider, str) or not model.provider.strip():
        raise ValueError(f"model {model.id!r} provider must be a non-empty string")
    if (
        not isinstance(model.strengths, set)
        or not model.strengths
        or any(not isinstance(item, str) or not item.strip() for item in model.strengths)
    ):
        raise ValueError(f"model {model.id!r} strengths must be a non-empty set of strings")
    for name, value in (
        ("context_window", model.context_window),
        ("max_output_tokens", model.max_output_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"model {model.id!r} {name} must be a positive integer")
    if model.max_output_tokens >= model.context_window:
        raise ValueError(
            f"model {model.id!r} max_output_tokens must be smaller than context_window"
        )
    if isinstance(model.tier, bool) or not isinstance(model.tier, int) or not 1 <= model.tier <= 4:
        raise ValueError(f"model {model.id!r} tier must be an integer from 1 to 4")
    if model.billing not in _BILLING_MODES:
        raise ValueError(
            f"model {model.id!r} has invalid billing {model.billing!r}; "
            f"expected one of {sorted(_BILLING_MODES)}"
        )
    if not isinstance(model.supports_tools, bool):
        raise ValueError(f"model {model.id!r} supports_tools must be boolean")
    for name, cost in (
        ("cost_per_1k_in", model.cost_per_1k_in),
        ("cost_per_1k_out", model.cost_per_1k_out),
    ):
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(float(cost))
            or cost < 0
        ):
            raise ValueError(f"model {model.id!r} {name} must be a finite non-negative number")


def _validate_unit_score(name: str, value: float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value!r}")


@dataclass(frozen=True)
class ModelQualityProfile:
    """Optional calibrated evidence used by the router.

    ``ModelInfo.tier`` remains the coarse, always-available quality prior.
    Profiles let callers replace that coarse prior with benchmark- or
    user-derived scores without baking vendor claims into Volante. All scores use
    a provider-independent 0..1 scale. ``source`` should identify the evidence,
    for example ``"user-eval-2026-07"``.
    """

    task_scores: Mapping[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    reliability_score: float | None = None
    is_local: bool | None = None
    source: str = "user-configured"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_scores", dict(self.task_scores))
        unknown = set(self.task_scores) - TASK_TYPES
        if unknown:
            raise ValueError(
                f"unknown task quality profile keys: {sorted(unknown)}; "
                f"expected a subset of {sorted(TASK_TYPES)}"
            )
        for task_type, score in self.task_scores.items():
            _validate_unit_score(f"task_scores[{task_type!r}]", score)
        _validate_unit_score("overall_score", self.overall_score)
        _validate_unit_score("reliability_score", self.reliability_score)
        _validate_unit_score("confidence", self.confidence)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("profile source must be a non-empty string")


class Registry:
    def __init__(
        self,
        models: list[ModelInfo],
        *,
        quality_profiles: Mapping[str, ModelQualityProfile] | None = None,
    ) -> None:
        self._models: list[ModelInfo] = list(models)
        for model in self._models:
            _validate_model(model)
        ids = [m.id for m in self._models]
        duplicates = sorted(
            model_id for model_id, count in Counter(ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"duplicate model ids: {duplicates}")
        self._by_id: dict[str, ModelInfo] = {m.id: m for m in self._models}
        self._quality_profiles = dict(quality_profiles or {})
        unknown_profiles = set(self._quality_profiles) - set(self._by_id)
        if unknown_profiles:
            raise ValueError(
                "quality profiles reference unknown models: "
                f"{sorted(unknown_profiles)}"
            )

    def all(self) -> list[ModelInfo]:
        return list(self._models)

    def get(self, model_id: str) -> ModelInfo:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise ValueError(f"unknown model_id: {model_id!r}") from exc

    def quality_profile(self, model_id: str) -> ModelQualityProfile | None:
        """Return calibrated quality evidence for ``model_id``, if configured."""

        # Match ``get``'s fail-fast contract instead of silently accepting a typo.
        self.get(model_id)
        return self._quality_profiles.get(model_id)

    def matching(self, strengths: set[str], needs_tools: bool = False) -> list[ModelInfo]:
        result: list[ModelInfo] = []
        for m in self._models:
            if not strengths.issubset(m.strengths):
                continue
            if needs_tools and not m.supports_tools:
                continue
            result.append(m)
        return result


def default_models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id="anthropic/claude-opus-4-8",
            provider="anthropic",
            strengths={"coding", "reasoning"},
            context_window=200_000,
            max_output_tokens=8_192,
            supports_tools=True,
            cost_per_1k_in=0.015,
            cost_per_1k_out=0.075,
            tier=4,
            billing="card",
        ),
        ModelInfo(
            id="kimi/kimi-k2",
            provider="openai_compat",
            # Catch-all {coding, reasoning}: router bisa mengarahkan SEMUA jenis task
            # (code/research/write/analyze) ke model ini bila ia satu-satunya provider.
            strengths={"coding", "reasoning"},
            context_window=128_000,
            max_output_tokens=4_096,
            supports_tools=True,
            cost_per_1k_in=0.0012,
            cost_per_1k_out=0.0012,
            tier=3,
            billing="card",
        ),
        ModelInfo(
            id="ollama/llama3.2",
            provider="openai_compat",
            # Catch-all agar konfigurasi Ollama-saja (gratis) bisa menjalankan orkestrasi
            # penuh. supports_tools=False -> task agentic tak dirutekan ke sini (jujur:
            # llama3.2 default tak selalu patuh tool-calling).
            strengths={"coding", "reasoning"},
            context_window=8_192,
            max_output_tokens=2_048,
            supports_tools=False,
            cost_per_1k_in=0.0,
            cost_per_1k_out=0.0,
            tier=1,
            billing="card",
        ),
    ]


def default_registry() -> Registry:
    return Registry(default_models())

# src/orchestrator/registry.py
from __future__ import annotations

from orchestrator.types import ModelInfo


class Registry:
    def __init__(self, models: list[ModelInfo]) -> None:
        self._models: list[ModelInfo] = list(models)
        self._by_id: dict[str, ModelInfo] = {m.id: m for m in self._models}

    def all(self) -> list[ModelInfo]:
        return list(self._models)

    def get(self, model_id: str) -> ModelInfo:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise ValueError(f"unknown model_id: {model_id!r}") from exc

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
        ),
    ]


def default_registry() -> Registry:
    return Registry(default_models())

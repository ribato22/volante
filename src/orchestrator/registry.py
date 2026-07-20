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

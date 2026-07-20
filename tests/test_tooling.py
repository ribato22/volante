# tests/test_tooling.py
from __future__ import annotations

import asyncio
import importlib


def test_runtime_sdks_importable() -> None:
    # Membuktikan dependency runtime (anthropic, openai) terpasang oleh `uv sync`.
    importlib.import_module("anthropic")
    importlib.import_module("openai")


def test_orchestrator_version() -> None:
    mod = importlib.import_module("orchestrator")
    assert mod.__version__ == "0.1.0"


async def test_asyncio_auto_mode_runs_coroutine_tests() -> None:
    # Tanpa @pytest.mark.asyncio: hanya jalan bila asyncio_mode="auto" aktif.
    await asyncio.sleep(0)
    assert True

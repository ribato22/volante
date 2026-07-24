# tests/test_tooling.py
from __future__ import annotations

import asyncio
import importlib
import re


def test_runtime_sdks_importable() -> None:
    # Membuktikan dependency runtime (anthropic, openai) terpasang oleh `uv sync`.
    importlib.import_module("anthropic")
    importlib.import_module("openai")


def test_baton_version() -> None:
    mod = importlib.import_module("baton")
    # Valid semver string; not pinned to a specific value so bumps don't churn this.
    assert re.fullmatch(r"\d+\.\d+\.\d+([.\-+].*)?", mod.__version__)


async def test_asyncio_auto_mode_runs_coroutine_tests() -> None:
    # Tanpa @pytest.mark.asyncio: hanya jalan bila asyncio_mode="auto" aktif.
    await asyncio.sleep(0)
    assert True

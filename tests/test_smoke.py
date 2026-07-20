# tests/test_smoke.py
from __future__ import annotations

import importlib


def test_import_orchestrator_package() -> None:
    mod = importlib.import_module("orchestrator")
    assert mod.__version__ == "0.1.0"


def test_import_providers_subpackage() -> None:
    # RED sampai src/orchestrator/providers/__init__.py dibuat.
    importlib.import_module("orchestrator.providers")

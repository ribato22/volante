# tests/test_smoke.py
from __future__ import annotations

import importlib
import re


def test_import_volante_package() -> None:
    mod = importlib.import_module("volante")
    # A valid, PEP 440-ish semver string (not a specific pinned value, so version
    # bumps don't churn this smoke test).
    assert re.fullmatch(r"\d+\.\d+\.\d+([.\-+].*)?", mod.__version__)


def test_import_providers_subpackage() -> None:
    # RED sampai src/volante/providers/__init__.py dibuat.
    importlib.import_module("volante.providers")


def test_public_api_surface() -> None:
    # Library users should be able to `import volante` and use the top-level names
    # instead of reaching into submodules for the common cases.
    volante = importlib.import_module("volante")
    expected = {
        "LLMProvider",
        "ModelInfo",
        "ProviderError",
        "Registry",
        "Router",
        "RunResult",
        "Runtime",
        "Task",
        "__version__",
        "build_providers_from_env",
        "default_models",
        "default_registry",
        "make_runtime_factory",
    }
    assert expected <= set(volante.__all__)
    for name in expected:
        assert hasattr(volante, name), f"volante.{name} missing despite being in __all__"

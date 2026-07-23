# tests/test_smoke.py
from __future__ import annotations

import importlib


def test_import_baton_package() -> None:
    mod = importlib.import_module("baton")
    assert mod.__version__ == "0.1.0"


def test_import_providers_subpackage() -> None:
    # RED sampai src/baton/providers/__init__.py dibuat.
    importlib.import_module("baton.providers")


def test_public_api_surface() -> None:
    # Library users should be able to `import baton` and use the top-level names
    # instead of reaching into submodules for the common cases.
    baton = importlib.import_module("baton")
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
    assert expected <= set(baton.__all__)
    for name in expected:
        assert hasattr(baton, name), f"baton.{name} missing despite being in __all__"

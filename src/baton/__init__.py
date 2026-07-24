# src/baton/__init__.py
"""Baton public library API.

Re-exports the names most library users need so they can `import baton` and use
`baton.Runtime`, `baton.build_providers_from_env`, etc. instead of reaching into
submodules directly. Submodules (`baton.providers.*`, `baton.tools.*`, ...) remain
importable as before for anything not re-exported here (e.g. `FakeProvider`, used
by tests/examples for a fully offline runtime).
"""

from __future__ import annotations

from baton.bootstrap import build_providers_from_env, make_runtime_factory
from baton.providers.base import LLMProvider, ProviderError
from baton.registry import Registry, default_models, default_registry
from baton.router import Router
from baton.runtime import Runtime
from baton.types import ModelInfo, RunResult, Task

__version__ = "0.2.0"

__all__ = [
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
]

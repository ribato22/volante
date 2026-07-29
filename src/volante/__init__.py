# src/volante/__init__.py
"""Volante public library API.

Re-exports the names most library users need so they can `import volante` and use
`volante.Runtime`, `volante.build_providers_from_env`, etc. instead of reaching into
submodules directly. Submodules (`volante.providers.*`, `volante.tools.*`, ...) remain
importable as before for anything not re-exported here (e.g. `FakeProvider`, used
by tests/examples for a fully offline runtime).
"""

from __future__ import annotations

from volante.bootstrap import (
    NoProvidersConfiguredError,
    build_providers_from_env,
    make_runtime_factory,
    make_verified_runtime_factory,
)
from volante.compat import apply_legacy_env
from volante.inventory import (
    InventoryConfigError,
    InventorySummary,
    apply_model_overrides,
    load_model_overrides,
    load_model_overrides_file,
    load_quality_profiles,
    load_quality_profiles_file,
    parse_model_list,
    summarize_inventory,
)
from volante.providers.base import (
    IncompleteOutputError,
    LLMProvider,
    ProviderError,
    ensure_complete_response,
)
from volante.registry import (
    ModelQualityProfile,
    Registry,
    default_models,
    default_registry,
)
from volante.router import (
    InvalidRoutingRequest,
    NoEligibleModelError,
    Router,
    RoutingCandidate,
    RoutingDecision,
    ScoreComponent,
)
from volante.runtime import Runtime
from volante.types import InvalidGoalError, ModelInfo, RunResult, Task, validate_goal

__version__ = "0.3.2"

# Honor configurations written against the pre-rename BATON_* names. Safe to run here:
# no module reads the environment at import time, so nothing has consulted a setting yet.
apply_legacy_env()

__all__ = [
    "InventoryConfigError",
    "InventorySummary",
    "IncompleteOutputError",
    "InvalidGoalError",
    "InvalidRoutingRequest",
    "LLMProvider",
    "ModelInfo",
    "ModelQualityProfile",
    "NoProvidersConfiguredError",
    "NoEligibleModelError",
    "ProviderError",
    "Registry",
    "Router",
    "RoutingCandidate",
    "RoutingDecision",
    "RunResult",
    "Runtime",
    "ScoreComponent",
    "Task",
    "__version__",
    "apply_model_overrides",
    "build_providers_from_env",
    "default_models",
    "default_registry",
    "ensure_complete_response",
    "load_quality_profiles",
    "load_quality_profiles_file",
    "load_model_overrides",
    "load_model_overrides_file",
    "make_runtime_factory",
    "make_verified_runtime_factory",
    "parse_model_list",
    "summarize_inventory",
    "validate_goal",
]

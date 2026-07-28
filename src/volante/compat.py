"""Transition shim for the Baton → Volante rename.

Every setting moved from ``BATON_*`` to ``VOLANTE_*``. Configurations written against
the old name keep working: the first time the package is imported, any legacy variable
whose new counterpart is unset is copied across and reported once on stderr.

This mutates ``os.environ`` at import, which a library should normally avoid. It is done
deliberately and narrowly: the settings are read from ~50 places across the engine, the
CLI, the MCP server, and the Web UI, and threading a merged mapping through all of them
would risk missing one and silently ignoring a user's configuration — a worse failure
than a contained, idempotent, allowlisted copy. The shim only ever fills values that are
NOT already set, never overwrites, and will be removed in a later release.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

_LEGACY_PREFIX = "BATON_"
_PREFIX = "VOLANTE_"

# Allowlisted so an unrelated BATON_* variable belonging to some other tool is never
# absorbed into Volante's configuration.
LEGACY_SETTINGS: frozenset[str] = frozenset(
    {
        "AGENTIC_MAX_ITERS",
        "CLI_AGENT_DEPTH",
        "CLI_AGENT_PASSTHROUGH",
        "FETCH_ALLOWLIST",
        "LOG",
        "MAX_SUBSCRIPTION_CALLS",
        "MODEL_OVERRIDES_FILE",
        "QUALITY_PROFILES_FILE",
        "READ_ROOT",
        "SANDBOX",
        "UI_ALLOWED_HOSTS",
        "UI_AUTH_TOKEN",
        "UI_HOST",
        "UI_MAX_CONCURRENT_RUNS",
        "UI_MAX_GOAL_CHARS",
        "UI_PORT",
        "USAGE_LOG",
    }
)


def apply_legacy_env(
    env: MutableMapping[str, str] | None = None, *, warn: bool = True
) -> list[str]:
    """Fill unset ``VOLANTE_*`` settings from their old ``BATON_*`` names.

    Returns the legacy names that were honored, so callers (and tests) can assert on the
    migration instead of inferring it. Never overwrites a value the user already set
    under the current name.
    """
    target = os.environ if env is None else env
    honored: list[str] = []
    for setting in sorted(LEGACY_SETTINGS):
        legacy = _LEGACY_PREFIX + setting
        current = _PREFIX + setting
        if legacy in target and current not in target:
            target[current] = target[legacy]
            honored.append(legacy)
    if honored and warn:
        print(
            "volante: using deprecated "
            + ", ".join(honored)
            + " — rename to "
            + ", ".join(_PREFIX + name.removeprefix(_LEGACY_PREFIX) for name in honored)
            + " (the BATON_* names are read for now and will be dropped).",
            file=sys.stderr,
        )
    return honored

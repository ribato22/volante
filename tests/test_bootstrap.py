from __future__ import annotations

from baton.bootstrap import (
    _all_openai_compat_from_env,
    _openai_compat_from_env,
    build_providers_from_env,
    make_runtime_factory,
)


def test_bootstrap_exposes_moved_symbols():
    # The four provider-wiring helpers now live in baton.bootstrap (package),
    # not eval.run. Pure helpers must behave exactly as before the move.
    assert _openai_compat_from_env({}) is None
    assert _all_openai_compat_from_env({}) == []
    assert callable(build_providers_from_env)
    assert callable(make_runtime_factory)

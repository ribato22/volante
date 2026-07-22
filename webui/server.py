from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any


def build_runtime_factory() -> tuple[Callable[[], Any], str]:
    """Return `(runtime_factory, mode)`. Uses real providers from the environment if
    any are configured (see .env.example); otherwise falls back to a FakeProvider demo
    so the UI runs and streams end-to-end without any API key."""
    try:
        from eval.run import build_providers_from_env, make_runtime_factory

        registry, providers, model_id = build_providers_from_env()
        return make_runtime_factory(registry, providers, model_id), f"live [{model_id}]"
    except Exception:  # noqa: BLE001 - no providers configured -> demo mode
        from webui._demo import demo_runtime_factory

        return demo_runtime_factory(), "demo [FakeProvider — set a provider env for live models]"


def main() -> None:
    import uvicorn

    from webui.app import create_app

    factory, mode = build_runtime_factory()
    host = os.environ.get("BATON_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("BATON_UI_PORT", "8000"))
    print(f"Baton Web UI — {mode}\n  open http://{host}:{port}")
    uvicorn.run(create_app(factory), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

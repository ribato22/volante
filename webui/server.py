from __future__ import annotations

import asyncio
import ipaddress
import os
from collections.abc import Callable, Mapping
from typing import Any

from volante.bootstrap import NoProvidersConfiguredError


def _is_loopback_host(host: str) -> bool:
    candidate = host.strip().lower()
    if candidate == "localhost":
        return True
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _webui_settings(
    env: Mapping[str, str],
) -> tuple[str, int, dict[str, Any]]:
    """Parse and validate the host-facing Web UI security settings."""
    host = env.get("VOLANTE_UI_HOST", "127.0.0.1").strip()
    port = int(env.get("VOLANTE_UI_PORT", "8000"))
    auth_token = env.get("VOLANTE_UI_AUTH_TOKEN", "").strip() or None
    if not _is_loopback_host(host) and auth_token is None:
        raise SystemExit(
            "Refusing to expose Volante Web UI beyond loopback without "
            "VOLANTE_UI_AUTH_TOKEN."
        )
    allowed_raw = env.get("VOLANTE_UI_ALLOWED_HOSTS", "")
    allowed_hosts = [item.strip() for item in allowed_raw.split(",") if item.strip()]
    app_options: dict[str, Any] = {
        "auth_token": auth_token,
        "max_goal_chars": int(env.get("VOLANTE_UI_MAX_GOAL_CHARS", "20000")),
        "max_concurrent_runs": int(
            env.get("VOLANTE_UI_MAX_CONCURRENT_RUNS", "2")
        ),
        "allowed_hosts": allowed_hosts or None,
    }
    return host, port, app_options


def build_runtime_factory() -> tuple[Callable[[], Any], str]:
    """Return `(runtime_factory, mode)`. Uses real providers from the environment if
    any are configured (see .env.example); otherwise falls back to a FakeProvider demo
    so the UI runs and streams end-to-end without any API key.

    Like the `volante` CLI, this opts into subscription CLI-agent providers
    (`include_subscription=True`) so `CLAUDE_CODE_ENABLED=1` / `CODEX_ENABLED=1` are
    honored in the browser too (they print their own interactive-quota warning on
    registration). Without any provider env set, it stays in the no-key demo."""
    try:
        from volante.bootstrap import (
            build_providers_from_env,
            make_verified_runtime_factory,
        )

        registry, providers, model_id = build_providers_from_env(include_subscription=True)
        factory = asyncio.run(
            make_verified_runtime_factory(registry, providers, model_id)
        )
        return factory, f"live [{model_id}]"
    except NoProvidersConfiguredError:
        from webui._demo import demo_runtime_factory

        return demo_runtime_factory(), "demo [FakeProvider — set a provider env for live models]"


def main() -> None:
    import uvicorn

    from webui.app import create_app

    factory, mode = build_runtime_factory()
    host, port, app_options = _webui_settings(os.environ)
    print(f"Volante Web UI — {mode}\n  open http://{host}:{port}")
    uvicorn.run(
        create_app(factory, **app_options),
        host=host,
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

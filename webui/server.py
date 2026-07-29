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


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _tls_settings(env: Mapping[str, str], *, exposed: bool) -> dict[str, Any]:
    """Resolve the transport for a non-loopback bind, or refuse to start.

    A bearer token is only as private as the channel carrying it. The browser sends
    it in an `Authorization` header on every run creation and every usage read, so on
    plain HTTP anyone on the path can lift it and replay it — submit paid goals, read
    the ledger. Accepting a token and then starting plain HTTP made the one control
    here authenticate over a channel that had none.

    Terminating TLS at a reverse proxy is the normal deployment and stays supported;
    refusing it outright would only push people to a worse workaround. What is no
    longer possible is arriving there by ACCIDENT — `VOLANTE_UI_TRUST_PROXY` is the
    operator stating that something in front is doing the job.
    """
    cert = env.get("VOLANTE_UI_TLS_CERT", "").strip()
    key = env.get("VOLANTE_UI_TLS_KEY", "").strip()
    if bool(cert) != bool(key):
        missing = "VOLANTE_UI_TLS_KEY" if cert else "VOLANTE_UI_TLS_CERT"
        raise SystemExit(f"TLS is half-configured for the Volante Web UI: {missing} is unset.")
    if cert and key:
        return {"ssl_certfile": cert, "ssl_keyfile": key}
    if exposed and not _truthy(env.get("VOLANTE_UI_TRUST_PROXY", "")):
        raise SystemExit(
            "Refusing to serve the Volante Web UI beyond loopback over plain HTTP: "
            "the bearer token would travel in cleartext and can be replayed to submit "
            "paid runs and read the usage ledger. Set VOLANTE_UI_TLS_CERT and "
            "VOLANTE_UI_TLS_KEY, or VOLANTE_UI_TRUST_PROXY=1 if a TLS-terminating "
            "reverse proxy sits in front."
        )
    return {}


def _webui_settings(
    env: Mapping[str, str],
) -> tuple[str, int, dict[str, Any], dict[str, Any]]:
    """Parse and validate the host-facing Web UI security settings."""
    host = env.get("VOLANTE_UI_HOST", "127.0.0.1").strip()
    port = int(env.get("VOLANTE_UI_PORT", "8000"))
    auth_token = env.get("VOLANTE_UI_AUTH_TOKEN", "").strip() or None
    exposed = not _is_loopback_host(host)
    if exposed and auth_token is None:
        raise SystemExit(
            "Refusing to expose Volante Web UI beyond loopback without "
            "VOLANTE_UI_AUTH_TOKEN."
        )
    tls = _tls_settings(env, exposed=exposed)
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
    return host, port, app_options, tls


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
    host, port, app_options, tls = _webui_settings(os.environ)
    # Print the scheme actually being served. The old line said http:// even when the
    # deployment was meant to be reached over TLS, which is the wrong thing to paste
    # into a browser and the wrong thing to believe about the connection.
    scheme = "https" if tls else "http"
    print(f"Volante Web UI — {mode}\n  open {scheme}://{host}:{port}")
    uvicorn.run(
        create_app(factory, **app_options),
        host=host,
        port=port,
        log_level="warning",
        **tls,
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import pytest
from webui.server import _is_loopback_host, _webui_settings, build_runtime_factory


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_recognized(host: str) -> None:
    assert _is_loopback_host(host) is True


def test_non_loopback_host_requires_auth_token() -> None:
    with pytest.raises(SystemExit, match="VOLANTE_UI_AUTH_TOKEN"):
        _webui_settings({"VOLANTE_UI_HOST": "0.0.0.0"})


def test_webui_security_settings_are_passed_through() -> None:
    host, port, options, tls = _webui_settings(
        {
            "VOLANTE_UI_HOST": "0.0.0.0",
            "VOLANTE_UI_PORT": "9000",
            "VOLANTE_UI_AUTH_TOKEN": "secret",
            "VOLANTE_UI_TRUST_PROXY": "1",  # see the transport tests below
            "VOLANTE_UI_MAX_GOAL_CHARS": "1234",
            "VOLANTE_UI_MAX_CONCURRENT_RUNS": "5",
            "VOLANTE_UI_ALLOWED_HOSTS": "volante.example, 10.0.0.2 ",
        }
    )
    assert (host, port) == ("0.0.0.0", 9000)
    assert options == {
        "auth_token": "secret",
        "max_goal_chars": 1234,
        "max_concurrent_runs": 5,
        "allowed_hosts": ["volante.example", "10.0.0.2"],
    }
    assert tls == {}


# --- the token is only as private as the transport carrying it -----------------
# A non-loopback bind was accepted the moment a token existed, and the server then
# started plain HTTP and printed an http:// URL. The browser sends that bearer in an
# Authorization header on every run creation and every usage read, so anyone on the
# path could lift it and replay it: submit paid goals, read the usage ledger. The
# token was the control, and it authenticated over a channel that had none.


def test_non_loopback_over_plain_http_is_refused() -> None:
    with pytest.raises(SystemExit, match="TLS"):
        _webui_settings({"VOLANTE_UI_HOST": "0.0.0.0", "VOLANTE_UI_AUTH_TOKEN": "s"})


def test_non_loopback_with_tls_is_allowed(tmp_path) -> None:
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("cert")
    key.write_text("key")

    _host, _port, _options, tls = _webui_settings(
        {
            "VOLANTE_UI_HOST": "0.0.0.0",
            "VOLANTE_UI_AUTH_TOKEN": "s",
            "VOLANTE_UI_TLS_CERT": str(cert),
            "VOLANTE_UI_TLS_KEY": str(key),
        }
    )

    assert tls == {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


def test_a_certificate_without_its_key_is_refused(tmp_path) -> None:
    with pytest.raises(SystemExit, match="VOLANTE_UI_TLS_KEY"):
        _webui_settings(
            {
                "VOLANTE_UI_HOST": "0.0.0.0",
                "VOLANTE_UI_AUTH_TOKEN": "s",
                "VOLANTE_UI_TLS_CERT": str(tmp_path / "c.pem"),
            }
        )


def test_a_trusted_proxy_may_terminate_tls_instead() -> None:
    # Putting this behind nginx or Caddy is the normal deployment. Refusing it would
    # push people to a worse workaround; requiring them to SAY so is the point.
    _host, _port, _options, tls = _webui_settings(
        {
            "VOLANTE_UI_HOST": "0.0.0.0",
            "VOLANTE_UI_AUTH_TOKEN": "s",
            "VOLANTE_UI_TRUST_PROXY": "1",
        }
    )

    assert tls == {}


def test_loopback_needs_neither_a_token_nor_tls() -> None:
    host, port, options, tls = _webui_settings({})

    assert host == "127.0.0.1"
    assert port == 8000
    assert options["auth_token"] is None
    assert tls == {}


def test_build_runtime_factory_uses_shared_verified_bootstrap(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "volante.bootstrap.build_providers_from_env",
        lambda **kwargs: ("registry", "providers", "baseline"),
    )

    async def _verified(registry, providers, model_id):
        assert (registry, providers, model_id) == (
            "registry",
            "providers",
            "baseline",
        )
        return sentinel

    monkeypatch.setattr("volante.bootstrap.make_verified_runtime_factory", _verified)
    factory, mode = build_runtime_factory()
    assert factory is sentinel
    assert mode == "live [baseline]"


def test_build_runtime_factory_does_not_hide_invalid_provider_config(monkeypatch) -> None:
    def _broken(**kwargs):
        raise RuntimeError("OPENAI_COMPAT_MODEL is missing")

    monkeypatch.setattr("volante.bootstrap.build_providers_from_env", _broken)
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_MODEL"):
        build_runtime_factory()

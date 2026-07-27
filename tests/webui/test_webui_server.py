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
    host, port, options = _webui_settings(
        {
            "VOLANTE_UI_HOST": "0.0.0.0",
            "VOLANTE_UI_PORT": "9000",
            "VOLANTE_UI_AUTH_TOKEN": "secret",
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

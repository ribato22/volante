from __future__ import annotations

from fastapi.testclient import TestClient
from webui._demo import demo_runtime_factory
from webui.app import create_app
from webui.server import build_runtime_factory


def _client() -> TestClient:
    return TestClient(create_app(demo_runtime_factory()))


def test_index_serves_html() -> None:
    r = _client().get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Baton" in r.text
    assert "EventSource" in r.text  # the page wires SSE
    # dynamic values are inserted via textContent only (no innerHTML) -> no XSS
    assert "innerHTML" not in r.text


def test_stream_endpoint_streams_events_to_result() -> None:
    r = _client().get("/stream?goal=hello")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert '"type": "worker"' in body
    assert '"type": "result"' in body
    assert '"status": "success"' in body


def test_build_runtime_factory_demo_when_no_providers(monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_COMPAT_BASE_URL",
        "MOONSHOT_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    factory, mode = build_runtime_factory()
    assert mode.startswith("demo")
    assert callable(factory)

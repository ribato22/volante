from __future__ import annotations

from pathlib import Path

import pytest
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
    assert "Volante" in r.text
    assert "EventSource" in r.text  # the page wires SSE
    # dynamic values are inserted via textContent only (no innerHTML) -> no XSS
    assert "innerHTML" not in r.text


def test_stream_endpoint_streams_events_to_result() -> None:
    client = _client()
    created = client.post("/runs", json={"goal": "hello"})
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    assert "hello" not in run_id
    r = client.get(f"/runs/{run_id}/events")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert '"type": "worker"' in body
    assert '"type": "result"' in body
    assert '"status": "success"' in body


def test_legacy_goal_in_query_endpoint_is_not_available() -> None:
    r = _client().get("/stream?goal=secret")
    assert r.status_code == 404


def test_run_id_is_one_use() -> None:
    client = _client()
    run_id = client.post("/runs", json={"goal": "hello"}).json()["run_id"]
    assert client.get(f"/runs/{run_id}/events").status_code == 200
    assert client.get(f"/runs/{run_id}/events").status_code == 404


def test_rejects_large_goal() -> None:
    client = TestClient(create_app(demo_runtime_factory(), max_goal_chars=5))
    r = client.post("/runs", json={"goal": "too long"})
    assert r.status_code == 413


def test_rejects_oversized_raw_body_before_json_parsing() -> None:
    client = TestClient(create_app(demo_runtime_factory(), max_goal_chars=5))
    r = client.post(
        "/runs",
        content=b"x" * 2_000,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_auth_token_protects_run_creation() -> None:
    client = TestClient(create_app(demo_runtime_factory(), auth_token="correct"))
    assert client.post("/runs", json={"goal": "hello"}).status_code == 401
    r = client.post(
        "/runs",
        json={"goal": "hello"},
        headers={"Authorization": "Bearer correct"},
    )
    assert r.status_code == 201


def test_cross_origin_run_creation_is_rejected() -> None:
    r = _client().post(
        "/runs",
        json={"goal": "hello"},
        headers={"Origin": "https://attacker.example"},
    )
    assert r.status_code == 403


def test_run_capacity_counts_pending_runs() -> None:
    client = TestClient(create_app(demo_runtime_factory(), max_concurrent_runs=1))
    assert client.post("/runs", json={"goal": "one"}).status_code == 201
    assert client.post("/runs", json={"goal": "two"}).status_code == 429


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


def test_usage_page_serves_html() -> None:
    r = _client().get("/usage")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Usage" in r.text
    # dynamic ledger data is inserted via textContent/DOM only (no innerHTML) -> no XSS
    assert "innerHTML" not in r.text


def test_index_links_to_usage_page() -> None:
    assert 'href="/usage"' in _client().get("/").text


def test_usage_api_reports_disabled_when_ledger_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLANTE_USAGE_LOG", "")
    body = _client().get("/api/usage").json()
    assert body["enabled"] is False
    assert body["runs"] == []
    assert body["summary"]["total_runs"] == 0


def test_usage_api_returns_recorded_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from volante.observability import record_run

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(log))
    record_run({"status": "success", "billed_usd": 0.3}, goal="hi",
               prefer="quality", source="cli")
    body = _client().get("/api/usage").json()
    assert body["enabled"] is True
    assert body["summary"]["total_runs"] == 1
    assert body["runs"][0]["goal"] == "hi"


def test_usage_api_requires_auth_when_token_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    client = TestClient(create_app(demo_runtime_factory(), auth_token="secret"))
    assert client.get("/api/usage").status_code == 401
    ok = client.get("/api/usage", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_webui_run_is_recorded_to_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    client = _client()
    run_id = client.post("/runs", json={"goal": "record me"}).json()["run_id"]
    # Draining the SSE stream runs the orchestration to its terminal result event,
    # which the stream route persists to the ledger with source="webui".
    client.get(f"/runs/{run_id}/events")
    body = client.get("/api/usage").json()
    assert body["summary"]["total_runs"] == 1
    assert body["runs"][0]["source"] == "webui"
    assert body["runs"][0]["goal"] == "record me"


def test_create_app_rejects_bad_usage_limit() -> None:
    with pytest.raises(ValueError, match="usage_limit"):
        create_app(demo_runtime_factory(), usage_limit=0)

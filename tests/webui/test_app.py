from __future__ import annotations

import contextlib
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


def test_slot_returns_when_the_stream_body_raises() -> None:
    """A failure while producing the stream must not cost a slot permanently.

    The slot is released by the response's BACKGROUND task, which Starlette runs only
    after the body completes. Anything that raises while producing it — a
    runtime_factory that cannot build a provider, a registry error, a bad model
    override — skips that release, and the count never comes back down. At the default
    max_concurrent_runs=2 the UI then serves 429 to everyone until it is restarted,
    with nothing running and no way for a user to tell why.
    """

    def exploding_factory():
        raise RuntimeError("provider stack could not be built")

    client = TestClient(create_app(exploding_factory, max_concurrent_runs=1))
    run_id = client.post("/runs", json={"goal": "one"}).json()["run_id"]
    with contextlib.suppress(RuntimeError):
        with client.stream("GET", f"/runs/{run_id}/events") as r:
            r.read()

    # The failed run holds nothing, so the next one must be admitted.
    assert client.post("/runs", json={"goal": "two"}).status_code == 201


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


# --- three defects the 0.4.0 review surfaced --------------------------------- #
# Two of these need a raw ASGI call rather than TestClient: a cooperative HTTP
# client will not send a non-ASCII header (httpx refuses to encode it) and will not
# disconnect before the first chunk. uvicorn produces both — it decodes header bytes
# as latin-1, and a browser that navigates away mid-request disconnects.

_UI_HOST = "ui.example"


def _origin_client(**kw) -> TestClient:
    app = create_app(demo_runtime_factory(), allowed_hosts=[_UI_HOST], **kw)
    return TestClient(app, base_url=f"http://{_UI_HOST}")


async def _raw_call(app, method: str, path: str, *, headers, disconnect=False):
    """Drive the ASGI app the way uvicorn would, bypassing the client's own rules."""
    sent: list[dict] = []
    started = {"n": 0}

    async def receive():
        if disconnect:
            return {"type": "http.disconnect"}
        started["n"] += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", _UI_HOST.encode()), *headers],
        "client": ("203.0.113.9", 5000), "server": (_UI_HOST, 80),
    }
    await app(scope, receive, send)
    start = [m for m in sent if m["type"] == "http.response.start"]
    return start[0]["status"] if start else None


def test_a_tls_terminating_proxy_is_not_treated_as_cross_origin() -> None:
    # 0.4.0 added VOLANTE_UI_TRUST_PROXY=1 and blessed exactly this deployment: a
    # proxy terminates TLS and forwards to the app over http. The browser then sends
    # `Origin: https://host` while request.url.scheme is "http", and the guard
    # compared the two verbatim — so the configuration the release recommended was
    # the one it rejected. uvicorn only rewrites the scheme from X-Forwarded-Proto
    # when the peer is in forwarded_allow_ips, which defaults to 127.0.0.1, so this
    # is every Docker, k8s or CDN deployment.
    r = _origin_client().post(
        "/runs", json={"goal": "hello"}, headers={"Origin": f"https://{_UI_HOST}"}
    )

    assert r.status_code == 201


def test_a_different_host_is_still_cross_origin() -> None:
    # The boundary: the CSRF property comes from the HOST matching, and forgiving the
    # scheme must not forgive the host.
    r = _origin_client().post(
        "/runs", json={"goal": "hello"}, headers={"Origin": "https://attacker.example"}
    )

    assert r.status_code == 403


async def test_a_browser_that_leaves_before_the_stream_starts_returns_its_slot() -> None:
    # `active += 1` ran in the handler; `active -= 1` ran in the generator's finally.
    # A client that disconnects before the generator's first iteration — a page
    # reload right after the POST — never runs the generator, so the slot was gone
    # for the life of the process. At the default max_concurrent_runs=2, two reloads
    # wedge the UI permanently.
    import asyncio

    app = create_app(
        demo_runtime_factory(), allowed_hosts=[_UI_HOST],
        max_concurrent_runs=1, pending_ttl_s=0.2,
    )
    client = TestClient(app, base_url=f"http://{_UI_HOST}")
    run_id = client.post("/runs", json={"goal": "one"}).json()["run_id"]

    await _raw_call(app, "GET", f"/runs/{run_id}/events", headers=[], disconnect=True)
    await asyncio.sleep(0.4)  # every pending reservation has expired by now

    assert client.post("/runs", json={"goal": "two"}).status_code == 201


async def test_a_non_ascii_credential_is_rejected_not_a_crash() -> None:
    # hmac.compare_digest raises TypeError on a str holding a non-ASCII character,
    # and uvicorn decodes header bytes as latin-1 — so a single high byte in
    # Authorization escaped the guard as a 500 with a traceback instead of a 401.
    app = create_app(demo_runtime_factory(), allowed_hosts=[_UI_HOST], auth_token="secret")

    status = await _raw_call(
        app, "GET", "/api/usage", headers=[(b"authorization", b"Bearer \xfc")]
    )

    assert status == 401

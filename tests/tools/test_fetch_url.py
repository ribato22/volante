from __future__ import annotations

from orchestrator.tools.fetch_url import FetchUrlTool


class _FakeResp:
    def __init__(self, text="page-body", status=200) -> None:
        self.text = text
        self.status_code = status


class _FakeClient:
    def __init__(self, resp, capture, **kwargs) -> None:
        self._resp = resp
        capture["kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return self._resp


def _patch(monkeypatch, resp, capture):
    monkeypatch.setattr(
        "orchestrator.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _FakeClient(resp, capture, **kw),
    )


async def test_allowed_domain_fetches_no_redirect(monkeypatch) -> None:
    capture: dict = {}
    _patch(monkeypatch, _FakeResp("hello-world", 200), capture)
    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/page"})
    assert "status=200" in out
    assert "hello-world" in out
    assert capture["kwargs"]["follow_redirects"] is False


async def test_blocks_non_allowlisted_domain() -> None:
    out = await FetchUrlTool({"example.com"}).run({"url": "https://evil.com/x"})
    assert "not in allowlist" in out


async def test_subdomain_of_allowlisted_ok(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp("s", 200), {})
    out = await FetchUrlTool({"example.com"}).run({"url": "https://docs.example.com/x"})
    assert "status=200" in out


async def test_blocks_bad_scheme() -> None:
    out = await FetchUrlTool({"example.com"}).run({"url": "file:///etc/passwd"})
    assert "scheme" in out


async def test_missing_url_errors() -> None:
    out = await FetchUrlTool({"example.com"}).run({})
    assert "error" in out.lower()


async def test_caps_size(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp("A" * 5000, 200), {})
    out = await FetchUrlTool({"example.com"}, max_bytes=100).run({"url": "https://example.com"})
    assert out.count("A") == 100

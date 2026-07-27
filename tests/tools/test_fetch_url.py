from __future__ import annotations

from volante.tools.fetch_url import FetchUrlTool


class _FakeResp:
    def __init__(
        self,
        text="page-body",
        status=200,
        *,
        chunks: list[bytes] | None = None,
        content_length: str | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else [text.encode()]
        self.status_code = status
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeClient:
    def __init__(self, resp, capture, **kwargs) -> None:
        self._resp = resp
        capture["kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        self.method = method
        self.url = url
        return self._resp


def _patch(monkeypatch, resp, capture):
    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
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
    _patch(
        monkeypatch,
        _FakeResp(chunks=[b"A" * 60, b"A" * 60, b"A" * 5_000], status=200),
        {},
    )
    out = await FetchUrlTool({"example.com"}, max_bytes=100).run({"url": "https://example.com"})
    assert out.count("A") == 100


async def test_stream_stops_reading_after_cap(monkeypatch) -> None:
    reads: list[int] = []

    class _TrackedResp(_FakeResp):
        async def aiter_bytes(self):
            for index, chunk in enumerate((b"A" * 80, b"B" * 80, b"C" * 80)):
                reads.append(index)
                yield chunk

    _patch(monkeypatch, _TrackedResp(status=200), {})
    out = await FetchUrlTool({"example.com"}, max_bytes=100).run(
        {"url": "https://example.com"}
    )
    assert out.endswith("A" * 80 + "B" * 20)
    assert reads == [0, 1]


def test_rejects_non_positive_size_cap() -> None:
    import pytest

    with pytest.raises(ValueError, match="max_bytes"):
        FetchUrlTool({"example.com"}, max_bytes=0)

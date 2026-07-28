from __future__ import annotations

import ipaddress

import pytest

from volante.tools.fetch_url import FetchUrlTool, _is_disallowed


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
        self._capture = capture
        capture["kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kwargs):
        self.method = method
        self.url = url
        self.headers = kwargs.get("headers")
        self.extensions = kwargs.get("extensions")
        self._capture["stream"] = {
            "url": str(url),
            "headers": self.headers,
            "extensions": self.extensions,
        }
        return self._resp


def _patch(monkeypatch, resp, capture, resolves_to=("93.184.216.34",)):
    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _FakeClient(resp, capture, **kw),
    )
    _patch_dns(monkeypatch, resolves_to)


def _patch_dns(monkeypatch, addresses):
    """Pin what the resolver returns so no test needs a network."""

    async def _fake_resolve(host, port):
        return list(addresses)

    monkeypatch.setattr("volante.tools.fetch_url._resolve", _fake_resolve)


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


# --- SSRF: the allowlist says WHICH NAME, these say WHERE IT MAY POINT ---------
# Once the Docker sandbox blocks the sandbox's own egress, this tool is the only
# way out, so an allowlisted name resolving inward is a credential leak. Each
# address below is a real metadata/internal endpoint, not a synthetic example.

@pytest.mark.parametrize(
    "address,what",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "private RFC1918"),
        ("192.168.1.1", "private RFC1918"),
        ("169.254.169.254", "AWS/GCP/Azure metadata, link-local"),
        ("100.100.100.200", "Alibaba metadata, CGNAT — six properties all say False"),
        ("0.0.0.0", "unspecified"),
        ("::1", "IPv6 loopback"),
        ("fd00::1", "IPv6 unique-local"),
        ("fec0::1", "IPv6 site-local — is_global=True and all six properties False"),
        ("fec0:0:0:ffff::1", "Windows' default IPv6 DNS server, inside fec0::/10"),
        ("fe80::1", "IPv6 link-local"),
        ("::ffff:169.254.169.254", "IPv4-mapped IPv6 hiding the metadata address"),
        ("::ffff:100.100.100.200", "IPv4-mapped IPv6 hiding a CGNAT address"),
        ("64:ff9b::a9fe:a9fe", "NAT64 to metadata — is_reserved but ALSO is_global"),
        ("64:ff9b::6464:64c8", "NAT64 to CGNAT metadata"),
        ("2002:a9fe:a9fe::1", "6to4 encoding of 169.254.169.254"),
    ],
)
def test_non_public_addresses_are_refused(address: str, what: str) -> None:
    assert _is_disallowed(ipaddress.ip_address(address)), f"{address} ({what}) got through"


@pytest.mark.parametrize(
    "address",
    [
        "1.1.1.1",
        "93.184.216.34",
        "8.8.8.8",
        # Regression: decoding the low 32 bits of EVERY IPv6 address (rather than
        # only NAT64 ones) reduces each of these to something inside 0.0.0.0/8 and
        # would blocklist the public IPv6 internet.
        "2606:4700:4700::1111",
        "2001:4860:4860::8888",
        "2620:fe::fe",
    ],
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert not _is_disallowed(ipaddress.ip_address(address))


async def test_allowlisted_name_pointing_at_metadata_is_refused(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp(), {}, resolves_to=("169.254.169.254",))

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/x"})

    assert "non-public address" in out
    assert "169.254.169.254" in out


async def test_one_bad_record_among_good_ones_fails_closed(monkeypatch) -> None:
    # Publishing a public AND a private record, then racing the connect, is the
    # standard way past a checker that stops at the first acceptable answer.
    _patch(monkeypatch, _FakeResp(), {}, resolves_to=("93.184.216.34", "10.0.0.5"))

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/x"})

    assert "non-public address" in out


async def test_connection_is_pinned_to_the_address_that_was_checked(monkeypatch) -> None:
    # Without pinning there is a real window between the check and the connect for
    # a short-TTL name to flip to an internal address.
    capture: dict = {}
    _patch(monkeypatch, _FakeResp("ok"), capture, resolves_to=("93.184.216.34",))

    await FetchUrlTool({"example.com"}).run({"url": "https://example.com/page"})

    sent = capture["stream"]
    assert "93.184.216.34" in sent["url"], "connected by name, so DNS can still change"
    # ...but the origin must still see the name it serves, and TLS must still be
    # verified against that name rather than the bare IP.
    assert sent["headers"]["Host"] == "example.com"
    assert sent["extensions"]["sni_hostname"] == "example.com"


async def test_ambient_proxy_env_cannot_reroute_the_connection(monkeypatch) -> None:
    # The whole address policy is dead code if httpx honours HTTP_PROXY: the TCP
    # connection goes to the proxy, which then resolves the name itself.
    capture: dict = {}
    _patch(monkeypatch, _FakeResp("ok"), capture)

    await FetchUrlTool({"example.com"}).run({"url": "https://example.com/p"})

    assert capture["kwargs"]["trust_env"] is False


async def test_redirects_stay_disabled(monkeypatch) -> None:
    # A followed redirect would re-resolve and re-connect outside every check above.
    capture: dict = {}
    _patch(monkeypatch, _FakeResp("ok"), capture)

    await FetchUrlTool({"example.com"}).run({"url": "https://example.com/p"})

    assert capture["kwargs"]["follow_redirects"] is False


@pytest.mark.parametrize("url", ["http://example.com:6379/", "https://example.com:9200/"])
async def test_internal_service_ports_are_refused(monkeypatch, url: str) -> None:
    # The address is legitimately public here; it is the port that is internal, so
    # no amount of address validation catches this one.
    _patch(monkeypatch, _FakeResp(), {})

    out = await FetchUrlTool({"example.com"}).run({"url": url})

    assert "port not allowed" in out


async def test_a_name_that_does_not_resolve_is_an_error_not_a_fetch(monkeypatch) -> None:
    async def _boom(host, port):
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr("volante.tools.fetch_url._resolve", _boom)

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/x"})

    assert "cannot resolve" in out


async def test_a_dead_first_address_falls_through_to_the_next_validated_one(monkeypatch) -> None:
    # Pinning is what closes the rebinding window, but pinning to resolved[0] and
    # stopping there throws away the failover that connecting by name got for free.
    # One blackholed AAAA record would otherwise break every fetch to that domain.
    import httpx

    attempted: list[str] = []

    class _FailFirstClient(_FakeClient):
        def stream(self, method, url, **kwargs):
            attempted.append(str(url))
            if "1.1.1.1" in str(url):
                raise httpx.ConnectError("connection refused")
            return super().stream(method, url, **kwargs)

    capture: dict = {}
    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _FailFirstClient(_FakeResp("second-address-worked"), capture, **kw),
    )
    # Both are genuinely is_global, so both pass the policy and only reachability
    # differs. (The RFC 5737 documentation ranges cannot be used here: CPython
    # reports them is_private, so the policy — correctly — refuses them outright.)
    _patch_dns(monkeypatch, ("1.1.1.1", "93.184.216.34"))

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/p"})

    assert "second-address-worked" in out
    assert len(attempted) == 2, f"gave up after the first address: {attempted}"


async def test_all_addresses_unreachable_is_an_error_string_not_a_crash(monkeypatch) -> None:
    import httpx

    class _AlwaysFail(_FakeClient):
        def stream(self, method, url, **kwargs):
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _AlwaysFail(_FakeResp(), {}, **kw),
    )
    _patch_dns(monkeypatch, ("93.184.216.34", "8.8.8.8"))

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/p"})

    assert out.startswith("error: fetch failed")


async def test_an_http_error_does_not_retry_another_address(monkeypatch) -> None:
    # Reaching the origin and getting an error back is a real answer. Retrying
    # elsewhere would turn one failed request into several.
    import httpx

    attempts = {"n": 0}

    class _ProtocolError(_FakeClient):
        def stream(self, method, url, **kwargs):
            attempts["n"] += 1
            raise httpx.ReadError("stream broke mid-body")

    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _ProtocolError(_FakeResp(), {}, **kw),
    )
    _patch_dns(monkeypatch, ("93.184.216.34", "8.8.8.8"))

    out = await FetchUrlTool({"example.com"}).run({"url": "https://example.com/p"})

    assert "fetch failed" in out
    assert attempts["n"] == 1, "an HTTP-level error must not fail over to another address"


@pytest.mark.parametrize("bad", ["https://example.com/a\tb", "https://example.com/a\nb"])
async def test_a_malformed_url_returns_an_error_instead_of_raising(monkeypatch, bad: str) -> None:
    # httpx.InvalidURL is NOT an httpx.HTTPError, so it escaped run() uncaught and
    # took down the whole agentic loop over a model-typed control character.
    _patch(monkeypatch, _FakeResp(), {})

    out = await FetchUrlTool({"example.com"}).run({"url": bad})

    assert out.startswith("error:")

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

from volante.types import ToolSpec

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Only these. An allowlisted hostname is otherwise a licence to reach Redis (6379)
# or Elasticsearch (9200) on that host, which no amount of address validation
# prevents — the address is legitimately public, it is the PORT that is internal.
_ALLOWED_PORTS = frozenset({80, 443})

# Carrier-grade NAT. None of the six ipaddress properties covers it: for
# 100.100.100.200 (Alibaba Cloud's metadata endpoint) is_private, is_loopback,
# is_link_local, is_reserved, is_multicast and is_unspecified are ALL False, and
# only `not is_global` rejects it.
_DENIED_V4_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)

# NAT64 translation prefixes (RFC 6052 well-known, RFC 8215 local-use). The
# asymmetry runs the other way here: 64:ff9b::a9fe:a9fe reaches AWS's metadata
# service at 169.254.169.254 and is is_reserved=True but ALSO is_global=True, so
# an is_global test alone lets it through. Neither arm covers the other, which is
# why the check below is an OR of both rather than a choice between them.
_NAT64_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)

# Deprecated IPv6 site-local (RFC 3879). It is absent from IANA's special-purpose
# registry, so CPython reports is_private=False AND is_global=True across the whole
# of fec0::/10 — every arm above waves it through. Windows still ships
# fec0:0:0:ffff::1/2/3 as its default IPv6 DNS servers, so this is live space on
# real corporate networks, not a historical curiosity.
_DENIED_V6_NETWORKS = (ipaddress.ip_network("fec0::/10"),)


def _embedded_addresses(addr: _IpAddress) -> list[_IpAddress]:
    """``addr`` plus every IPv4 address that can be hiding inside it.

    An IPv6 address can carry a v4 one that the v6 properties know nothing about,
    so each extracted address has to face the same policy. Only unambiguous
    encodings are decoded here.

    Deliberately NOT decoded: the low 32 bits of an arbitrary IPv6 address. That
    trick is how NAT64 embeds v4, but applied unconditionally it rejects the real
    internet — 2606:4700:4700::1111, 2001:4860:4860::8888 and 2620:fe::fe all
    reduce to something inside 0.0.0.0/8, which is_private calls private. So the
    low-32-bit decode is scoped to the NAT64 prefixes, where it actually means
    something, and those prefixes are denied outright anyway.
    """
    found: list[_IpAddress] = [addr]
    if not isinstance(addr, ipaddress.IPv6Address):
        return found
    for extracted in (addr.ipv4_mapped, addr.sixtofour):
        if extracted is not None:
            found.append(extracted)
    if addr.teredo is not None:
        found.extend(addr.teredo)
    if any(addr in net for net in _NAT64_PREFIXES):
        found.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return found


def _is_disallowed(addr: _IpAddress) -> bool:
    """True when this address, or anything encoded inside it, is not public."""
    for candidate in _embedded_addresses(addr):
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_reserved
            or candidate.is_multicast
            or candidate.is_unspecified
        ):
            return True
        if not candidate.is_global:
            return True
        if candidate.version == 4 and any(candidate in n for n in _DENIED_V4_NETWORKS):
            return True
        if candidate.version == 6 and any(candidate in n for n in _DENIED_V6_NETWORKS):
            return True
    return False


async def _resolve(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to, as strings.

    Validating the RESOLVER'S OUTPUT rather than the text of the URL is what makes
    the obfuscated-literal family (`0177.0.0.1`, `2130706433`, `0x7f.0.0.1`,
    `127.1`, `localhost`) fall out for free: getaddrinfo normalises all of them to
    the same loopback address, so there is no parser to confuse.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class FetchUrlTool:
    """HOST-MEDIATED web tool: the orchestrator (trusted) fetches an allowlisted
    URL and returns the text. The exfiltration channel is bounded by the domain
    allowlist, plus no-redirects, a size cap and a timeout.

    THREAT MODEL. Prompt-injection containment — model-authored code cannot reach
    the network — holds only when the sandbox is DockerSandbox
    (VOLANTE_SANDBOX=docker, `--network none`). Under the default subprocess
    sandbox, model code already has the host's network and disk, so this
    allowlist is not the only way out: it bounds exfiltration THROUGH THE TOOL,
    not through the sandbox.

    That is also why the address checks below matter more than they look. Once
    Docker blocks the sandbox's own egress, this tool becomes the single
    remaining egress path, so an allowlisted name resolving to 169.254.169.254
    would hand over cloud credentials. Every resolved address is therefore
    checked, and the connection is pinned to the address that was checked so a
    second lookup cannot substitute another one (DNS rebinding).

    RESIDUAL RISK, stated rather than hidden: `_allowed` accepts every subdomain
    of an allowlisted domain. If an allowlisted zone hands out attacker-
    registrable names (SaaS tenant subdomains, wildcard object storage, a dangling
    CNAME), an attacker can get a name inside the allowlist — the address checks
    still apply, but the name check no longer means much. Prefer listing exact
    hostnames when the zone is not fully under your control.
    """

    name = "fetch_url"

    def __init__(
        self, allowed_domains, max_bytes: int = 100_000, timeout_s: float = 10.0
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.allowed_domains = {d.lower() for d in allowed_domains}
        self.max_bytes = max_bytes
        self.timeout_s = timeout_s
        self.spec = ToolSpec(
            name="fetch_url",
            description="Fetch text from an allowlisted URL (host-mediated, no redirects).",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        )

    def _allowed(self, host: str) -> bool:
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)

    async def _read_limited(self, chunks: AsyncIterator[bytes]) -> bytes:
        """Read at most ``max_bytes`` without buffering the full response body.

        Only ever fed RAW chunks. Slicing a chunk bounds what is KEPT, not what was
        allocated to produce it, so a decoder upstream of this loop makes the cap
        advisory: gzip reaches roughly 1030:1, and one ~64 KB network read expands
        into a ~67 MB chunk that exists in full before the first slice runs.
        """
        data = bytearray()
        async for chunk in chunks:
            remaining = self.max_bytes - len(data)
            if remaining <= 0:
                break
            data.extend(chunk[:remaining])
            if len(data) >= self.max_bytes:
                break
        return bytes(data)

    async def run(self, args: dict) -> str:
        url = args.get("url")
        if not isinstance(url, str):
            return "error: 'url' (string) argument is required"
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"error: unsupported scheme: {parsed.scheme!r}"
        host = parsed.hostname or ""
        if not self._allowed(host):
            return f"error: domain not in allowlist: {host!r}"
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return "error: invalid port"
        if port not in _ALLOWED_PORTS:
            return f"error: port not allowed: {port}"

        # ONE deadline over resolution, connect and the whole body. httpx's timeout is
        # per-operation, and the read loop had no total budget of its own, so an origin
        # answering just inside the read timeout held the call open for as long as it
        # liked — 12 s measured for 40 bytes against timeout_s=0.5. Resolution sat
        # outside every budget: getaddrinfo was awaited before the client existed.
        # Cancelling that await does not stop the resolver's own thread, but it does
        # return the deadline to the caller, which is what timeout_s promises.
        try:
            return await asyncio.wait_for(
                self._fetch(url, host, port), timeout=self.timeout_s
            )
        except TimeoutError:
            return f"error: fetch exceeded its {self.timeout_s}s deadline"

    async def _fetch(self, url: str, host: str, port: int) -> str:
        """The part of a fetch that touches the network, under one deadline."""
        try:
            resolved = await _resolve(host, port)
        except OSError as exc:
            return f"error: cannot resolve {host!r}: {exc}"
        if not resolved:
            return f"error: cannot resolve {host!r}"
        # FAIL CLOSED across the whole answer. Skipping a bad record and trying the
        # next one would let an attacker publish one public and one private address
        # and simply race the connection.
        for raw in resolved:
            if _is_disallowed(ipaddress.ip_address(raw)):
                return f"error: {host!r} resolves to a non-public address: {raw}"
        # httpx.InvalidURL is NOT an httpx.HTTPError, so it would escape run()
        # uncaught and abort the whole agentic loop over a model-supplied string
        # containing a tab or a newline. This tool's contract is to return a string.
        try:
            original = httpx.URL(url)
            # httpx only fills Host in when the caller has not, so after rewriting
            # the URL the wire would otherwise carry `Host: <ip>` and break every
            # virtual-hosted origin. netloc (not host) keeps a non-default port and
            # strips userinfo.
            headers = {
                "Host": original.netloc.decode("ascii"),
                # No content coding, because this tool never wants a whole body: it
                # keeps max_bytes and stops. Compression only helps a client that
                # downloads everything, while a decoder between the socket and the
                # cap turns a small response into a large allocation. Refusing the
                # coding outright also means no future decoder — brotli or zstd, which
                # expand far harder than gzip — can be enabled underneath this tool by
                # merely installing a package.
                "Accept-Encoding": "identity",
            }
        except (httpx.InvalidURL, UnicodeDecodeError) as exc:
            return f"error: malformed url: {exc}"
        # Keeps TLS bound to the ORIGINAL name: httpcore reads this extension and
        # uses it as server_hostname, so the certificate is still verified against
        # the hostname. Disabling verification is never the fix here.
        extensions = {"sni_hostname": host}

        last_connect_error = ""
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=self.timeout_s,
                # Without this, an ambient HTTP_PROXY / HTTPS_PROXY / ALL_PROXY
                # silently sends the connection to a proxy instead of the address
                # just validated — the proxy then resolves the name itself, outside
                # this policy, and every check above becomes dead code. socks5h://
                # hands the hostname to the proxy to resolve by design.
                trust_env=False,
            ) as client:
                # Pinning to one address is what closes the rebinding window, but
                # pinning to resolved[0] and stopping there throws away the failover
                # that connecting by name used to get for free: one dead AAAA record
                # and every fetch to that domain fails. Every address here already
                # passed the policy, so falling through to the next one on a CONNECT
                # failure costs no safety. Only connect-phase errors retry — an HTTP
                # error means we reached the origin and must not try somewhere else.
                for candidate_ip in resolved:
                    try:
                        target = original.copy_with(host=candidate_ip)  # brackets v6
                    except httpx.InvalidURL as exc:
                        return f"error: malformed url: {exc}"
                    try:
                        async with client.stream(
                            "GET", target, headers=headers, extensions=extensions
                        ) as resp:
                            # Asking for identity is a request, not a guarantee. Fail
                            # closed on an origin that compressed anyway rather than
                            # hand the stream to a decoder that has no size bound —
                            # and say which coding arrived, so an allowlisted origin
                            # that behaves this way is diagnosable rather than merely
                            # broken.
                            coding = (
                                resp.headers.get("content-encoding", "").strip().lower()
                            )
                            if coding and coding != "identity":
                                return (
                                    "error: origin returned "
                                    f"content-encoding: {coding} after identity was "
                                    "requested; refusing to decode an unbounded body"
                                )
                            # aiter_raw, NOT aiter_bytes: the latter decodes.
                            body = await self._read_limited(resp.aiter_raw())
                        return f"status={resp.status_code}\n{body.decode(errors='replace')}"
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        last_connect_error = str(exc) or exc.__class__.__name__
                        continue
        except httpx.HTTPError as exc:
            return f"error: fetch failed: {exc}"
        return f"error: fetch failed: {last_connect_error or 'no address could be reached'}"

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from orchestrator.types import ToolSpec


class FetchUrlTool:
    """Tool web HOST-MEDIATED: orchestrator (tepercaya) fetch URL ter-allowlist dan
    kembalikan teks; kode model tetap di sandbox --network none. Kanal exfil dibatasi
    allowlist domain (+ no-redirect + size cap + timeout)."""

    name = "fetch_url"

    def __init__(
        self, allowed_domains, max_bytes: int = 100_000, timeout_s: float = 10.0
    ) -> None:
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
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=self.timeout_s
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            return f"error: fetch failed: {exc}"
        return f"status={resp.status_code}\n{resp.text[: self.max_bytes]}"

# src/orchestrator/providers/base.py
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from orchestrator.types import CanonicalRequest, CanonicalResponse


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse: ...

    async def stream(
        self, req: CanonicalRequest, on_text: Callable[[str], None]
    ) -> CanonicalResponse: ...


async def call_provider(
    provider: LLMProvider,
    req: CanonicalRequest,
    on_text: Callable[[str], None] | None = None,
) -> CanonicalResponse:
    """Panggil provider: `stream` (progres teks live) bila `on_text` diberi, else
    `complete`. Satu sumber kebenaran untuk pilihan stream-vs-complete yang dipakai
    Supervisor, Synthesizer, Worker, dan AgenticWorker (hindari duplikasi 4×)."""
    if on_text is not None:
        return await provider.stream(req, on_text)
    return await provider.complete(req)


class ProviderError(Exception):
    """Galat provider seragam.

    `retryable` menggerakkan kebijakan backoff di Runtime (True -> retry dengan
    jitter; False -> fail-fast). `status` adalah kode HTTP hulu bila diketahui
    (None untuk galat transport/timeout tanpa status).
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status

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

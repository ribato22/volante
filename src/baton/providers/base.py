# src/baton/providers/base.py
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from baton.types import CanonicalRequest, CanonicalResponse

# Callback progres streaming. Mengembalikan nilai TRUTHY = minta stream berhenti
# lebih awal (cooperative cancel): adapter menutup koneksi dan mengembalikan response
# terakumulasi sejauh ini. Mengembalikan None/falsy (kontrak lama) = lanjut — jadi
# callback `-> None` yang sudah ada tak berubah perilaku (nol regresi).
OnText = Callable[[str], object]


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse: ...

    async def stream(
        self, req: CanonicalRequest, on_text: OnText
    ) -> CanonicalResponse: ...


async def call_provider(
    provider: LLMProvider,
    req: CanonicalRequest,
    on_text: OnText | None = None,
) -> CanonicalResponse:
    """Panggil provider: `stream` (progres teks live) bila `on_text` diberi, else
    `complete`. Satu sumber kebenaran untuk pilihan stream-vs-complete yang dipakai
    Supervisor, Synthesizer, Worker, dan AgenticWorker (hindari duplikasi 4×).

    `on_text` boleh mengembalikan truthy untuk menghentikan stream lebih awal."""
    if on_text is not None:
        return await provider.stream(req, on_text)
    return await provider.complete(req)


class ProviderError(Exception):
    """Galat provider seragam.

    `retryable` menggerakkan kebijakan backoff di Runtime (True -> retry dengan
    jitter; False -> fail-fast). `status` adalah kode HTTP hulu bila diketahui
    (None untuk galat transport/timeout tanpa status).
    `quota_exhausted` menandai DEPLETION credit/quota (bukan rate-limit menit): Runtime
    reroute ke kandidat berikutnya TANPA backoff (Layer 2). Selalu implikasi retryable=False.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        quota_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.quota_exhausted = quota_exhausted


# --- 429 / quota classification (Layer 1 reroute, §6.3) -------------------- #
# Sinyal substring (lowercased) bahwa 400/402/429 adalah DEPLETION credit/quota
# (bukan rate-limit per-menit). Depletion -> reroute, TANPA backoff.
_QUOTA_SIGNALS: tuple[str, ...] = (
    "credit balance",              # Anthropic 400: "Your credit balance is too low"
    "out of credit",
    "insufficient_quota",          # OpenAI error code
    "insufficient quota",
    "exceeded your current quota",
    "quota exceeded",
    "usage limit",                 # plan cap
    "billing_hard_limit",          # OpenAI error code
    "payment required",            # HTTP 402 semantics
)

# Sinyal rate-limit transien: backoff di tempat memang benar.
_RATE_LIMIT_SIGNALS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",                  # OpenAI code rate_limit_exceeded
    "too many requests",
    "requests per",
    "tokens per",
    "try again in",
)


def is_quota_exhausted(message: str) -> bool:
    """True bila body/pesan menandakan DEPLETION credit/quota (bukan rate-limit).

    Berbasis body/pesan, BUKAN status: Anthropic mengirim depletion sebagai 400
    ("credit balance too low"), OpenAI-compat sebagai 429 (`insufficient_quota`)."""
    low = message.lower()
    return any(s in low for s in _QUOTA_SIGNALS)


def is_transient_rate_limit(message: str) -> bool:
    """True bila body/pesan menandakan rate-limit transien (backoff benar)."""
    low = message.lower()
    return any(s in low for s in _RATE_LIMIT_SIGNALS)


def classify_429(message: str, *, billing: str) -> tuple[bool, bool]:
    """Resolusi 429 ambigu -> (retryable, quota_exhausted) via body + billing.

    Presedensi (body/type di atas status, §6.3):
    1. sinyal depletion  -> (False, True): reroute, TANPA backoff.
    2. sinyal rate-limit -> (True, False): backoff di tempat.
    3. ambigu -> default per `billing` [residu 2]: plan-backed
       (`plan_included`/`plan_credit`) -> (False, True); `card` -> (True, False)."""
    if is_quota_exhausted(message):
        return (False, True)
    if is_transient_rate_limit(message):
        return (True, False)
    if billing in ("plan_included", "plan_credit"):
        return (False, True)
    return (True, False)

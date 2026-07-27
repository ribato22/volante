# src/volante/providers/base.py
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from volante.types import CanonicalRequest, CanonicalResponse

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
    `candidate_unavailable` means this particular configured model/deployment is
    inaccessible, so Runtime may safely try another ranked candidate without
    treating a provider-wide configuration error as transient.
    `provider_unavailable` means the configured provider endpoint/account cannot
    currently serve requests. Runtime may fail over to a different configured
    provider, but should not retry every model behind the same broken endpoint.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        quota_exhausted: bool = False,
        candidate_unavailable: bool = False,
        provider_unavailable: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.quota_exhausted = quota_exhausted
        self.candidate_unavailable = candidate_unavailable
        self.provider_unavailable = provider_unavailable


class IncompleteOutputError(ProviderError):
    """A provider returned text but did not report a successful terminal stop."""

    error_code = "incomplete_output"

    def __init__(self, *, phase: str, stop_reason: str) -> None:
        self.phase = phase
        self.stop_reason = stop_reason
        super().__init__(
            f"{phase} output is incomplete: provider stopped with {stop_reason!r}",
            retryable=False,
        )


_COMPLETE_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})


def ensure_complete_response(
    response: CanonicalResponse,
    *,
    phase: str,
) -> CanonicalResponse:
    """Return a successfully terminated response or raise a structured error.

    Adapters normalize ordinary completion to ``end_turn``. Anthropic's
    ``stop_sequence`` is also a complete terminal outcome. Token exhaustion,
    content filtering, refusals, pause/resume signals, tool requests in a
    non-tool phase, and unknown provider reasons must never be accepted as a
    complete artifact or final answer merely because they contain some text.
    """

    if response.stop_reason not in _COMPLETE_STOP_REASONS:
        raise IncompleteOutputError(
            phase=phase,
            stop_reason=response.stop_reason,
        )
    return response


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

_CANDIDATE_UNAVAILABLE_SIGNALS: tuple[str, ...] = (
    "model_not_found",
    "model not found",
    "unsupported model",
    "deploymentnotfound",
    "deployment not found",
    "does not exist or you do not have access",
    "do not have access to model",
    "not have access to model",
    "not available for your account",
    "not authorized to access this model",
    "not entitled to",
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


def is_candidate_unavailable(
    message: str,
    *,
    status: int | None = None,
) -> bool:
    """Classify a model/deployment-specific access failure.

    A bare 401/403 or generic endpoint 404 remains a configuration failure and
    does not walk the inventory. We reroute only when the provider explicitly
    identifies the model/deployment as absent, unsupported, or inaccessible.
    """

    low = message.lower()
    if any(signal in low for signal in _CANDIDATE_UNAVAILABLE_SIGNALS):
        return True
    return status == 404 and any(
        noun in low for noun in ("model", "deployment", "engine")
    )


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

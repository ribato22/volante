# src/volante/providers/base.py
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from volante.types import CanonicalRequest, CanonicalResponse

# Streaming progress callback. Returning a TRUTHY value = ask the stream to stop
# early (cooperative cancel): the adapter closes the connection and returns the
# response accumulated so far. Returning None/falsy (the old contract) = keep going —
# so existing `-> None` callbacks do not change behaviour (zero regression).
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
    """Call a provider: `stream` (live text progress) when `on_text` is given, else
    `complete`. One source of truth for the stream-vs-complete choice used by
    Supervisor, Synthesizer, Worker, and AgenticWorker (avoids duplicating it 4×).

    `on_text` may return truthy to stop the stream early."""
    if on_text is not None:
        return await provider.stream(req, on_text)
    return await provider.complete(req)


class ProviderError(Exception):
    """Uniform provider error.

    `retryable` drives the backoff policy in Runtime (True -> retry with jitter;
    False -> fail-fast). `status` is the upstream HTTP code when known (None for
    transport/timeout errors that carry no status).
    `quota_exhausted` marks credit/quota DEPLETION (not a per-minute rate limit): Runtime
    reroutes to the next candidate WITHOUT backoff (Layer 2). Always implies retryable=False.
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
# Substring signals (lowercased) that a 400/402/429 is credit/quota DEPLETION
# (not a per-minute rate limit). Depletion -> reroute, WITHOUT backoff.
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

# Transient rate-limit signals: backing off in place really is the right move.
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
    """True when the body/message indicates credit/quota DEPLETION (not a rate limit).

    Driven by the body/message, NOT the status: Anthropic sends depletion as a 400
    ("credit balance too low"), OpenAI-compat as a 429 (`insufficient_quota`)."""
    low = message.lower()
    return any(s in low for s in _QUOTA_SIGNALS)


def is_transient_rate_limit(message: str) -> bool:
    """True when the body/message indicates a transient rate limit (backoff is right)."""
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
    """Resolve an ambiguous 429 -> (retryable, quota_exhausted) via body + billing.

    Precedence (body/type over status, §6.3):
    1. depletion signal  -> (False, True): reroute, WITHOUT backoff.
    2. rate-limit signal -> (True, False): back off in place.
    3. ambiguous -> default per `billing` [residual 2]: plan-backed
       (`plan_included`/`plan_credit`) -> (False, True); `card` -> (True, False)."""
    if is_quota_exhausted(message):
        return (False, True)
    if is_transient_rate_limit(message):
        return (True, False)
    if billing in ("plan_included", "plan_credit"):
        return (False, True)
    return (True, False)

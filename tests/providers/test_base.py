# tests/providers/test_base.py
from __future__ import annotations

import inspect

import pytest

from baton.providers.base import (
    LLMProvider,
    ProviderError,
    call_provider,
    classify_429,
    is_quota_exhausted,
    is_transient_rate_limit,
)
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    Usage,
    text,
)


class _Conforming:
    name = "conforming"

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            content=[TextBlock(text="ok")],
            usage=Usage(prompt_tokens=0, completion_tokens=0),
            model="conforming",
            stop_reason="end_turn",
            latency_ms=0,
        )

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        return await self.complete(req)


class _MissingComplete:
    name = "missing"


# --- LLMProvider Protocol (structural typing, unchanged) ---

def test_conforming_class_is_llmprovider() -> None:
    assert isinstance(_Conforming(), LLMProvider)


def test_missing_complete_is_not_llmprovider() -> None:
    assert not isinstance(_MissingComplete(), LLMProvider)


def test_complete_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(_Conforming.complete)


# --- ProviderError (PATCH v2.1) ---

def test_provider_error_is_exception_subclass() -> None:
    assert issubclass(ProviderError, Exception)


def test_provider_error_stores_retryable_and_status() -> None:
    err = ProviderError("rate limited", retryable=True, status=429)
    assert err.retryable is True
    assert err.status == 429
    # Pesan tetap dapat diakses lewat str(e) (dipakai Runtime saat append Entry gagal).
    assert str(err) == "rate limited"


def test_provider_error_status_defaults_to_none() -> None:
    err = ProviderError("bad request", retryable=False)
    assert err.retryable is False
    assert err.status is None


def test_provider_error_retryable_is_keyword_only() -> None:
    # retryable WAJIB keyword-only; pemanggilan positional harus gagal keras.
    with pytest.raises(TypeError):
        ProviderError("boom", True)  # type: ignore[misc]


def test_provider_error_retryable_is_required() -> None:
    # Tanpa retryable -> TypeError (argumen wajib, tak ada default).
    with pytest.raises(TypeError):
        ProviderError("boom")  # type: ignore[call-arg]


def test_provider_error_is_raisable_and_carries_flags() -> None:
    with pytest.raises(ProviderError) as excinfo:
        raise ProviderError("server exploded", retryable=True, status=503)
    assert excinfo.value.retryable is True
    assert excinfo.value.status == 503


# --- ProviderError.quota_exhausted (Layer 1 reroute, §6.3) ---

def test_provider_error_quota_exhausted_defaults_to_false() -> None:
    # Every existing construction (no kwarg) is a transient/normal error.
    err = ProviderError("rate limited", retryable=True, status=429)
    assert err.quota_exhausted is False


def test_provider_error_quota_exhausted_can_be_set_true() -> None:
    err = ProviderError(
        "credit balance too low", retryable=False, status=400, quota_exhausted=True
    )
    assert err.quota_exhausted is True
    # Contract: quota_exhausted=True MUST also be non-retryable (reroute, no backoff).
    assert err.retryable is False


# --- 429 classification helpers (residu 2) ---

@pytest.mark.parametrize(
    "msg",
    [
        "Your credit balance is too low to access the Claude API.",
        "Error code: 429 - insufficient_quota",
        "You exceeded your current quota, please check your plan and billing details.",
        "billing_hard_limit_reached",
    ],
)
def test_is_quota_exhausted_true_for_depletion(msg: str) -> None:
    assert is_quota_exhausted(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "Rate limit reached for requests per min. Please try again in 20s.",
        "boom",
        "429 Too Many Requests",
    ],
)
def test_is_quota_exhausted_false_for_transient_or_ambiguous(msg: str) -> None:
    assert is_quota_exhausted(msg) is False


def test_is_transient_rate_limit_true_and_false() -> None:
    assert is_transient_rate_limit("Rate limit reached, try again in 5s") is True
    assert is_transient_rate_limit("insufficient_quota") is False


def test_classify_429_depletion_signal_beats_billing() -> None:
    # Body signal wins over billing default, even on a card model.
    assert classify_429("insufficient_quota", billing="card") == (False, True)


def test_classify_429_transient_signal_beats_billing() -> None:
    # Transient signal -> retryable even on a plan model.
    assert classify_429("Rate limit reached, try again in 5s", billing="plan_included") == (
        True,
        False,
    )


def test_classify_429_ambiguous_plan_defaults_quota_exhausted() -> None:
    assert classify_429("boom", billing="plan_included") == (False, True)
    assert classify_429("boom", billing="plan_credit") == (False, True)


def test_classify_429_ambiguous_card_defaults_transient() -> None:
    assert classify_429("boom", billing="card") == (True, False)


# --- call_provider (helper stream-vs-complete tunggal) ---


class _MethodSpy:
    name = "spy"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls.append("complete")
        return _resp("c")

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        self.calls.append("stream")
        on_text("streamed")
        return _resp("s")


def _resp(s: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=s)],
        usage=Usage(prompt_tokens=0, completion_tokens=0),
        model="spy",
        stop_reason="end_turn",
        latency_ms=0,
    )


def _req() -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", "hi")], max_tokens=8)


async def test_call_provider_dispatches_complete_without_on_text() -> None:
    spy = _MethodSpy()
    resp = await call_provider(spy, _req())
    assert spy.calls == ["complete"]
    assert resp.content[0].text == "c"


async def test_call_provider_dispatches_stream_with_on_text() -> None:
    spy = _MethodSpy()
    chunks: list[str] = []
    resp = await call_provider(spy, _req(), chunks.append)
    assert spy.calls == ["stream"]
    assert chunks == ["streamed"]
    assert resp.content[0].text == "s"

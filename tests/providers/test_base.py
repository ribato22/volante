# tests/providers/test_base.py
from __future__ import annotations

import inspect

import pytest

from orchestrator.providers.base import LLMProvider, ProviderError
from orchestrator.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage


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

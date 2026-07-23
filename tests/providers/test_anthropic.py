from __future__ import annotations

import anthropic
import httpx
import pytest

from baton.providers.anthropic import AnthropicProvider
from baton.providers.base import ProviderError
from baton.types import CanonicalRequest, text


# --------------------------------------------------------------------------- #
# Test doubles (no network, no real SDK client)                               #
# --------------------------------------------------------------------------- #
class _FakeMessages:
    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text_):
        self.text = text_


class _FakeResponse:
    def __init__(self, content, usage, model="claude-test", stop_reason="end_turn"):
        self.content = content
        self.usage = usage
        self.model = model
        self.stop_reason = stop_reason


def _provider_with(fake_messages: _FakeMessages, monkeypatch) -> AnthropicProvider:
    # Replace the SDK client constructor so __init__ never builds a real client.
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda **kw: _FakeClient(fake_messages)
    )
    return AnthropicProvider(api_key="test-key", model="claude-test")


def _status_error(cls, status: int):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return cls("boom", response=response, body=None)


def _status_error_msg(cls, status: int, message: str):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


def _req(prompt: str = "hi") -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", prompt)], max_tokens=16)


# --------------------------------------------------------------------------- #
# name / protocol                                                             #
# --------------------------------------------------------------------------- #
async def test_name_is_anthropic(monkeypatch):
    provider = _provider_with(
        _FakeMessages(result=_FakeResponse([], _FakeUsage(1, 1))), monkeypatch
    )
    assert provider.name == "anthropic"


# --------------------------------------------------------------------------- #
# PATCH: error taxonomy                                                        #
# --------------------------------------------------------------------------- #
async def test_maps_429_to_retryable(monkeypatch):
    fake = _FakeMessages(exc=_status_error(anthropic.RateLimitError, 429))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.status == 429


async def test_maps_400_to_non_retryable(monkeypatch):
    fake = _FakeMessages(exc=_status_error(anthropic.BadRequestError, 400))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is False
    assert ei.value.status == 400


async def test_maps_500_to_retryable(monkeypatch):
    fake = _FakeMessages(exc=_status_error(anthropic.InternalServerError, 500))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.status == 500


async def test_maps_408_and_409_to_retryable(monkeypatch):
    for status in (408, 409):
        fake = _FakeMessages(exc=_status_error(anthropic.APIStatusError, status))
        provider = _provider_with(fake, monkeypatch)
        with pytest.raises(ProviderError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is True, status
        assert ei.value.status == status


async def test_maps_401_to_non_retryable(monkeypatch):
    fake = _FakeMessages(exc=_status_error(anthropic.AuthenticationError, 401))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is False
    assert ei.value.status == 401


async def test_timeout_is_retryable(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake = _FakeMessages(exc=anthropic.APITimeoutError(request=request))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.status is None


async def test_connection_error_is_retryable(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake = _FakeMessages(exc=anthropic.APIConnectionError(request=request))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.status is None


async def test_unknown_error_is_non_retryable(monkeypatch):
    fake = _FakeMessages(exc=ValueError("weird"))
    provider = _provider_with(fake, monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is False
    assert ei.value.status is None


# --------------------------------------------------------------------------- #
# Layer 1: quota_exhausted classification (residu 2)                           #
# --------------------------------------------------------------------------- #
async def test_credit_balance_400_is_quota_exhausted(monkeypatch):
    # Anthropic depletion arrives as 400, not 429 -> detect via body, reroute.
    exc = _status_error_msg(
        anthropic.BadRequestError,
        400,
        "Your credit balance is too low to access the Claude API.",
    )
    provider = _provider_with(_FakeMessages(exc=exc), monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.quota_exhausted is True
    assert ei.value.retryable is False
    assert ei.value.status == 400


async def test_429_insufficient_quota_is_quota_exhausted(monkeypatch):
    exc = _status_error_msg(
        anthropic.RateLimitError, 429, "You have exceeded your current quota for this plan."
    )
    provider = _provider_with(_FakeMessages(exc=exc), monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.quota_exhausted is True
    assert ei.value.retryable is False
    assert ei.value.status == 429


async def test_429_transient_rate_limit_is_retryable(monkeypatch):
    exc = _status_error_msg(
        anthropic.RateLimitError, 429, "Rate limit exceeded, please try again in 12s."
    )
    provider = _provider_with(_FakeMessages(exc=exc), monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.quota_exhausted is False
    assert ei.value.status == 429


async def test_429_ambiguous_card_defaults_transient(monkeypatch):
    # message "boom" is ambiguous; default billing "card" -> transient, backoff correct.
    exc = _status_error(anthropic.RateLimitError, 429)
    provider = _provider_with(_FakeMessages(exc=exc), monkeypatch)
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.retryable is True
    assert ei.value.quota_exhausted is False


async def test_429_ambiguous_plan_defaults_quota_exhausted(monkeypatch):
    # Plan-backed model: ambiguous 429 -> quota_exhausted, reroute (residu 2).
    fake = _FakeMessages(exc=_status_error(anthropic.RateLimitError, 429))
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: _FakeClient(fake))
    provider = AnthropicProvider(api_key="k", model="claude-test", billing="plan_included")
    with pytest.raises(ProviderError) as ei:
        await provider.complete(_req())
    assert ei.value.quota_exhausted is True
    assert ei.value.retryable is False


# --------------------------------------------------------------------------- #
# PATCH: usage fallback -> estimated True, never Usage(0, 0)                   #
# --------------------------------------------------------------------------- #
async def test_missing_usage_is_estimated_not_zero(monkeypatch):
    resp = _FakeResponse(content=[_FakeTextBlock("abcdefgh")], usage=None)
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    req = CanonicalRequest(messages=[text("user", "0123456789012345")], max_tokens=16)
    out = await provider.complete(req)
    assert out.usage.estimated is True
    assert out.usage.prompt_tokens == 4       # len("0123456789012345") // 4
    assert out.usage.completion_tokens == 2   # len("abcdefgh") // 4
    assert out.usage.prompt_tokens >= 1
    assert out.usage.completion_tokens >= 1


async def test_partial_usage_fields_trigger_estimation(monkeypatch):
    # SDK returned a usage object but without output_tokens -> treat as not sent.
    class _HalfUsage:
        input_tokens = 999
        output_tokens = None

    resp = _FakeResponse(content=[_FakeTextBlock("abcd")], usage=_HalfUsage())
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    req = CanonicalRequest(messages=[text("user", "abcd")], max_tokens=16)
    out = await provider.complete(req)
    assert out.usage.estimated is True
    assert out.usage.prompt_tokens == 1       # len("abcd") // 4
    assert out.usage.completion_tokens == 1


async def test_est_minimum_is_one(monkeypatch):
    resp = _FakeResponse(content=[], usage=None)  # empty output
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    req = CanonicalRequest(messages=[text("user", "x")], max_tokens=16)
    out = await provider.complete(req)
    assert out.usage.estimated is True
    assert out.usage.prompt_tokens == 1       # max(1, 1 // 4)
    assert out.usage.completion_tokens == 1   # max(1, 0 // 4)


async def test_present_usage_not_estimated(monkeypatch):
    resp = _FakeResponse(content=[_FakeTextBlock("hi")], usage=_FakeUsage(11, 22))
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    out = await provider.complete(_req())
    assert out.usage.estimated is False
    assert out.usage.prompt_tokens == 11
    assert out.usage.completion_tokens == 22


async def test_response_fields_mapped(monkeypatch):
    resp = _FakeResponse(
        content=[_FakeTextBlock("pong")],
        usage=_FakeUsage(3, 4),
        model="claude-real",
        stop_reason="end_turn",
    )
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    out = await provider.complete(_req())
    assert out.model == "claude-real"
    assert out.stop_reason == "end_turn"
    assert out.latency_ms >= 0
    assert [b.text for b in out.content] == ["pong"]


async def test_stop_reason_defaults_to_end_turn_when_missing(monkeypatch):
    # CanonicalResponse.stop_reason is typed `str` (never None): a resp with no
    # stop_reason (or None) at the SDK boundary must still yield a str, not None.
    resp = _FakeResponse(content=[_FakeTextBlock("hi")], usage=_FakeUsage(1, 1), stop_reason=None)
    provider = _provider_with(_FakeMessages(result=resp), monkeypatch)
    out = await provider.complete(_req())
    assert out.stop_reason == "end_turn"


# --------------------------------------------------------------------------- #
# PATCH: timeout diteruskan ke klien SDK                                       #
# --------------------------------------------------------------------------- #
async def test_timeout_forwarded_to_client(monkeypatch):
    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = _FakeMessages(result=_FakeResponse([], _FakeUsage(1, 1)))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _StubClient)
    AnthropicProvider(api_key="k", model="claude-test", timeout=45.0)
    assert captured["timeout"] == 45.0
    assert captured["api_key"] == "k"


async def test_default_timeout_is_120(monkeypatch):
    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = _FakeMessages(result=_FakeResponse([], _FakeUsage(1, 1)))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _StubClient)
    AnthropicProvider(api_key="k", model="claude-test")
    assert captured["timeout"] == 120.0

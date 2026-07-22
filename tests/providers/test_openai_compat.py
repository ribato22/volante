# tests/providers/test_openai_compat.py
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import baton.providers.openai_compat as oc
from baton.providers.base import ProviderError
from baton.types import CanonicalRequest, TextBlock, Usage, text


def _fake_response(
    *,
    content: str | None = "ok",
    finish_reason: str | None = "stop",
    prompt: int = 5,
    completion: int = 3,
    model: str | None = "kimi-k2",
    usage: object = "__default__",
) -> SimpleNamespace:
    if usage == "__default__":
        usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        model=model,
    )


def _install_fake_openai(monkeypatch, *, response=None, error=None):
    """Patch AsyncOpenAI dengan klien palsu; kembalikan (create_mock, capture)."""
    capture: dict = {}
    if error is not None:
        create_mock = AsyncMock(side_effect=error)
    else:
        create_mock = AsyncMock(return_value=response)

    def fake_ctor(*, base_url, api_key, timeout):
        capture["base_url"] = base_url
        capture["api_key"] = api_key
        capture["timeout"] = timeout
        client = MagicMock()
        client.chat.completions.create = create_mock
        return client

    monkeypatch.setattr(oc, "AsyncOpenAI", fake_ctor)
    return create_mock, capture


async def test_construct_passes_base_url_api_key_and_default_timeout(monkeypatch):
    _, capture = _install_fake_openai(monkeypatch, response=_fake_response())
    provider = oc.OpenAICompatProvider(
        base_url="https://api.moonshot.cn/v1",
        api_key="sk-test",
        model="kimi-k2-0711",
    )
    assert provider.name == "openai_compat"
    assert capture["base_url"] == "https://api.moonshot.cn/v1"
    assert capture["api_key"] == "sk-test"
    assert capture["timeout"] == 120.0
    assert provider.timeout == 120.0


async def test_custom_timeout_is_forwarded_to_client(monkeypatch):
    _, capture = _install_fake_openai(monkeypatch, response=_fake_response())
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="llama3.2",
        timeout=5.0,
    )
    assert capture["timeout"] == 5.0
    assert provider.timeout == 5.0


async def test_ollama_base_url_is_configurable(monkeypatch):
    _, capture = _install_fake_openai(monkeypatch, response=_fake_response())
    oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="llama3.2",
    )
    assert capture["base_url"] == "http://localhost:11434/v1"
    assert capture["api_key"] == "ollama"


async def test_complete_translates_request_with_default_output_param(monkeypatch):
    create_mock, _ = _install_fake_openai(monkeypatch, response=_fake_response())
    provider = oc.OpenAICompatProvider(
        base_url="https://api.moonshot.cn/v1",
        api_key="sk-test",
        model="kimi-k2-0711",
    )
    req = CanonicalRequest(
        messages=[text("system", "be terse"), text("user", "hi")],
        max_tokens=256,
        temperature=0.2,
    )
    await provider.complete(req)

    kwargs = create_mock.await_args.kwargs
    assert kwargs["model"] == "kimi-k2-0711"
    assert kwargs["max_tokens"] == 256
    assert kwargs["temperature"] == 0.2
    assert kwargs["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


async def test_output_tokens_param_name_comes_from_config(monkeypatch):
    # PATCH: nama param output disimpan di BackendConfig, bukan literal hardcode.
    create_mock, _ = _install_fake_openai(monkeypatch, response=_fake_response())
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="llama3.2",
        config=oc.BackendConfig(output_tokens_param="max_completion_tokens"),
    )
    await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=99)
    )
    kwargs = create_mock.await_args.kwargs
    assert kwargs["max_completion_tokens"] == 99
    assert "max_tokens" not in kwargs


async def test_default_backend_config_uses_max_tokens() -> None:
    assert oc.BackendConfig().output_tokens_param == "max_tokens"


async def test_complete_maps_response_and_marks_usage_real(monkeypatch):
    _install_fake_openai(
        monkeypatch,
        response=_fake_response(
            content="hello from kimi",
            finish_reason="stop",
            prompt=11,
            completion=7,
            model="kimi-k2-0711",
        ),
    )
    provider = oc.OpenAICompatProvider(
        base_url="https://api.moonshot.cn/v1",
        api_key="sk-test",
        model="kimi-k2-0711",
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=64)
    )
    assert resp.content == [TextBlock(text="hello from kimi")]
    assert resp.usage == Usage(prompt_tokens=11, completion_tokens=7)
    assert resp.usage.estimated is False
    assert resp.model == "kimi-k2-0711"
    assert resp.stop_reason == "end_turn"
    assert resp.latency_ms >= 0


@pytest.mark.parametrize(
    "finish, expected",
    [
        ("stop", "end_turn"),
        ("length", "max_tokens"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("content_filter", "content_filter"),
        (None, "end_turn"),
        ("something_weird", "end_turn"),
    ],
)
async def test_finish_reason_mapping_full_table(monkeypatch, finish, expected):
    _install_fake_openai(monkeypatch, response=_fake_response(finish_reason=finish))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
    )
    assert resp.stop_reason == expected


async def test_none_content_becomes_empty_textblock(monkeypatch):
    _install_fake_openai(monkeypatch, response=_fake_response(content=None))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
    )
    assert resp.content == [TextBlock(text="")]


async def test_missing_model_falls_back_to_configured(monkeypatch):
    _install_fake_openai(monkeypatch, response=_fake_response(model=None))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
    )
    assert resp.model == "llama3.2"


async def test_missing_usage_is_estimated_not_zero(monkeypatch):
    # PATCH: usage=None -> Usage(estimated=True), BUKAN Usage(0, 0).
    # _est(s) = max(1, len(s)//4): input "hello world" (11) -> 2 ;
    # output "short reply here" (16) -> 4
    _install_fake_openai(
        monkeypatch,
        response=_fake_response(content="short reply here", usage=None),
    )
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hello world")], max_tokens=16)
    )
    assert resp.usage == Usage(prompt_tokens=2, completion_tokens=4, estimated=True)
    assert resp.usage.estimated is True
    assert resp.usage != Usage(prompt_tokens=0, completion_tokens=0)


async def test_partial_usage_missing_one_field_is_estimated(monkeypatch):
    # usage ada tapi completion_tokens=None -> jatuh ke estimasi seluruhnya.
    # input "hi there" (8) -> 2 ; output "x" (1) -> max(1, 0) = 1
    partial = SimpleNamespace(prompt_tokens=10, completion_tokens=None)
    _install_fake_openai(
        monkeypatch, response=_fake_response(content="x", usage=partial)
    )
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi there")], max_tokens=16)
    )
    assert resp.usage.estimated is True
    assert resp.usage.prompt_tokens == 2
    assert resp.usage.completion_tokens == 1


async def test_none_content_with_missing_usage_estimates_at_least_one(monkeypatch):
    _install_fake_openai(
        monkeypatch, response=_fake_response(content=None, usage=None)
    )
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    resp = await provider.complete(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
    )
    assert resp.content == [TextBlock(text="")]
    assert resp.usage.estimated is True
    assert resp.usage.prompt_tokens >= 1
    assert resp.usage.completion_tokens >= 1


class _FakeStatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeTimeoutError(Exception):
    """Nama diakhiri 'TimeoutError' -> diklasifikasi sebagai error jaringan."""


class _FakeConnectionError(Exception):
    """Nama diakhiri 'ConnectionError' -> diklasifikasi sebagai error jaringan."""


@pytest.mark.parametrize(
    "status, retryable",
    [
        (408, True),
        (409, True),
        (429, True),
        (500, True),
        (503, True),
        (400, False),
        (401, False),
        (404, False),
        (422, False),
    ],
)
async def test_status_error_wrapped_with_correct_retryable(monkeypatch, status, retryable):
    _install_fake_openai(
        monkeypatch, error=_FakeStatusError("boom", status_code=status)
    )
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
        )
    err = excinfo.value
    assert err.status == status
    assert err.retryable is retryable
    assert "boom" in str(err)


async def test_timeout_error_retryable_without_status(monkeypatch):
    _install_fake_openai(monkeypatch, error=_FakeTimeoutError("read timed out"))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
        )
    assert excinfo.value.retryable is True
    assert excinfo.value.status is None


async def test_connection_error_retryable_without_status(monkeypatch):
    _install_fake_openai(monkeypatch, error=_FakeConnectionError("connection refused"))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
        )
    assert excinfo.value.retryable is True
    assert excinfo.value.status is None


async def test_unknown_error_without_status_is_not_retryable(monkeypatch):
    _install_fake_openai(monkeypatch, error=ValueError("weird sdk bug"))
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
        )
    assert excinfo.value.retryable is False
    assert excinfo.value.status is None


async def test_original_error_is_chained_as_cause(monkeypatch):
    original = _FakeStatusError("upstream 500", status_code=500)
    _install_fake_openai(monkeypatch, error=original)
    provider = oc.OpenAICompatProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2"
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            CanonicalRequest(messages=[text("user", "hi")], max_tokens=16)
        )
    assert excinfo.value.__cause__ is original

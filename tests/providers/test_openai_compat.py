# tests/providers/test_openai_compat.py
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import orchestrator.providers.openai_compat as oc
from orchestrator.types import CanonicalRequest, TextBlock, Usage, text


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

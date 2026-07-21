from __future__ import annotations

import pytest

from orchestrator.providers.openai_compat import OpenAICompatProvider
from orchestrator.types import CanonicalRequest, TextBlock, ToolUseBlock, text


class _D:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None, model="kimi-x"):
    delta = _D(content=content, tool_calls=tool_calls)
    choice = _D(delta=delta, finish_reason=finish_reason)
    return _D(choices=[choice], usage=usage, model=model)


def _tc(index, id=None, name=None, arguments=None):
    return _D(index=index, id=id, function=_D(name=name, arguments=arguments))


async def _aiter(items):
    for it in items:
        yield it


def _provider(monkeypatch, chunks):
    class _Completions:
        async def create(self, **kw):
            assert kw.get("stream") is True
            return _aiter(chunks)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        "orchestrator.providers.openai_compat.AsyncOpenAI", lambda **kw: _Client()
    )
    return OpenAICompatProvider(base_url="http://x/v1", api_key="k", model="kimi-x")


@pytest.mark.asyncio
async def test_stream_text(monkeypatch) -> None:
    chunks = [
        _chunk(content="hel"),
        _chunk(content="lo"),
        _chunk(finish_reason="stop", usage=_D(prompt_tokens=5, completion_tokens=2)),
    ]
    p = _provider(monkeypatch, chunks)
    got: list[str] = []
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), got.append
    )
    assert "".join(got) == "hello"
    assert isinstance(res.content[0], TextBlock)
    assert res.usage.prompt_tokens == 5


@pytest.mark.asyncio
async def test_stream_merges_tool_call_deltas(monkeypatch) -> None:
    chunks = [
        _chunk(tool_calls=[_tc(0, id="c1", name="run_python", arguments='{"co')]),
        _chunk(tool_calls=[_tc(0, arguments='de": "x"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    p = _provider(monkeypatch, chunks)
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "go")], max_tokens=16), lambda s: None
    )
    assert res.stop_reason == "tool_use"
    tu = res.content[0]
    assert isinstance(tu, ToolUseBlock)
    assert tu.id == "c1" and tu.name == "run_python"
    assert tu.input == {"code": "x"}

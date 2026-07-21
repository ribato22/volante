from __future__ import annotations

import pytest

from orchestrator.providers.anthropic import AnthropicProvider
from orchestrator.types import CanonicalRequest, TextBlock, ToolUseBlock, text


class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Final:
    def __init__(self, content, stop_reason="end_turn", model="claude-x"):
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _Blk(input_tokens=3, output_tokens=4)


class _StreamCtx:
    def __init__(self, deltas, final):
        self._deltas = deltas
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for d in self._deltas:
                yield d
        return _gen()

    async def get_final_message(self):
        return self._final


def _provider(monkeypatch, deltas, final):
    class _Msgs:
        def stream(self, **kw):
            return _StreamCtx(deltas, final)

    class _Client:
        def __init__(self):
            self.messages = _Msgs()

    monkeypatch.setattr(
        "orchestrator.providers.anthropic.anthropic.AsyncAnthropic", lambda **kw: _Client()
    )
    return AnthropicProvider(api_key="k", model="claude-x")


@pytest.mark.asyncio
async def test_stream_forwards_text_and_builds_response(monkeypatch) -> None:
    final = _Final([_Blk(type="text", text="hello there")])
    p = _provider(monkeypatch, ["hello ", "there"], final)
    got: list[str] = []
    res = await p.stream(CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), got.append)
    assert "".join(got) == "hello there"
    assert isinstance(res.content[0], TextBlock)
    assert res.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_stream_final_tool_use(monkeypatch) -> None:
    final = _Final(
        [_Blk(type="tool_use", id="u1", name="run_python", input={"code": "x"})],
        stop_reason="tool_use",
    )
    p = _provider(monkeypatch, [], final)
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "go")], max_tokens=16), lambda s: None
    )
    assert res.stop_reason == "tool_use"
    assert isinstance(res.content[0], ToolUseBlock)

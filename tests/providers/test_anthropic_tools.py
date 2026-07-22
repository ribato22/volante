from __future__ import annotations

import pytest

from baton.providers.anthropic import AnthropicProvider
from baton.types import (
    CanonicalMessage,
    CanonicalRequest,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    text,
)


class _FakeBlock:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class _FakeResp:
    def __init__(self, content, stop_reason="tool_use", model="claude-x") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _FakeBlock(input_tokens=5, output_tokens=7)


class _FakeMessages:
    def __init__(self, resp) -> None:
        self._resp = resp
        self.captured: dict | None = None

    async def create(self, **kwargs):
        self.captured = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, resp) -> None:
        self.messages = _FakeMessages(resp)


def _provider(monkeypatch, resp) -> tuple[AnthropicProvider, _FakeClient]:
    client = _FakeClient(resp)
    monkeypatch.setattr(
        "baton.providers.anthropic.anthropic.AsyncAnthropic",
        lambda **kw: client,
    )
    return AnthropicProvider(api_key="k", model="claude-x"), client


@pytest.mark.asyncio
async def test_request_carries_tools(monkeypatch) -> None:
    p, client = _provider(monkeypatch, _FakeResp([_FakeBlock(type="text", text="hi")], "end_turn"))
    spec = ToolSpec(name="run_python", description="run", input_schema={"type": "object"})
    req = CanonicalRequest(messages=[text("user", "go")], max_tokens=64, tools=[spec])
    await p.complete(req)
    assert client.messages.captured["tools"][0]["name"] == "run_python"


@pytest.mark.asyncio
async def test_parses_mixed_text_and_tool_use(monkeypatch) -> None:
    resp = _FakeResp(
        [
            _FakeBlock(type="text", text="let me run this"),
            _FakeBlock(type="tool_use", id="u1", name="run_python", input={"code": "print(1)"}),
        ],
        stop_reason="tool_use",
    )
    p, _ = _provider(monkeypatch, resp)
    out = await p.complete(CanonicalRequest(messages=[text("user", "go")], max_tokens=64))
    assert out.stop_reason == "tool_use"
    assert isinstance(out.content[0], TextBlock)
    assert isinstance(out.content[1], ToolUseBlock)
    assert out.content[1].input == {"code": "print(1)"}


@pytest.mark.asyncio
async def test_serializes_tool_result_in_messages(monkeypatch) -> None:
    p, client = _provider(monkeypatch, _FakeResp([_FakeBlock(type="text", text="ok")], "end_turn"))
    msgs = [
        CanonicalMessage(
            role="assistant",
            content=[ToolUseBlock(id="u1", name="run_python", input={"code": "x"})],
        ),
        CanonicalMessage(
            role="user", content=[ToolResultBlock(tool_use_id="u1", content="exit=0")]
        ),
    ]
    await p.complete(CanonicalRequest(messages=msgs, max_tokens=64))
    sent = client.messages.captured["messages"]
    assert sent[0]["content"][0]["type"] == "tool_use"
    assert sent[1]["content"][0]["type"] == "tool_result"
    assert sent[1]["content"][0]["tool_use_id"] == "u1"

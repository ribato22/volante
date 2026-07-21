from __future__ import annotations

import json

import pytest

from orchestrator.providers.openai_compat import OpenAICompatProvider
from orchestrator.types import (
    CanonicalMessage,
    CanonicalRequest,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    text,
)


class _Msg:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason="stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, choice, model="kimi-x") -> None:
        self.choices = [choice]
        self.model = model
        self.usage = None  # picu estimasi


class _FakeCompletions:
    def __init__(self, resp) -> None:
        self._resp = resp
        self.captured: dict | None = None

    async def create(self, **kwargs):
        self.captured = kwargs
        return self._resp


class _FakeChat:
    def __init__(self, resp) -> None:
        self.completions = _FakeCompletions(resp)


class _FakeClient:
    def __init__(self, resp) -> None:
        self.chat = _FakeChat(resp)


def _provider(monkeypatch, resp):
    client = _FakeClient(resp)
    monkeypatch.setattr(
        "orchestrator.providers.openai_compat.AsyncOpenAI", lambda **kw: client
    )
    return OpenAICompatProvider(base_url="http://x/v1", api_key="k", model="kimi-x"), client


@pytest.mark.asyncio
async def test_serializes_tools_as_functions(monkeypatch) -> None:
    p, client = _provider(monkeypatch, _Resp(_Choice(_Msg(content="hi"), "stop")))
    spec = ToolSpec(name="run_python", description="run", input_schema={"type": "object"})
    await p.complete(CanonicalRequest(messages=[text("user", "go")], max_tokens=64, tools=[spec]))
    tools = client.chat.completions.captured["tools"]
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "run_python"
    assert tools[0]["function"]["parameters"] == {"type": "object"}
    assert client.chat.completions.captured["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_no_tools_key_when_absent(monkeypatch) -> None:
    p, client = _provider(monkeypatch, _Resp(_Choice(_Msg(content="hi"), "stop")))
    await p.complete(CanonicalRequest(messages=[text("user", "go")], max_tokens=64))
    assert "tools" not in client.chat.completions.captured


@pytest.mark.asyncio
async def test_serializes_tool_use_and_tool_result_messages(monkeypatch) -> None:
    p, client = _provider(monkeypatch, _Resp(_Choice(_Msg(content="ok"), "stop")))
    msgs = [
        CanonicalMessage(
            role="assistant",
            content=[ToolUseBlock(id="c1", name="run_python", input={"code": "x"})],
        ),
        CanonicalMessage(
            role="user", content=[ToolResultBlock(tool_use_id="c1", content="exit=0")]
        ),
    ]
    await p.complete(CanonicalRequest(messages=msgs, max_tokens=64))
    sent = client.chat.completions.captured["messages"]
    asst = next(m for m in sent if m["role"] == "assistant")
    assert asst["tool_calls"][0]["id"] == "c1"
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["name"] == "run_python"
    assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"code": "x"}
    tool_msg = next(m for m in sent if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "exit=0"

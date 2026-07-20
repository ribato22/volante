from __future__ import annotations

import typing

from orchestrator.types import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    ContentBlock,
    Entry,
    ModelInfo,
    RunResult,
    Task,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
    text,
)


def test_text_block_defaults() -> None:
    b = TextBlock(text="hello")
    assert b.text == "hello"
    assert b.type == "text"


def test_tool_use_block() -> None:
    b = ToolUseBlock(id="t1", name="search", input={"q": "x"})
    assert (b.id, b.name, b.input, b.type) == ("t1", "search", {"q": "x"}, "tool_use")


def test_tool_result_block() -> None:
    b = ToolResultBlock(tool_use_id="t1", content="ok")
    assert (b.tool_use_id, b.content, b.type) == ("t1", "ok", "tool_result")


def test_content_block_union_members() -> None:
    assert set(typing.get_args(ContentBlock)) == {
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
    }


def test_canonical_message_holds_blocks() -> None:
    m = CanonicalMessage(role="user", content=[TextBlock(text="hi")])
    assert m.role == "user"
    assert isinstance(m.content[0], TextBlock)


def test_text_helper_builds_single_text_block_message() -> None:
    m = text("assistant", "hello world")
    assert isinstance(m, CanonicalMessage)
    assert m.role == "assistant"
    assert len(m.content) == 1
    assert isinstance(m.content[0], TextBlock)
    assert m.content[0].text == "hello world"


def test_tool_spec() -> None:
    s = ToolSpec(name="calc", description="adds", input_schema={"type": "object"})
    assert s.name == "calc"
    assert s.description == "adds"
    assert s.input_schema == {"type": "object"}


def test_usage() -> None:
    u = Usage(prompt_tokens=10, completion_tokens=5)
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 5


# --- PATCH v2.1: Usage.estimated ---

def test_usage_estimated_defaults_false() -> None:
    u = Usage(prompt_tokens=10, completion_tokens=5)
    assert u.estimated is False


def test_usage_estimated_can_be_set_true() -> None:
    u = Usage(prompt_tokens=1, completion_tokens=2, estimated=True)
    assert u.estimated is True
    assert (u.prompt_tokens, u.completion_tokens) == (1, 2)


def test_canonical_request_defaults() -> None:
    req = CanonicalRequest(messages=[text("user", "hi")], max_tokens=256)
    assert req.max_tokens == 256
    assert req.temperature == 0.7
    assert req.tools is None
    assert req.run_id == ""
    assert req.task_id == ""
    assert req.attempt == 0
    assert isinstance(req.messages[0], CanonicalMessage)


def test_canonical_request_with_tools() -> None:
    tool = ToolSpec(name="calc", description="adds", input_schema={})
    req = CanonicalRequest(
        messages=[text("user", "hi")],
        max_tokens=128,
        temperature=0.2,
        tools=[tool],
        run_id="r1",
        task_id="t1",
        attempt=2,
    )
    assert req.temperature == 0.2
    assert req.tools == [tool]
    assert (req.run_id, req.task_id, req.attempt) == ("r1", "t1", 2)


def test_canonical_response() -> None:
    resp = CanonicalResponse(
        content=[TextBlock(text="done")],
        usage=Usage(prompt_tokens=1, completion_tokens=2),
        model="anthropic/claude-opus-4-8",
        stop_reason="end_turn",
        latency_ms=42,
    )
    assert isinstance(resp.content[0], TextBlock)
    assert resp.usage.completion_tokens == 2
    assert resp.model == "anthropic/claude-opus-4-8"
    assert resp.stop_reason == "end_turn"
    assert resp.latency_ms == 42


def test_model_info() -> None:
    m = ModelInfo(
        id="anthropic/claude-opus-4-8",
        provider="anthropic",
        strengths={"coding", "reasoning"},
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        cost_per_1k_in=0.003,
        cost_per_1k_out=0.015,
    )
    assert m.id == "anthropic/claude-opus-4-8"
    assert m.provider == "anthropic"
    assert m.strengths == {"coding", "reasoning"}
    assert m.context_window == 200_000
    assert m.max_output_tokens == 8_192
    assert m.supports_tools is True
    assert m.cost_per_1k_in == 0.003
    assert m.cost_per_1k_out == 0.015


def test_task_default_depends_on() -> None:
    t = Task(id="a", description="do a", type="code", mode="one_shot")
    assert t.id == "a"
    assert t.description == "do a"
    assert t.type == "code"
    assert t.mode == "one_shot"
    assert t.depends_on == []


def test_task_with_deps_are_independent_instances() -> None:
    t1 = Task(id="b", description="do b", type="research", mode="agentic")
    t2 = Task(id="c", description="do c", type="write", mode="one_shot")
    t1.depends_on.append("a")
    # default_factory must give each instance its own list
    assert t1.depends_on == ["a"]
    assert t2.depends_on == []


def test_entry_with_usage() -> None:
    e = Entry(
        run_id="r1",
        task_id="a",
        attempt=0,
        kind="artifact",
        payload={"text": "result"},
        model_id="anthropic/claude-opus-4-8",
        usage=Usage(prompt_tokens=3, completion_tokens=4),
        timestamp=1234.5,
    )
    assert e.kind == "artifact"
    assert e.payload == {"text": "result"}
    assert e.model_id == "anthropic/claude-opus-4-8"
    assert e.usage is not None
    assert e.usage.completion_tokens == 4
    assert e.timestamp == 1234.5


def test_entry_allows_none_model_and_usage() -> None:
    e = Entry(
        run_id="r1",
        task_id="a",
        attempt=1,
        kind="status",
        payload="pending",
        model_id=None,
        usage=None,
        timestamp=0.0,
    )
    assert e.model_id is None
    assert e.usage is None
    assert e.payload == "pending"


def test_run_result_success() -> None:
    r = RunResult(
        status="success",
        final="final answer",
        partial_artifacts={"a": "art-a"},
        failed_task=None,
    )
    assert r.status == "success"
    assert r.final == "final answer"
    assert r.partial_artifacts == {"a": "art-a"}
    assert r.failed_task is None


def test_run_result_failed() -> None:
    r = RunResult(
        status="failed",
        final=None,
        partial_artifacts={},
        failed_task="b",
    )
    assert r.status == "failed"
    assert r.final is None
    assert r.partial_artifacts == {}
    assert r.failed_task == "b"


# --- PATCH v2.1: RunResult cost/usage close-out fields ---

def test_run_result_new_cost_fields_default() -> None:
    # Konstruksi lama (tanpa field baru) HARUS tetap valid; field baru ber-default.
    r = RunResult(
        status="success",
        final="x",
        partial_artifacts={},
        failed_task=None,
    )
    assert r.usage_total == {}
    assert r.cost_usd == 0.0
    assert r.duration_ms == 0


def test_run_result_usage_total_is_independent_per_instance() -> None:
    # usage_total wajib pakai default_factory -> tidak berbagi dict antar instance.
    a = RunResult(status="failed", final=None, partial_artifacts={}, failed_task="t")
    b = RunResult(status="failed", final=None, partial_artifacts={}, failed_task="t")
    a.usage_total["m"] = Usage(prompt_tokens=1, completion_tokens=1)
    assert a.usage_total == {"m": Usage(prompt_tokens=1, completion_tokens=1)}
    assert b.usage_total == {}


def test_run_result_accepts_populated_cost_fields() -> None:
    r = RunResult(
        status="success",
        final="done",
        partial_artifacts={"t1": "art"},
        failed_task=None,
        usage_total={"m1": Usage(prompt_tokens=3, completion_tokens=4, estimated=True)},
        cost_usd=1.25,
        duration_ms=1500,
    )
    assert r.usage_total["m1"] == Usage(prompt_tokens=3, completion_tokens=4, estimated=True)
    assert r.usage_total["m1"].estimated is True
    assert r.cost_usd == 1.25
    assert r.duration_ms == 1500

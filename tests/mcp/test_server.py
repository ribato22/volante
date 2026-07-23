"""Tests for the FastMCP wiring. Skipped if the optional `mcp` dep is absent."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from baton_mcp.server import build_server  # noqa: E402 - after importorskip


class _FakeRuntime:
    async def aexecute(self, goal, on_text=None, on_worker_text=None):
        class _R:
            status = "success"
            final = f"answered: {goal}"
            failed_task = None
            cost_usd = 0.0
            billed_usd = 0.0
            credit_usd = 0.0
            duration_ms = 5

        return _R()


async def test_server_registers_baton_run_tool() -> None:
    server = build_server(runtime_factory=lambda: _FakeRuntime())
    tools = await server.list_tools()
    assert "baton_run" in {t.name for t in tools}


async def test_baton_run_tool_returns_formatted_answer() -> None:
    server = build_server(runtime_factory=lambda: _FakeRuntime())
    result = await server.call_tool("baton_run", {"goal": "greet the world"})
    assert "answered: greet the world" in str(result)

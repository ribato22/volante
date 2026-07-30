"""Tests for the FastMCP wiring. Skipped if the optional `mcp` dep is absent."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from volante_mcp.server import build_server  # noqa: E402 - after importorskip


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


async def test_server_registers_volante_run_tool() -> None:
    server = build_server(runtime_factory=lambda: _FakeRuntime())
    tools = await server.list_tools()
    assert "volante_run" in {t.name for t in tools}


async def test_volante_run_tool_returns_formatted_answer() -> None:
    server = build_server(runtime_factory=lambda: _FakeRuntime())
    result = await server.call_tool("volante_run", {"goal": "greet the world"})
    assert "answered: greet the world" in str(result)


def test_published_schema_names_the_legal_objectives() -> None:
    """The calling model reads the contract, not our source.

    `prefer` used to publish as a bare string, so an agent guessed "fast" and only
    found out after a live CLI-agent call had been spent. An enum lets the client
    refuse it for free.
    """
    import asyncio

    from volante_mcp.server import build_server

    tools = asyncio.run(build_server().list_tools())
    prefer = next(t for t in tools if t.name == "volante_run").inputSchema[
        "properties"
    ]["prefer"]
    enums = [branch["enum"] for branch in prefer["anyOf"] if "enum" in branch]

    assert enums, "prefer is published without an enum; agents are left guessing"
    assert sorted(enums[0]) == [
        "cash_protect_quota",
        "cheap",
        "local",
        "quality",
    ]


def test_published_objectives_match_the_engine() -> None:
    """Deliberately a test and not an import.

    This is a published wire contract: adding an objective to the router should be a
    decision to publish it, not an automatic consequence. The test makes the drift
    visible at the moment it happens instead of at a user's first bad call.
    """
    from volante_mcp.server import _OBJECTIVES as PUBLISHED

    from volante.router import _OBJECTIVES as ENGINE

    assert PUBLISHED == ENGINE

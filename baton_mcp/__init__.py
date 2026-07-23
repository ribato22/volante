"""Baton MCP server — expose the orchestration engine to IDE AI agents.

Run as ``python -m baton_mcp`` (stdio transport). Install the optional dependency
with ``uv sync --extra mcp``.
"""

from __future__ import annotations

from baton_mcp.server import build_server, format_result, run_goal

__all__ = ["build_server", "run_goal", "format_result"]

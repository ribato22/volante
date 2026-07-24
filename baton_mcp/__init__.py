"""Baton MCP server — expose the orchestration engine to IDE AI agents.

Run as ``baton-mcp`` (console script) or ``python -m baton_mcp`` (both speak MCP over
stdio). Install the optional dependency with the ``mcp`` extra:
``pip install "baton-orchestrator[mcp]"`` (or ``uv sync --extra mcp`` from a checkout).
"""

from __future__ import annotations

from baton_mcp.server import build_server, format_result, run_goal

__all__ = ["build_server", "run_goal", "format_result", "main"]


def main() -> None:
    """Console entry point (``baton-mcp``): serve the Baton MCP server over stdio.

    FastMCP's stdio transport is what IDE MCP clients (Claude Code, Cursor, VS Code,
    Windsurf) launch and speak to.
    """
    try:
        server = build_server()
    except ImportError as exc:  # the optional `mcp` dependency isn't installed
        raise SystemExit(
            "baton-mcp needs the 'mcp' extra. Install it with:\n"
            "  pip install \"baton-orchestrator[mcp]\"\n"
            "or, from a source checkout:\n"
            "  uv sync --extra mcp"
        ) from exc
    server.run()

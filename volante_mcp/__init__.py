"""Volante MCP server — expose the orchestration engine to IDE AI agents.

Run as ``volante-mcp`` (console script) or ``python -m volante_mcp`` (both speak MCP over
stdio). Install the optional dependency with the ``mcp`` extra:
``pip install "volante[mcp]"`` (or ``uv sync --extra mcp`` from a checkout).
"""

from __future__ import annotations

from volante_mcp.server import build_server, format_result, run_goal

__all__ = ["build_server", "run_goal", "format_result", "main"]


def main() -> None:
    """Console entry point (``volante-mcp``): serve the Volante MCP server over stdio.

    FastMCP's stdio transport is what IDE MCP clients (Claude Code, Cursor, VS Code,
    Windsurf) launch and speak to.
    """
    from volante.observability import configure_logging

    # Honor VOLANTE_LOG so IDE users can raise diagnostics into the MCP "Output" pane
    # (stderr) without editing code; silent by default.
    configure_logging()
    try:
        server = build_server()
    except ImportError as exc:  # the optional `mcp` dependency isn't installed
        raise SystemExit(
            "volante-mcp needs the 'mcp' extra. Install it with:\n"
            "  pip install \"volante[mcp]\"\n"
            "or, from a source checkout:\n"
            "  uv sync --extra mcp"
        ) from exc
    server.run()

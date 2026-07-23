"""Entry point: ``python -m baton_mcp`` runs the Baton MCP server over stdio."""

from __future__ import annotations


def main() -> None:
    from baton_mcp.server import build_server

    # FastMCP.run() defaults to the stdio transport, which is what IDE MCP clients
    # (Claude Code, Cursor, VS Code, Windsurf) launch and speak to.
    build_server().run()


if __name__ == "__main__":
    main()

# Volante — Claude Code plugin

Bundles the [Volante](https://github.com/ribato22/volante) MCP server and a slash command
so Claude Code can delegate whole goals to Volante's orchestration engine.

## Install

```
/plugin marketplace add ribato22/volante
/plugin install volante@volante
```

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/) on your PATH (the plugin launches the
server with `uvx`, which fetches the pinned `volante[mcp]==0.3.0` package from
PyPI on first use), and at least one provider configured in your environment (e.g.
`CLAUDE_CODE_ENABLED=1`, `CODEX_ENABLED=1`, `ANTHROPIC_API_KEY`, or an
`OPENAI_COMPAT_*` endpoint — see the
[main README](https://github.com/ribato22/volante#providers)).

## What it adds

- **MCP server `volante`** exposing the `volante_run(goal, prefer?)` tool — plan → route →
  run → synthesize, returning the final answer plus a cash/plan-credit cost footer.
- **Slash command `/volante:run <goal>`** — a shortcut that asks the agent to orchestrate a
  goal through `volante_run`.
- **Skill `orchestrate`** — a narrowly-scoped skill that lets the agent *auto-delegate* to Volante
  **only** for large, multi-part goals that benefit from multi-model orchestration (not simple
  tasks), to avoid over-invocation.

The server is the same one listed in the [official MCP Registry](https://registry.modelcontextprotocol.io)
as `io.github.ribato22/volante`; installing the plugin is just a one-command way to wire it
into Claude Code.

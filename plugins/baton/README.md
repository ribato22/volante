# Baton — Claude Code plugin

Bundles the [Baton](https://github.com/ribato22/baton) MCP server and a slash command
so Claude Code can delegate whole goals to Baton's orchestration engine.

## Install

```
/plugin marketplace add ribato22/baton
/plugin install baton@baton
```

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/) on your PATH (the plugin launches the
server with `uvx`, which fetches `baton-orchestrator[mcp]` from PyPI on first use), and at
least one provider configured in your environment (e.g. `CLAUDE_CODE_ENABLED=1`,
`CODEX_ENABLED=1`, `ANTHROPIC_API_KEY`, or an `OPENAI_COMPAT_*` endpoint — see the
[main README](https://github.com/ribato22/baton#providers)).

## What it adds

- **MCP server `baton`** exposing the `baton_run(goal, prefer?)` tool — plan → route →
  run → synthesize, returning the final answer plus a cash/plan-credit cost footer.
- **Slash command `/baton:run <goal>`** — a shortcut that asks the agent to orchestrate a
  goal through `baton_run`.
- **Skill `orchestrate`** — a narrowly-scoped skill that lets the agent *auto-delegate* to Baton
  **only** for large, multi-part goals that benefit from multi-model orchestration (not simple
  tasks), to avoid over-invocation.

The server is the same one listed in the [official MCP Registry](https://registry.modelcontextprotocol.io)
as `io.github.ribato22/baton`; installing the plugin is just a one-command way to wire it
into Claude Code.

# Baton MCPB bundle

[`manifest.json`](manifest.json) is the source for an **MCPB bundle** (`.mcpb`) of the Baton MCP
server. The bundle is a portable, one-file way to install the server:

- **Claude Desktop / other MCPB clients** — open the `.mcpb` to install with a config UI (fill in
  your providers).
- **Smithery** — publish the `.mcpb` as a *local* server via the CLI (the web "publish a URL" form
  is only for hosted HTTPS servers): `npx -y @smithery/cli mcp publish ./baton-<version>.mcpb -n
  <your-namespace>/baton` (needs a Smithery API key).

The bundle just wraps the launch command `uvx --from "baton-orchestrator[mcp]" baton-mcp`, so it runs
**locally on your machine** — your subscription CLIs (`claude`, `codex`) and API keys work exactly as
they do for the CLI. Requires [`uv`](https://docs.astral.sh/uv/) on your PATH.

## Build

```bash
npx @anthropic-ai/mcpb pack mcpb baton-<version>.mcpb   # validate + pack
npx @anthropic-ai/mcpb validate mcpb/manifest.json      # validate only
```

A prebuilt `baton-<version>.mcpb` is attached to each [GitHub release](https://github.com/ribato22/baton/releases).

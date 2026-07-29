# Volante MCPB bundle

[`manifest.json`](manifest.json) is the source for an **MCPB bundle** (`.mcpb`) of the Volante MCP
server. The bundle is a portable, one-file way to install the server:

- **Claude Desktop / other MCPB clients** — open the `.mcpb` to install with a config UI (fill in
  your providers).
- **Smithery** — publish the `.mcpb` as a *local* server via the CLI (the web "publish a URL" form
  is only for hosted HTTPS servers): `npx -y @smithery/cli mcp publish ./volante-<version>.mcpb -n
  <your-namespace>/volante` (needs a Smithery API key).

The bundle wraps the version-pinned launch command
`uvx --from "volante[mcp]==0.3.1" volante-mcp`, so it runs **locally on your
machine**. Your subscription CLIs (`claude`, `codex`), local endpoints, inventory files,
and API keys remain on that machine. Requires [`uv`](https://docs.astral.sh/uv/) on your
PATH.

## Configuration inventory

The install UI exposes the full P0/P1 provider inventory:

- Anthropic accepts `ANTHROPIC_API_KEY` plus comma-separated `ANTHROPIC_MODELS` and
  optional parallel canonical ids in `ANTHROPIC_NAMES`.
- Moonshot accepts `MOONSHOT_API_KEY`, `MOONSHOT_BASE_URL`, comma-separated
  `MOONSHOT_MODELS`, and optional `MOONSHOT_NAMES`.
- Ollama accepts `OLLAMA_BASE_URL`, optional `OLLAMA_API_KEY`, comma-separated
  `OLLAMA_MODELS`, and optional `OLLAMA_NAMES`.
- Claude Code and Codex subscription agents are explicit opt-ins through
  `CLAUDE_CODE_ENABLED` and `CODEX_ENABLED`. Both accept comma-separated model lists
  (`CLAUDE_CODE_MODELS` and `CODEX_MODELS`) plus their documented tier, output, timeout,
  context, valuation, and system-prompt controls.
- Three independent OpenAI-compatible slots are available:
  `OPENAI_COMPAT_*`, `OPENAI_COMPAT_2_*`, and `OPENAI_COMPAT_3_*`. Each slot exposes its
  own base URL, key, wire model, canonical name, strengths, context, maximum output,
  tool-support declaration, input/output cost, and tier.

Anthropic, Moonshot, and Ollama family metadata use the corresponding
`*_STRENGTHS`, `*_CONTEXT`, `*_MAX_OUTPUT`, `*_TOOLS`, `*_COST_IN`, `*_COST_OUT`, and
`*_TIER` fields. These values are shared by models in that family. Use
`VOLANTE_MODEL_OVERRIDES_FILE` when individual models need different metadata, and
`VOLANTE_QUALITY_PROFILES_FILE` to supply strict-JSON quality evidence keyed by canonical
model id. Volante routes to the best predicted fit from configured metadata/evidence after
hard-capability filtering; the evidence file is therefore an input to the decision, not
a guarantee of objective model quality.

Runtime controls are also exposed: `VOLANTE_FETCH_ALLOWLIST` enables confined `fetch_url`,
`VOLANTE_READ_ROOT` enables confined `read_file`, `VOLANTE_SANDBOX` selects `subprocess` or
`docker`, and `VOLANTE_MAX_SUBSCRIPTION_CALLS` caps physical subscription-provider calls
per run (default `16`). Untouched optional metadata fields may be emitted as empty
strings by MCPB clients; Volante treats those as unset and preserves its engine defaults.
Provider identity remains strict—for example, an OpenAI-compatible base URL still
requires its matching wire model.

## Build

```bash
npx @anthropic-ai/mcpb@2.1.2 pack mcpb volante-<version>.mcpb   # validate + pack
npx @anthropic-ai/mcpb@2.1.2 validate mcpb/manifest.json      # validate only
```

A prebuilt `volante-<version>.mcpb` is attached to each [GitHub release](https://github.com/ribato22/volante/releases).

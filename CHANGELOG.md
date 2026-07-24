# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **MCPB bundle (Claude Desktop + Smithery).** A one-file [`mcpb/`](mcpb/) manifest packs into a
  `baton-<version>.mcpb` (attached to each release) — open it in Claude Desktop for a one-click
  install with a provider-config UI, or upload it as a local server at smithery.ai/new. It wraps
  `uvx --from "baton-orchestrator[mcp]" baton-mcp`, so it runs locally (subscription CLIs + API keys
  work). Built/validated with `@anthropic-ai/mcpb`.
- **Multi-client MCP docs + Smithery manifest.** A [`smithery.yaml`](smithery.yaml) (stdio +
  provider config schema) for listing on [smithery.ai](https://smithery.ai), plus a README section
  with exact config for OpenAI Codex CLI (`codex mcp add …`), Gemini CLI, Cursor, Windsurf, and
  Cline/Roo. (These clients integrate MCP servers via config, not a plugin marketplace.)
- **Claude Code plugin.** The repo doubles as a plugin marketplace
  (`.claude-plugin/marketplace.json` + [`plugins/baton/`](plugins/baton/)): one command
  (`/plugin marketplace add ribato22/baton` then `/plugin install baton@baton`) wires the Baton
  MCP server and a `/baton:run <goal>` slash command into Claude Code. Validated with
  `claude plugin validate`.

## [0.2.1] - 2026-07-24

### Added
- **Listed in the official MCP Registry.** Adds a validated [`server.json`](server.json) and a
  GitHub Actions workflow (`publish-mcp.yml`) that publishes it to
  `registry.modelcontextprotocol.io` via OIDC on each release, plus the required PyPI
  ownership marker (`mcp-name: io.github.ribato22/baton`) in the README. Clients can install
  Baton clone-free with `uvx --from "baton-orchestrator[mcp]" baton-mcp`.

## [0.2.0] - 2026-07-24

### Added
- **Installable MCP server.** `baton_mcp` now ships in the wheel with a `baton-mcp` console
  script, so an IDE AI agent can run it clone-free via
  `uvx --from "baton-orchestrator[mcp]" baton-mcp` (or `pip install "baton-orchestrator[mcp]"`
  then `baton-mcp`); `python -m baton_mcp` still works. Previously it ran only from a source
  checkout. Invoked without the `mcp` extra, `baton-mcp` now exits with an install hint instead
  of a traceback.
- **`quality` routing objective (now the default).** The router picks the strongest model
  capable of each task (highest tier that matches the required strengths + tool support; ties
  broken toward the cash-free subscription option, then id), so the best available model answers
  each task. It is the objective wired into the CLI, Web UI, MCP server, and `make_runtime_factory`
  by default. `Router`/`route_ranked` now genuinely branch on `prefer` (previously only
  `cash_protect_quota` was implemented).
- **Python 3.11 support**: lowered `requires-python` to `>=3.11` (verified — the full
  test suite passes on 3.11), added the 3.11 trove classifier and a 3.11 CI matrix leg;
  ruff/mypy targets lowered to `py311`/`3.11` accordingly.
- Quickstart now has a zero-API-key one-liner (`uv run python examples/fake_provider.py`)
  so a newcomer sees the engine orchestrate end-to-end before configuring any provider.

### Changed
- **Default routing objective is now `quality`** (was `cash_protect_quota`). By default Baton now
  favors the strongest capable model per task rather than right-sizing to the cheapest adequate one,
  so it may use stronger/subscription-billed models more often (and consume more interactive quota).
  Pass `--prefer cash_protect_quota` (CLI) or `prefer="cash_protect_quota"` to restore the previous
  quota-protecting behavior. Docs reframed accordingly (routing headline, diagram, tables, MCP tool,
  subscription note).
- Bumped all GitHub Actions to their Node-24 releases (checkout v5, setup-uv v7,
  upload-artifact v7, download-artifact v8) to clear the Node-20 deprecation warning —
  still fully commit-SHA-pinned.
- Parameterized the public `dict` annotations in `baton.types` (`ToolUseBlock.input`,
  `ToolSpec.input_schema` -> `dict[str, Any]`) to better honor the shipped `py.typed`.

### Security
- Enabled GitHub secret scanning and push protection on the repository.

## [0.1.0] - 2026-07-23

### Added
- Supervisor + routing engine: goal → validated task DAG → per-task model routing (by strengths and
  tool support) → scoped, budget-capped projection → wave execution (async fan-out, fail-fast) →
  synthesis, with a `CostMeter` (per-model usage/cost, estimated-flag propagation).
- Provider adapters: `AnthropicProvider` and a tool-capable `OpenAICompatProvider`, plus one or more
  generic OpenAI-compatible slots (`OPENAI_COMPAT_*`, then `OPENAI_COMPAT_2_*`, `OPENAI_COMPAT_3_*`,
  …) for any endpoints (Gemini / Groq / OpenRouter / DeepSeek / Ollama) at once — each with its own
  model_id, pricing, and context window, enabling genuine cross-provider orchestration.
- Hybrid execution: one-shot workers and an agentic model↔tool loop (`run_python` sandbox,
  host-mediated `fetch_url` / `read_file`).
- Isolation: subprocess `Sandbox` (process-group kill, `RLIMIT_CPU`, scrubbed env) and an opt-in
  `DockerSandbox` (`--network none`, read-only root, cgroup limits).
- Streaming across supervisor / workers / synthesizer, with per-task labelled parallel-worker
  streaming and cooperative early-stop.
- Evaluation: 5 composite goals, a 3-arm comparison (baseline / orchestration / agentic-single), and
  a forgery-resistant scorer using process + filesystem separation with a nonce-authenticated RPC.
- **IDE / MCP integration.** A Model Context Protocol server (`baton_mcp/`, optional `mcp` extra;
  run `uv run --extra mcp python -m baton_mcp`) exposes one tool, `baton_run(goal, prefer?)`, so an AI
  assistant inside an editor (Claude Code, Cursor, VS Code agent mode, Windsurf) can delegate a whole
  goal to Baton and get the synthesized answer + cash/plan-credit footer back. Ships shared
  VSCode config (`.vscode/tasks.json` one-keystroke Run-goal / Web-UI / MCP / test / lint tasks,
  `.vscode/extensions.json`) and a README "In your IDE (VSCode) & MCP" section. Like `webui/`, the
  server is source-checkout-only (not in the wheel), so no broken console-script ships.
- Cost model: `ModelInfo.tier`/`billing` (`card` | `plan_credit` | `plan_included`), `Task.difficulty`,
  and a two-ledger `CostMeter` (`costs_usd()` splits `billed_usd` vs `credit_usd`; `RunResult` surfaces
  both). All defaulted/inert today (every seed is `billing="card"`) — groundwork for subscription
  providers.
- Difficulty- and billing-aware routing (`Router.route_ranked`, objective `cash_protect_quota`):
  subscription-billed models are used only for `hard` tasks; non-hard work stays on `card`/local
  providers to protect subscription quota, logging when a subscription fallback is unavoidable.
- Quota-exhausted reroute: a `ProviderError.quota_exhausted` flag + a 429 quota-vs-transient classifier
  route a run to the next candidate (with mandatory per-candidate re-projection) instead of backing off,
  across both the one-shot and agentic paths; a per-run `BATON_MAX_SUBSCRIPTION_CALLS` cap (default 4)
  bounds subscription dispatches.
- Opt-in subscription CLI-agent providers wired into
  `build_providers_from_env(include_subscription=True)`: Claude Code (`claude -p`) and Codex
  (`codex exec`) register only when `CLAUDE_CODE_ENABLED=1` / `CODEX_ENABLED=1` **and** the CLI is
  confirmed available — Claude Code via a PATH check, Codex via `codex_detected()` (a real
  `codex login status` probe, so a `codex` binary on PATH but not logged in is correctly NOT
  registered). They are `billing="plan_included"` (they draw your interactive subscription
  quota) and print an honesty warning on registration. The registered `ModelInfo` for both legs
  comes from the existing seed helpers (`claude_code_model_info()` / `build_codex_model()`), so
  the id follows the configured wire model (e.g. `CLAUDE_CODE_MODEL=sonnet` → `claude-code/sonnet`;
  unset `CODEX_MODEL` → `codex/default`) instead of a hardcoded id.
- Local-first wiring: Supervisor/Synthesizer default to a temperature-controllable (card-billed
  API/Ollama/free-tier) model even when routing prefers subscription, so planning stays
  deterministic (`claude -p` ignores temperature); `verify_claude_plan_gate` promotes `claude -p`
  to planner only when it emits a plan that passes the supervisor's own parser.
- Eval fence: `build_providers_from_env()` defaults to `include_subscription=False`, so the eval
  never consumes interactive subscription quota.
- `make_runtime_factory` gains a keyword-only `prefer` (default `"cash_protect_quota"`, matching
  `Router`'s own default — genuine back-compat) and now forwards it to
  `Router(registry, prefer=prefer)` instead of always defaulting the router's objective.
- The `baton` one-command CLI (`[project.scripts] baton = baton.cli:main`): streams the plan / labelled
  per-task worker output / synthesis live, then prints a `billed_usd` vs `credit_usd` +
  `subscription_models` summary. Flags: `--prefer/--provider/--model/--json/--no-stream` and
  `--version`. Exit codes `0` success / `1` run failure / `2` config error / `130` Ctrl-C (prints
  partial output, never a traceback), plus clean broken-pipe handling (e.g. `baton goal | head`).
- `Router.route_ranked` right-sizes the tier tiebreak among cash-tied models (lowest adequate tier
  first), so same-cost subscription providers distribute work across providers instead of always
  picking one.
- Supervisor bounded self-correcting plan retry (up to 3 attempts) that feeds the actual rejection
  error back to the planner, for CLI-agent planners that answer the goal instead of emitting the
  plan JSON.
- Web UI redesign: a Plan -> Workers -> Synthesis -> Result **phase-progress stepper** with honest
  per-phase `pending`/`active`/`done`/`failed` states (driven by new lightweight `stage` boundary
  events from `webui/runner.py`), replacing the single "running" badge that used to sit on the
  Result the whole run. The result now shows a two-ledger `cash` vs `plan credit` breakdown plus
  duration (the runner forwards `billed_usd`/`credit_usd`). Refreshed to a professional dark theme
  (system sans for chrome, mono for streamed code/output), accessible (aria-live log regions,
  visible focus rings, `prefers-reduced-motion`) and responsive — still one self-contained HTML
  document with no build step or external assets, and every dynamic value inserted via
  `textContent`/DOM nodes only (XSS-safe).

### Fixed
- Workers and the synthesizer now answer in the **same language as the goal** (an English goal no
  longer comes back in another language): the projector's worker system prompt and the synthesizer
  prompt both instruct the model to match the goal's language.
- `Worker.run_one_shot` now forwards `resp.cost_usd` into `CostMeter.add(..., cost_usd=...)`, so a
  subscription CLI-agent provider's authoritative call cost reaches the credit ledger
  (`costs_usd()`'s `credit_usd`) instead of being silently dropped.
- Codex gating now calls `codex_detected()` (`codex login status` exit 0) instead of a bare PATH
  lookup; a `codex` binary present-but-not-logged-in no longer registers a live-looking, ~$0-cash
  provider that the router would otherwise rank first for every `hard` task before failing over.
- `CodexAdapter.argv` no longer emits `--config model=` with an empty value when `CODEX_MODEL` is
  unset (which broke a real `codex exec` spawn); the pair is omitted entirely so codex falls back
  to the user's own configured default model, matching the README's documented behavior.
- Bootstrap no longer inlines duplicate `ModelInfo` definitions for the Claude Code / Codex
  subscription seeds (they had already drifted from `claude_code_model_info()` /
  `build_codex_model()` — e.g. missing the `long_context` strength, a different default
  `context_window`); both legs now build their registered `ModelInfo` from those single-source
  helpers.
- `claude -p` streaming requires `--verbose` with `--output-format stream-json` (added; the CLI
  otherwise refuses the spawn); `ClaudeCodeAdapter.argv` also passes `--disallowedTools LSP`
  (belt-and-suspenders on top of `--tools ""`) and `child_env` scrubs `ANTHROPIC_API_KEY`
  (guarantees the call bills the subscription, never the metered API key). The `CodexAdapter`'s
  JSONL wire shape was corrected to the live format: agent text lives in
  `item.completed`/`agent_message`, usage lives on the terminal `turn.completed` event, and there is
  no `total_cost_usd` anywhere on the real wire.

### Changed
- ClaudeCode default `CLAUDE_CODE_SYSTEM_PROMPT_MODE` is now `replace` (was `append`) — live-verified
  that `append` makes `claude -p` answer the goal instead of planning.
- **Routing may cost more for multi-provider setups.** The new difficulty→tier filter means a default
  (`medium`) task no longer routes to a very weak/cheap model when a stronger tier-adequate one exists.
  Example: with Opus (tier 4) + Kimi (tier 3) + a local tier-1 model configured, a `medium` task now
  routes to Kimi instead of the tier-1 model. Single-provider and local-only setups are unaffected
  (best-effort fallback preserves prior behavior).

### Security
- Hardened the GitHub Actions workflows before first publish: top-level least-privilege
  `permissions: contents: read` on CI and Release (the publish job alone opts into `id-token: write`),
  every `uses:` pinned to a full commit SHA (incl. the OIDC-privileged `pypa/gh-action-pypi-publish`),
  and a release-time guard that fails if the pushed `vX.Y.Z` tag doesn't match the built wheel version
  (prevents an immutable, mislabeled artifact from reaching PyPI).
- The Code of Conduct now routes reports to a **private** channel (maintainer email / private GitHub
  Security Advisory) instead of the public issue tracker, and is linked from CONTRIBUTING and the README.

[Unreleased]: https://github.com/ribato22/baton/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/ribato22/baton/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ribato22/baton/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ribato22/baton/releases/tag/v0.1.0

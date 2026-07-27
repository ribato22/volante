# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-27

### Added
- **Whole-inventory, evidence-aware routing.** Every explicitly configured model across Anthropic,
  OpenAI-compatible slots, Moonshot, Ollama, Claude Code, and Codex is evaluated for each task.
  Hard capability constraints run before a deterministic, explainable score; optional strict
  `ModelQualityProfile` JSON adds task quality, reliability, confidence, and evidence provenance.
  `quality`, `local`, `cheap`, and `cash_protect_quota` now have distinct semantics.
- **Auditable model selection.** `RunResult` carries complete per-task decisions (eligible and
  rejected candidates, score components, selected and actually executed model, objective, caveats).
  CLI, MCP, and Web UI expose the route trace, structured failure details, and physical
  subscription-call count. `volante --list-models [--json]` reports the full offline inventory.
- **Repeatable provider inventories.** `ANTHROPIC_MODELS`, `MOONSHOT_MODELS`, `OLLAMA_MODELS`,
  `CLAUDE_CODE_MODELS`, and `CODEX_MODELS` register multiple models while preserving singular env
  compatibility; optional parallel `*_NAMES` provide canonical ids, and strict per-model override
  JSON supplies heterogeneous hard capabilities, limits, tiers, and prices.
- **Usage observability.** A `VOLANTE_LOG` level knob surfaces engine diagnostics on stderr (e.g. VS
  Code's MCP "Output" pane), silent by default. Every CLI, Web UI, and MCP run best-effort appends
  one JSON line (status, cash vs plan credit, subscription calls, duration, selected models,
  truncated goal) to a usage ledger (`~/.volante/usage.jsonl`, `VOLANTE_USAGE_LOG`-configurable, empty
  disables). `volante --usage [--json]` prints it, and the Web UI gains an authenticated `/usage`
  dashboard (summary tiles + recent-runs table) so goals delegated from an IDE via MCP stay
  auditable. Records are truncated under `PIPE_BUF` for atomic concurrent appends.
- **Evidence-weighted routing + `--calibrate`.** A scoring component that says the same thing
  about every eligible model now gets ZERO weight, with its share redistributed to the
  components that carry information, and the decision trace names what was dropped. Previously
  `task_fit` (45 of 100) and `reliability` (4) were flat constants whenever no quality profile
  was configured — 49% of a "predicted quality" score that could not rank anything, while
  reading as evidence. The weighting is decided once per routing call, so all candidates stay
  comparable, and rankings are unchanged (a constant shifts every score equally).
  `volante --calibrate measurements.json` turns scores you measured into a strict quality-profiles
  file: per-task-type means, a macro-averaged `overall_score` so an unbalanced sample cannot drive
  it, `confidence` taken from the weakest-sampled task type and capped below 1.0, and a
  `reliability_score` only when you actually recorded failed runs (`null`), so it cannot become a
  copy of the quality score. The output is round-tripped through Volante's own strict loader before
  success is reported. The optional strength vocabulary that also activates `task_fit` is now
  documented in the README and `.env.example`.
- **In-band capability disclosure.** `RunResult.capability_notice` states when a run lacked a
  capability — code execution withheld for want of a sandbox, or running unisolated — and the CLI,
  `--json`, the MCP result footer, and the Web UI all show it on success as well as failure. A
  degraded answer (code that was never actually executed) is no longer indistinguishable from a
  full-capability one for callers who never see this process's stderr, such as an IDE MCP client.
- **Estimated-cost disclosure.** `RunResult.cost_estimated` reports whether any usage in the run was
  inferred because a provider returned no token counts, and every surface that prints a dollar
  figure now says so: the CLI summary and `--json`, the MCP result footer, the Web UI result panel
  and `/usage` table, and each usage-ledger record. Amounts Volante inferred are no longer
  indistinguishable from provider-reported ones.
- **Runtime safety invariants.** Strict task/plan validation, safe per-task workspace containment,
  model-aware context budgets, structured planning/task/synthesis failures, phase deadlines,
  authoritative cost propagation, and one physical subscription-call gate across planning,
  retries, worker/agent turns, and synthesis.
- **MCPB bundle (Claude Desktop + Smithery).** A one-file [`mcpb/`](mcpb/) manifest packs into a
  `volante-<version>.mcpb` (attached to each release) — open it in Claude Desktop for a one-click
  install with a provider-config UI, or upload it as a local server at smithery.ai/new. It wraps
  `uvx --from "volante[mcp]==0.3.0" volante-mcp`, so it runs locally (subscription CLIs +
  API keys work). Built/validated with a pinned `@anthropic-ai/mcpb`.
- **Multi-client MCP docs + Smithery manifest.** A [`smithery.yaml`](smithery.yaml) (stdio +
  provider config schema) for listing on [smithery.ai](https://smithery.ai), plus a README section
  with exact config for OpenAI Codex CLI (`codex mcp add …`), Gemini CLI, Cursor, Windsurf, and
  Cline/Roo. (These clients integrate MCP servers via config, not a plugin marketplace.)
- **Published to Smithery + a narrow orchestrate skill.** Volante is published to
  [Smithery](https://smithery.ai/server/ribato/volante) (`ribato/volante`, via the MCPB bundle). The
  Claude Code plugin also gains a tightly-scoped `orchestrate` skill that auto-delegates to
  `volante_run` **only** for large, multi-part goals (not simple tasks), to avoid over-invocation.
- **Claude Code plugin.** The repo doubles as a plugin marketplace
  (`.claude-plugin/marketplace.json` + [`plugins/volante/`](plugins/volante/)): one command
  (`/plugin marketplace add ribato22/volante` then `/plugin install volante@volante`) wires the Volante
  MCP server and a `/volante:run <goal>` slash command into Claude Code. Validated with
  `claude plugin validate`.

### Changed
- **Renamed: Baton → Volante.** The PyPI distribution moves from `baton-orchestrator` to
  `volante`, the importable package and CLI from `baton` to `volante`, the MCP server from
  `baton-mcp` to `volante-mcp`, its tool from `baton_run` to `volante_run`, and every setting
  from `BATON_*` to `VOLANTE_*`. A *volante* is the deep-lying midfielder who steers the
  game — literally "steering wheel" in Brazilian Portuguese — reading the whole pitch and sending
  the ball where it does the most good, which is the job this router does for models.
  The old name collided with several unrelated orchestration projects and could never hold the
  plain `baton` name on PyPI, which is why installs said `baton-orchestrator` while imports said
  `baton`. Existing `BATON_*` environment settings are still honored, with a deprecation notice,
  and will be dropped in a later release. Releases up to `0.2.1` remain published under
  `baton-orchestrator`; nothing is republished under the old name.
- **Code execution is now secure by default and fails closed.** `run_python` previously ran
  model-generated code in an unisolated subprocess by default, reachable on every CLI, Web UI, and
  MCP run — with the caller's filesystem and network. Volante now auto-selects the container sandbox
  when a Docker daemon answers, and when none does it withholds `run_python` entirely (the planner
  is never told it can execute code) instead of downgrading silently. The weak sandbox became an
  informed opt-in, `VOLANTE_SANDBOX=subprocess`, which warns on every start, and an unrecognized
  `VOLANTE_SANDBOX` value is rejected rather than treated as "subprocess". **Breaking for hosts
  without Docker:** agentic code-execution tasks stop being planned until you install/start Docker
  or opt in explicitly.
- **Public alpha positioning and routing claims.** Volante is now presented as a transparent,
  user-owned model router and orchestration control plane: usable through its CLI, library, Web UI,
  and MCP server while still explicitly alpha. Documentation describes quality as an evidence-based
  prediction after hard-capability filtering—not an empirically universal winner—and records that
  representative cross-provider benchmarks and automatic profile calibration remain future work.
  The documented regression suite is reported conservatively as 800+ tests.

### Fixed
- **`Runtime` reuse now fails loudly instead of misreporting.** A `Runtime` runs exactly one goal —
  its planner is non-re-entrant and its cost meter, subscription counter, and route trace are all
  per-run — but a second or overlapping `aexecute` used to report the first run's accumulated cost
  as if it were the new run's, or surface as an obscure `planning_error`. It now raises immediately
  with an actionable message; build a fresh `Runtime` per goal (which is what the runtime factory
  already returns on every code path).
- **Provider and tool boundaries.** SDK-internal retries are disabled so Volante owns retry policy;
  CLI-agent child environments use an explicit allowlist and hardened non-interactive flags;
  CLI/sandbox output and `fetch_url`/`read_file` input are bounded while streaming rather than after
  unbounded buffering. Agentic plans declare required tools and cannot claim success without
  actually invoking them.
- **Output and availability integrity.** Planning, workers, agent turns, and synthesis reject empty
  or non-terminal/truncated provider responses. Model/deployment-specific availability failures
  and provider-wide authentication, endpoint, timeout, or exhausted-transient failures reroute to
  the next ranked candidate and remain visible in the decision trace. Planning and synthesis now
  have bounded cross-provider phase fallbacks; malformed semantic requests still fail fast.
- **Web UI trust boundary.** Prompts move from query strings to bounded authenticated POST requests
  exchanged for opaque one-use SSE ids, with origin/host checks, concurrent-run limits, TTLs, and
  security headers. Non-loopback binding requires an access token.
- **Distribution configuration parity.** MCPB, Smithery, and the official MCP Registry manifest
  now use the engine's `OPENAI_COMPAT_KEY` contract, expose the required companion model id, mark
  secrets correctly, emit subscription toggles as exact `1`/`0`, and keep package/plugin/manifest
  versions in lockstep through automated contract tests.
- **Shared subscription planner gate.** CLI, MCP, Web UI, and the public async runtime-factory
  path now enforce the same live parse-plan check for subscription-only planners.
- **Release supply chain.** Releases now test/lint/type-check first, build and validate Python +
  MCPB artifacts, publish a GitHub Release, and then update the MCP Registry. The registry
  publisher is version/checksum pinned instead of executing an unverified `latest` download.

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
- **`quality` routing objective (now the default).** The router ranks eligible models by predicted
  fit from configured metadata (tier, required strengths, and tool support; ties broken toward the
  cash-free subscription option, then id). It is the objective wired into the CLI, Web UI, MCP
  server, and `make_runtime_factory` by default. `Router`/`route_ranked` now genuinely branch on
  `prefer` (previously only `cash_protect_quota` was implemented).
- **Python 3.11 support**: lowered `requires-python` to `>=3.11` (verified — the full
  test suite passes on 3.11), added the 3.11 trove classifier and a 3.11 CI matrix leg;
  ruff/mypy targets lowered to `py311`/`3.11` accordingly.
- Quickstart now has a zero-API-key one-liner (`uv run python examples/fake_provider.py`)
  so a newcomer sees the engine orchestrate end-to-end before configuring any provider.

### Changed
- **Default routing objective is now `quality`** (was `cash_protect_quota`). By default Baton now
  favors the highest predicted fit among hard-capability-eligible models rather than right-sizing to
  the cheapest adequate one, so it may use higher-tier/subscription-billed models more often (and
  consume more interactive quota). Pass `--prefer cash_protect_quota` (CLI) or
  `prefer="cash_protect_quota"` to restore the previous quota-protecting behavior. Docs reframed
  accordingly (routing headline, diagram, tables, MCP tool, subscription note).
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

[Unreleased]: https://github.com/ribato22/baton/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ribato22/baton/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/ribato22/baton/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ribato22/baton/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ribato22/baton/releases/tag/v0.1.0

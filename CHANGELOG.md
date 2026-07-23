# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Fixed
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

## [0.1.0] - 2026-07-22

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
- Optional Web UI (`webui/`): a FastAPI + Server-Sent-Events app that streams a run live in the
  browser (plan, per-task worker output, synthesis, result); uses real providers or a no-key
  `FakeProvider` demo. Install with the `ui` extra; run via `python -m webui`.
- Project docs: README, LICENSE (MIT), SECURITY, CONTRIBUTING, CI, and design specs under `docs/`.

[Unreleased]: https://github.com/ribato/baton/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ribato/baton/releases/tag/v0.1.0

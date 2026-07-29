# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **0.3.2 guarded writing model code to the workspace; reading it back was still unguarded.**
  The escape that release fixed was not "a write follows a symlink", it was "the workspace is
  storage the model controls" — and the eval harness then read `solution.py` and `test_*.py`
  straight back out of that same bind mount with `exists()` / `is_file()` / `read_text()`, every
  one of which follows a link. Model code could name any host file the host user can read as its
  own solution and have the trusted evaluator pull the contents into the scored result and the
  reported artifacts, in the read direction, past the same `--network none` / `--read-only`
  container that never saw the write. A `read_model_file` helper now sits beside
  `write_model_code`: `O_NOFOLLOW` so a link is refused, `O_NONBLOCK` plus a regular-file check
  so a planted FIFO cannot block the evaluator forever with no timeout anywhere near the call,
  and a byte cap so an unbounded read on a model-chosen path is no longer a model-chosen
  allocation. Nine new tests cover the read side; the seven added in 0.3.2 only covered writes.

### Fixed
- **A cancelled CLI-agent call left the `claude`/`codex` child running, and the provider's own
  timeout had not started.** Both runner paths wrote the whole prompt and awaited
  `stdin.drain()` BEFORE entering the region that installs the deadline and the `killpg`
  cleanup. `drain()` blocks as soon as the prompt exceeds the ~64 KB pipe buffer and the child
  has not begun reading — and canonical prompts run to hundreds of kilobytes under the
  configured context budgets, so this needs no oversized request. An outer Runtime timeout then
  cancelled a coroutine whose only `finally` removed the temp workdir: the child was neither
  killed nor reaped, one leaked process per cancellation. The feed is now a task inside the
  deadline, so `timeout` covers it and both the timeout and cancellation handlers kill the
  process group. Running it CONCURRENTLY with the stdout/stderr drains also closes the mirror
  deadlock, where a child filling its stdout pipe blocks forever because nothing drains it while
  the runner is still filling stdin — reproduced at 24 MB of child output, above the point where
  the stream reader pauses its transport. Six tests now cover the feed: none did before.

- **The 3x Opus price the 0.3.1 release was named for was still live on the Claude Code path.**
  `claude_code_model_info` valued plan consumption at $0.015/$0.075 per 1k, so a subscription user
  reading `credit_usd` saw three times the value they had actually consumed. Worse, a test asserted
  those exact numbers — it was enforcing the bug, not guarding against it. Both now read the
  published $5/$25 per MTok, and the test is bound to the same fact table the registry is checked
  against so the two cannot drift apart again.
- **Seven of the nine rows in the Anthropic fact table were guarded by nothing** — including the
  flagship a user is most likely to configure. A table built to stop stale numbers that checks two
  of its nine rows is a guard in name only. All nine are now pinned on all four fields, with a
  membership test so a new row cannot be added without an expectation, and a unit-sanity test that
  fails if anyone pastes a per-MTok figure into a per-1k field.
- **`.env.example` handed out pre-0.3.1 Kimi numbers** as copy-pasteable overrides. Since env
  overrides beat the code, following the example would have silently reinstated the values the
  release had just corrected.

### Added
- **An unpriced model is now reported as unpriced, not as free.** The generic OpenAI-compatible
  slot defaults its rates to zero because it cannot know what an arbitrary endpoint charges — so a
  user pointing it at OpenRouter, DeepSeek or Groq got a confident `billed_usd: $0.000000` sitting
  on top of a real bill. A card-billed model with no configured rate now raises a capability
  notice that travels with the result, naming the models and the env vars to set. Locally-run
  models are excluded by both provider and id prefix: Ollama's zero is the truth, because
  inference runs on the user's own hardware and there is no vendor rate to convert.

### Changed
- **The package's comments and docstrings are now English** — 100 of them across 21 files, proven
  comment-only by comparing normalised ASTs before and after. The Indonesian sat on exactly the
  surfaces an outside contributor reads first: the provider contract, the runtime, the cost ledger.
  The four user-visible Indonesian STRING LITERALS are translated too, in a separate change because
  they are behaviour rather than documentation: two `ProviderError` messages a user reads when
  Claude Code is not logged in or out of quota, and the two truncation markers that appear inside a
  model's own prompt and in tool output. Tests asserting on the old text were updated with them. No
  Indonesian string reaches a user of the shipped package now.
- **The README now opens with `pip install volante`.** The Quickstart was `git clone`, `pip install`
  appeared once and only for the MCP server, and every CLI example used `uv run` — so a user
  arriving from the PyPI badge had no documented route to a working CLI.
- **The MCP startup error tells the two failure modes apart.** A missing extra and an incompatible
  `mcp` both raise `ImportError`, and the old message assumed the first — sending users whose `mcp`
  was merely too new to reinstall the extra they already had, in a loop with no exit.
- **The benchmark's rank-swap claim is stated at the weight the data carries.** The `analyze` half
  of it is one goal, not nine; that is now said in the sentence that makes the claim rather than
  left for a reader to discover in the artifacts.

## [0.3.2] - 2026-07-29

Two blockers, both found by an audit that attacked the code instead of reading it.
Anyone on 0.3.1 should upgrade; anyone who ran agentic goals under the Docker
sandbox should assume the workspace could reach files outside it.

### Security
- **The Docker sandbox could be escaped to an arbitrary host file write.** Model
  code was written with `Path.write_text`, which follows symlinks, into a workspace
  that PERSISTS across agentic iterations — so one tool call could plant
  `_snippet.py` as a symlink and the next wrote model-authored bytes straight
  through it. The escape travels through the bind mount rather than the container,
  so `--network none`, `--cap-drop ALL`, `--read-only` and `--pids-limit` never saw
  it, and a purely relative link to `.git/hooks/pre-commit` needed no knowledge of
  host paths. SECURITY.md called that mode "real isolation"; it was not. Writes now
  go through `write_model_code`, which unlinks any planted link without following it
  and opens with `O_NOFOLLOW | O_EXCL` so a link reappearing in the race window is
  an error rather than a silent redirect. The subprocess sandbox had the identical
  bug and is fixed the same way. Not one of the 1027 tests that existed touched a
  symlink; seven now do.

### Fixed
- **Every published MCP install path was dead.** `mcp` 2.0.0 (2026-07-28) removed
  `mcp.server.fastmcp`, which `volante_mcp` imports, and the dependency was declared
  as an unbounded `mcp>=1.2` — so `uvx`, the MCPB bundle, the Claude Code plugin,
  Smithery and the MCP Registry record all resolved the newest release and died at
  startup, one day before this release. The failure was invisible locally and in CI
  because `uv.lock` pinned an older `mcp`. Now `mcp>=1.2,<2`.
- CI installs the `[mcp]` extra from **live PyPI, unlocked**, and imports what the
  server actually imports. The wheel smoke test never installed that extra and every
  other job resolved through the lockfile, which is precisely why a released version
  could ship a server that could not start.

## [0.3.1] - 2026-07-29

A correctness release. 0.3.0 reported Anthropic costs three times too high and
defaulted to a Moonshot model the vendor had already retired; anyone running that
version should upgrade.

### Fixed
- **Anthropic model metadata was a generation out of date, and it cost users money.** The seeded
  `anthropic/claude-opus-4-8` row — and the byte-identical env defaults on the live bootstrap path —
  carried 200k context, 8,192 output tokens and $0.015/$0.075 per 1k. The published figures are 1M
  context, 128k output and $5/$25 per MTok, i.e. $0.005/$0.025 per 1k. Every `billed_usd` reported
  for that model, on the CLI, in `--json`, in the MCP footer and in `~/.volante/usage.jsonl`, was
  therefore exactly **3x too high**; routing scored `context_headroom` and `output_capacity` against
  a fifth of the real window, and the agentic loop budgeted context the same way. The old row
  back-converts to $15/$75 per MTok — still Anthropic's published price for Opus 4.1 — so it was
  carried forward from an older generation rather than re-derived.
- **The seeded Moonshot model no longer exists.** The `kimi-k2` series was discontinued on
  2026-05-25 and all five wire ids sit in the vendor's deprecated table, so the bootstrap default
  `kimi-k2-0711-preview` would fail against the live API. Replaced with `kimi/kimi-k3`
  (1,048,576 context, 131,072 output, $3/MTok cache-miss input, $15/MTok output). The stored input
  price is the cache-MISS rate: this schema holds one input price, and assuming cache hits would
  understate cold traffic tenfold.
- **The Ollama context window was a number no Ollama has ever defaulted to.** 8,192 matched no
  release; Ollama has been VRAM-tiered since v0.15.5, with 4k as the bottom tier, and the
  OpenAI-compatible shim cannot raise it from the client. Now 4,096 with a 1,024 output cap, and
  labelled in-code as a deployment floor we chose — not a vendor fact. Over-claiming here failed
  silently, because Ollama drops the oldest messages to fit rather than raising.
- **`fetch_url` could be pointed at internal addresses.** It checked the scheme and a domain
  allowlist, then let httpx do the rest — so an allowlisted name resolving to 169.254.169.254
  reached the cloud metadata service. This matters more since the sandbox began blocking its own
  egress, which leaves this tool as the only remaining way out. Now: `trust_env=False` (an ambient
  `HTTP_PROXY`/`ALL_PROXY` otherwise routed the connection to an unvalidated proxy and made every
  address check dead code), ports restricted to 80/443, every resolved address validated with the
  request failing closed if any single one is non-public, and the connection pinned to the address
  that was actually checked — with the `Host` header and TLS SNI still bound to the original
  hostname, so certificate verification is unchanged.

### Changed
- **The README now publishes the benchmark instead of describing its format.** It previously
  printed a placeholder table and stated that no numbers were published, while five real result
  artifacts sat in the repo. It now carries the actual run — `VERDICT: BASELINE`, orchestration
  winning 1 goal of 9 at 7.3x the cost and 4.7x the latency — together with the limits that make
  it a narrow result rather than a settled one.
- **Tests now guard vendor facts, not relations.** Every numeric assertion about a model used to be
  a bound or a comparison (`context_window >= 100_000`, `kimi.cost < opus.cost`), which infinitely
  many wrong numbers satisfy — the suite stayed green through all of the above. Vendor facts are
  now pinned to exact values in a table carrying its sources, separated from project policy, with a
  unit-conversion test that fails on a per-MTok/per-1k slip and a coverage test that fails when a
  seeded model records no source.
- **`requires-python` is now `>=3.11.10`** (was `>=3.11`). The `ipaddress` `is_private`/`is_global`
  correctness fix (gh-113171) landed in 3.11.10, and the new address checks depend on it, so this
  floor is a security boundary rather than a packaging preference.
- **Anthropic defaults are per wire model, not per family.** `ANTHROPIC_MODELS` can register Opus
  and Haiku side by side, so one shared default over-claimed a 200k model's window by 5x — and
  over-claiming context fails silently. Facts are now keyed by exact wire name, with an unknown
  model falling back to a floor that is deliberately wrong in the safe direction on both axes:
  capability below every listed model, price above every listed model.

### Security
- Beyond the `fetch_url` hardening above, an adversarial review of that hardening found three more
  holes in it, all now closed: IPv6 site-local `fec0::/10` passed every arm of the address policy
  (CPython reports it `is_private=False` **and** `is_global=True`, and Windows still ships
  `fec0:0:0:ffff::1` as a default DNS server); pinning to the first resolved address discarded the
  failover that connecting by name used to provide, so one dead `AAAA` record broke every fetch to
  a domain; and `httpx.InvalidURL` is not an `httpx.HTTPError`, so a model-supplied URL containing
  a tab or newline escaped the tool uncaught and aborted the run.
- **Retired model ids no longer ship in the install surfaces.** `smithery.yaml` and
  `mcpb/manifest.json` each pinned `kimi-k2-0711-preview` as an always-set default, so correcting
  `bootstrap.py` alone left the dead id live in two install paths whose source a user never sees;
  `.env.example` and the README's copy-pasteable override block carried the old Anthropic numbers,
  which would have silently reverted the pricing fix through a documented, supported mechanism.
  A test now fails if any of those four surfaces mentions a retired id.
- The synthesizer's output budget is clamped the way the supervisor's already was. With the real
  128k output cap now in the registry, half of a 1M window no longer bounded it, so a synthesis
  could request 128k tokens on a non-streaming call and cross the client timeout — failing after
  the tokens were generated and billed.

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

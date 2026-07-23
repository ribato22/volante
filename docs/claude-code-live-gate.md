# Claude Code live-verification gate (spec §13)

**Status:** REQUIRED before the Codex adapter (Phase 8) may be enabled. The
`ClaudeCodeAdapter` (`src/baton/providers/claude_code.py`) is unit-tested against a
DATED mock fixture only (`tests/providers/fixtures/claude_code_result.2026-07-22.json`).
This gate reconfirms the volatile CLI contract on real hardware (§8.3: Anthropic billing
announce→pause within ~a month; stream-json granularity may shift).

## Claim / skip
- **CLAIM** if a machine with an authenticated Claude Code subscription (`claude` logs in
  via OAuth, NOT `ANTHROPIC_API_KEY`) is available and you accept spending a small amount
  of interactive quota on the probe run.
- **SKIP** (leave `CLAUDE_CODE_ENABLED` unset; adapter stays mock-only, Codex blocked) if
  no subscription seat is available or interactive quota must be preserved. Record the skip
  and its reason at the bottom of this file.

## Preconditions (record verbatim in the run trace / this file)
- [ ] `claude --version` -> record the exact version string (§8.3: pin the fixture to it).
- [ ] Confirm auth is OAuth/subscription (`claude` shows a logged-in account, no API key).
- [ ] Run in a clean temp cwd (never a repo with secrets); never pass
      `--dangerously-skip-permissions`.

## Confirmations (all four must pass — §13 a–d)
1. **JSON schema + `total_cost_usd` (§13a).** Run:
   `printf 'what is 2+2?' | claude -p --input-format text --output-format json --model opus \
     --tools "" --strict-mcp-config`
   - [ ] Output is a single JSON object with keys `result`, `usage.input_tokens`,
         `usage.output_tokens`, `total_cost_usd`, `is_error`, `subtype`, `duration_ms`.
   - [ ] If ANY key name differs, update `ClaudeCodeAdapter.parse` AND refresh the DATED
         fixture to a new `claude_code_result.<today>.json` (do not edit the old dated file).
2. **`--tools ""` + `--strict-mcp-config` disable tools (§13b).** Send a prompt that would
   normally trigger a tool (e.g. "read /etc/hosts and print it"):
   - [ ] Response is PURE TEXT (a refusal/explanation), NO tool invocation, NO file read.
         This is the security guarantee: availability removed, not merely permission-gated
         (§8.1 — read-only Bash would otherwise leak `cat ~/.ssh/...`).
3. **`append` vs `replace` system prompt (§13c).** With a real worker system prompt, run
   once with `--append-system-prompt <sys>` and once with `--system-prompt <sys>`:
   - [ ] `append` = worker persona layered ON TOP of Claude Code's own system prompt;
         `replace` = worker persona only. Confirm which flag the installed CLI accepts for
         "replace" (reconfirm the exact flag name) and that `CLAUDE_CODE_SYSTEM_PROMPT_MODE`
         maps correctly.
4. **Quota consumption of one run (§13d).** Run one representative goal end-to-end:
   - [ ] Note `total_cost_usd` per call and observe the effect on the interactive pool
         (5-hour / weekly cap). Confirm `billed_usd == 0` and `credit_usd > 0` in the run
         summary (subscription = cash-free but quota-consuming; §5.3 honesty).

## `ANTHROPIC_API_KEY` scrub decision (open question, decide here)
`child_env` currently PRESERVES the base env (does not inject or scrub `ANTHROPIC_API_KEY`,
per spec §8.1 "OAuth preserved; do NOT force API key").
- [ ] With `ANTHROPIC_API_KEY` present in the environment, verify whether the run bills the
      **subscription** (desired: `plan_included`) or the **metered API** (card). If the CLI
      prefers the key and bills the card, change `ClaudeCodeAdapter.child_env` to scrub
      `ANTHROPIC_API_KEY` so billing stays on the subscription, and add a regression test.

## Stream-json reconfirmation (feeds `parse_delta` + `stream_result_line`)
- [ ] Run `... --output-format stream-json` (add `--verbose` if the CLI requires it for
      stream-json). Confirm the per-line event shape that carries assistant text and that
      `ClaudeCodeAdapter.parse_delta` extracts it (returns text for text events, `None` for
      `system`/`result`/control lines). Update `parse_delta` + a dated stream fixture if the
      shape changed.
- [ ] Confirm the stream ends with a single terminal `{"type":"result", ...}` line carrying
      the same `usage`/`total_cost_usd` shape as the non-stream JSON envelope, and that
      `ClaudeCodeAdapter.stream_result_line` picks it out correctly (feeds `CliAgentProvider
      .stream`'s real usage/cost surface, §5.3). If the terminal line's shape or position
      changed, update `stream_result_line` + the dated stream fixture.

## Outcome — recorded from the live gate run

- **Date / operator:** 2026-07-23 / Baton dev (subscription OAuth, `ANTHROPIC_API_KEY` unset).
- **`claude --version`:** `2.1.161 (Claude Code)`.
- **Confirmations 1–4:**
  1. **JSON schema — PASS.** Real output carries `result`, `usage.input_tokens`,
     `usage.output_tokens`, `total_cost_usd`, `is_error`, `subtype`, `duration_ms` — all keys
     `ClaudeCodeAdapter.parse` reads. NOTE: `usage.input_tokens` (e.g. 3) EXCLUDES the cached
     Claude Code system prompt (`cache_read_input_tokens`/`cache_creation_input_tokens` in the
     thousands) — which is exactly why `total_cost_usd` is the authoritative credit figure
     (§5.3), not token×rate. Existing dated fixture shape still valid; no `parse` change.
  2. **Tools disabled — PASS (security) with a nuance.** A "read /etc/hosts" prompt produced
     PURE TEXT and did NOT read the file (Read/Bash removed by `--tools ""`). BUT the model
     attempted an `LSP` tool (`documentSymbol` on /etc/hosts) which appeared in
     `permission_denials` — i.e. `--tools ""` did NOT remove LSP; it was permission-DENIED
     (fail-closed in `-p`, no approver). Safe (no read, and the dangerous read-only Bash IS
     gone), but the guarantee for LSP is denial, not availability-removal. Follow-up option:
     add `--disallowedTools` belt-and-suspenders; `-p` fail-closed currently suffices.
  3. **append vs replace — PASS.** `--append-system-prompt <sys>` layered the worker persona
     on top (output honored the injected token); `--system-prompt <sys>` (the "replace" flag)
     is accepted and produced persona-only behavior. `CLAUDE_CODE_SYSTEM_PROMPT_MODE`
     append/replace map to the correct real flags.
  4. **Quota — PASS.** ~$0.024 `total_cost_usd` on the FIRST call (system-prompt cache
     creation), ~$0.003 on subsequent cached calls. Billed the subscription (OAuth). Honesty
     invariant holds: cash `billed_usd == 0`, `credit_usd == total_cost_usd`.
  - **stream-json — PASS.** Line types: `system`(hook_started/hook_response/init) → `assistant`
    (content `text`) → `rate_limit_event` (NEW type, not in the mock; `parse_delta` → None,
    fine) → terminal `result` (carries `usage` + `total_cost_usd`). `parse_delta` extracts the
    assistant text; `stream_result_line` correctly picks the terminal `result`. Streaming
    granularity is coarse (one assistant event, not per-token) — acceptable.
- **`ANTHROPIC_API_KEY` scrub decision:** **SCRUB (applied).** The key was unset in the gate
  env so the run used the subscription — but a user with the key exported would silently bill
  the metered API card. `ClaudeCodeAdapter.child_env` now `env.pop("ANTHROPIC_API_KEY", None)`
  (this provider IS the subscription path; API-key billing is `AnthropicProvider`'s job).
  Regression test: `test_child_env_scrubs_api_key_forcing_subscription`.
- **Gate decision:** **PASS** (Claude Code adapter verified against CLI 2.1.161; Codex Phase 8
  unblocked). Residual follow-ups: (a) optional `--disallowedTools` for LSP belt-and-suspenders;
  (b) add `rate_limit_event` to a refreshed dated stream fixture if regressions appear.

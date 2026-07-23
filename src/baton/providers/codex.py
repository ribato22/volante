# src/baton/providers/codex.py
"""Codex CLI adapter (`codex exec --json`) — subscription (ChatGPT sign-in) path.

live-verify DEFERRED (mock-backed merge, spec §14): the JSONL event wire-strings
used below (`thread.started` / `turn.started` / `agent_message` / `turn.completed`)
are provisional dated fixtures; reconfirm them against the installed Codex CLI at
the live gate (§13) before flipping this leg on. Unit tests mock the injected
runner (§11) — no real `codex` spawn.

Auth gotcha (openai/codex #2000): a ChatGPT sign-in can auto-provision an
`OPENAI_API_KEY` into the environment. If present, `codex exec` would bill the
metered API instead of the shared subscription pool, so `child_env` SCRUBS both
`OPENAI_API_KEY` and `CODEX_API_KEY`; auth then comes from `~/.codex/auth.json`,
a secret we never read or log (§8.1).
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from baton.providers.base import ProviderError
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    TextBlock,
    Usage,
)

if TYPE_CHECKING:
    from baton.providers.cli_agent import CliRunResult

_SCRUB_KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY")
_DEPTH_ENV = "BATON_CLI_AGENT_DEPTH"  # mirrors CliAgentProvider.depth_env default

# Baton-internal SENTINEL key for the stream_result_line() text-bridge (see there).
# Namespaced (leading underscore + "baton") so it can NEVER collide with a real
# Codex CLI wire key -- unlike a plausible key such as "message", which the real
# `turn.completed` event could plausibly grow one day.
_STREAM_MESSAGE_KEY = "_baton_stream_message"


def _est(s: str) -> int:
    """Cheap token estimate; never 0 (contract: JANGAN Usage(0, 0))."""
    return max(1, len(s) // 4)


def _prompt_text(req: CanonicalRequest) -> str:
    parts: list[str] = []
    for m in req.messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
    return "\n".join(parts)


class CodexAdapter:
    """Implements the CliAgentAdapter Protocol for `codex exec --json`."""

    name = "codex"

    def argv(
        self,
        req: CanonicalRequest,
        *,
        model: str,
        max_output: int,
        system_prompt_mode: str,
        stream: bool,
    ) -> list[str]:
        # `codex exec --json` always emits JSONL; `stream` does not change argv.
        # max_output / system_prompt_mode have no codex exec flag (documented §8.3).
        out = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if model:
            # Falsy/empty model (CODEX_MODEL unset) -> OMIT `--config model=...` entirely
            # so codex exec falls back to the user's OWN configured default model, rather
            # than passing an explicit-but-empty `model=` (which breaks a real spawn).
            out += ["--config", f"model={model}"]
        return out

    def child_env(self, base: dict[str, str], *, depth: int) -> dict[str, str]:
        env = dict(base)  # copy: never mutate the caller's environment
        for key in _SCRUB_KEYS:
            env.pop(key, None)
        # `depth` is already the CHILD's intended depth (CliAgentProvider bumps it
        # before calling child_env) -- write through verbatim, don't double-bump.
        env[_DEPTH_ENV] = str(depth)  # anti-recursion guard (§8.2)
        return env

    def stdin(self, req: CanonicalRequest) -> str:
        # codex exec reads its prompt from stdin (no positional PROMPT in argv);
        # system + user text is folded into one prompt (exec has no system slot).
        return _prompt_text(req)

    def parse(self, result: CliRunResult, req: CanonicalRequest) -> CanonicalResponse:
        texts: list[str] = []
        usage_in: int | None = None
        usage_out: int | None = None
        cost_usd: float | None = None
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate non-JSON banner/log lines
            etype = evt.get("type")
            if etype == "agent_message":
                msg = evt.get("message") or evt.get("text") or ""
                if msg:
                    texts.append(msg)
            elif etype == "turn.completed":
                usage = evt.get("usage") or {}
                usage_in = usage.get("input_tokens")
                usage_out = usage.get("output_tokens")
                cost_usd = evt.get("total_cost_usd", cost_usd)
                # `stream_result_line` synthesizes a self-contained terminal line
                # (folds the accumulated agent_message text into the Baton-internal
                # SENTINEL key below) so a single-line `parse()` on it -- as
                # CliAgentProvider.stream does -- still recovers the final text.
                # The sentinel is namespaced/Baton-internal: the real Codex CLI
                # cannot emit it, so this branch is a guaranteed no-op on the
                # `complete()` path regardless of what the live `turn.completed`
                # wire shape turns out to carry (§14) -- in particular, a real
                # (plausible) `message` field on `turn.completed` is IGNORED here.
                synth_msg = evt.get(_STREAM_MESSAGE_KEY)
                if synth_msg:
                    texts.append(synth_msg)
        final_text = "\n".join(texts)
        if usage_in is None or usage_out is None:
            usage = Usage(
                prompt_tokens=_est(_prompt_text(req)),
                completion_tokens=_est(final_text),
                estimated=True,
            )
        else:
            usage = Usage(prompt_tokens=int(usage_in), completion_tokens=int(usage_out))
        return CanonicalResponse(
            content=[TextBlock(text=final_text)],
            usage=usage,
            model="codex",  # provider tag; registry id (codex/<m>) is the accounting key
            stop_reason="end_turn",
            latency_ms=0,
            cost_usd=cost_usd,  # provider-authoritative call cost → credit ledger (§5.3)
        )

    def parse_delta(self, line: str) -> str | None:
        line = line.strip()
        if not line:
            return None
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return None
        if evt.get("type") == "agent_message":
            return evt.get("message") or evt.get("text") or None
        return None

    def classify_error(self, result: CliRunResult) -> ProviderError:
        if result.timed_out:
            # transient: backoff on the same candidate (killpg handled by base).
            return ProviderError("codex exec timed out", retryable=True, status=None)
        blob = f"{result.stderr}\n{result.stdout}".lower()
        if "not logged in" in blob or "codex login" in blob:
            return ProviderError(
                "codex not logged in", retryable=False, status=None,
                quota_exhausted=True,  # pragmatic: reroute to direct (§6.3)
            )
        if any(k in blob for k in ("usage limit", "try again in", "rate limit", "quota")):
            # Codex hard-pause is hours-long → reroute, not seconds of backoff (§6.3).
            return ProviderError(
                "codex usage/quota limit reached", retryable=False, status=None,
                quota_exhausted=True,
            )
        return ProviderError(
            f"codex exec failed (exit {result.returncode})",
            retryable=False, status=None,
        )

    def is_error(self, result: CliRunResult) -> bool:
        # codex exec can exit 0 while a turn still failed mid-run (PROVISIONAL wire
        # shape, live-verify deferred §14): either a standalone `{"type":"error"}`
        # event, or a truthy `error` field carried on `turn.completed`. Any other
        # shape / unparseable JSONL defaults to False (returncode already covers it).
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "error":
                return True
            if etype == "turn.completed" and evt.get("error"):
                return True
        return False

    def stream_result_line(self, lines: list[str]) -> str | None:
        # codex exec --json ends a successful turn with a `turn.completed` JSONL
        # line carrying `usage` (+ optional `total_cost_usd`) -- but UNLIKE
        # Claude's self-contained terminal `result` envelope, it carries no final
        # text (that lives on the earlier `agent_message` event(s)). CliAgentProvider
        # .stream() feeds ONLY this ONE returned line into `parse()`, so we
        # SYNTHESIZE a self-contained line here: fold the accumulated agent_message
        # text into a Baton-internal SENTINEL key (`_STREAM_MESSAGE_KEY`, NOT the
        # plausible-real-wire-key `message`) on a copy of the last `turn.completed`
        # event. The sentinel is namespaced so the real Codex CLI can never emit
        # it -- immune to whatever the live `turn.completed` shape turns out to be.
        # PROVISIONAL (§14): a bridging shape, not the real Codex CLI terminal
        # line -- reconfirm at the live gate.
        texts: list[str] = []
        terminal: dict | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                evt = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            if evt.get("type") == "agent_message":
                msg = evt.get("message") or evt.get("text") or ""
                if msg:
                    texts.append(msg)
            elif evt.get("type") == "turn.completed":
                terminal = evt  # keep walking -- want the LAST one (never trust wire order)
        if terminal is None:
            return None
        merged = dict(terminal)
        merged[_STREAM_MESSAGE_KEY] = "\n".join(texts)
        return json.dumps(merged)


def codex_detected(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Detect a usable Codex subscription login: `codex login status` exits 0.

    Injectable `run` keeps this unit-testable without spawning a real process
    (bootstrap gating, §7.2 / Phase 9). Any spawn/OS failure ⇒ not available."""
    try:
        proc = run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def build_codex_model(env: dict[str, str]) -> ModelInfo:
    """Registry seed for the Codex subscription leg (§5.1, §6.1, contract 2.2).

    tier is REQUIRED-explicit via CODEX_TIER (never sniffed from a `-mini` name);
    billing is `plan_included` (draws the shared ChatGPT subscription pool).
    cost_per_1k_* are valuation-only (cash is $0 on the plan) — left 0.0 until a
    real underlying rate is confirmed at live-verify (§8.3). CODEX_MODEL unset ->
    empty string: `CodexAdapter.argv` then OMITS `--config model=...` entirely, so
    `codex exec` follows the user's own Codex config; the id falls back to
    "codex/default" (sensible + consistent with that omission) instead of a
    hardcoded wire-model guess."""
    tier_raw = env.get("CODEX_TIER")
    if not tier_raw:
        raise ValueError("CODEX_TIER must be set explicitly (no -mini name sniffing)")
    model = env.get("CODEX_MODEL", "")
    return ModelInfo(
        id=f"codex/{model or 'default'}",
        provider="codex",
        strengths={"coding", "reasoning"},
        context_window=int(env.get("CODEX_CONTEXT", "256000")),
        max_output_tokens=int(env.get("CODEX_MAX_OUTPUT", "4096")),
        supports_tools=bool(env.get("CODEX_TOOLS", "").strip()),
        # valuation-only (subscription = $0 cash); real rate optional via env (§8.3)
        cost_per_1k_in=float(env.get("CODEX_COST_IN", "0")),
        cost_per_1k_out=float(env.get("CODEX_COST_OUT", "0")),
        tier=int(tier_raw),
        billing="plan_included",
    )

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
        return [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--config",
            f"model={model}",
        ]

    def child_env(self, base: dict[str, str], *, depth: int) -> dict[str, str]:
        env = dict(base)  # copy: never mutate the caller's environment
        for key in _SCRUB_KEYS:
            env.pop(key, None)
        env[_DEPTH_ENV] = str(depth + 1)  # anti-recursion guard (§8.2)
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

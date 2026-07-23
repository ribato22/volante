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

# src/volante/providers/claude_code.py
from __future__ import annotations

import json
import os
from pathlib import Path

from volante.providers.base import ProviderError
from volante.providers.cli_agent import CliRunResult
from volante.types import CanonicalRequest, CanonicalResponse, ModelInfo, TextBlock, Usage

DEPTH_ENV = "VOLANTE_CLI_AGENT_DEPTH"  # Phase 6 env contract: recursion guard (Volante-in-Claude)


def _system_text(req: CanonicalRequest) -> str:
    parts: list[str] = []
    for m in req.messages:
        if m.role != "system":
            continue
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
    return "\n".join(parts)


def _user_text(req: CanonicalRequest) -> str:
    parts: list[str] = []
    for m in req.messages:
        if m.role == "system":
            continue
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
    return "\n".join(parts)


def _est(s: str) -> int:
    """Cheap token estimate; never 0 (contract: NEVER Usage(0, 0))."""
    return max(1, len(s) // 4)


def _try_json(s: str) -> dict | None:
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


class ClaudeCodeAdapter:
    """CliAgentAdapter (Phase 6) for the `claude -p` SUBSCRIPTION/OAuth path.

    The canonical argv strips ALL built-in tools (`--tools ""`) + zero MCP
    (`--strict-mcp-config`) and WITHOUT `--bare` (so subscription OAuth stays alive, §8.1);
    NEVER `--dangerously-skip-permissions`. The provider ignores `req.temperature` &
    `req.max_tokens` (the CLI manages sampling/length itself) — §8.3, gate rationale §7.1.
    """

    name = "claude_code"

    def argv(
        self,
        req: CanonicalRequest,
        *,
        model: str,
        max_output: int,  # deliberately unused: the CLI ignores the length cap (§8.3)
        system_prompt_mode: str,
        stream: bool,
        scratch_dir: Path,
    ) -> list[str]:
        out = [
            "claude", "-p",
            "--input-format", "text",
            "--output-format", ("stream-json" if stream else "json"),
            "--model", model,
            "--tools", "",              # REQUIRED: drop every built-in tool (§8.1)
            "--strict-mcp-config",      # zero MCP; NEVER --bare (it kills OAuth)
            "--safe-mode",              # disable user/project hooks, plugins, skills, memory
            "--no-session-persistence", # no prompt/task retention on disk
            "--disable-slash-commands",
            "--no-chrome",
            # Belt-and-suspenders (§13 gate, live-verified 2026-07-23, CLI 2.1.161):
            # `-p` fail-closed permission-denial is the PRIMARY guarantee (the
            # built-in LSP tool was DENIED, so no file was read -- safe), but
            # `--tools ""` alone did NOT remove LSP's AVAILABILITY. Disallow it
            # explicitly too so availability-removal is complete, not just relying
            # on runtime permission denial.
            "--disallowedTools", "LSP",
        ]
        if stream:
            # the CLI requires --verbose for `--print --output-format stream-json`
            # (live-verified 2026-07-23, CLI 2.1.161: without it `claude -p` refuses).
            out.append("--verbose")
        sys_text = _system_text(req)
        if sys_text:
            # Through a FILE, not an argv element. Projector puts the user's goal in
            # this system message ("Overall goal: ..."), and argv is public: on Linux
            # /proc/<pid>/cmdline is world-readable by default, and process inspection
            # reads it everywhere. Goal text is not a credential — SECURITY.md is
            # clear that secrets must not enter model context at all — but it is the
            # user's business problem, and it was legible to anyone who could run
            # `ps`. 0600, inside a scratch directory the provider deletes when the
            # call returns. The user message already travelled by stdin.
            prompt_file = scratch_dir / "system-prompt.txt"
            fd = os.open(prompt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(sys_text)
            flag = (
                "--append-system-prompt-file"
                if system_prompt_mode == "append"
                else "--system-prompt-file"
            )
            out += [flag, str(prompt_file)]
        return out

    def stdin(self, req: CanonicalRequest) -> str:
        # user prompt via stdin (--input-format text); the system prompt travels
        # through a 0600 file, never argv (see `argv`).
        return _user_text(req)

    def child_env(self, base: dict[str, str], *, depth: int) -> dict[str, str]:
        env = dict(base)
        # `depth` is already the CHILD's intended depth (CliAgentProvider bumps it
        # before calling child_env) -- write through verbatim, don't double-bump.
        env[DEPTH_ENV] = str(depth)  # recursion guard (§8.2)
        # §13 gate decision (verified 2026-07-23, CLI 2.1.161): SCRUB ANTHROPIC_API_KEY
        # so `claude -p` always bills the OAuth SUBSCRIPTION (plan_included), never the
        # metered API card, even when the user has the key exported. This provider IS the
        # subscription path (billing="plan_included"); API-key billing is AnthropicProvider's
        # job. Mirrors CodexAdapter scrubbing OPENAI_API_KEY/CODEX_API_KEY. Copy (dict(base))
        # means the caller's env is never mutated.
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    def parse(self, result: CliRunResult, req: CanonicalRequest) -> CanonicalResponse:
        data = _try_json(result.stdout)
        if data is None:
            # JSON did not parse -> fall back to a flagged estimate, no authoritative
            # cost, and NO claim that the turn finished: `--output-format json` always
            # emits an envelope, so stdout that is not one means the CLI died
            # mid-answer, was truncated, or changed format. Reporting `end_turn` here
            # told ensure_complete_response the opposite of what we know.
            text_out = result.stdout.strip()
            return CanonicalResponse(
                content=[TextBlock(text=text_out)],
                usage=Usage(
                    prompt_tokens=_est(_user_text(req)),
                    completion_tokens=_est(text_out),
                    estimated=True,
                ),
                model=self.name,
                stop_reason="unparseable_output",
                latency_ms=0,
                cost_usd=None,
            )
        result_text = str(data.get("result") or "")
        usage_json = data.get("usage") or {}
        in_tok = usage_json.get("input_tokens")
        out_tok = usage_json.get("output_tokens")
        if in_tok is None or out_tok is None:
            usage = Usage(
                prompt_tokens=_est(_user_text(req)),
                completion_tokens=_est(result_text),
                estimated=True,
            )
        else:
            usage = Usage(prompt_tokens=int(in_tok), completion_tokens=int(out_tok))
        cost = data.get("total_cost_usd")
        subtype = data.get("subtype")
        return CanonicalResponse(
            content=[TextBlock(text=result_text)],
            usage=usage,
            model=str(data.get("model") or self.name),
            # `subtype` is how the envelope reports its own outcome, so an ABSENT one
            # is not a success — defaulting the missing value to `end_turn` asserted
            # a completion the wire never claimed.
            stop_reason="end_turn" if subtype == "success" else str(subtype or "unknown"),
            latency_ms=int(data.get("duration_ms") or 0),
            cost_usd=float(cost) if cost is not None else None,
        )

    def parse_delta(self, line: str) -> str | None:
        # The stream-json schema was reconfirmed live at the §13 gate; the "assistant"
        # event granularity at the time of writing = whole text messages (not
        # character-by-character deltas).
        data = _try_json(line)
        if data is None or data.get("type") != "assistant":
            return None
        msg = data.get("message") or {}
        text_out = "".join(
            str(b.get("text", ""))
            for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        return text_out or None

    def classify_error(self, result: CliRunResult) -> ProviderError:
        data = _try_json(result.stdout)
        subtype = str((data or {}).get("subtype", ""))
        detail_text = str((data or {}).get("result", "")) if data else result.stdout
        blob = f"{result.stderr}\n{detail_text}\n{subtype}".lower()
        # Not logged in yet / auth lost -> pragmatic: reroute to a direct candidate (Phase 5).
        if "not logged in" in blob or "/login" in blob or "invalid api key" in blob:
            return ProviderError(
                "claude_code: not logged in (run `claude` to authenticate)",
                retryable=False,
                quota_exhausted=True,
            )
        # Subscription usage limit (5-hour/weekly hard pause) -> quota exhausted, reroute.
        if any(k in blob for k in ("usage limit", "rate limit", "quota", "limit reached")):
            return ProviderError(
                "claude_code: subscription usage limit reached",
                retryable=False,
                quota_exhausted=True,
            )
        # Any other error -> FAIL the task (non-retryable, non-quota).
        detail = result.stderr.strip() or detail_text.strip() or f"exit {result.returncode}"
        return ProviderError(
            f"claude_code error: {detail}",
            retryable=False,
            quota_exhausted=False,
        )

    def is_error(self, result: CliRunResult) -> bool:
        # claude -p can exit 0 while the JSON envelope carries is_error=true
        # (max-turns / mid-run execution error); returncode alone can't see this.
        data = _try_json(result.stdout)
        if data is None:
            return False
        return bool(data.get("is_error", False))

    def stream_result_line(self, lines: list[str]) -> str | None:
        # `claude -p --output-format stream-json` ends with a terminal
        # `{"type":"result", ..., "usage":{...}, "total_cost_usd":...}` line -- same
        # envelope shape `parse` already consumes. Walk backwards for the LAST one
        # (defensive; the CLI emits exactly one, but never trust wire order).
        for line in reversed(lines):
            data = _try_json(line)
            if data is not None and data.get("type") == "result":
                return line
        return None


def claude_code_model_info(
    model: str = "opus",
    *,
    tier: int = 4,
    context_window: int = 200_000,
    max_output_tokens: int = 4_096,  # conservative: the CLI ignores the cap, over-reserve (§8.3)
) -> ModelInfo:
    """ModelInfo seed for the Claude Code subscription provider (§5.1 / contract 2.2).

    billing="plan_included": rides the interactive subscription pool (cash $0; the
    value is recorded as credit_usd). cost_per_1k_* = the underlying opus API rate,
    ONLY to value consumption (not cash). supports_tools=False (the --tools "" path).
    Registry registration + CLAUDE_CODE_ENABLED gating = Phase 9 (bootstrap).
    """
    return ModelInfo(
        id=f"claude-code/{model}",
        provider="claude_code",
        strengths={"coding", "reasoning", "long_context"},
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_tools=False,
        # The underlying Opus API rate, used to VALUE plan consumption as credit_usd
        # (never as cash). It was 0.015/0.075 — the Opus 4.1 generation — so the
        # release that fixed that 3x error everywhere else left it live on this path:
        # a user reading credit_usd for their subscription saw three times the value
        # they actually consumed. Verified 2026-07-28 on
        # platform.claude.com/docs/en/about-claude/pricing: $5/$25 per MTok, and this
        # field is per THOUSAND, so /1000.
        cost_per_1k_in=0.005,
        cost_per_1k_out=0.025,
        tier=tier,
        billing="plan_included",
    )

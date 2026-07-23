# src/baton/providers/claude_code.py
from __future__ import annotations

from baton.types import CanonicalRequest, TextBlock

DEPTH_ENV = "BATON_CLI_AGENT_DEPTH"  # kontrak env Fase 6: guard rekursi (Baton-in-Claude)


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


class ClaudeCodeAdapter:
    """CliAgentAdapter (Fase 6) untuk `claude -p` jalur LANGGANAN/OAuth.

    Argv kanonik menghapus SEMUA built-in tool (`--tools ""`) + nol MCP
    (`--strict-mcp-config`) dan TANPA `--bare` (agar OAuth langganan tetap hidup, §8.1);
    JANGAN `--dangerously-skip-permissions`. Provider mengabaikan `req.temperature` &
    `req.max_tokens` (CLI kelola sampling/panjang sendiri) — §8.3, alasan gerbang §7.1.
    """

    name = "claude_code"

    def argv(
        self,
        req: CanonicalRequest,
        *,
        model: str,
        max_output: int,  # sengaja tak dipakai: CLI abaikan cap panjang (§8.3)
        system_prompt_mode: str,
        stream: bool,
    ) -> list[str]:
        out = [
            "claude", "-p",
            "--input-format", "text",
            "--output-format", ("stream-json" if stream else "json"),
            "--model", model,
            "--tools", "",              # WAJIB: buang semua built-in tool (§8.1)
            "--strict-mcp-config",      # nol MCP; JANGAN --bare (mematikan OAuth)
        ]
        sys_text = _system_text(req)
        if sys_text:
            flag = (
                "--append-system-prompt"
                if system_prompt_mode == "append"
                else "--system-prompt"
            )
            out += [flag, sys_text]
        return out

    def stdin(self, req: CanonicalRequest) -> str:
        # prompt user via stdin (--input-format text); sistem sudah di argv.
        return _user_text(req)

    def child_env(self, base: dict[str, str], *, depth: int) -> dict[str, str]:
        env = dict(base)
        env[DEPTH_ENV] = str(depth + 1)  # guard rekursi: anak +1 (§8.2)
        # OAuth langganan DIPERTAHANKAN: TIDAK menyuntik/menghapus ANTHROPIC_API_KEY
        # di sini (kontras Codex yang scrub OPENAI_API_KEY). Keputusan scrub = §13.
        return env

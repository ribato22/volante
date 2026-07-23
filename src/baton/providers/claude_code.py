# src/baton/providers/claude_code.py
from __future__ import annotations

import json

from baton.providers.base import ProviderError
from baton.providers.cli_agent import CliRunResult
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage

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


def _est(s: str) -> int:
    """Estimasi token murah; tak pernah 0 (kontrak: JANGAN Usage(0, 0))."""
    return max(1, len(s) // 4)


def _try_json(s: str) -> dict | None:
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


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

    def parse(self, result: CliRunResult, req: CanonicalRequest) -> CanonicalResponse:
        data = _try_json(result.stdout)
        if data is None:
            # JSON tak terparse -> fallback estimasi bertanda, tanpa cost otoritatif.
            text_out = result.stdout.strip()
            return CanonicalResponse(
                content=[TextBlock(text=text_out)],
                usage=Usage(
                    prompt_tokens=_est(_user_text(req)),
                    completion_tokens=_est(text_out),
                    estimated=True,
                ),
                model=self.name,
                stop_reason="end_turn",
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
            stop_reason="end_turn" if subtype == "success" else str(subtype or "end_turn"),
            latency_ms=int(data.get("duration_ms") or 0),
            cost_usd=float(cost) if cost is not None else None,
        )

    def parse_delta(self, line: str) -> str | None:
        # Skema stream-json direkonfirmasi live di gerbang §13; granularitas event
        # "assistant" saat penulisan = pesan teks (bukan delta huruf-per-huruf).
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
        # Belum login / auth hilang -> pragmatis: reroute ke kandidat direct (Fase 5).
        if "not logged in" in blob or "/login" in blob or "invalid api key" in blob:
            return ProviderError(
                "claude_code: not logged in (jalankan `claude` untuk autentikasi)",
                retryable=False,
                quota_exhausted=True,
            )
        # Batas pemakaian langganan (hard-pause 5-jam/weekly) -> habis kuota, reroute.
        if any(k in blob for k in ("usage limit", "rate limit", "quota", "limit reached")):
            return ProviderError(
                "claude_code: batas pemakaian langganan tercapai",
                retryable=False,
                quota_exhausted=True,
            )
        # Galat lain -> GAGALKAN task (non-retryable, non-quota).
        detail = result.stderr.strip() or detail_text.strip() or f"exit {result.returncode}"
        return ProviderError(
            f"claude_code error: {detail}",
            retryable=False,
            quota_exhausted=False,
        )

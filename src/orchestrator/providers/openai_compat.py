# src/orchestrator/providers/openai_compat.py
from __future__ import annotations

import time
from dataclasses import dataclass

from openai import AsyncOpenAI

from orchestrator.types import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    ContentBlock,
    TextBlock,
    Usage,
)

# chat.completions finish_reason -> canonical stop_reason (tabel lengkap).
# Apa pun yang tidak terdaftar (termasuk None / unknown) jatuh ke "end_turn".
_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "content_filter",
}


@dataclass(frozen=True)
class BackendConfig:
    """Konfigurasi wire untuk backend OpenAI-compatible.

    ``output_tokens_param`` adalah field chat.completions yang membatasi panjang
    output. Disimpan di sini sebagai satu-satunya sumber kebenaran agar literal
    ("max_tokens") tidak tersebar di kode pembangun request; backend yang mengeja
    beda (mis. "max_completion_tokens") cukup ganti config, bukan edit kode.
    """

    output_tokens_param: str = "max_tokens"


def _to_chat_messages(messages: list[CanonicalMessage]) -> list[dict]:
    """Canonical messages -> chat.completions messages (text-only, Fase 0-1)."""
    out: list[dict] = []
    for m in messages:
        parts = [b.text for b in m.content if isinstance(b, TextBlock)]
        out.append({"role": m.role, "content": "".join(parts)})
    return out


class OpenAICompatProvider:
    name: str = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        config: BackendConfig | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.config = config or BackendConfig()
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        create_kwargs: dict = {
            "model": self.model,
            "messages": _to_chat_messages(req.messages),
            "temperature": req.temperature,
            self.config.output_tokens_param: req.max_tokens,
        }
        start = time.monotonic()
        resp = await self._client.chat.completions.create(**create_kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = resp.choices[0]
        content: list[ContentBlock] = [TextBlock(text=choice.message.content)]
        stop_reason = _FINISH_REASON_MAP.get(choice.finish_reason, "end_turn")
        usage = Usage(
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
        )
        return CanonicalResponse(
            content=content,
            usage=usage,
            model=resp.model,
            stop_reason=stop_reason,
            latency_ms=latency_ms,
        )

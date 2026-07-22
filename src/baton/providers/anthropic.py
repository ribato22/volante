from __future__ import annotations

import time
from typing import Any

import anthropic

from baton.providers.base import ProviderError, classify_429, is_quota_exhausted
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

_RETRYABLE_STATUSES = frozenset({408, 409, 429})


def _est(s: str) -> int:
    """Cheap token estimate; never returns 0 (contract: JANGAN Usage(0, 0))."""
    return max(1, len(s) // 4)


def _retryable_status(status: int) -> bool:
    return status in _RETRYABLE_STATUSES or status >= 500


def _to_provider_error(exc: Exception, billing: str = "card") -> ProviderError:
    # Timeout / connection failures are always retryable (status unknown).
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return ProviderError(str(exc), retryable=True, status=None)
    status = getattr(exc, "status_code", None)
    istatus = status if isinstance(status, int) else None
    msg = str(exc)
    # Credit/quota DEPLETION may arrive as 400 (credit balance) OR 429 (plan cap):
    # detect via body/message, not status alone -> reroute, NO backoff (§6.3).
    if is_quota_exhausted(msg):
        return ProviderError(msg, retryable=False, status=istatus, quota_exhausted=True)
    if istatus == 429:
        retryable, quota = classify_429(msg, billing=billing)
        return ProviderError(msg, retryable=retryable, status=429, quota_exhausted=quota)
    if istatus is not None:
        return ProviderError(msg, retryable=_retryable_status(istatus), status=istatus)
    return ProviderError(msg, retryable=False, status=None)


def _content_to_anthropic(blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultBlock):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                }
            )
    return out


def _split_messages(messages: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """Extract system text (top-level `system` param) from canonical messages."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            for b in m.content:
                if isinstance(b, TextBlock):
                    system_parts.append(b.text)
            continue
        out.append({"role": m.role, "content": _content_to_anthropic(m.content)})
    return "\n".join(system_parts), out


def _extract_content(resp: Any) -> list[Any]:
    blocks: list[Any] = []
    for b in getattr(resp, "content", []) or []:
        btype = getattr(b, "type", None)
        if btype == "text":
            blocks.append(TextBlock(text=getattr(b, "text", "")))
        elif btype == "tool_use":
            blocks.append(ToolUseBlock(id=b.id, name=b.name, input=b.input))
    return blocks


def _prompt_text(req: CanonicalRequest) -> str:
    parts: list[str] = []
    for m in req.messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
    return "\n".join(parts)


def _completion_text(content: list[Any]) -> str:
    return "\n".join(b.text for b in content if isinstance(b, TextBlock))


def _extract_usage(resp: Any, req: CanonicalRequest, content: list[Any]) -> Usage:
    usage = getattr(resp, "usage", None)
    in_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    out_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    if in_tokens is None or out_tokens is None:
        return Usage(
            prompt_tokens=_est(_prompt_text(req)),
            completion_tokens=_est(_completion_text(content)),
            estimated=True,
        )
    return Usage(prompt_tokens=in_tokens, completion_tokens=out_tokens)


class AnthropicProvider:
    """LLMProvider adapter over anthropic.AsyncAnthropic (PATCH v2.1)."""

    name: str

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        *,
        billing: str = "card",
    ) -> None:
        self.name = "anthropic"
        self.model = model
        self.timeout = timeout
        self.billing = billing
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        system_text, messages = _split_messages(req.messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if req.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in req.tools
            ]

        start = time.perf_counter()
        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — mapped to ProviderError below
            raise _to_provider_error(exc, self.billing) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        content = _extract_content(resp)
        usage = _extract_usage(resp, req, content)
        return CanonicalResponse(
            content=content,
            usage=usage,
            model=getattr(resp, "model", None) or self.model,
            stop_reason=getattr(resp, "stop_reason", None),
            latency_ms=latency_ms,
        )

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        system_text, messages = _split_messages(req.messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if req.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in req.tools
            ]
        start = time.perf_counter()
        parts: list[str] = []
        stopped = False
        final = None
        try:
            # `async with` menutup stream pada exit NORMAL, early-stop, MAUPUN
            # CancelledError (timeout) -> tak ada koneksi bocor.
            async with self._client.messages.stream(**kwargs) as s:
                async for delta in s.text_stream:
                    parts.append(delta)
                    if on_text(delta):  # truthy -> cooperative stop
                        stopped = True
                        break
                if not stopped:
                    final = await s.get_final_message()
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise _to_provider_error(exc, self.billing) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)
        if stopped:
            # Early-stop: get_final_message tak tersedia; bangun response parsial dari
            # teks terakumulasi (tool_use/usage server tak lengkap -> estimasi bertanda).
            text_out = "".join(parts)
            return CanonicalResponse(
                content=[TextBlock(text=text_out)],
                usage=Usage(
                    prompt_tokens=_est(_prompt_text(req)),
                    completion_tokens=_est(text_out),
                    estimated=True,
                ),
                model=self.model,
                stop_reason="end_turn",
                latency_ms=latency_ms,
            )
        content = _extract_content(final)
        usage = _extract_usage(final, req, content)
        return CanonicalResponse(
            content=content,
            usage=usage,
            model=getattr(final, "model", None) or self.model,
            stop_reason=getattr(final, "stop_reason", None),
            latency_ms=latency_ms,
        )

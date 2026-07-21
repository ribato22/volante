# src/orchestrator/providers/openai_compat.py
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from orchestrator.providers.base import ProviderError
from orchestrator.types import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
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

# HTTP status transien yang layak di-retry.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 409, 429})


@dataclass(frozen=True)
class BackendConfig:
    """Konfigurasi wire untuk backend OpenAI-compatible.

    ``output_tokens_param`` adalah field chat.completions yang membatasi panjang
    output. Satu sumber kebenaran agar literal tidak tersebar di kode request.
    """

    output_tokens_param: str = "max_tokens"


def _est(s: str) -> int:
    """Estimasi token kasar, dipakai HANYA saat server tak mengirim usage."""
    return max(1, len(s) // 4)


def _to_chat_messages(messages: list[CanonicalMessage]) -> list[dict]:
    """Canonical -> chat.completions messages, termasuk tool_use/tool_result."""
    out: list[dict] = []
    for m in messages:
        tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]
        if tool_results:
            for tr in tool_results:
                out.append(
                    {"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content}
                )
            continue
        tool_uses = [b for b in m.content if isinstance(b, ToolUseBlock)]
        text_out = "".join(b.text for b in m.content if isinstance(b, TextBlock))
        if tool_uses:
            out.append(
                {
                    "role": m.role,
                    "content": text_out or None,
                    "tool_calls": [
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {"name": b.name, "arguments": json.dumps(b.input)},
                        }
                        for b in tool_uses
                    ],
                }
            )
            continue
        out.append({"role": m.role, "content": text_out})
    return out


def _join_input_text(messages: list[CanonicalMessage]) -> str:
    return "".join(
        b.text for m in messages for b in m.content if isinstance(b, TextBlock)
    )


def _status_of(err: BaseException) -> int | None:
    status = getattr(err, "status_code", None)
    if status is None:
        status = getattr(err, "status", None)
    return status if isinstance(status, int) else None


def _is_network_error(err: BaseException) -> bool:
    if isinstance(
        err, (APITimeoutError, APIConnectionError, TimeoutError, ConnectionError)
    ):
        return True
    name = type(err).__name__
    return name.endswith("TimeoutError") or name.endswith("ConnectionError")


def _classify_error(err: BaseException) -> tuple[bool, int | None]:
    """(retryable, status): 408/409/429 atau >=500 atau timeout/koneksi -> retryable."""
    status = _status_of(err)
    if status is not None:
        return (status in _RETRYABLE_STATUSES or status >= 500), status
    return _is_network_error(err), None


async def _aclose_quietly(stream: object) -> None:
    """Tutup stream best-effort (early-stop/cancel) supaya koneksi tak bocor. Async
    generator punya aclose(); openai AsyncStream punya close(). Galat close ditelan."""
    closer = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 — cleanup best-effort
        pass


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
        if req.tools:
            create_kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]
            create_kwargs["tool_choice"] = "auto"
        start = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(**create_kwargs)
        except Exception as err:
            retryable, status = _classify_error(err)
            raise ProviderError(str(err), retryable=retryable, status=status) from err
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = resp.choices[0]
        text_out = choice.message.content or ""
        tool_calls = getattr(choice.message, "tool_calls", None) or []
        content: list[ContentBlock] = []
        if text_out:
            content.append(TextBlock(text=text_out))
        for tc in tool_calls:
            raw = getattr(tc.function, "arguments", "") or ""
            try:
                args = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                args = {}
            content.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))
        if not content:
            content = [TextBlock(text="")]
        stop_reason = _FINISH_REASON_MAP.get(choice.finish_reason, "end_turn")

        raw_usage = getattr(resp, "usage", None)
        prompt_toks = getattr(raw_usage, "prompt_tokens", None)
        completion_toks = getattr(raw_usage, "completion_tokens", None)
        if prompt_toks is None or completion_toks is None:
            usage = Usage(
                prompt_tokens=_est(_join_input_text(req.messages)),
                completion_tokens=_est(
                    text_out
                    + "".join(
                        getattr(tc.function, "arguments", "") or "" for tc in tool_calls
                    )
                ),
                estimated=True,
            )
        else:
            usage = Usage(prompt_tokens=prompt_toks, completion_tokens=completion_toks)

        return CanonicalResponse(
            content=content,
            usage=usage,
            model=resp.model or self.model,
            stop_reason=stop_reason,
            latency_ms=latency_ms,
        )

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        create_kwargs: dict = {
            "model": self.model,
            "messages": _to_chat_messages(req.messages),
            "temperature": req.temperature,
            self.config.output_tokens_param: req.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            create_kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]
            create_kwargs["tool_choice"] = "auto"

        start = time.monotonic()
        text_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        finish_reason: str | None = None
        usage_obj = None
        model_name = self.model
        try:
            stream = await self._client.chat.completions.create(**create_kwargs)
            # finally menutup stream pada exit NORMAL, early-stop (break), MAUPUN
            # CancelledError (timeout) -> koneksi tak bocor.
            try:
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        usage_obj = chunk.usage
                    if getattr(chunk, "model", None):
                        model_name = chunk.model
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    piece = getattr(delta, "content", None)
                    if piece:
                        text_parts.append(piece)
                        if on_text(piece):  # truthy -> cooperative stop
                            break
                    for tc in getattr(delta, "tool_calls", None) or []:
                        acc = tool_acc.setdefault(
                            tc.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if tc.id:
                            acc["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                acc["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                acc["arguments"] += fn.arguments
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
            finally:
                await _aclose_quietly(stream)
        except Exception as err:
            retryable, status = _classify_error(err)
            raise ProviderError(str(err), retryable=retryable, status=status) from err
        latency_ms = int((time.monotonic() - start) * 1000)

        text_out = "".join(text_parts)
        content: list[ContentBlock] = []
        if text_out:
            content.append(TextBlock(text=text_out))
        for idx in sorted(tool_acc):
            acc = tool_acc[idx]
            raw = acc["arguments"] or ""
            try:
                args = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                args = {}
            content.append(ToolUseBlock(id=acc["id"] or "", name=acc["name"] or "", input=args))
        if not content:
            content = [TextBlock(text="")]
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, "end_turn")

        prompt_toks = getattr(usage_obj, "prompt_tokens", None)
        completion_toks = getattr(usage_obj, "completion_tokens", None)
        if prompt_toks is None or completion_toks is None:
            # Sertakan argumen tool_call dalam estimasi output — simetris dengan
            # complete(); tanpa ini completion di-undercount saat ada tool call.
            tool_args = "".join(acc["arguments"] or "" for acc in tool_acc.values())
            usage = Usage(
                prompt_tokens=_est(_join_input_text(req.messages)),
                completion_tokens=_est(text_out + tool_args),
                estimated=True,
            )
        else:
            usage = Usage(prompt_tokens=prompt_toks, completion_tokens=completion_toks)

        return CanonicalResponse(
            content=content,
            usage=usage,
            model=model_name,
            stop_reason=stop_reason,
            latency_ms=latency_ms,
        )

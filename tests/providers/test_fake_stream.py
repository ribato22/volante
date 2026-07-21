from __future__ import annotations

import pytest

from orchestrator.providers.fake import FakeProvider
from orchestrator.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage, text


def _resp(txt: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=txt)], usage=Usage(1, 1), model="fake",
        stop_reason="end_turn", latency_ms=0,
    )


@pytest.mark.asyncio
async def test_stream_forwards_text_and_returns_response() -> None:
    p = FakeProvider(responses=[_resp("hello world")])
    chunks: list[str] = []
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=10),
        chunks.append,
    )
    assert "".join(chunks) == "hello world"
    assert res.content[0].text == "hello world"

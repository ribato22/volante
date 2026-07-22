from __future__ import annotations

import pytest

from baton.providers.fake import FakeProvider
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage, text


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


@pytest.mark.asyncio
async def test_stream_early_stop_on_truthy_callback() -> None:
    # on_text truthy -> berhenti; response parsial (hanya blok yang sempat diemit).
    resp = CanonicalResponse(
        content=[TextBlock(text="AA"), TextBlock(text="BB")],
        usage=Usage(1, 1), model="fake", stop_reason="end_turn", latency_ms=0,
    )
    p = FakeProvider(responses=[resp])
    got: list[str] = []

    def cb(s: str) -> bool:
        got.append(s)
        return True  # stop setelah blok pertama

    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=10), cb
    )
    assert got == ["AA"]  # blok kedua tak diteruskan
    assert res.content[0].text == "AA"  # response parsial

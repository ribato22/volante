# tests/providers/test_fake.py
from __future__ import annotations

from baton.providers.fake import FakeProvider
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage, text


def _make_req(s: str) -> CanonicalRequest:
    return CanonicalRequest(messages=[text("user", s)], max_tokens=64)


def _resp(s: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=s)],
        usage=Usage(prompt_tokens=1, completion_tokens=2),
        model="queued",
        stop_reason="end_turn",
        latency_ms=5,
    )


async def test_returns_queued_in_order_then_echoes() -> None:
    queued = [_resp("first"), _resp("second")]
    provider = FakeProvider(responses=queued)
    req = _make_req("hello world")

    r1 = await provider.complete(req)
    assert r1 is queued[0]

    r2 = await provider.complete(req)
    assert r2 is queued[1]

    # Antrean habis -> echo teks pesan terakhir.
    r3 = await provider.complete(req)
    assert isinstance(r3.content[0], TextBlock)
    assert r3.content[0].text == "hello world"
    assert r3.usage == Usage(prompt_tokens=0, completion_tokens=0)
    # Echo deterministik: usage nyata (bukan fallback estimasi adapter jaringan).
    assert r3.usage.estimated is False
    assert r3.stop_reason == "end_turn"
    assert r3.model == "fake"
    assert r3.latency_ms == 0


async def test_default_none_echoes_immediately() -> None:
    provider = FakeProvider()
    r = await provider.complete(_make_req("echo me"))
    assert r.content[0].text == "echo me"
    assert r.usage.prompt_tokens == 0
    assert r.usage.completion_tokens == 0
    assert r.stop_reason == "end_turn"


async def test_name_flows_into_model_and_attribute() -> None:
    provider = FakeProvider(name="stub")
    assert provider.name == "stub"
    r = await provider.complete(_make_req("x"))
    assert r.model == "stub"


async def test_echo_of_empty_messages_is_empty_text() -> None:
    provider = FakeProvider()
    r = await provider.complete(CanonicalRequest(messages=[], max_tokens=8))
    assert r.content == [TextBlock(text="")]

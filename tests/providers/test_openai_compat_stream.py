from __future__ import annotations

import asyncio

import pytest

from baton.providers.openai_compat import OpenAICompatProvider
from baton.types import CanonicalRequest, TextBlock, ToolUseBlock, text


class _D:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None, model="kimi-x"):
    delta = _D(content=content, tool_calls=tool_calls)
    choice = _D(delta=delta, finish_reason=finish_reason)
    return _D(choices=[choice], usage=usage, model=model)


def _tc(index, id=None, name=None, arguments=None):
    return _D(index=index, id=id, function=_D(name=name, arguments=arguments))


async def _aiter(items):
    for it in items:
        yield it


def _provider(monkeypatch, chunks):
    class _Completions:
        async def create(self, **kw):
            assert kw.get("stream") is True
            return _aiter(chunks)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        "baton.providers.openai_compat.AsyncOpenAI", lambda **kw: _Client()
    )
    return OpenAICompatProvider(base_url="http://x/v1", api_key="k", model="kimi-x")


@pytest.mark.asyncio
async def test_stream_text(monkeypatch) -> None:
    chunks = [
        _chunk(content="hel"),
        _chunk(content="lo"),
        _chunk(finish_reason="stop", usage=_D(prompt_tokens=5, completion_tokens=2)),
    ]
    p = _provider(monkeypatch, chunks)
    got: list[str] = []
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), got.append
    )
    assert "".join(got) == "hello"
    assert isinstance(res.content[0], TextBlock)
    assert res.usage.prompt_tokens == 5


class _SpyStream:
    """Stream mock ber-aclose (seperti async generator / openai AsyncStream) untuk
    memverifikasi penutupan pada early-stop / cancel."""

    def __init__(self, chunks, hang_after=None):
        self._chunks = chunks
        self._hang_after = hang_after
        self.closed = False

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for i, c in enumerate(self._chunks):
            yield c
            if self._hang_after is not None and i == self._hang_after:
                await asyncio.Event().wait()  # gantung sampai dibatalkan

    async def aclose(self):
        self.closed = True


def _provider_with_stream(monkeypatch, stream_obj):
    class _Completions:
        async def create(self, **kw):
            return stream_obj

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        "baton.providers.openai_compat.AsyncOpenAI", lambda **kw: _Client()
    )
    return OpenAICompatProvider(base_url="http://x/v1", api_key="k", model="kimi-x")


@pytest.mark.asyncio
async def test_stream_early_stop_forwards_partial_and_closes(monkeypatch) -> None:
    # on_text truthy -> berhenti; chunk berikutnya tak diproses; stream ditutup.
    stream = _SpyStream(
        [_chunk(content="a"), _chunk(content="b"), _chunk(content="c", finish_reason="stop")]
    )
    p = _provider_with_stream(monkeypatch, stream)
    got: list[str] = []

    def cb(s: str) -> bool:
        got.append(s)
        return True  # stop setelah chunk pertama

    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), cb
    )
    assert got == ["a"]
    assert "".join(b.text for b in res.content if isinstance(b, TextBlock)) == "a"
    assert stream.closed is True  # finally menutup stream pada early-stop


@pytest.mark.asyncio
async def test_stream_cancellation_closes_stream(monkeypatch) -> None:
    # Cancel di tengah stream -> CancelledError merambat + finally menutup koneksi.
    stream = _SpyStream([_chunk(content="a"), _chunk(content="b")], hang_after=0)
    p = _provider_with_stream(monkeypatch, stream)
    started = asyncio.Event()

    def cb(s: str) -> None:
        started.set()

    task = asyncio.create_task(
        p.stream(CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), cb)
    )
    await started.wait()  # chunk pertama telah diproses -> gen kini menggantung
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed is True  # ditutup meski dibatalkan


@pytest.mark.asyncio
async def test_stream_stop_reason_defaults_to_end_turn_when_no_finish_reason(
    monkeypatch,
) -> None:
    # CanonicalResponse.stop_reason is typed `str` (never None): if the stream ends
    # without any chunk ever setting finish_reason (e.g. early-stop before the
    # terminal chunk), stop_reason must still fall back to "end_turn".
    chunks = [_chunk(content="a"), _chunk(content="b")]  # no finish_reason anywhere
    p = _provider(monkeypatch, chunks)
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "hi")], max_tokens=16), lambda s: None
    )
    assert res.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_stream_merges_tool_call_deltas(monkeypatch) -> None:
    chunks = [
        _chunk(tool_calls=[_tc(0, id="c1", name="run_python", arguments='{"co')]),
        _chunk(tool_calls=[_tc(0, arguments='de": "x"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    p = _provider(monkeypatch, chunks)
    res = await p.stream(
        CanonicalRequest(messages=[text("user", "go")], max_tokens=16), lambda s: None
    )
    assert res.stop_reason == "tool_use"
    tu = res.content[0]
    assert isinstance(tu, ToolUseBlock)
    assert tu.id == "c1" and tu.name == "run_python"
    assert tu.input == {"code": "x"}

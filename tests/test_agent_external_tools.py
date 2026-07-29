from __future__ import annotations

from pathlib import Path

from volante.agent import AgenticWorker
from volante.cost import CostMeter
from volante.providers.fake import FakeProvider
from volante.tools.fetch_url import FetchUrlTool
from volante.tools.run_python import RunPythonTool
from volante.tools.sandbox import Sandbox
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    ToolUseBlock,
    Usage,
    text,
)


class _FakeResp:
    def __init__(self, text="PAGE-BODY", status=200) -> None:
        self._body = text.encode()
        self.status_code = status
        self.headers = {"content-length": str(len(self._body))}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_raw(self):
        yield self._body


class _FakeClient:
    def __init__(self, resp, **kwargs) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kwargs):
        return self._resp


def _resp(content: list, stop: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=content,
        usage=Usage(prompt_tokens=3, completion_tokens=2),
        model="m1",
        stop_reason=stop,
        latency_ms=1,
    )


def _req() -> CanonicalRequest:
    return CanonicalRequest(
        messages=[text("user", "fetch then compute")], max_tokens=256, task_id="t1"
    )


async def test_agentic_loop_fetch_url_then_run_python(tmp_path: Path, monkeypatch) -> None:
    # fetch_url is host-mediated: patch httpx so NO network is touched, and pin
    # the resolver too — the tool now validates every resolved address before it
    # connects, so an unpatched lookup would make this test need DNS.
    monkeypatch.setattr(
        "volante.tools.fetch_url.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResp("PAGE-BODY", 200), **kw),
    )

    async def _fake_resolve(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr("volante.tools.fetch_url._resolve", _fake_resolve)
    tools = {
        "fetch_url": FetchUrlTool({"example.com"}),
        "run_python": RunPythonTool(Sandbox(tmp_path)),  # real local subprocess sandbox
    }
    provider = FakeProvider(
        responses=[
            _resp(
                [ToolUseBlock(id="u1", name="fetch_url", input={"url": "https://example.com"})],
                "tool_use",
            ),
            _resp(
                [ToolUseBlock(id="u2", name="run_python", input={"code": "print('computed')"})],
                "tool_use",
            ),
            _resp([TextBlock(text="all done")], "end_turn"),
        ]
    )

    res = await AgenticWorker({"m1": provider}, CostMeter()).run(_req(), "m1", tools)

    assert res.final_text == "all done"

    tool_results = [t for t in res.turns if t.kind == "tool_result"]
    assert len(tool_results) == 2
    # fetch_url ran host-side FIRST (status + body surfaced into the transcript)...
    assert "status=200" in tool_results[0].payload
    assert "PAGE-BODY" in tool_results[0].payload
    # ...then run_python executed in the sandbox.
    assert "exit=0" in tool_results[1].payload
    assert "computed" in tool_results[1].payload

    tool_use_payloads = [t.payload for t in res.turns if t.kind == "tool_use"]
    assert any("fetch_url" in p for p in tool_use_payloads)
    assert any("run_python" in p for p in tool_use_payloads)

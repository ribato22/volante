from __future__ import annotations

import pytest
from webui._demo import demo_runtime_factory
from webui.runner import stream_events


@pytest.mark.asyncio
async def test_stream_events_emits_plan_workers_and_result() -> None:
    make = demo_runtime_factory()
    events = [ev async for ev in stream_events(make(), "demo goal")]

    types = [e["type"] for e in events]
    assert "phase" in types  # planning + synthesis streamed via on_text
    assert "worker" in types  # parallel workers streamed via on_worker_text

    worker_tasks = {e["task"] for e in events if e["type"] == "worker"}
    assert worker_tasks == {"research", "outline", "compose"}  # the demo DAG

    result = next(e for e in events if e["type"] == "result")
    assert result["status"] == "success"
    assert result["final"]  # synthesized answer present
    assert "cost_usd" in result and "duration_ms" in result
    assert events[-1]["type"] == "result"  # terminal event is last


@pytest.mark.asyncio
async def test_stream_events_surfaces_error() -> None:
    class _Boom:
        async def aexecute(self, goal, on_text=None, on_worker_text=None):
            raise RuntimeError("kaboom")

    events = [ev async for ev in stream_events(_Boom(), "g")]
    assert events == [{"type": "error", "message": "RuntimeError: kaboom"}]

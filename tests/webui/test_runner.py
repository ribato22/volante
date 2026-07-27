from __future__ import annotations

import asyncio

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
    assert "Python concurrency" in result["final"]
    assert "Artifacts:" not in result["final"]  # never expose echo/scaffolding as value
    assert "cost_usd" in result and "duration_ms" in result
    assert "subscription_calls" in result
    assert result["routing_decisions"]
    assert events[-1]["type"] == "result"  # terminal event is last


@pytest.mark.asyncio
async def test_stream_events_surfaces_error() -> None:
    class _Boom:
        async def aexecute(self, goal, on_text=None, on_worker_text=None):
            raise RuntimeError("kaboom")

    events = [ev async for ev in stream_events(_Boom(), "g")]
    assert events == [{"type": "error", "message": "RuntimeError: kaboom"}]


@pytest.mark.asyncio
async def test_closing_stream_cancels_expensive_runtime() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _Slow:
        async def aexecute(self, goal, on_text=None, on_worker_text=None):
            assert on_text is not None
            on_text("started")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    stream = stream_events(_Slow(), "g")
    assert await anext(stream) == {"type": "phase", "text": "started"}
    await started.wait()
    await stream.aclose()

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_result_event_carries_the_estimate_flag() -> None:
    # The browser can only mark inferred amounts if the terminal event ships the flag.
    make = demo_runtime_factory()
    events = [ev async for ev in stream_events(make(), "demo goal")]
    result = next(e for e in events if e["type"] == "result")
    assert "cost_estimated" in result
    assert isinstance(result["cost_estimated"], bool)

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


async def stream_events(runtime: Any, goal: str) -> AsyncIterator[dict]:
    """Run ``runtime.aexecute(goal)`` and yield UI events as they happen.

    Bridges the engine's synchronous ``on_text`` / ``on_worker_text`` callbacks to
    an ``asyncio.Queue`` that this async generator drains — so planning, per-task
    worker output, and synthesis stream to the caller live, followed by a terminal
    ``result`` (or ``error``) event. No network of its own; the runtime decides that.

    Event shapes::

        {"type": "phase",  "text": <delta>}              # planning + synthesis
        {"type": "worker", "task": <id>, "text": <delta>} # a parallel worker's delta
        {"type": "result", "status", "final", "failed_task", "cost_usd", "duration_ms"}
        {"type": "error",  "message": <str>}
    """
    queue: asyncio.Queue = asyncio.Queue()

    def on_text(delta: str) -> None:
        queue.put_nowait({"type": "phase", "text": delta})

    def on_worker(task_id: str, delta: str) -> None:
        queue.put_nowait({"type": "worker", "task": task_id, "text": delta})

    async def _run() -> None:
        try:
            res = await runtime.aexecute(goal, on_text=on_text, on_worker_text=on_worker)
            queue.put_nowait(
                {
                    "type": "result",
                    "status": res.status,
                    "final": res.final,
                    "failed_task": res.failed_task,
                    "cost_usd": res.cost_usd,
                    "duration_ms": res.duration_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface ANY failure to the UI
            queue.put_nowait({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            queue.put_nowait(None)  # sentinel: producer done

    task = asyncio.create_task(_run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        await task

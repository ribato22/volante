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

    A lightweight ``stage`` event marks phase boundaries so the UI can show an
    honest Plan -> Workers -> Synthesis -> Result progress stepper instead of a
    single "running" badge. It is inferred from the engine's own callback order
    (plan streams via ``on_text`` first, then workers via ``on_worker_text``, then
    synthesis via ``on_text`` again) — no extra engine coupling. Nothing is emitted
    until the corresponding phase actually starts, so a run that fails during
    planning still yields only its ``error`` event.

    Event shapes::

        {"type": "stage",  "stage": "workers" | "synthesis"}  # phase boundary crossed
        {"type": "phase",  "text": <delta>}              # planning + synthesis
        {"type": "worker", "task": <id>, "text": <delta>} # a parallel worker's delta
        {"type": "result", "status", "final", "failed_task",
                            "error_code", "error_message", "cost_usd", "billed_usd",
                            "credit_usd", "cost_estimated", "capability_notice", "duration_ms",
                            "subscription_calls", "routing_decisions"}
        {"type": "error",  "message": <str>}
    """
    # Synchronous provider callbacks cannot await downstream SSE backpressure.
    # Bound the bridge: if a client is too slow, stop the expensive model run
    # instead of buffering an unbounded amount of model-controlled text.
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    # Mutable phase flags shared with the sync callbacks (closures can't rebind).
    stage = {"workers": False, "synthesis": False}

    def on_text(delta: str) -> None:
        # on_text streams both planning and synthesis; once a worker has run, the
        # next on_text delta is the synthesis phase — emit its boundary once.
        if stage["workers"] and not stage["synthesis"]:
            stage["synthesis"] = True
            queue.put_nowait({"type": "stage", "stage": "synthesis"})
        try:
            queue.put_nowait({"type": "phase", "text": delta})
        except asyncio.QueueFull as exc:
            raise RuntimeError("UI stream consumer is too slow") from exc

    def on_worker(task_id: str, delta: str) -> None:
        if not stage["workers"]:
            stage["workers"] = True
            try:
                queue.put_nowait({"type": "stage", "stage": "workers"})
            except asyncio.QueueFull as exc:
                raise RuntimeError("UI stream consumer is too slow") from exc
        try:
            queue.put_nowait({"type": "worker", "task": task_id, "text": delta})
        except asyncio.QueueFull as exc:
            raise RuntimeError("UI stream consumer is too slow") from exc

    async def _run() -> None:
        try:
            res = await runtime.aexecute(goal, on_text=on_text, on_worker_text=on_worker)
            await queue.put(
                {
                    "type": "result",
                    "status": res.status,
                    "final": res.final,
                    "failed_task": res.failed_task,
                    "error_code": getattr(res, "error_code", None),
                    "error_message": getattr(res, "error_message", None),
                    "cost_usd": res.cost_usd,
                    "billed_usd": res.billed_usd,
                    "credit_usd": res.credit_usd,
                    "cost_estimated": getattr(res, "cost_estimated", False),
                    "capability_notice": getattr(res, "capability_notice", None),
                    "duration_ms": res.duration_ms,
                    "subscription_calls": getattr(res, "subscription_calls", 0),
                    "routing_decisions": getattr(res, "routing_decisions", {}),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface ANY failure to the UI
            await queue.put(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            current = asyncio.current_task()
            if current is None or current.cancelling() == 0:
                await queue.put(None)  # sentinel: producer done

    task = asyncio.create_task(_run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

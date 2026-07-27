"""Fully offline example: NO API key / network required.

Wires `volante.providers.fake.FakeProvider` into a `Runtime` by hand (mirroring how
`webui/_demo.py` powers the Web UI's no-key demo mode and how the test suite builds
FakeProvider-backed runtimes). The planner's first call returns a fixed 3-task DAG
(two independent tasks fanned out in parallel, one that depends on both); every
subsequent call returns a deterministic canned artifact or final answer. This lets
a newcomer see a meaningful orchestration flow — plan -> parallel workers ->
synthesis — end to end without configuring anything.

Run:

    uv run python examples/fake_provider.py
"""

from __future__ import annotations

import asyncio
import json

from volante import ModelInfo, Registry, Router, Runtime
from volante.cost import CostMeter
from volante.projector import Projector
from volante.providers.fake import FakeProvider
from volante.supervisor import Supervisor
from volante.synthesizer import Synthesizer
from volante.types import CanonicalResponse, TextBlock, Usage
from volante.worker import Worker

_MID = "demo/fake"

# The supervisor's first call returns this DAG; later calls consume canned,
# human-readable artifacts in deterministic wave order.
_PLAN = json.dumps(
    [
        {"id": "research", "description": "research the topic", "type": "research",
         "mode": "one_shot", "depends_on": []},
        {"id": "outline", "description": "draft an outline", "type": "write",
         "mode": "one_shot", "depends_on": []},
        {"id": "compose", "description": "compose the final answer", "type": "analyze",
         "mode": "one_shot", "depends_on": ["research", "outline"]},
    ]
)


def _plan_response() -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=_PLAN)], usage=Usage(40, 25),
        model=_MID, stop_reason="end_turn", latency_ms=1,
    )


def _text_response(value: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=value)],
        usage=Usage(20, max(1, len(value) // 4)),
        model=_MID,
        stop_reason="end_turn",
        latency_ms=1,
    )


def build_fake_runtime() -> Runtime:
    """A `Runtime` backed entirely by `FakeProvider` — no network, no API key."""
    registry = Registry(
        [
            ModelInfo(
                id=_MID, provider="fake", strengths={"coding", "reasoning"},
                context_window=128_000, max_output_tokens=4_096, supports_tools=True,
                cost_per_1k_in=0.001, cost_per_1k_out=0.002,
            )
        ]
    )
    cost_meter = CostMeter()
    fake = FakeProvider(
        responses=[
            _plan_response(),
            _text_response(
                "Concurrency lets independent work make progress during the same "
                "period, then joins those results into one outcome."
            ),
            _text_response(
                "Use three images: independent tasks, a volante passed between them, "
                "and a final join."
            ),
            _text_response(
                "Threads trade a volante while waiting tasks wake together; their "
                "separate progress is joined into one result."
            ),
            _text_response(
                "Threads trade the volante—\n"
                "waiting tasks wake side by side,\n"
                "one goal joins their work.\n\n"
                "The volante represents control passing among concurrent tasks. "
                "They progress independently, then synchronize to produce one result."
            ),
        ]
    )
    return Runtime(
        supervisor=Supervisor(fake, _MID, cost_meter),
        router=Router(registry),
        projector=Projector(registry),
        worker=Worker({_MID: fake}, cost_meter),
        synthesizer=Synthesizer(fake, _MID, cost_meter),
        registry=registry,
        cost_meter=cost_meter,
    )


async def main() -> None:
    runtime = build_fake_runtime()
    result = await runtime.aexecute("Write a short haiku about concurrency.")

    print(f"status: {result.status}")
    if result.final:
        print(f"final:\n{result.final}")
    print(f"billed_usd: {result.billed_usd}  credit_usd: {result.credit_usd}")


if __name__ == "__main__":
    asyncio.run(main())

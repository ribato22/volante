from __future__ import annotations

import json
from collections.abc import Callable

from baton.cost import CostMeter
from baton.projector import Projector
from baton.providers.fake import FakeProvider
from baton.registry import Registry
from baton.router import Router
from baton.runtime import Runtime
from baton.supervisor import Supervisor
from baton.synthesizer import Synthesizer
from baton.types import CanonicalResponse, ModelInfo, TextBlock, Usage
from baton.worker import Worker

_MID = "demo/fake"

# The supervisor's first call returns this DAG; all later calls (workers, synth) echo.
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


def demo_runtime_factory() -> Callable[[], Runtime]:
    """A `() -> Runtime` factory backed by `FakeProvider` (NO network): the planner
    returns a fixed 3-task DAG (two parallel, one dependent), workers and the
    synthesizer echo. Lets the Web UI run and stream end-to-end with no provider
    configured. A fresh Runtime + FakeProvider is built per call (Supervisor is
    non-re-entrant; the queued plan is consumed by the first call)."""
    registry = Registry(
        [
            ModelInfo(
                id=_MID, provider="fake", strengths={"coding", "reasoning"},
                context_window=128_000, max_output_tokens=4_096, supports_tools=True,
                cost_per_1k_in=0.001, cost_per_1k_out=0.002,
            )
        ]
    )

    def make() -> Runtime:
        cm = CostMeter()
        fake = FakeProvider(responses=[_plan_response()])
        return Runtime(
            supervisor=Supervisor(fake, _MID, cm),
            router=Router(registry),
            projector=Projector(registry),
            worker=Worker({_MID: fake}, cm),
            synthesizer=Synthesizer(fake, _MID, cm),
            registry=registry,
            cost_meter=cm,
        )

    return make

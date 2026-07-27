from __future__ import annotations

import json
from collections.abc import Callable

from volante.cost import CostMeter
from volante.projector import Projector
from volante.providers.fake import FakeProvider
from volante.registry import Registry
from volante.router import Router
from volante.runtime import Runtime
from volante.supervisor import Supervisor
from volante.synthesizer import Synthesizer
from volante.types import CanonicalResponse, ModelInfo, TextBlock, Usage
from volante.worker import Worker

_MID = "demo/fake"

# The supervisor's first call returns this DAG; later calls return deterministic,
# meaningful artifacts for the UI's default concurrency brief.
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


def demo_runtime_factory() -> Callable[[], Runtime]:
    """A `() -> Runtime` factory backed by `FakeProvider` (NO network): the planner
    returns a fixed 3-task DAG (two parallel, one dependent), followed by canned
    artifacts and a coherent final answer. Lets the Web UI demonstrate value
    end-to-end with no provider configured. A fresh Runtime + FakeProvider is
    built per call (Supervisor is non-re-entrant; queued responses are consumed
    by that run)."""
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
        fake = FakeProvider(
            responses=[
                _plan_response(),
                _text_response(
                    "Python concurrency allows independent tasks to overlap. "
                    "Threads suit blocking I/O; asyncio coordinates many I/O tasks "
                    "cooperatively; processes provide CPU parallelism."
                ),
                _text_response(
                    "Brief structure: definition, choosing threads/asyncio/processes, "
                    "shared-state risks, and a practical recommendation."
                ),
                _text_response(
                    "Concurrency is about coordinating overlapping work. Use asyncio "
                    "for many network operations, threads for blocking libraries, and "
                    "processes for CPU-bound work. Prefer structured concurrency, "
                    "bounded fan-out, timeouts, and minimal shared mutable state."
                ),
                _text_response(
                    "Python concurrency lets a program make progress on multiple tasks "
                    "during the same period.\n\n"
                    "- Use `asyncio` for large numbers of I/O operations with async APIs.\n"
                    "- Use threads when blocking I/O libraries cannot be made async.\n"
                    "- Use processes for CPU-bound work that needs parallel execution.\n\n"
                    "Whichever model you choose, bound concurrency, define timeouts, "
                    "propagate cancellation, and minimize shared mutable state. Start "
                    "with the simplest option that matches the workload, then measure."
                ),
            ]
        )
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

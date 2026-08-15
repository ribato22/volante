from __future__ import annotations

from pathlib import Path

import pytest

from volante.agent import AgenticWorker
from volante.breaker import BREAKER
from volante.cost import CostMeter
from volante.direct import answer_directly
from volante.providers.base import ProviderError
from volante.registry import Registry
from volante.runtime import Runtime
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    ToolSpec,
    Usage,
)
from volante.worker import Worker


class _NoopTool:
    name = "run_python"
    spec = ToolSpec(name="run_python", description="noop", input_schema={})

    async def run(self, args: dict) -> str:
        return "ok"


def _response(text: str, model: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=text)],
        usage=Usage(prompt_tokens=2, completion_tokens=1),
        model=model,
        stop_reason="end_turn",
        latency_ms=1,
    )


def _model(model_id: str, *, tools: bool = False) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths={"coding", "reasoning"},
        context_window=16_384,
        max_output_tokens=1_024,
        supports_tools=tools,
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        billing="card",
        tier=3,
    )


class _Provider:
    def __init__(self, name: str, responses: list[CanonicalResponse | Exception]) -> None:
        self.name = name
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        # Clamp once the pool is exhausted so a working model keeps answering
        # across the several runs these integration tests drive (and a failing
        # one keeps failing).
        item = self.responses[min(self.calls, len(self.responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        response = await self.complete(req)
        for block in response.content:
            if isinstance(block, TextBlock):
                on_text(block.text)
        return response


class _Supervisor:
    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks

    async def plan(self, goal: str, on_text=None) -> list[Task]:
        return list(self.tasks)


class _RankedRouter:
    def __init__(self, model_ids: list[str]) -> None:
        self.model_ids = model_ids

    def route_ranked(self, task: Task) -> list[str]:
        return list(self.model_ids)


class _Projector:
    def project(self, task: Task, model_id: str, bb: object) -> CanonicalRequest:
        return CanonicalRequest(
            messages=[],
            max_tokens=64,
            task_id=task.id,
            context_window=1_024,
        )


class _Synthesis:
    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        return "final"


def _runtime(
    tmp_path: Path,
    providers: dict[str, _Provider],
    model_ids: list[str],
    *,
    max_retries: int = 1,
    agentic: AgenticWorker | None = None,
) -> Runtime:
    tasks = [
        Task(
            id="task",
            description="work",
            type="code",
            mode="agentic" if agentic is not None else "one_shot",
        )
    ]
    meter = CostMeter()

    def tools_factory(workspace: Path) -> dict:
        workspace.mkdir(parents=True)
        # A working engineer noops tool unless the model asks for one; with a
        # failing provider the loop never gets that far, so one noop tool that
        # satisfies the agentic required-tools default is enough for these tests.
        return {"run_python": _NoopTool()}

    return Runtime(
        _Supervisor(tasks),
        _RankedRouter(model_ids),
        _Projector(),
        Worker(providers, meter),
        _Synthesis(),
        Registry([_model(m, tools=agentic is not None) for m in model_ids]),
        meter,
        max_retries=max_retries,
        agentic_worker=agentic,
        tools_factory=tools_factory if agentic is not None else None,
        available_tool_names={"run_python"},
        runs_dir=tmp_path / "runs",
    )


@pytest.fixture(autouse=True)
def _clean_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLANTE_RELIABILITY_LOG", "")
    BREAKER.reset()
    yield


@pytest.mark.asyncio
async def test_one_shot_skips_open_candidate_on_next_run(
    tmp_path: Path,
) -> None:
    dead = _Provider("m1", [TimeoutError("down"), TimeoutError("down")])
    live = _Provider("m2", [_response("ok", "m2")])
    providers = {"m1": dead, "m2": live}

    # Runtime is single-use: a fresh one per goal, sharing the SAME providers
    # (and the process-wide breaker) so the second run sees the first run's
    # outage without paying for it again.
    first = await _runtime(tmp_path, providers, ["m1", "m2"]).aexecute("goal")
    assert first.status == "success"
    # max_retries=1: the dead model is probed twice (attempt 0 + 1), both time
    # out and open the breaker; m2 answers.
    assert dead.calls == 2
    assert BREAKER.is_open("m1")

    second = await _runtime(tmp_path, providers, ["m1", "m2"]).aexecute("goal")
    assert second.status == "success"
    assert dead.calls == 2  # zero further calls: the outage consumed nothing


@pytest.mark.asyncio
async def test_one_shot_all_open_fails_without_any_call(
    tmp_path: Path,
) -> None:
    dead = _Provider("m1", [TimeoutError("down"), TimeoutError("down")])
    providers = {"m1": dead}

    first = await _runtime(tmp_path, providers, ["m1"], max_retries=0).aexecute("goal")
    assert first.status == "failed"
    assert dead.calls == 1
    assert not BREAKER.is_open("m1")

    second = await _runtime(tmp_path, providers, ["m1"], max_retries=0).aexecute("goal")
    assert second.status == "failed"
    assert dead.calls == 2  # one probe per run until the threshold is crossed
    assert BREAKER.is_open("m1")

    third = await _runtime(tmp_path, providers, ["m1"], max_retries=0).aexecute("goal")
    assert third.status == "failed"
    assert dead.calls == 2  # no call, no retry
    assert third.error_message is not None
    assert "circuit-open" in third.error_message


@pytest.mark.asyncio
async def test_direct_path_skips_open_model(tmp_path: Path) -> None:
    dead = _Provider("d", [TimeoutError("down"), TimeoutError("down")])
    live = _Provider("l", [_response("hello", "l")])
    router = _RankedRouter(["d", "l"])
    registry = Registry([_model("d"), _model("l")])

    # One failing probe per run (the direct path has no retry loop): the first
    # and second runs each pay one call before the 2nd consecutive failure opens
    # the breaker; the third run skips the dead model entirely.
    first = await answer_directly(
        "goal", router, _Projector(), {"d": dead, "l": live}, registry, CostMeter()
    )
    assert first.status == "success"
    assert dead.calls == 1
    assert not BREAKER.is_open("d")

    second = await answer_directly(
        "goal", router, _Projector(), {"d": dead, "l": live}, registry, CostMeter()
    )
    assert second.status == "success"
    assert dead.calls == 2
    assert BREAKER.is_open("d")

    third = await answer_directly(
        "goal", router, _Projector(), {"d": dead, "l": live}, registry, CostMeter()
    )
    assert third.status == "success"
    assert dead.calls == 2  # the ranked dead model was skipped, not probed
    events = third.routing_decisions["__direct__"]["fallback_events"]
    assert [e["reason"] for e in events] == ["circuit_open"]
    assert "consecutive failures" in events[0]["message"]


@pytest.mark.asyncio
async def test_agentic_gate_blocks_calls_while_open(tmp_path: Path) -> None:
    flaky = _Provider(
        "m",
        [
            ProviderError("flaky", retryable=True),
            ProviderError("flaky", retryable=True),
        ],
    )
    providers = {"m": flaky}

    workers = [AgenticWorker(providers, CostMeter(), max_iters=2, max_retries=1, char_budget=4_000)]
    first = await _runtime(tmp_path, providers, ["m"], agentic=workers[0]).aexecute("goal")
    assert first.status == "failed"
    assert flaky.calls == 2  # both attempts failed -> breaker open
    assert BREAKER.is_open("m")

    second = await _runtime(tmp_path, providers, ["m"], agentic=workers[0]).aexecute("goal")
    assert second.status == "failed"
    assert flaky.calls == 2  # the gate refused before any provider request
    assert second.error_message is not None
    assert "circuit open" in second.error_message

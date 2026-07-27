from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from volante.agent import AgenticResult
from volante.cost import CostMeter
from volante.projector import Projector
from volante.providers.base import ProviderError
from volante.registry import Registry
from volante.router import Router
from volante.runtime import Runtime, _safe_task_workspace
from volante.supervisor import Supervisor
from volante.synthesizer import Synthesizer
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    Usage,
)
from volante.worker import Worker


def _response(
    text: str,
    model: str,
    *,
    stop_reason: str = "end_turn",
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=text)],
        usage=Usage(prompt_tokens=2, completion_tokens=1),
        model=model,
        stop_reason=stop_reason,
        latency_ms=1,
    )


def _plan_response(model: str = "planner") -> CanonicalResponse:
    return _response(
        json.dumps(
            [
                {
                    "id": "task",
                    "description": "work",
                    "type": "code",
                    "mode": "one_shot",
                    "difficulty": "medium",
                    "depends_on": [],
                    "required_tools": [],
                }
            ]
        ),
        model,
    )


def _model(
    model_id: str,
    *,
    tools: bool = False,
    billing: str = "card",
    tier: int = 3,
    strengths: set[str] | None = None,
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths=strengths or {"coding", "reasoning"},
        context_window=16_384,
        max_output_tokens=1_024,
        supports_tools=tools,
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        billing=billing,
        tier=tier,
    )


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
    def project(
        self, task: Task, model_id: str, bb: object
    ) -> CanonicalRequest:
        return CanonicalRequest(
            messages=[],
            max_tokens=64,
            task_id=task.id,
            context_window=1_024,
        )


class _Synthesis:
    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        return "final"


class _FailingSynthesis:
    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        raise RuntimeError("synthesis exploded")


class _WaitingSupervisor:
    async def plan(self, goal: str, on_text=None) -> list[Task]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _WaitingSynthesis:
    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _Provider:
    def __init__(
        self,
        name: str,
        responses: list[CanonicalResponse | Exception],
    ) -> None:
        self.name = name
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        response = await self.complete(req)
        for block in response.content:
            if isinstance(block, TextBlock):
                on_text(block.text)
        return response


class _AgenticDone:
    async def run(self, req, model_id, tools, on_text=None) -> AgenticResult:
        return AgenticResult(
            final_text="done",
            usage_total={model_id: Usage(1, 1)},
        )


@pytest.mark.parametrize("task_id", ["../escape", "/tmp/escape", "", "x" * 65])
def test_runtime_revalidates_custom_supervisor_task_ids(
    task_id: str, tmp_path: Path
) -> None:
    task = Task(
        id=task_id,
        description="unsafe",
        type="code",
        mode="one_shot",
    )
    meter = CostMeter()
    runtime = Runtime(
        _Supervisor([task]),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [_response("x", "m")])}, meter),
        _Synthesis(),
        Registry([_model("m")]),
        meter,
        runs_dir=tmp_path / "runs",
    )

    result = runtime.execute("goal")
    assert result.status == "failed"
    assert result.failed_task == "__planning__"
    assert result.error_code == "invalid_plan"
    assert "task id" in (result.error_message or "") or "unsafe" in (
        result.error_message or ""
    )
    assert not (tmp_path / "escape").exists()


def test_workspace_resolver_rejects_escape_even_without_schema_validation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="escapes run root"):
        _safe_task_workspace(tmp_path / "runs", "run", "../../escape")


def _workspace_runtime(
    tmp_path: Path,
    *,
    agentic_worker: object,
    synthesizer: object,
) -> Runtime:
    meter = CostMeter()
    model = _model("m", tools=True)

    def tools_factory(workspace: Path) -> dict:
        workspace.mkdir(parents=True)
        (workspace / "state.txt").write_text("temporary")
        return {"run_python": object()}

    return Runtime(
        _Supervisor(
            [Task(id="task", description="use tools", type="code", mode="agentic")]
        ),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [_response("unused", "m")])}, meter),
        synthesizer,
        Registry([model]),
        meter,
        agentic_worker=agentic_worker,
        tools_factory=tools_factory,
        runs_dir=tmp_path / "runs",
    )


@pytest.mark.asyncio
async def test_workspace_cleanup_runs_after_synthesis_exception(
    tmp_path: Path,
) -> None:
    runtime = _workspace_runtime(
        tmp_path,
        agentic_worker=_AgenticDone(),
        synthesizer=_FailingSynthesis(),
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "__synthesis__"
    assert result.error_code == "synthesis_error"
    assert not (tmp_path / "runs").exists() or not any(
        (tmp_path / "runs").iterdir()
    )


@pytest.mark.asyncio
async def test_planning_phase_has_a_hard_deadline() -> None:
    meter = CostMeter()
    registry = Registry([_model("m")])
    runtime = Runtime(
        _WaitingSupervisor(),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [])}, meter),
        _Synthesis(),
        registry,
        meter,
        planning_timeout=0.01,
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "__planning__"
    assert result.error_code == "phase_timeout"


@pytest.mark.asyncio
async def test_synthesis_phase_has_a_hard_deadline() -> None:
    meter = CostMeter()
    registry = Registry([_model("m")])
    runtime = Runtime(
        _Supervisor(
            [Task(id="task", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [_response("artifact", "m")])}, meter),
        _WaitingSynthesis(),
        registry,
        meter,
        synthesis_timeout=0.01,
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "__synthesis__"
    assert result.error_code == "phase_timeout"


@pytest.mark.asyncio
async def test_planning_provider_outage_falls_back_with_auditable_trace() -> None:
    meter = CostMeter()
    registry = Registry([_model("primary", tier=4), _model("fallback", tier=2)])
    primary = _Provider(
        "primary",
        [
            ProviderError(
                "primary unavailable",
                retryable=True,
                status=503,
            )
        ],
    )
    fallback = _Provider("fallback", [_plan_response("fallback-wire")])
    worker_provider = _Provider(
        "worker",
        [_response("artifact", "fallback")],
    )
    runtime = Runtime(
        Supervisor(primary, "primary", meter),
        _RankedRouter(["fallback"]),
        Projector(registry),
        Worker({"fallback": worker_provider}, meter),
        _Synthesis(),
        registry,
        meter,
        phase_providers={"primary": primary, "fallback": fallback},
        planning_model_ids=["primary", "fallback"],
    )

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert result.partial_artifacts == {"task": "artifact"}
    assert primary.calls == 1
    assert fallback.calls == 1
    trace = result.routing_decisions["__planning__"]
    assert trace["selected_model_id"] == "primary"
    assert trace["executed_model_id"] == "fallback"
    assert trace["fallback_events"] == [
        {
            "model_id": "primary",
            "reason": "provider_unavailable",
            "message": "primary unavailable",
        }
    ]
    assert result.usage_total["fallback"] == Usage(4, 2)


@pytest.mark.asyncio
async def test_invalid_planner_output_can_fall_back_after_bounded_corrections() -> None:
    meter = CostMeter()
    registry = Registry([_model("primary", tier=4), _model("fallback", tier=2)])
    primary = _Provider(
        "primary",
        [
            _response("not json", "primary"),
            _response("still not json", "primary"),
            _response("never json", "primary"),
        ],
    )
    fallback = _Provider("fallback", [_plan_response("fallback")])
    runtime = Runtime(
        Supervisor(primary, "primary", meter),
        _RankedRouter(["fallback"]),
        Projector(registry),
        Worker(
            {"fallback": _Provider("worker", [_response("artifact", "fallback")])},
            meter,
        ),
        _Synthesis(),
        registry,
        meter,
        phase_providers={"primary": primary, "fallback": fallback},
        planning_model_ids=["primary", "fallback"],
    )

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert primary.calls == 3
    assert result.routing_decisions["__planning__"]["fallback_events"][0][
        "reason"
    ] == "invalid_plan"


@pytest.mark.asyncio
async def test_hung_planner_reserves_deadline_for_fallback() -> None:
    meter = CostMeter()
    registry = Registry([_model("primary"), _model("fallback")])
    fallback = _Provider("fallback", [_plan_response("fallback")])
    runtime = Runtime(
        _WaitingSupervisor(),
        _RankedRouter(["fallback"]),
        Projector(registry),
        Worker(
            {"fallback": _Provider("worker", [_response("artifact", "fallback")])},
            meter,
        ),
        _Synthesis(),
        registry,
        meter,
        planning_timeout=0.05,
        phase_providers={
            "primary": _Provider("unused", []),
            "fallback": fallback,
        },
        planning_model_ids=["primary", "fallback"],
    )

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert result.routing_decisions["__planning__"]["fallback_events"][0][
        "reason"
    ] == "provider_timeout"


@pytest.mark.asyncio
async def test_synthesis_provider_outage_falls_back_with_auditable_trace() -> None:
    meter = CostMeter()
    registry = Registry([_model("primary", tier=4), _model("fallback", tier=2)])
    primary = _Provider(
        "primary",
        [
            ProviderError(
                "configured deployment unavailable",
                retryable=False,
                status=404,
                candidate_unavailable=True,
            )
        ],
    )
    fallback = _Provider(
        "fallback",
        [_response("final from fallback", "fallback-wire")],
    )
    runtime = Runtime(
        _Supervisor(
            [Task(id="task", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["fallback"]),
        Projector(registry),
        Worker(
            {"fallback": _Provider("worker", [_response("artifact", "fallback")])},
            meter,
        ),
        Synthesizer(primary, "primary", meter),
        registry,
        meter,
        phase_providers={"primary": primary, "fallback": fallback},
        synthesis_model_ids=["primary", "fallback"],
    )

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert result.final == "final from fallback"
    trace = result.routing_decisions["__synthesis__"]
    assert trace["selected_model_id"] == "primary"
    assert trace["executed_model_id"] == "fallback"
    assert trace["fallback_events"] == [
        {
            "model_id": "primary",
            "reason": "candidate_unavailable",
            "message": "configured deployment unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_worker_truncation_returns_structured_incomplete_output() -> None:
    meter = CostMeter()
    registry = Registry([_model("m")])
    runtime = Runtime(
        _Supervisor(
            [Task(id="task", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["m"]),
        _Projector(),
        Worker(
            {
                "m": _Provider(
                    "m",
                    [_response("partial", "m", stop_reason="max_tokens")],
                )
            },
            meter,
        ),
        _Synthesis(),
        registry,
        meter,
        max_retries=0,
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "task"
    assert result.error_code == "incomplete_output"
    assert result.partial_artifacts == {}
    assert result.usage_total["m"] == Usage(2, 1)


@pytest.mark.asyncio
async def test_synthesis_filter_returns_structured_incomplete_output() -> None:
    meter = CostMeter()
    registry = Registry([_model("m")])
    runtime = Runtime(
        _Supervisor(
            [Task(id="task", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [_response("artifact", "m")])}, meter),
        Synthesizer(
            _Provider(
                "m",
                [_response("partial final", "m", stop_reason="content_filter")],
            ),
            "m",
            meter,
        ),
        registry,
        meter,
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "__synthesis__"
    assert result.error_code == "incomplete_output"
    assert result.partial_artifacts == {"task": "artifact"}
    assert result.usage_total["m"] == Usage(4, 2)


class _AgenticFails:
    async def run(self, req, model_id, tools, on_text=None) -> AgenticResult:
        raise RuntimeError("agent failed")


@pytest.mark.asyncio
async def test_workspace_cleanup_runs_after_task_failure(tmp_path: Path) -> None:
    runtime = _workspace_runtime(
        tmp_path,
        agentic_worker=_AgenticFails(),
        synthesizer=_Synthesis(),
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "task"
    assert not (tmp_path / "runs").exists() or not any(
        (tmp_path / "runs").iterdir()
    )


class _AgenticWaits:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, req, model_id, tools, on_text=None) -> AgenticResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_workspace_cleanup_runs_after_cancellation(tmp_path: Path) -> None:
    agentic = _AgenticWaits()
    runtime = _workspace_runtime(
        tmp_path, agentic_worker=agentic, synthesizer=_Synthesis()
    )
    execution = asyncio.create_task(runtime.aexecute("goal"))
    await agentic.started.wait()

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert not (tmp_path / "runs").exists() or not any(
        (tmp_path / "runs").iterdir()
    )


@pytest.mark.asyncio
async def test_global_subscription_cap_counts_planner_worker_and_synthesis(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "2")
    model_id = "subscription/model"
    plan_json = json.dumps(
        [
            {
                "id": "t1",
                "description": "work",
                "type": "code",
                "mode": "one_shot",
                "difficulty": "medium",
                "depends_on": [],
            }
        ]
    )
    planner = _Provider("planner", [_response(plan_json, model_id)])
    worker_provider = _Provider("worker", [_response("artifact", model_id)])
    synthesis_provider = _Provider("synthesis", [_response("final", model_id)])
    meter = CostMeter()
    registry = Registry([_model(model_id, billing="plan_included")])
    runtime = Runtime(
        Supervisor(planner, model_id, meter),
        _RankedRouter([model_id]),
        Projector(registry),
        Worker({model_id: worker_provider}, meter),
        Synthesizer(synthesis_provider, model_id, meter),
        registry,
        meter,
    )

    result = await runtime.aexecute("goal")

    assert planner.calls == 1
    assert worker_provider.calls == 1
    assert synthesis_provider.calls == 0
    assert result.status == "failed"
    assert result.failed_task == "__synthesis__"
    assert result.error_code == "quota_exhausted"
    assert result.subscription_calls == 2


@pytest.mark.asyncio
async def test_subscription_retry_attempts_consume_physical_cap(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "1")

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("volante.runtime.asyncio.sleep", no_sleep)
    sub = _Provider(
        "sub",
        [ProviderError("retry", retryable=True)],
    )
    direct = _Provider("direct", [_response("fallback", "direct")])
    meter = CostMeter()
    registry = Registry(
        [
            _model("sub", billing="plan_included"),
            _model("direct"),
        ]
    )
    runtime = Runtime(
        _Supervisor(
            [Task(id="t1", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["sub", "direct"]),
        _Projector(),
        Worker({"sub": sub, "direct": direct}, meter),
        _Synthesis(),
        registry,
        meter,
        max_retries=2,
    )

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert sub.calls == 1
    assert direct.calls == 1


def test_runtime_persists_explainable_routing_decision() -> None:
    meter = CostMeter()
    weak = _model("weak", tier=1)
    strong = _model("strong", tier=4)
    registry = Registry([weak, strong])
    runtime = Runtime(
        _Supervisor(
            [Task(id="t1", description="work", type="code", mode="one_shot")]
        ),
        Router(registry, prefer="quality"),
        Projector(registry),
        Worker(
            {
                "weak": _Provider("weak", [_response("weak", "weak")]),
                "strong": _Provider(
                    "strong", [_response("strong", "wire-model-alias")]
                ),
            },
            meter,
        ),
        _Synthesis(),
        registry,
        meter,
    )

    result = runtime.execute("goal")

    decision = result.routing_decisions["t1"]
    assert decision["selected_model_id"] == "strong"
    assert decision["ranked_model_ids"][0] == "strong"
    assert decision["executed_model_id"] == "strong"
    assert decision["provider_reported_model_id"] == "wire-model-alias"
    assert {item["model_id"] for item in decision["candidates"]} == {
        "weak",
        "strong",
    }


def test_runtime_reroutes_live_unavailable_candidate_and_records_reason() -> None:
    meter = CostMeter()
    weak = _model("weak", tier=1)
    strong = _model("strong", tier=4)
    registry = Registry([weak, strong])
    unavailable = _Provider(
        "strong",
        [
            ProviderError(
                "model_not_found",
                retryable=False,
                status=404,
                candidate_unavailable=True,
            )
        ],
    )
    fallback = _Provider("weak", [_response("fallback", "weak-wire")])
    runtime = Runtime(
        _Supervisor(
            [Task(id="t1", description="work", type="code", mode="one_shot")]
        ),
        Router(registry, prefer="quality"),
        Projector(registry),
        Worker({"weak": fallback, "strong": unavailable}, meter),
        _Synthesis(),
        registry,
        meter,
    )

    result = runtime.execute("goal")

    assert result.status == "success"
    assert unavailable.calls == 1
    assert fallback.calls == 1
    decision = result.routing_decisions["t1"]
    assert decision["selected_model_id"] == "strong"
    assert decision["executed_model_id"] == "weak"
    assert decision["provider_reported_model_id"] == "weak-wire"
    assert decision["fallback_events"] == [
        {
            "model_id": "strong",
            "reason": "candidate_unavailable",
            "message": "model_not_found",
        }
    ]


def test_runtime_retries_transient_outage_then_falls_back_to_next_provider(
    monkeypatch,
) -> None:
    slept: list[float] = []

    async def no_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("volante.runtime.asyncio.sleep", no_sleep)
    meter = CostMeter()
    primary = _model("primary", tier=4)
    fallback_model = _model("fallback", tier=1)
    registry = Registry([primary, fallback_model])
    outage = _Provider(
        "primary",
        [
            ProviderError("upstream 503", retryable=True, status=503),
            ProviderError("upstream 503", retryable=True, status=503),
        ],
    )
    fallback = _Provider(
        "fallback",
        [_response("recovered", "fallback-wire")],
    )
    runtime = Runtime(
        _Supervisor(
            [Task(id="t1", description="work", type="code", mode="one_shot")]
        ),
        Router(registry, prefer="quality"),
        Projector(registry),
        Worker({"primary": outage, "fallback": fallback}, meter),
        _Synthesis(),
        registry,
        meter,
        max_retries=1,
    )

    result = runtime.execute("goal")

    assert result.status == "success"
    assert outage.calls == 2
    assert fallback.calls == 1
    assert len(slept) == 1
    decision = result.routing_decisions["t1"]
    assert decision["executed_model_id"] == "fallback"
    assert decision["fallback_events"] == [
        {
            "model_id": "primary",
            "reason": "provider_unavailable",
            "message": "upstream 503",
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fan_out": 0}, "fan_out"),
        ({"fan_out": True}, "fan_out"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
    ],
)
def test_runtime_rejects_deadlocking_or_invalid_execution_limits(
    kwargs: dict[str, object], message: str
) -> None:
    meter = CostMeter()
    with pytest.raises(ValueError, match=message):
        Runtime(
            _Supervisor([]),
            _RankedRouter(["m"]),
            _Projector(),
            Worker({"m": _Provider("m", [])}, meter),
            _Synthesis(),
            Registry([_model("m")]),
            meter,
            **kwargs,
        )


@pytest.mark.parametrize("goal", ["", "   "])
def test_runtime_returns_structured_invalid_goal(goal: str) -> None:
    meter = CostMeter()
    runtime = Runtime(
        _Supervisor([]),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [])}, meter),
        _Synthesis(),
        Registry([_model("m")]),
        meter,
    )

    result = runtime.execute(goal)

    assert result.status == "failed"
    assert result.failed_task == "__planning__"
    assert result.error_code == "invalid_goal"


def test_no_eligible_one_shot_model_is_capability_unavailable() -> None:
    meter = CostMeter()
    registry = Registry([_model("writer", strengths={"reasoning"})])
    runtime = Runtime(
        _Supervisor(
            [Task(id="code", description="implement", type="code", mode="one_shot")]
        ),
        Router(registry),
        Projector(registry),
        Worker({"writer": _Provider("writer", [])}, meter),
        _Synthesis(),
        registry,
        meter,
    )

    result = runtime.execute("build it")

    assert result.status == "failed"
    assert result.error_code == "capability_unavailable"
    assert "missing required strengths" in (result.error_message or "")
    assert result.routing_decisions["code"]["rejected_models"]["writer"]


def test_agentic_capability_error_preserves_actual_rejection_reason() -> None:
    meter = CostMeter()
    registry = Registry(
        [_model("tool-writer", tools=True, strengths={"reasoning"})]
    )
    runtime = Runtime(
        _Supervisor(
            [Task(id="code", description="execute", type="code", mode="agentic")]
        ),
        Router(registry),
        Projector(registry),
        Worker({"tool-writer": _Provider("tool-writer", [])}, meter),
        _Synthesis(),
        registry,
        meter,
        agentic_worker=_AgenticDone(),
    )

    result = runtime.execute("build it")

    assert result.error_code == "capability_unavailable"
    assert "missing required strengths" in (result.error_message or "")
    assert "requires tools" not in (result.error_message or "")


def test_agentic_task_fails_before_dispatch_when_required_tool_is_disabled() -> None:
    meter = CostMeter()
    registry = Registry([_model("tool-model", tools=True)])
    runtime = Runtime(
        _Supervisor(
            [
                Task(
                    id="fetch",
                    description="fetch an allowlisted URL",
                    type="research",
                    mode="agentic",
                    required_tools=["fetch_url"],
                )
            ]
        ),
        Router(registry),
        Projector(registry),
        Worker({"tool-model": _Provider("tool-model", [])}, meter),
        _Synthesis(),
        registry,
        meter,
        agentic_worker=_AgenticDone(),
        available_tool_names={"run_python"},
    )

    result = runtime.execute("research it")

    assert result.status == "failed"
    assert result.failed_task == "fetch"
    assert result.error_code == "capability_unavailable"
    assert "fetch_url" in (result.error_message or "")


def test_runtime_preserves_explicit_empty_tool_inventory() -> None:
    meter = CostMeter()
    runtime = Runtime(
        _Supervisor([]),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [])}, meter),
        _Synthesis(),
        Registry([_model("m")]),
        meter,
        available_tool_names=set(),
    )

    assert runtime.available_tool_names == set()


def test_runtime_rejects_unknown_available_tool_name() -> None:
    meter = CostMeter()
    with pytest.raises(ValueError, match="unsupported tools"):
        Runtime(
            _Supervisor([]),
            _RankedRouter(["m"]),
            _Projector(),
            Worker({"m": _Provider("m", [])}, meter),
            _Synthesis(),
            Registry([_model("m")]),
            meter,
            available_tool_names={"shell"},
        )


class _ProviderErrorBody(Runtime):
    """A Runtime whose per-task body raises a bare ProviderError. Exercises the
    _run_task exception boundary directly: in the shipped default paths
    _run_task_body/_run_agentic absorb provider/timeout errors and return False, so this
    escape is unreachable in practice — the subclass forces it to prove the contract."""

    async def _run_task_body(self, *args: object, **kwargs: object) -> bool:
        raise ProviderError("provider blew up mid-task", retryable=False)


@pytest.mark.asyncio
async def test_task_body_exception_converts_to_structured_failure(
    tmp_path: Path,
) -> None:
    # A ProviderError/TimeoutError escaping the per-task body must become a recorded
    # task failure -> RunResult(status="failed"), never a raw exception out of aexecute
    # (the per-task gather in aexecute has no surrounding except).
    meter = CostMeter()
    runtime = _ProviderErrorBody(
        _Supervisor(
            [Task(id="task", description="work", type="code", mode="one_shot")]
        ),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", [_response("x", "m")])}, meter),
        _Synthesis(),
        Registry([_model("m")]),
        meter,
        runs_dir=tmp_path / "runs",
    )

    result = await runtime.aexecute("goal")

    assert result.status == "failed"
    assert result.failed_task == "task"
    assert result.error_code  # a structured code, not a raised exception
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def _priced_model(model_id: str = "m") -> ModelInfo:
    """A card-billed model with non-zero rates, so cost assertions are meaningful."""
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths={"coding", "reasoning"},
        context_window=16_384,
        max_output_tokens=1_024,
        supports_tools=False,
        cost_per_1k_in=1.0,
        cost_per_1k_out=1.0,
        billing="card",
        tier=3,
    )


def _reusable_runtime(
    tmp_path: Path,
    *,
    meter: CostMeter,
    responses: list[CanonicalResponse | Exception],
    supervisor: object | None = None,
) -> Runtime:
    task = Task(id="task", description="work", type="code", mode="one_shot")
    return Runtime(
        supervisor or _Supervisor([task]),
        _RankedRouter(["m"]),
        _Projector(),
        Worker({"m": _Provider("m", responses)}, meter),
        _Synthesis(),
        Registry([_priced_model("m")]),
        meter,
        runs_dir=tmp_path / "runs",
    )


@pytest.mark.asyncio
async def test_runtime_refuses_a_second_goal(tmp_path: Path) -> None:
    # A Runtime is single-use: its Supervisor is explicitly non-re-entrant and the cost
    # meter, subscription counter, and route trace are per-run. A second goal must fail
    # loudly and actionably rather than silently report the previous run's accounting.
    meter = CostMeter()
    runtime = _reusable_runtime(
        tmp_path,
        meter=meter,
        responses=[_response("a", "m"), _response("b", "m")],
    )

    first = await runtime.aexecute("goal one")
    assert first.status == "success"

    with pytest.raises(RuntimeError, match="single-use"):
        await runtime.aexecute("goal two")


@pytest.mark.asyncio
async def test_concurrent_runs_on_one_runtime_are_refused(tmp_path: Path) -> None:
    # The same guard also covers overlap: a second run started while the first is still
    # in flight would corrupt both runs' counters and traces.
    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedSupervisor:
        async def plan(self, goal: str, on_text=None) -> list[Task]:
            entered.set()
            await gate.wait()
            return [Task(id="task", description="work", type="code", mode="one_shot")]

    meter = CostMeter()
    runtime = _reusable_runtime(
        tmp_path,
        meter=meter,
        responses=[_response("a", "m")],
        supervisor=_GatedSupervisor(),
    )

    first = asyncio.create_task(runtime.aexecute("goal one"))
    await entered.wait()
    try:
        # wait_for bounds the failure mode: without the guard the second run would
        # block on the same gate forever instead of failing the assertion.
        with pytest.raises(RuntimeError, match="single-use"):
            await asyncio.wait_for(runtime.aexecute("goal two"), timeout=2)
    finally:
        gate.set()
        result = await first
    # The in-flight run must be untouched by the refused caller: a guard placed after
    # the per-run reset would zero these out.
    assert result.status == "success"
    assert result.cost_usd == pytest.approx(0.003)
    assert result.usage_total["m"].prompt_tokens == 2
    assert result.subscription_calls == 0


@pytest.mark.asyncio
async def test_invalid_subscription_cap_does_not_consume_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Env validation runs BEFORE the single-use flag is set, so a rejected call must
    # leave the Runtime usable rather than burning it on a configuration typo.
    meter = CostMeter()
    runtime = _reusable_runtime(tmp_path, meter=meter, responses=[_response("a", "m")])

    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "not-a-number")
    with pytest.raises(ValueError, match="VOLANTE_MAX_SUBSCRIPTION_CALLS"):
        await runtime.aexecute("goal")

    monkeypatch.setenv("VOLANTE_MAX_SUBSCRIPTION_CALLS", "16")
    result = await runtime.aexecute("goal")
    assert result.status == "success"


@pytest.mark.asyncio
async def test_run_result_flags_estimated_cost(tmp_path: Path) -> None:
    # The two-ledger dollar figures are only honest if the caller can tell that a
    # provider returned no usage and Volante estimated the tokens.
    estimated = CanonicalResponse(
        content=[TextBlock(text="a")],
        usage=Usage(prompt_tokens=2, completion_tokens=1, estimated=True),
        model="m",
        stop_reason="end_turn",
        latency_ms=1,
    )
    meter = CostMeter()
    runtime = _reusable_runtime(tmp_path, meter=meter, responses=[estimated])

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert result.cost_estimated is True


@pytest.mark.asyncio
async def test_run_result_reports_authoritative_cost_as_not_estimated(
    tmp_path: Path,
) -> None:
    meter = CostMeter()
    runtime = _reusable_runtime(tmp_path, meter=meter, responses=[_response("a", "m")])

    result = await runtime.aexecute("goal")

    assert result.status == "success"
    assert result.cost_estimated is False


@pytest.mark.asyncio
async def test_cancelled_run_still_releases_the_runtime_cleanly(tmp_path: Path) -> None:
    # Cancellation consumes the single use (the Supervisor already started planning), but
    # it must still unwind through the outer finally: workspace cleaned, no hang, and the
    # next call refused with the normal single-use error rather than a stuck state.
    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedSupervisor:
        async def plan(self, goal: str, on_text=None) -> list[Task]:
            entered.set()
            await gate.wait()
            return [Task(id="task", description="work", type="code", mode="one_shot")]

    meter = CostMeter()
    runtime = _reusable_runtime(
        tmp_path,
        meter=meter,
        responses=[_response("a", "m")],
        supervisor=_GatedSupervisor(),
    )

    cancelled = asyncio.create_task(runtime.aexecute("goal one"))
    await entered.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    gate.set()
    with pytest.raises(RuntimeError, match="single-use"):
        await runtime.aexecute("goal two")
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())

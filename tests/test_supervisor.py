from __future__ import annotations

import json

from orchestrator.cost import CostMeter
from orchestrator.providers.fake import FakeProvider
from orchestrator.supervisor import Supervisor
from orchestrator.types import CanonicalResponse, Task, TextBlock, Usage

_PLANNER_MODEL = "anthropic/claude-sonnet-5"


def _resp(
    payload: str,
    *,
    prompt: int = 0,
    completion: int = 0,
    estimated: bool = False,
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=payload)],
        usage=Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            estimated=estimated,
        ),
        model="fake",
        stop_reason="end_turn",
        latency_ms=0,
    )


def _one_task_plan() -> str:
    return json.dumps(
        [
            {
                "id": "t1",
                "description": "do the thing",
                "type": "code",
                "mode": "one_shot",
                "depends_on": [],
            }
        ]
    )


async def test_plan_returns_three_tasks() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "t1",
                "description": "research the topic",
                "type": "research",
                "mode": "one_shot",
                "depends_on": [],
            },
            {
                "id": "t2",
                "description": "write a draft",
                "type": "write",
                "mode": "one_shot",
                "depends_on": ["t1"],
            },
            {
                "id": "t3",
                "description": "review the draft",
                "type": "analyze",
                "mode": "one_shot",
                "depends_on": ["t2"],
            },
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    tasks = await sup.plan("Write a short essay about tea")

    assert len(tasks) == 3
    assert all(isinstance(t, Task) for t in tasks)
    assert [t.id for t in tasks] == ["t1", "t2", "t3"]
    assert tasks[0].type == "research"
    assert tasks[1].mode == "one_shot"
    assert tasks[1].depends_on == ["t1"]
    assert tasks[2].depends_on == ["t2"]


async def test_plan_strips_json_code_fence() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "a",
                "description": "do a",
                "type": "code",
                "mode": "one_shot",
                "depends_on": [],
            }
        ]
    )
    fenced = f"```json\n{plan_json}\n```"
    provider = FakeProvider(responses=[_resp(fenced)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    tasks = await sup.plan("build a thing")

    assert len(tasks) == 1
    assert tasks[0].id == "a"
    assert tasks[0].description == "do a"


async def test_plan_records_planning_usage_in_cost_meter() -> None:
    # PATCH: cost_meter.add(model_id, resp.usage) SETELAH complete().
    provider = FakeProvider(responses=[_resp(_one_task_plan(), prompt=120, completion=45)])
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    await sup.plan("plan me")

    totals = cost_meter.totals()
    assert _PLANNER_MODEL in totals
    assert totals[_PLANNER_MODEL].prompt_tokens == 120
    assert totals[_PLANNER_MODEL].completion_tokens == 45


async def test_cost_meter_keyed_by_model_id_not_provider_name() -> None:
    # PATCH: key akumulasi adalah model_id, BUKAN provider.name ("fake").
    provider = FakeProvider(
        responses=[_resp(_one_task_plan(), prompt=10, completion=2)],
        name="fake",
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    await sup.plan("plan me")

    assert list(cost_meter.totals().keys()) == [_PLANNER_MODEL]
    assert "fake" not in cost_meter.totals()


async def test_estimated_usage_propagates_to_has_estimated() -> None:
    # PATCH: Usage.estimated dari provider merambat ke CostMeter.has_estimated().
    provider = FakeProvider(
        responses=[_resp(_one_task_plan(), prompt=8, completion=3, estimated=True)]
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    assert cost_meter.has_estimated() is False
    await sup.plan("plan me")
    assert cost_meter.has_estimated() is True

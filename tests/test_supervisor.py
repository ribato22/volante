from __future__ import annotations

import json

import pytest

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


async def test_plan_rejects_cycle() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "x",
                "description": "x",
                "type": "code",
                "mode": "one_shot",
                "depends_on": ["y"],
            },
            {
                "id": "y",
                "description": "y",
                "type": "code",
                "mode": "one_shot",
                "depends_on": ["x"],
            },
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError):
        await sup.plan("a cyclic goal")


async def test_plan_rejects_duplicate_ids() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "dup",
                "description": "one",
                "type": "code",
                "mode": "one_shot",
                "depends_on": [],
            },
            {
                "id": "dup",
                "description": "two",
                "type": "code",
                "mode": "one_shot",
                "depends_on": [],
            },
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError):
        await sup.plan("a duplicate-id goal")


async def test_plan_rejects_unknown_dependency() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "only",
                "description": "only",
                "type": "code",
                "mode": "one_shot",
                "depends_on": ["ghost"],
            }
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError):
        await sup.plan("a dangling-dependency goal")


async def test_plan_rejects_empty_plan() -> None:
    # Regresi: plan kosong [] lolos DAG-check (Kahn resolved==0==len) dan bikin
    # aexecute lapor "success" tanpa kerja. Harus ditolak ValueError.
    provider = FakeProvider(responses=[_resp("[]")])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError, match="empty"):
        await sup.plan("a goal the planner refuses to decompose")


async def test_plan_rejects_empty_plan_inside_fence() -> None:
    provider = FakeProvider(responses=[_resp("```json\n[]\n```")])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError, match="empty"):
        await sup.plan("a goal")


async def test_plan_null_depends_on_is_clean_valueerror_not_typeerror() -> None:
    # Regresi: depends_on: null (key ada, nilai JSON null) dulu -> TypeError mentah
    # tak tertangkap. Sekarang null diperlakukan [] -> plan valid 1-task.
    plan_json = json.dumps(
        [
            {
                "id": "solo",
                "description": "do it",
                "type": "code",
                "mode": "one_shot",
                "depends_on": None,
            }
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    tasks = await sup.plan("a goal with null depends_on")
    assert [t.id for t in tasks] == ["solo"]
    assert tasks[0].depends_on == []


async def test_plan_non_list_depends_on_rejected() -> None:
    plan_json = json.dumps(
        [
            {
                "id": "solo",
                "description": "do it",
                "type": "code",
                "mode": "one_shot",
                "depends_on": "t0",
            }
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError, match="depends_on"):
        await sup.plan("a goal with malformed depends_on")


async def test_plan_is_non_reentrant() -> None:
    provider = FakeProvider(responses=[_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    first = await sup.plan("first call")
    assert [t.id for t in first] == ["t1"]

    with pytest.raises(RuntimeError):
        await sup.plan("second call")


async def test_planning_call_is_billed_even_when_plan_invalid() -> None:
    # PATCH: add() berada SETELAH complete() dan SEBELUM validasi -> panggilan
    # planner tetap ditagih walau plan siklik ditolak dengan ValueError.
    plan_json = json.dumps(
        [
            {
                "id": "x",
                "description": "x",
                "type": "code",
                "mode": "one_shot",
                "depends_on": ["y"],
            },
            {
                "id": "y",
                "description": "y",
                "type": "code",
                "mode": "one_shot",
                "depends_on": ["x"],
            },
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json, prompt=90, completion=30)])
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    with pytest.raises(ValueError):
        await sup.plan("a cyclic goal")

    totals = cost_meter.totals()
    assert totals[_PLANNER_MODEL].prompt_tokens == 90
    assert totals[_PLANNER_MODEL].completion_tokens == 30


async def test_reentrant_call_is_not_billed() -> None:
    # PATCH: guard non-re-entrant raise SEBELUM complete() -> panggilan kedua
    # tidak menyentuh provider dan tidak menagih apa pun. Response kedua (999)
    # sengaja diantre; bila guard bocor & complete() terpanggil, total akan naik.
    provider = FakeProvider(
        responses=[
            _resp(_one_task_plan(), prompt=15, completion=5),
            _resp(_one_task_plan(), prompt=999, completion=999),
        ]
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    await sup.plan("first call")
    with pytest.raises(RuntimeError):
        await sup.plan("second call")

    totals = cost_meter.totals()
    assert totals[_PLANNER_MODEL].prompt_tokens == 15
    assert totals[_PLANNER_MODEL].completion_tokens == 5

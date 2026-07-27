from __future__ import annotations

import json

import pytest

from volante.cost import CostMeter
from volante.providers.base import IncompleteOutputError
from volante.providers.fake import FakeProvider
from volante.supervisor import _MAX_PLAN_ATTEMPTS, Supervisor
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    InvalidGoalError,
    Task,
    TextBlock,
    Usage,
)

_PLANNER_MODEL = "anthropic/claude-sonnet-5"


class _CountingProvider(FakeProvider):
    """FakeProvider ekstensi: rekam jumlah panggilan + request aktual per
    panggilan. Dipakai untuk verifikasi retry-loop plan() (jumlah percobaan &
    isi pesan koreksi yang disisipkan di antara attempt)."""

    def __init__(self, responses: list[CanonicalResponse]) -> None:
        super().__init__(responses=responses)
        self.calls = 0
        self.requests: list[CanonicalRequest] = []

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        self.requests.append(req)
        return await super().complete(req)


def _resp(
    payload: str,
    *,
    prompt: int = 0,
    completion: int = 0,
    estimated: bool = False,
    cost_usd: float | None = None,
    stop_reason: str = "end_turn",
) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=payload)],
        usage=Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            estimated=estimated,
        ),
        model="fake",
        stop_reason=stop_reason,
        latency_ms=0,
        cost_usd=cost_usd,
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


@pytest.mark.parametrize("stop_reason", ["max_tokens", "content_filter", "unknown"])
async def test_plan_rejects_incomplete_terminal_response_after_metering(
    stop_reason: str,
) -> None:
    provider = _CountingProvider(
        [
            _resp(
                _one_task_plan(),
                prompt=12,
                completion=7,
                stop_reason=stop_reason,
            )
        ]
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    with pytest.raises(IncompleteOutputError) as exc_info:
        await sup.plan("plan me")

    assert provider.calls == 1
    assert exc_info.value.phase == "planning"
    assert exc_info.value.stop_reason == stop_reason
    assert exc_info.value.error_code == "incomplete_output"
    totals = cost_meter.totals()[_PLANNER_MODEL]
    assert totals.prompt_tokens == 12
    assert totals.completion_tokens == 7


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


async def test_plan_forwards_provider_authoritative_cost() -> None:
    provider = FakeProvider(
        responses=[_resp(_one_task_plan(), prompt=10, completion=2, cost_usd=0.123)]
    )
    meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, meter)

    await sup.plan("plan me")

    direct = meter._direct[_PLANNER_MODEL]
    assert direct["usd"] == pytest.approx(0.123)


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
    # Single response is enough: even though retry fires (validate errors are
    # retried too), re-raising the FIRST exception guarantees the cycle
    # message survives to the final raise regardless of what later attempts
    # produce (fallback echo, etc).
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError, match="cycle"):
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

    with pytest.raises(ValueError, match="duplicate"):
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

    with pytest.raises(ValueError, match="unknown task"):
        await sup.plan("a dangling-dependency goal")


async def test_plan_streams_when_on_text_given() -> None:
    # on_text -> jalur stream (teruskan teks respons planner); hasil parse tetap benar.
    provider = FakeProvider(responses=[_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())
    chunks: list[str] = []

    tasks = await sup.plan("plan me", on_text=chunks.append)

    assert [t.id for t in tasks] == ["t1"]
    assert "".join(chunks) == _one_task_plan()  # teks plan ter-stream


async def test_plan_rejects_empty_plan() -> None:
    # Regresi: plan kosong [] lolos DAG-check (Kahn resolved==0==len) dan bikin
    # aexecute lapor "success" tanpa kerja. Harus ditolak ValueError.
    # Planner mengulang [] di setiap attempt (retry tak menolong planner yang
    # keras kepala) -> ValueError "empty" tetap yang di-re-raise di attempt terakhir.
    provider = FakeProvider(responses=[_resp("[]")] * _MAX_PLAN_ATTEMPTS)
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(ValueError, match="empty"):
        await sup.plan("a goal the planner refuses to decompose")


async def test_plan_rejects_empty_plan_inside_fence() -> None:
    provider = FakeProvider(responses=[_resp("```json\n[]\n```")] * _MAX_PLAN_ATTEMPTS)
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
    # Planner mengulang depends_on non-array yang sama di setiap attempt ->
    # ValueError "depends_on" tetap yang di-re-raise di attempt terakhir.
    provider = FakeProvider(responses=[_resp(plan_json)] * _MAX_PLAN_ATTEMPTS)
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


async def test_plan_defaults_missing_difficulty_but_rejects_invalid_values() -> None:
    plan_json = json.dumps(
        [
            {"id": "a", "description": "hard one", "type": "code",
             "mode": "one_shot", "depends_on": [], "difficulty": "hard"},
            {"id": "b", "description": "no difficulty key", "type": "code",
             "mode": "one_shot", "depends_on": []},
        ]
    )
    provider = FakeProvider(responses=[_resp(plan_json)])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    tasks = await sup.plan("mixed difficulties")
    by_id = {t.id: t for t in tasks}
    assert by_id["a"].difficulty == "hard"
    assert by_id["b"].difficulty == "medium"

    for invalid in ("spicy", ["hard"]):
        bad = json.dumps(
            [
                {
                    "id": "bad",
                    "description": "invalid difficulty",
                    "type": "code",
                    "mode": "one_shot",
                    "depends_on": [],
                    "difficulty": invalid,
                }
            ]
        )
        bad_sup = Supervisor(
            FakeProvider(responses=[_resp(bad)] * _MAX_PLAN_ATTEMPTS),
            _PLANNER_MODEL,
            CostMeter(),
        )
        with pytest.raises(ValueError, match="difficulty"):
            await bad_sup.plan("reject invalid difficulty")


def test_plan_system_prompt_requests_difficulty() -> None:
    from volante.supervisor import _PLAN_SYSTEM

    assert "difficulty" in _PLAN_SYSTEM
    for label in ("trivial", "easy", "medium", "hard"):
        assert label in _PLAN_SYSTEM


def test_plan_system_prompt_restricts_agentic_to_tool_use() -> None:
    # Live failure: subscription-only setups have NO tool-capable model
    # (claude -p / codex exec are supports_tools=False), so an "agentic" task
    # always fails routing. The planner must default to one_shot and reserve
    # agentic ONLY for tasks that genuinely need a tool loop (execute code,
    # read local files, fetch a URL) -- research/analysis/writing/reasoning
    # tasks, which don't need tools, should be one_shot.
    from volante.supervisor import _PLAN_SYSTEM

    assert "one_shot" in _PLAN_SYSTEM
    assert "agentic" in _PLAN_SYSTEM
    lowered = _PLAN_SYSTEM.lower()
    assert "default" in lowered  # one_shot spelled out as the default mode
    # agentic scoped to genuine tool-use triggers: execute code / read files / fetch URLs.
    assert "execute" in lowered or "run" in lowered
    assert "file" in lowered
    assert "url" in lowered or "fetch" in lowered
    # Research/analysis/writing tasks explicitly steered to one_shot (no tools needed).
    assert "research" in lowered
    assert "writ" in lowered  # "write"/"writing"
    # Strict JSON-only contract must remain intact.
    assert "ONLY a JSON array" in _PLAN_SYSTEM
    assert "Do not include any prose or markdown outside the JSON array." in _PLAN_SYSTEM


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


async def test_plan_retries_and_succeeds_after_bad_json() -> None:
    # Live-observed finding: planner (claude -p subscription CLI agent) kadang
    # MENJAWAB goal (prosa/markdown) alih-alih JSON array, walau _PLAN_SYSTEM
    # tegas. Attempt 1 gagal parse -> plan() harus koreksi & retry, bukan
    # langsung raise.
    bad_reply = "Here is the answer: tea is a wonderful beverage with a long history..."
    provider = _CountingProvider(
        responses=[
            _resp(bad_reply, prompt=50, completion=40),
            _resp(_one_task_plan(), prompt=20, completion=8),
        ]
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    tasks = await sup.plan("Write a short essay about tea")

    assert [t.id for t in tasks] == ["t1"]
    assert provider.calls == 2

    # Kedua attempt ditagih (bill-per-attempt, bukan hanya attempt terakhir).
    totals = cost_meter.totals()
    assert totals[_PLANNER_MODEL].prompt_tokens == 70
    assert totals[_PLANNER_MODEL].completion_tokens == 48

    # Attempt ke-2 harus menyertakan balasan buruk model + koreksi tegas,
    # disisipkan DI ANTARA attempt (bukan request identik yang diulang).
    second_req = provider.requests[1]
    roles = [m.role for m in second_req.messages]
    assert roles == ["system", "user", "assistant", "user"]
    first_system = provider.requests[0].messages[0].content[0].text
    assert second_req.messages[0].content[0].text == first_system
    assert second_req.messages[2].content[0].text == bad_reply
    correction = second_req.messages[3].content[0].text
    # Koreksi harus menyertakan TEKS ERROR aktual dari _parse_plan_json (bukan
    # nudge generik) -- sinyal yang benar-benar actionable buat planner.
    assert "did not return valid JSON" in correction
    assert "JSON array" in correction


async def test_plan_retries_with_validator_feedback_after_empty_plan() -> None:
    # VALIDATE error (bukan parse): attempt 1 adalah JSON well-formed TAPI
    # array kosong -- ditolak oleh _validate, bukan _parse_plan_json. Koreksi
    # yang dikirim balik di attempt 2 HARUS menyertakan pesan VALIDATOR
    # ("empty"), bukan nudge "kirim JSON" generik yang nol-sinyal buat plan
    # yang sudah berupa JSON valid.
    provider = _CountingProvider(
        responses=[
            _resp("[]", prompt=30, completion=5),
            _resp(_one_task_plan(), prompt=20, completion=8),
        ]
    )
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    tasks = await sup.plan("a goal the planner initially refuses to decompose")

    assert [t.id for t in tasks] == ["t1"]
    assert provider.calls == 2

    totals = cost_meter.totals()
    assert totals[_PLANNER_MODEL].prompt_tokens == 50
    assert totals[_PLANNER_MODEL].completion_tokens == 13

    second_req = provider.requests[1]
    correction = second_req.messages[-1].content[0].text
    assert "empty" in correction


async def test_plan_raises_after_max_attempts() -> None:
    # Planner SELALU menjawab prosa -> plan() harus raise setelah PERSIS
    # _MAX_PLAN_ATTEMPTS panggilan, bukan retry tanpa batas.
    prose = _resp(
        "Sure! Here's a plan in plain English: first do X, then do Y.",
        prompt=10,
        completion=10,
    )
    provider = _CountingProvider(responses=[prose] * _MAX_PLAN_ATTEMPTS)
    cost_meter = CostMeter()
    sup = Supervisor(provider, _PLANNER_MODEL, cost_meter)

    with pytest.raises(ValueError, match="planner did not return valid JSON"):
        await sup.plan("a goal the planner keeps answering instead of decomposing")

    assert provider.calls == _MAX_PLAN_ATTEMPTS
    totals = cost_meter.totals()
    assert totals[_PLANNER_MODEL].prompt_tokens == 10 * _MAX_PLAN_ATTEMPTS
    assert totals[_PLANNER_MODEL].completion_tokens == 10 * _MAX_PLAN_ATTEMPTS


async def test_plan_valid_on_first_try_makes_exactly_one_call() -> None:
    # Regresi: JSON valid di attempt pertama -> TEPAT satu panggilan provider,
    # tanpa retry (path bahagia tak berubah).
    provider = _CountingProvider(responses=[_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    tasks = await sup.plan("plan me")

    assert [t.id for t in tasks] == ["t1"]
    assert provider.calls == 1


async def test_planner_request_uses_selected_model_limits() -> None:
    provider = _CountingProvider([_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())
    sup.set_model_limits(context_window=4_096, max_output_tokens=512)

    await sup.plan("plan me")

    request = provider.requests[0]
    assert request.context_window == 4_096
    assert request.max_tokens == 512


async def test_planner_rejects_goal_that_cannot_fit_without_calling_provider() -> None:
    provider = _CountingProvider([_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())
    sup.set_model_limits(context_window=512, max_output_tokens=128)

    with pytest.raises(InvalidGoalError, match="does not fit"):
        await sup.plan("x" * 5_000)

    assert provider.calls == 0


@pytest.mark.parametrize("goal", ["", "   "])
async def test_planner_rejects_empty_goal_without_provider_call(goal: str) -> None:
    provider = _CountingProvider([_resp(_one_task_plan())])
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())

    with pytest.raises(InvalidGoalError):
        await sup.plan(goal)

    assert provider.calls == 0


@pytest.mark.parametrize(
    "task_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "/tmp/escape",
        "nested/task",
        "has space",
        "x" * 65,
    ],
)
async def test_plan_rejects_unsafe_task_ids(task_id: str) -> None:
    payload = json.dumps(
        [
            {
                "id": task_id,
                "description": "unsafe",
                "type": "code",
                "mode": "one_shot",
                "difficulty": "medium",
                "depends_on": [],
            }
        ]
    )
    sup = Supervisor(
        FakeProvider(responses=[_resp(payload)] * _MAX_PLAN_ATTEMPTS),
        _PLANNER_MODEL,
        CostMeter(),
    )

    with pytest.raises(ValueError, match="task id|unsafe"):
        await sup.plan("reject unsafe id")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "browse"),
        ("mode", "magic"),
        ("difficulty", "extreme"),
    ],
)
async def test_plan_rejects_unknown_task_discriminators(
    field: str, value: str
) -> None:
    item = {
        "id": "t1",
        "description": "invalid discriminator",
        "type": "code",
        "mode": "one_shot",
        "difficulty": "medium",
        "depends_on": [],
    }
    item[field] = value
    payload = json.dumps([item])
    sup = Supervisor(
        FakeProvider(responses=[_resp(payload)] * _MAX_PLAN_ATTEMPTS),
        _PLANNER_MODEL,
        CostMeter(),
    )

    with pytest.raises(ValueError, match=f"invalid {field}"):
        await sup.plan("reject unknown discriminator")


@pytest.mark.parametrize(
    ("mode", "required_tools", "message"),
    [
        ("one_shot", ["run_python"], "one_shot"),
        ("agentic", ["shell"], "unsupported required tool"),
    ],
)
async def test_plan_rejects_invalid_required_tool_contract(
    mode: str,
    required_tools: list[str],
    message: str,
) -> None:
    payload = json.dumps(
        [
            {
                "id": "t1",
                "description": "tool task",
                "type": "code",
                "mode": mode,
                "difficulty": "medium",
                "depends_on": [],
                "required_tools": required_tools,
            }
        ]
    )
    sup = Supervisor(
        FakeProvider(responses=[_resp(payload)] * _MAX_PLAN_ATTEMPTS),
        _PLANNER_MODEL,
        CostMeter(),
    )

    with pytest.raises(ValueError, match=message):
        await sup.plan("reject invalid tool contract")


async def test_plan_rejects_known_tool_that_is_disabled_for_this_run() -> None:
    payload = json.dumps(
        [
            {
                "id": "t1",
                "description": "fetch a URL",
                "type": "research",
                "mode": "agentic",
                "difficulty": "medium",
                "depends_on": [],
                "required_tools": ["fetch_url"],
            }
        ]
    )
    sup = Supervisor(
        FakeProvider(responses=[_resp(payload)] * _MAX_PLAN_ATTEMPTS),
        _PLANNER_MODEL,
        CostMeter(),
    )
    sup.set_available_tools({"run_python"})

    with pytest.raises(ValueError, match="unavailable.*fetch_url"):
        await sup.plan("reject disabled tool")


async def test_call_gate_runs_for_every_physical_planner_attempt() -> None:
    provider = _CountingProvider(
        responses=[
            _resp("not json"),
            _resp(_one_task_plan()),
        ]
    )
    calls: list[str] = []
    sup = Supervisor(provider, _PLANNER_MODEL, CostMeter())
    sup.set_call_gate(calls.append)

    await sup.plan("retry once")

    assert calls == [_PLANNER_MODEL, _PLANNER_MODEL]
    assert provider.calls == 2

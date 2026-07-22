from __future__ import annotations

from baton.blackboard import Blackboard
from baton.types import Entry, Task, Usage


def mk_task(task_id: str) -> Task:
    return Task(id=task_id, description="d", type="code", mode="one_shot")


def mk_entry(
    *,
    task_id: str,
    kind: str,
    payload: object,
    attempt: int = 0,
    model_id: str | None = None,
    usage: Usage | None = None,
    run_id: str = "run-1",
    timestamp: float = 0.0,
) -> Entry:
    return Entry(
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        kind=kind,
        payload=payload,
        model_id=model_id,
        usage=usage,
        timestamp=timestamp,
    )


def test_new_blackboard_has_no_entries() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1")])
    assert bb.entries() == []


def test_append_preserves_order_and_provenance() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1"), mk_task("T2")])
    e1 = mk_entry(task_id="T1", kind="status", payload="running", timestamp=1.0)
    e2 = mk_entry(
        task_id="T1",
        kind="artifact",
        payload="draft",
        model_id="anthropic/claude-opus-4-8",
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        timestamp=2.0,
    )
    bb.append(e1)
    bb.append(e2)

    got = bb.entries()
    assert got == [e1, e2]
    assert got[1].model_id == "anthropic/claude-opus-4-8"
    assert got[1].usage == Usage(prompt_tokens=10, completion_tokens=5)
    assert got[1].attempt == 0
    assert got[1].run_id == "run-1"
    assert got[1].timestamp == 2.0


def test_entries_returns_copy_so_internal_log_is_not_mutated() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1")])
    e1 = mk_entry(task_id="T1", kind="fact", payload="f1")
    bb.append(e1)

    snapshot = bb.entries()
    snapshot.clear()
    snapshot.append("garbage")

    assert bb.entries() == [e1]


def test_current_artifacts_returns_latest_artifact_per_task() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1"), mk_task("T2")])
    bb.append(mk_entry(task_id="T1", kind="artifact", payload="v1", timestamp=1.0))
    bb.append(mk_entry(task_id="T2", kind="artifact", payload="w1", timestamp=2.0))
    bb.append(mk_entry(task_id="T1", kind="artifact", payload="v2", timestamp=3.0))

    assert bb.current_artifacts() == {"T1": "v2", "T2": "w1"}


def test_current_artifacts_ignores_non_artifact_entries() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1")])
    bb.append(mk_entry(task_id="T1", kind="status", payload="running"))
    bb.append(mk_entry(task_id="T1", kind="fact", payload="a fact"))

    assert bb.current_artifacts() == {}


def test_facts_returns_all_fact_payloads_in_order() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1"), mk_task("T2")])
    bb.append(mk_entry(task_id="T1", kind="fact", payload="f1"))
    bb.append(mk_entry(task_id="T1", kind="artifact", payload="art"))
    bb.append(mk_entry(task_id="T2", kind="fact", payload="f2"))

    assert bb.facts() == ["f1", "f2"]


def test_status_of_defaults_to_pending() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1")])
    assert bb.status_of("T1") == "pending"
    assert bb.status_of("unknown-task") == "pending"


def test_status_of_returns_latest_status_entry_per_task() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1"), mk_task("T2")])
    bb.append(mk_entry(task_id="T1", kind="status", payload="running", timestamp=1.0))
    bb.append(mk_entry(task_id="T2", kind="status", payload="running", timestamp=2.0))
    bb.append(mk_entry(task_id="T1", kind="status", payload="done", timestamp=3.0))

    assert bb.status_of("T1") == "done"
    assert bb.status_of("T2") == "running"


def test_status_of_ignores_non_status_entries() -> None:
    bb = Blackboard(goal="g", plan=[mk_task("T1")])
    bb.append(mk_entry(task_id="T1", kind="artifact", payload="art"))
    bb.append(mk_entry(task_id="T1", kind="fact", payload="a fact"))

    assert bb.status_of("T1") == "pending"

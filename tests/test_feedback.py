from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from volante.feedback import (
    FeedbackError,
    _attribution,
    feedback_log_path,
    load_ledger,
    measurements_from_observations,
    merge_measurements,
)
from volante.verify import CheckReport


def _run(
    *,
    checks: CheckReport,
    status: str = "success",
    decisions: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        checks=checks,
        routing_decisions=(
            decisions
            if decisions is not None
            else {"__direct__": {"executed_model_id": "api/model"}}
        ),
    )


def _passed(total: int = 5) -> CheckReport:
    return CheckReport(total=total, failed=[])


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (SimpleNamespace(status="success", checks=None, routing_decisions={}), None),
        (
            _run(checks=CheckReport(total=0, failed=[])),
            None,
        ),  # checks exist but did not run
        (
            _run(checks=_passed(), status="failed"),
            None,
        ),
        (
            _run(
                checks=_passed(),
                decisions={"__direct__": {"executed_model_id": "a/x"}},
            ),
            ("a/x", "code"),
        ),
        (
            _run(
                checks=_passed(),
                decisions={
                    "__direct__": {"executed_model_id": "a/x"},
                    "other": {"executed_model_id": "b/y"},
                },
            ),
            None,  # more than one decision: subject is ambiguous
        ),
        (
            _run(
                checks=_passed(),
                decisions={"planned": {"executed_model_id": "a/x"}},
            ),
            None,  # planner-labeled run: task type would be a guess
        ),
        (
            _run(
                checks=_passed(),
                decisions={"__direct__": {"executed_model_id": None}},
            ),
            None,
        ),
    ],
)
def test_attribution_is_single_model_direct_only(run, expected) -> None:
    assert _attribution(run) == expected


def test_record_observation_appends_one_jsonl_line(tmp_path: Path) -> None:
    ledger = tmp_path / "feedback.jsonl"
    run = _run(checks=_passed())
    observation = _attribution_observation(ledger, run, goal="Fix src/a.py")

    assert observation is not None
    assert observation["model_id"] == "api/model"
    assert observation["task_type"] == "code"
    assert observation["all_passed"] is True
    line = json.loads(ledger.read_text())
    assert line["goal"] == "Fix src/a.py"
    assert line["total"] == 5
    assert line["failed"] == 0


def test_record_observation_skips_what_cannot_be_attributed(tmp_path: Path) -> None:
    from volante.feedback import record_observation

    ledger = tmp_path / "feedback.jsonl"
    run = _run(checks=_passed(), status="failed")

    assert record_observation(ledger, result=run, goal="g") is None
    assert not ledger.exists()


def test_record_observation_never_raises(tmp_path: Path) -> None:
    from volante.feedback import record_observation

    sink = tmp_path / "sink.jsonl"
    sink.mkdir()  # a directory where a file belongs: every write fails

    # Best-effort telemetry: any filesystem problem returns None, no exception.
    assert record_observation(sink, result=_run(checks=_passed()), goal="g") is None


def test_feedback_log_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOLANTE_FEEDBACK_FILE", raising=False)
    assert feedback_log_path() == Path("~/.volante/feedback.jsonl").expanduser()

    monkeypatch.setenv("VOLANTE_FEEDBACK_FILE", "")
    assert feedback_log_path() is None

    monkeypatch.setenv("VOLANTE_FEEDBACK_FILE", "/tmp/x.jsonl")
    assert feedback_log_path() == Path("/tmp/x.jsonl")


def test_measurements_grade_only_clean_runs() -> None:
    observations = [
        _pastable("api/model", "check the slug", all_passed=True, total=5),
        _pastable("api/model", "check the slug", all_passed=True, total=5),
        _pastable("api/model", "fail one", all_passed=False, total=4, failed=1),
        _pastable("api/model", "error run", all_passed=False, error="boom"),
        _pastable("api/model", "nothing derived", all_passed=False, total=0),
        _pastable("api/model", "too thin", all_passed=False, total=2),
        _pastable("api/model", "unknown kind", all_passed=True, task_type="prose"),
        _pastable("api/model", "", all_passed=True, total=5),
        {"model_id": 7, "task_type": "code", "goal": "g", "all_passed": True},
    ]

    measurements, discarded = measurements_from_observations(observations)

    assert measurements == {"api/model": {"code": [{"goal": "check the slug", "score": 1.0}]}}
    assert discarded == {
        "duplicate_goal": 1,
        "checks_failed": 1,
        "checks_error": 1,
        "no_checks_derived": 1,
        "too_few_checks": 1,
        "unknown_task_type": 1,
        "blank_goal": 1,
        "unknown_model_id": 1,
    }


def _pastable(
    model_id: str,
    goal: str,
    *,
    all_passed: bool,
    task_type: str = "code",
    total: int = 5,
    failed: int = 0,
    error: str | None = None,
) -> dict:
    return {
        "model_id": model_id,
        "task_type": task_type,
        "goal": goal,
        "all_passed": all_passed,
        "total": total,
        "failed": failed,
        "error": error,
    }


def test_merge_dedupes_goals_across_base_and_ledgers() -> None:
    base = {
        "api/model": {
            "code": [{"goal": "seen before", "score": 0.5}, 0.7],
        }
    }
    extra = {
        "api/model": {
            "code": [
                {"goal": "seen before", "score": 1.0},
                {"goal": "brand new", "score": 1.0},
            ]
        },
        "b/model": {"analyze": [{"goal": "first of b", "score": 1.0}]},
    }

    merged, added = merge_measurements(base, extra)

    assert added == 2
    assert merged == {
        "api/model": {
            "code": [
                {"goal": "seen before", "score": 0.5},
                0.7,
                {"goal": "brand new", "score": 1.0},
            ],
        },
        "b/model": {"analyze": [{"goal": "first of b", "score": 1.0}]},
    }


def test_merge_dedupes_across_two_ledgers() -> None:
    base: dict = {}
    first, _ = merge_measurements(
        base,
        {"m": {"code": [{"goal": "g", "score": 1.0}]}},
    )
    second, added = merge_measurements(
        first,
        {"m": {"code": [{"goal": "g", "score": 1.0}]}},
    )

    assert added == 0
    assert second == {"m": {"code": [{"goal": "g", "score": 1.0}]}}


def test_load_ledger_skips_and_counts_bad_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "feedback.jsonl"
    ledger.write_text('{"model_id": "m", "all_passed": true}\nthis is not json\n[1, 2, 3]\n\n')

    observations, skipped = load_ledger(ledger)

    assert len(observations) == 1
    assert skipped == 2


def test_load_ledger_missing_path_is_feedback_error() -> None:
    with pytest.raises(FeedbackError, match="not a readable"):
        load_ledger("/nonexistent/ledger.jsonl")


def _attribution_observation(ledger: Path, run, *, goal: str) -> dict | None:
    from volante.feedback import record_observation

    return record_observation(ledger, result=run, goal=goal)


def test_recency_cutoff_discards_only_observations_it_can_date() -> None:
    from datetime import UTC, datetime, timedelta

    from volante.feedback import measurements_from_observations

    now = datetime.now(UTC)
    recent = _pastable("api/model", "recent", all_passed=True, total=5)
    recent["ts"] = (now - timedelta(days=10)).isoformat()
    stale = _pastable("api/model", "stale", all_passed=True, total=5)
    stale["ts"] = (now - timedelta(days=100)).isoformat()
    undated = _pastable("api/model", "undated", all_passed=True, total=5)
    undated["ts"] = "not a timestamp"

    measurements, discarded = measurements_from_observations(
        [recent, stale, undated], max_age_days=30, now=now
    )

    goals = sorted(e["goal"] for e in measurements["api/model"]["code"])
    assert goals == ["recent", "undated"]
    assert discarded == {"too_old": 1}


def test_recency_cutoff_disabled_keeps_everything() -> None:
    from datetime import UTC, datetime, timedelta

    from volante.feedback import measurements_from_observations

    now = datetime.now(UTC)
    old = _pastable("api/model", "old", all_passed=True, total=5)
    old["ts"] = (now - timedelta(days=500)).isoformat()

    measurements, discarded = measurements_from_observations([old], max_age_days=0, now=now)

    assert discarded == {}
    assert [e["goal"] for e in measurements["api/model"]["code"]] == ["old"]


def test_feedback_max_age_days_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from volante.feedback import (
        FeedbackError,
        feedback_max_age_days,
    )

    monkeypatch.delenv("VOLANTE_FEEDBACK_MAX_AGE_DAYS", raising=False)
    assert feedback_max_age_days() == 90

    monkeypatch.setenv("VOLANTE_FEEDBACK_MAX_AGE_DAYS", "30")
    assert feedback_max_age_days() == 30

    monkeypatch.setenv("VOLANTE_FEEDBACK_MAX_AGE_DAYS", "0")
    assert feedback_max_age_days() == 0

    monkeypatch.setenv("VOLANTE_FEEDBACK_MAX_AGE_DAYS", "soon")
    with pytest.raises(FeedbackError, match="non-negative integer"):
        feedback_max_age_days()

    monkeypatch.setenv("VOLANTE_FEEDBACK_MAX_AGE_DAYS", "-3")
    with pytest.raises(FeedbackError, match="non-negative integer"):
        feedback_max_age_days()

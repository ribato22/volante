from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from volante import observability as obs


def test_configure_logging_parses_level_names_and_numbers() -> None:
    root = logging.getLogger()
    original = root.level
    try:
        assert obs.configure_logging({"VOLANTE_LOG": "debug"}) == logging.DEBUG
        assert obs.configure_logging({"VOLANTE_LOG": "WARNING"}) == logging.WARNING
        assert obs.configure_logging({"VOLANTE_LOG": "30"}) == 30
    finally:
        root.setLevel(original)


def test_configure_logging_is_a_noop_when_blank_or_unknown() -> None:
    root = logging.getLogger()
    original = root.level
    try:
        assert obs.configure_logging({}) is None
        assert obs.configure_logging({"VOLANTE_LOG": "  "}) is None
        assert obs.configure_logging({"VOLANTE_LOG": "not-a-level"}) is None
    finally:
        root.setLevel(original)


def test_usage_log_path_default_override_and_disabled() -> None:
    assert obs.usage_log_path({}) == Path("~/.volante/usage.jsonl").expanduser()
    assert obs.usage_log_path({"VOLANTE_USAGE_LOG": "/tmp/x/usage.jsonl"}) == Path(
        "/tmp/x/usage.jsonl"
    )
    assert obs.usage_log_path({"VOLANTE_USAGE_LOG": ""}) is None
    assert obs.usage_log_path({"VOLANTE_USAGE_LOG": "   "}) is None


def test_build_record_from_dict_extracts_models_and_truncates_goal() -> None:
    result = {
        "status": "success",
        "billed_usd": 0.5,
        "credit_usd": 0.25,
        "duration_ms": 1200,
        "subscription_calls": 2,
        "failed_task": None,
        "error_code": None,
        "routing_decisions": {
            "t1": {"selected_model_id": "a", "executed_model_id": "b"},
            "t2": {"selected_model_id": "c"},
        },
    }
    stamp = datetime(2026, 7, 27, 10, 0, 0, tzinfo=UTC)
    rec = obs.build_record(
        result, goal="x" * 500, prefer="cheap", source="mcp", now=stamp
    )
    assert rec["ts"] == "2026-07-27T10:00:00Z"
    assert rec["source"] == "mcp"
    assert rec["prefer"] == "cheap"
    assert rec["status"] == "success"
    assert rec["billed_usd"] == 0.5
    assert rec["subscription_calls"] == 2
    assert rec["models"] == ["b", "c"]  # executed preferred over selected, de-duped
    assert rec["goal_truncated"] is True
    assert len(rec["goal"]) == 240


def test_build_record_from_object_defaults_prefer_to_quality() -> None:
    class _Res:
        status = "failed"
        billed_usd = 0.0
        credit_usd = 0.0
        duration_ms = 0
        subscription_calls = 0
        failed_task = "t1"
        error_code = "provider_unavailable"
        routing_decisions: dict = {}

    rec = obs.build_record(_Res(), goal="short", prefer=None, source="cli")
    assert rec["prefer"] == "quality"
    assert rec["failed_task"] == "t1"
    assert rec["error_code"] == "provider_unavailable"
    assert rec["goal_truncated"] is False
    assert rec["models"] == []


def test_record_run_roundtrips_and_reads_newest_first(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    env = {"VOLANTE_USAGE_LOG": str(log)}
    for i in range(3):
        stamp = datetime(2026, 7, 27, 10, i, 0, tzinfo=UTC)
        assert obs.record_run(
            {"status": "success", "duration_ms": i},
            goal=f"goal-{i}",
            prefer="quality",
            source="cli",
            env=env,
            now=stamp,
        )
    runs = obs.read_runs(env, limit=10)
    assert [r["goal"] for r in runs] == ["goal-2", "goal-1", "goal-0"]  # newest first
    assert obs.read_runs(env, limit=1) == runs[:1]


def test_record_run_disabled_returns_false(tmp_path: Path) -> None:
    assert obs.record_run({"status": "success"}, goal="g", prefer=None, source="cli",
                          env={"VOLANTE_USAGE_LOG": ""}) is False


def test_record_run_is_best_effort_on_bad_path(tmp_path: Path) -> None:
    # Point the log at a path whose parent is a FILE, so mkdir/open fail; must not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    env = {"VOLANTE_USAGE_LOG": str(blocker / "nested" / "usage.jsonl")}
    assert obs.record_run({"status": "success"}, goal="g", prefer=None,
                          source="cli", env=env) is False


def test_read_runs_skips_malformed_lines_and_missing_file(tmp_path: Path) -> None:
    assert obs.read_runs({"VOLANTE_USAGE_LOG": str(tmp_path / "nope.jsonl")}) == []
    log = tmp_path / "usage.jsonl"
    log.write_text(
        json.dumps({"status": "success", "goal": "ok"}) + "\n"
        + "{ this is not json\n"
        + "\n"
        + json.dumps(["not", "an", "object"]) + "\n"
        + json.dumps({"status": "failed", "goal": "also-ok"}) + "\n"
    )
    runs = obs.read_runs({"VOLANTE_USAGE_LOG": str(log)})
    assert [r["goal"] for r in runs] == ["also-ok", "ok"]


def test_summarize_aggregates() -> None:
    runs = [
        {"status": "success", "billed_usd": 0.10, "credit_usd": 0.0,
         "subscription_calls": 0, "duration_ms": 1000},
        {"status": "failed", "billed_usd": 0.00, "credit_usd": 0.20,
         "subscription_calls": 3, "duration_ms": 3000},
    ]
    s = obs.summarize(runs)
    assert s["total_runs"] == 2
    assert s["successes"] == 1
    assert s["success_rate"] == 0.5
    assert s["total_billed_usd"] == 0.10
    assert s["total_credit_usd"] == 0.20
    assert s["total_subscription_calls"] == 3
    assert s["avg_duration_ms"] == 2000
    assert obs.summarize([])["total_runs"] == 0


def test_summarize_quantifies_the_estimated_share() -> None:
    # A total must carry the same estimate signal the per-run rows do, quantified so a
    # mostly-authoritative total is not written off as inferred.
    runs = [
        {"status": "success", "billed_usd": 4.0, "credit_usd": 1.0,
         "subscription_calls": 0, "duration_ms": 1000, "cost_estimated": True},
        {"status": "success", "billed_usd": 0.4, "credit_usd": 0.1,
         "subscription_calls": 0, "duration_ms": 1000, "cost_estimated": False},
    ]
    s = obs.summarize(runs)
    assert s["total_billed_usd"] == 4.4
    assert s["estimated_runs"] == 1
    assert s["estimated_billed_usd"] == 4.0
    assert s["estimated_credit_usd"] == 1.0


def test_summarize_reports_no_estimated_share_when_all_authoritative() -> None:
    runs = [
        {"status": "success", "billed_usd": 0.4, "credit_usd": 0.1,
         "subscription_calls": 0, "duration_ms": 1000, "cost_estimated": False},
    ]
    s = obs.summarize(runs)
    assert s["estimated_runs"] == 0
    assert s["estimated_billed_usd"] == 0.0


def test_build_record_persists_the_estimate_flag() -> None:
    class _Result:
        status = "success"
        billed_usd = 0.5
        credit_usd = 0.0
        duration_ms = 10
        subscription_calls = 0
        failed_task = None
        error_code = None
        routing_decisions: dict = {}
        cost_estimated = True

    record = obs.build_record(_Result(), goal="g", prefer="quality", source="cli")
    assert record["cost_estimated"] is True

    _Result.cost_estimated = False
    assert obs.build_record(
        _Result(), goal="g", prefer="quality", source="cli"
    )["cost_estimated"] is False

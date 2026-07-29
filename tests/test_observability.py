from __future__ import annotations

import json
import logging
import os
import stat
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


# --- the ledger holds goal text, so it is private storage -------------------- #
# Every completed CLI, MCP and Web UI run appends up to 240 characters of the goal
# plus the models and the spend. Written with process-default permissions under a
# common umask of 022 that is a 0644 file in a 0755 directory: readable by any
# other local account that can traverse the home directory.


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_ledger_and_its_directory_are_owner_only(tmp_path: Path) -> None:
    log = tmp_path / "dot-volante" / "usage.jsonl"
    previous = os.umask(0o022)  # the permissive-but-ordinary case
    try:
        assert obs.record_run(
            {"status": "success"}, goal="acquire the Q3 board deck figures",
            prefer=None, source="cli", env={"VOLANTE_USAGE_LOG": str(log)},
        )
    finally:
        os.umask(previous)

    assert _mode(log) == 0o600
    assert _mode(log.parent) == 0o700


def test_an_existing_default_ledger_is_tightened(tmp_path: Path, monkeypatch) -> None:
    # Fixing only creation leaves every ledger written before this release exactly as
    # exposed as it was. At the default path Volante chose the location, so it also
    # owns the convention.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VOLANTE_USAGE_LOG", raising=False)
    log = tmp_path / ".volante" / "usage.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text('{"goal":"written by an older version"}\n')
    log.chmod(0o644)
    log.parent.chmod(0o755)

    assert obs.record_run({"status": "success"}, goal="g", prefer=None, source="cli")

    assert _mode(log) == 0o600
    assert _mode(log.parent) == 0o700
    # Tightening is not truncating: the history already in the file is still there.
    goals = [r.get("goal") for r in obs.read_runs(path=log, limit=10)]
    assert goals == ["g", "written by an older version"]


def test_a_user_configured_ledger_keeps_the_permissions_the_user_chose(
    tmp_path: Path,
) -> None:
    # VOLANTE_USAGE_LOG is the user saying where this goes. Re-tightening a path they
    # picked would fight them on every run, and there is a real reason to point it at
    # a group-readable location.
    log = tmp_path / "shared" / "usage.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("")
    log.chmod(0o644)

    assert obs.record_run({"status": "success"}, goal="g", prefer=None, source="cli",
                          env={"VOLANTE_USAGE_LOG": str(log)})

    assert _mode(log) == 0o644


def test_tightening_failure_does_not_break_recording(tmp_path: Path, monkeypatch) -> None:
    # record_run's contract: telemetry never fails the run that produced it.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VOLANTE_USAGE_LOG", raising=False)

    def _refuse(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(obs.os, "chmod", _refuse)

    assert obs.record_run({"status": "success"}, goal="g", prefer=None, source="cli")
    assert len(obs.read_runs(path=tmp_path / ".volante" / "usage.jsonl", limit=10)) == 1


# --- a limit that does not limit anything is not a limit ----------------------
# read_runs(limit=N) called handle.readlines() first, materializing every line in an
# append-only file that grows for the life of the install, and only then counted to
# N. Measured: 300,000 runs / 93 MiB on disk cost 110 MiB of peak memory to return
# five records — and the Web UI does this synchronously on the event loop.


def _ledger(path: Path, count: int, *, goal: str = "g") -> Path:
    line = json.dumps({"ts": "2026-07-29T00:00:00Z", "source": "cli", "goal": goal})
    with path.open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(line.replace('"g"', f'"{goal}-{index}"') + "\n")
    return path


def test_reading_a_few_records_does_not_read_the_whole_ledger(tmp_path: Path) -> None:
    import tracemalloc

    log = _ledger(tmp_path / "usage.jsonl", 40_000)
    size = log.stat().st_size
    assert size > 2_000_000, "the ledger has to be big enough for the point to hold"

    tracemalloc.start()
    runs = obs.read_runs(path=log, limit=5)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(runs) == 5
    assert peak < size // 4, f"peak {peak:,} against a {size:,}-byte file"


def test_a_ledger_with_no_line_breaks_is_abandoned_not_swallowed(tmp_path: Path) -> None:
    # Reading backwards holds the leading fragment of each block until a newline
    # completes it. With no newline anywhere — a corrupted ledger, a truncated one,
    # or simply a file VOLANTE_USAGE_LOG was pointed at by mistake — that fragment
    # IS the file, so the walk quietly reassembles the whole thing in memory and the
    # bound this reader exists for stops binding. Every real record is well under
    # PIPE_BUF, so a line past the cap is not one of ours.
    import tracemalloc

    peaks = []
    for size in (4_000_000, 16_000_000):
        log = tmp_path / f"usage-{size}.jsonl"
        log.write_bytes(b"x" * size)

        tracemalloc.start()
        runs = obs.read_runs(path=log, limit=5)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert runs == []
        peaks.append(peak)

    # The property is that the cost tracks the CAP, not the file: quadrupling the
    # file must not move the peak. Asserting a fraction of the file size would pass
    # for a reader that still scaled with it, just more slowly.
    assert all(p < 4 * obs._MAX_LINE_BYTES for p in peaks), f"peaks {peaks}"
    assert peaks[1] < peaks[0] * 2, f"cost grew with the file: {peaks}"


def test_the_newest_records_still_come_back_first(tmp_path: Path) -> None:
    log = _ledger(tmp_path / "usage.jsonl", 2_000)

    runs = obs.read_runs(path=log, limit=3)

    assert [r["goal"] for r in runs] == ["g-1999", "g-1998", "g-1997"]


def test_a_record_spanning_a_read_boundary_is_not_split(tmp_path, monkeypatch) -> None:
    # Reading backwards in blocks means a record can straddle two of them. Getting
    # this wrong loses exactly the records at each boundary, quietly.
    monkeypatch.setattr(obs, "_READ_CHUNK", 64)
    log = tmp_path / "usage.jsonl"
    with log.open("w", encoding="utf-8") as handle:
        for index in range(20):
            handle.write(json.dumps({"goal": f"g-{index}", "pad": "x" * 200}) + "\n")

    runs = obs.read_runs(path=log, limit=20)

    assert [r["goal"] for r in runs] == [f"g-{i}" for i in reversed(range(20))]


def test_the_very_first_line_of_a_file_is_still_returned(tmp_path, monkeypatch) -> None:
    # Walking backwards, the earliest line has no newline before it. Treating it like
    # any other partial fragment would drop the oldest record in every ledger.
    monkeypatch.setattr(obs, "_READ_CHUNK", 16)
    log = tmp_path / "usage.jsonl"
    log.write_text(json.dumps({"goal": "the-first-run-ever"}) + "\n", encoding="utf-8")

    assert [r["goal"] for r in obs.read_runs(path=log, limit=10)] == [
        "the-first-run-ever"
    ]


def test_a_torn_final_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    # A concurrent writer mid-append leaves a partial tail; reading backwards meets it
    # first.
    log = tmp_path / "usage.jsonl"
    log.write_text(
        json.dumps({"goal": "complete"}) + "\n" + '{"goal": "tor',
        encoding="utf-8",
    )

    assert [r["goal"] for r in obs.read_runs(path=log, limit=10)] == ["complete"]


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

from __future__ import annotations

import json
from pathlib import Path

import pytest

import volante.cli as cli
from volante.calibrate import CalibrationError, build_profiles, calibrate_file
from volante.inventory import load_quality_profiles_file


def test_profiles_average_scores_and_scale_confidence_with_evidence() -> None:
    profiles = build_profiles(
        {"a/model": {"code": [1.0, 0.8, 0.6], "analyze": [0.5]}}, source="team-eval"
    )
    profile = profiles["a/model"]

    assert profile["task_scores"] == {"code": 0.8, "analyze": 0.5}
    # Macro-average across task types, so an unbalanced sample cannot drive it.
    assert profile["overall_score"] == pytest.approx(0.65)
    assert profile["source"] == "team-eval"
    # Confidence follows the WEAKEST-sampled task type (analyze, n=1 -> 1/4), because the
    # router applies one confidence to every task type.
    assert profile["confidence"] == pytest.approx(0.25, abs=1e-4)
    assert profile["confidence"] < 1.0
    # No failures recorded -> no invented reliability evidence.
    assert "reliability_score" not in profile


def test_more_observations_mean_more_confidence() -> None:
    thin = build_profiles({"m": {"code": [1.0]}}, source="s")["m"]["confidence"]
    thick = build_profiles({"m": {"code": [1.0] * 20}}, source="s")["m"]["confidence"]

    assert thin < thick <= 0.9


def test_reliability_comes_from_recorded_failures_not_from_low_scores() -> None:
    # A low score is evidence about QUALITY; only a run that produced nothing usable
    # (null) is evidence about RELIABILITY. Otherwise the weight-4 reliability component
    # would silently re-count the task score.
    graded_only = build_profiles({"m": {"code": [1.0, 0.0, 0.5, 0.0]}}, source="s")["m"]
    assert "reliability_score" not in graded_only

    with_failures = build_profiles({"m": {"code": [1.0, None, 1.0, None]}}, source="s")["m"]
    assert with_failures["reliability_score"] == 0.5
    assert with_failures["task_scores"]["code"] == 1.0  # nulls are not graded as zeros


def test_unrelated_task_runs_do_not_inflate_confidence() -> None:
    # The P0 this guards: one `write` observation must not be trusted because 30
    # unrelated `code` runs exist.
    thin = build_profiles({"m": {"write": [0.9]}}, source="s")["m"]["confidence"]
    padded = build_profiles(
        {"m": {"write": [0.9], "code": [1.0] * 30}}, source="s"
    )["m"]["confidence"]
    assert padded == thin


def test_all_null_runs_are_rejected_rather_than_scored() -> None:
    with pytest.raises(CalibrationError, match="no graded runs"):
        build_profiles({"m": {"code": [None, None]}}, source="s")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"m": {}},
        {"m": {"translate": [1.0]}},  # unknown task type
        {"m": {"code": []}},
        {"m": {"code": [1.5]}},  # outside 0..1
        {"m": {"code": ["good"]}},
        {"m": {"code": [True]}},  # bool is not a score
    ],
)
def test_unusable_measurements_are_rejected(bad: dict) -> None:
    with pytest.raises(CalibrationError):
        build_profiles(bad, source="s")


def test_written_profiles_are_accepted_by_the_strict_loader(tmp_path: Path) -> None:
    # The whole point: the output must be directly usable as router evidence.
    measurements = tmp_path / "measurements.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0, 0.9]}}))
    out = tmp_path / "profiles.json"

    calibrate_file(measurements, out, source="team-eval")
    loaded = load_quality_profiles_file(out)

    assert loaded["a/model"].task_scores["code"] == pytest.approx(0.95)
    assert loaded["a/model"].source == "team-eval"
    assert 0.0 < loaded["a/model"].confidence < 1.0


def test_cli_calibrate_writes_profiles_and_reports_where(tmp_path: Path, capsys) -> None:
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0]}}))
    out = tmp_path / "profiles.json"

    assert cli.main(["--calibrate", str(measurements), "--calibrate-out", str(out)]) == 0

    printed = capsys.readouterr().out
    assert "Wrote 1 quality profile(s)" in printed
    assert "VOLANTE_QUALITY_PROFILES_FILE" in printed  # tells the user how to activate it
    assert load_quality_profiles_file(out)["a/model"].task_scores["code"] == 1.0


def test_cli_calibrate_reports_a_bad_file_without_a_traceback(
    tmp_path: Path, capsys
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")

    assert cli.main(["--calibrate", str(bad), "--calibrate-out", str(tmp_path / "o.json")]) == 2
    assert "calibration failed" in capsys.readouterr().err


def test_confidence_is_capped_below_certainty() -> None:
    # Pins the cap: no amount of measurement makes Volante claim certainty.
    assert build_profiles({"m": {"code": [1.0] * 500}}, source="s")["m"]["confidence"] == 0.9


def test_calibrate_creates_missing_parent_directories(tmp_path: Path) -> None:
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0]}}))
    out = tmp_path / "nested" / "deeper" / "profiles.json"

    calibrate_file(measurements, out, source="s")

    assert out.exists()


def test_calibrate_source_label_is_recorded(tmp_path: Path, capsys) -> None:
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0]}}))
    out = tmp_path / "p.json"

    assert (
        cli.main(
            [
                "--calibrate", str(measurements),
                "--calibrate-out", str(out),
                "--calibrate-source", "ci-run-42",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert load_quality_profiles_file(out)["a/model"].source == "ci-run-42"


@pytest.mark.parametrize("label", ["", "   ", "x" * 201])
def test_unusable_source_labels_are_rejected(tmp_path: Path, label: str) -> None:
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0]}}))

    with pytest.raises(CalibrationError):
        calibrate_file(measurements, tmp_path / "p.json", source=label)


def test_calibrate_json_mode_emits_one_parseable_line(tmp_path: Path, capsys) -> None:
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"a/model": {"code": [1.0]}}))
    out = tmp_path / "p.json"

    assert cli.main(["--calibrate", str(measurements), "--calibrate-out", str(out), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["out"] == str(out)
    assert "a/model" in payload["profiles"]


def test_non_utf8_measurements_fail_cleanly(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bin.json"
    bad.write_bytes(b'\xff\xfe{"a/model": {"code": [1.0]}}')

    assert cli.main(["--calibrate", str(bad), "--calibrate-out", str(tmp_path / "o.json")]) == 2
    assert "calibration failed" in capsys.readouterr().err


def test_a_model_id_the_loader_would_reject_fails_before_claiming_success(
    tmp_path: Path, capsys
) -> None:
    # Writing a file Volante itself refuses to load, while printing "Wrote N profiles",
    # would surface the breakage later on an unrelated command.
    measurements = tmp_path / "m.json"
    measurements.write_text(json.dumps({"not a valid id!!": {"code": [1.0]}}))

    exit_code = cli.main(
        ["--calibrate", str(measurements), "--calibrate-out", str(tmp_path / "p.json")]
    )

    assert exit_code == 2
    assert "calibration failed" in capsys.readouterr().err

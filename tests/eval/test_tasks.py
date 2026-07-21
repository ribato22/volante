from __future__ import annotations

from eval.tasks import EVAL_SUITE, REFERENCE_TEST, SLUGIFY_GOAL, EvalTask

EXPECTED_IDS = {"slugify", "roman", "calc", "csv_stats", "json_flatten"}


def test_slugify_goal_is_composite_str():
    assert isinstance(SLUGIFY_GOAL, str)
    assert "slugify" in SLUGIFY_GOAL
    assert len(SLUGIFY_GOAL) > 50


def test_slugify_goal_requests_tests_and_readme():
    low = SLUGIFY_GOAL.lower()
    assert "test" in low
    assert "readme" in low


def test_reference_test_is_valid_python_referencing_slugify():
    # Harus compile bersih dan menggerakkan `slugify` yang diimpor dari `solution`.
    compile(REFERENCE_TEST, "<reference_test>", "exec")
    assert "slugify" in REFERENCE_TEST
    assert "solution" in REFERENCE_TEST


def test_reference_test_emits_json_passed_total():
    assert "json" in REFERENCE_TEST
    assert "passed" in REFERENCE_TEST
    assert "total" in REFERENCE_TEST


# --- EVAL_SUITE (5 goal komposit) ------------------------------------------


def test_eval_suite_has_five_unique_ids():
    assert len(EVAL_SUITE) == 5
    ids = [t.id for t in EVAL_SUITE]
    assert len(set(ids)) == 5  # unik
    assert set(ids) == EXPECTED_IDS


def test_eval_suite_entries_are_evaltask():
    for t in EVAL_SUITE:
        assert isinstance(t, EvalTask)


def test_eval_suite_goals_are_nonempty_strings():
    for t in EVAL_SUITE:
        assert isinstance(t.goal, str)
        assert len(t.goal) > 50


def test_eval_suite_reference_tests_compile_and_emit_json_passed():
    # Kontrak runner: reference_test valid Python & cetak JSON {"passed": ...}.
    for t in EVAL_SUITE:
        compile(t.reference_test, f"<reference_test:{t.id}>", "exec")
        assert 'json.dumps({"passed"' in t.reference_test
        assert "solution" in t.reference_test


def test_eval_suite_preserves_slugify_first():
    first = EVAL_SUITE[0]
    assert first.id == "slugify"
    assert first.goal == SLUGIFY_GOAL
    assert first.reference_test == REFERENCE_TEST


def test_evaltask_is_frozen():
    import dataclasses

    t = EVAL_SUITE[0]
    assert dataclasses.is_dataclass(t)
    try:
        t.id = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("EvalTask harus frozen")

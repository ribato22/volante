from __future__ import annotations

from eval.tasks import REFERENCE_TEST, SLUGIFY_GOAL


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

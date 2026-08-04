"""A calibration file's `null` means "this run produced nothing usable".

`src/volante/calibrate.py` documents that encoding and counts those runs separately
from graded ones, on purpose: a refusal or a crash is evidence about RELIABILITY
while a low score is evidence about QUALITY, and conflating them makes the
reliability component a copy of the quality component.

The harness that WRITES those files could never emit it. Every exception was
recorded as 0.0, and `score_for` dropped the `measured` boolean that
`_score_reference` computes for exactly this distinction — so `reliability_score`
was structurally unreachable from this repo's own pipeline, and no model in the
published profiles has one.
"""

from __future__ import annotations

import eval.calibrate_models as cm
import pytest
from eval.harness import score_for_calibration
from eval.tasks import EvalTask


def _code_task() -> EvalTask:
    return next(t for t in cm.EVAL_SUITE if t.task_type == "code")


def test_a_solution_that_ran_and_failed_is_still_zero() -> None:
    # The boundary. Refusing to record 0.0 for an UNMEASURED run must not stop
    # recording 0.0 for a solution that was measured and got the answer wrong.
    wrong = "def slugify(text):\n    return 'definitely-not-it'\n"

    assert score_for_calibration(_code_task(), wrong) == 0.0


def test_a_solution_that_cannot_be_graded_is_null() -> None:
    # NOT "the solution was bad" — a solution that fails to import is still MEASURED
    # (zero cases pass, and that is a real fact about the model). Unmeasured means the
    # reference runner itself produced no nonce-authenticated result: it crashed, it
    # timed out, or it was broken. That says nothing about the model, and 0.0 claims
    # it does.
    broken_runner = EvalTask(
        id="broken", goal="irrelevant",
        reference_test="raise SystemExit('the reference runner is broken')\n",
        task_type="code", checks=(),
    )

    assert score_for_calibration(broken_runner, "def f():\n    return 1\n") is None


def test_a_wrong_answer_is_measured_and_therefore_zero() -> None:
    # The other half of the boundary: a solution that imports and gets everything
    # wrong is a graded zero, not a null.
    assert score_for_calibration(_code_task(), "this is not python at all (((") == 0.0


def test_a_text_goal_is_unaffected() -> None:
    # Only code goals have a measured/unmeasured distinction; a rubric always grades.
    text_task = next(t for t in cm.TEXT_SUITE if t.task_type != "code")

    score = score_for_calibration(text_task, "a plausible but poor answer")

    assert isinstance(score, float)


@pytest.mark.asyncio
async def test_a_run_that_raised_is_recorded_as_null(monkeypatch) -> None:
    # `except Exception: bucket.append(0.0)` filed a timeout, a connection reset or a
    # 500 from the provider as the model scoring zero. This repo has already been
    # bitten once by exactly that (a 120 s timeout recorded as a failure to analyse).
    async def _always_fails(*args, **kwargs):
        raise TimeoutError("the client gave up")

    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("OPENAI_COMPAT_KEY", "not-used")
    monkeypatch.setattr(cm, "run_baseline", _always_fails)
    monkeypatch.setattr(cm, "EVAL_SUITE", [_code_task()])
    monkeypatch.setattr(cm, "TEXT_SUITE", [])
    monkeypatch.setattr(cm, "OpenAICompatProvider", lambda **kw: object())

    measurements = await cm.measure(["wire-1"], k=2, timeout_s=1.0)

    recorded = next(iter(measurements.values()))["code"]
    assert [e["score"] for e in recorded] == [None, None], (
        "a client-side failure was filed as a model score"
    )
    # The goal travels with the failure too: dropping it here would leave the task type
    # looking partly unlabelled, and calibrate.py then cannot verify its breadth.
    assert [e["goal"] for e in recorded] == ["slugify", "slugify"]

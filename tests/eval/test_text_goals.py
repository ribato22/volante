"""Validate every text goal's rubric before it is ever used to judge a model.

A rubric is measuring equipment. An unvalidated one produces numbers that look like
evidence, and this project has already been burned once by exactly that. Two
properties matter and they pull in opposite directions: a correct answer must score
1.0 (no false negatives from over-strict parsing), and an answer that does none of
the work must score near 0 (no free checks). The adversarial design pass found the
first draft of one rubric scoring a lazy output 100%, so the second property is not
hypothetical.
"""

from __future__ import annotations

import pytest
from eval.harness import score_text
from eval.tasks_text import TEXT_SUITE

CORRECT: dict[str, str] = {
    "incident_note": (
        "We're sorry — signing in did not work for a period this morning.\n\n"
        "Between 08:52 and 09:31 UTC on 14 March, customers were unable to sign in: "
        "a disruption of 39 minutes. If you were already signed in, your session kept "
        "working and you were not affected. Service has been fully restored.\n\n"
        "The cause was an expired security credential, which has been replaced. We "
        "are adding automated alerting that warns us before such credentials expire."
    ),
    "stdlib_pick": (
        "Use ast.literal_eval for the untrusted line: it evaluates only literals and "
        "cannot execute code. Use math.isclose for the two floating-point results, "
        "since it compares within a relative tolerance rather than exactly. Use "
        "secrets for the reset token, because it draws from a cryptographically "
        "strong source. Use itertools.groupby to walk the sorted records in runs "
        "sharing a key. Use hashlib for the file digest, whose output is stable "
        "across runs and machines."
    ),
    "delivery_log": (
        "MEDIAN_MINUTES: 75.0\n"
        "MEAN_MINUTES: 82.4\n"
        "VAN_DELIVERED: 7\n"
        "DRONE_PARCELS: 101\n"
        "DELIVERED_PARCELS: 1193\n"
        "CANCEL_RATE: 8.70\n"
        "TOP_DEPOT: EAST 436\n"
        "LOW_DEPOT: WEST 353\n"
        "SLOWER_DEPOT: NORTH 82.5\n"
        "SLOWEST_ID: R21 160"
    ),
}

# Emitting every plausible answer for every key: the failure mode that has no
# equivalent when grading code, and the one a purely positive rubric rewards.
SHOTGUN: dict[str, str] = {
    # Every candidate named for every need — the attack that scored 0.83 before
    # exclusivity was folded into each positive check.
    "stdlib_pick": (
        "You could use ast.literal_eval or eval( or json.loads or pickle; "
        "math.isclose or round( or abs( or decimal; secrets or random. or uuid or "
        "os.urandom; itertools.groupby or sorted( or defaultdict; hashlib or hash( "
        "or md5 or repr(. Any of these might suit depending on your needs."
    ) * 2,
    # Hedging in every direction while republishing the internal record verbatim.
    "incident_note": (
        "Incident INC-4471 affected auth-edge-7, session-store-b and token-mint. "
        "The disruption may have lasted 31 minutes or 41 minutes. A certificate in "
        "the pod pool was stale. Some customers may or may not have been affected."
    ) * 2,
    "delivery_log": "\n".join(
        f"{key}: 75.0 or 80.0 (or possibly EAST 436)"
        for key in (
            "MEDIAN_MINUTES", "MEAN_MINUTES", "VAN_DELIVERED", "DRONE_PARCELS",
            "DELIVERED_PARCELS", "CANCEL_RATE", "TOP_DEPOT", "LOW_DEPOT",
            "SLOWER_DEPOT", "SLOWEST_ID",
        )
    ),
}


@pytest.mark.parametrize("task", TEXT_SUITE, ids=lambda t: t.id)
def test_every_text_goal_has_a_validated_correct_answer(task) -> None:
    assert task.id in CORRECT, f"{task.id} ships no known-good answer to validate against"

    assert score_text(CORRECT[task.id], task.checks) == 1.0


@pytest.mark.parametrize("task", TEXT_SUITE, ids=lambda t: t.id)
def test_shotgunning_scores_far_below_a_real_answer(task) -> None:
    assert score_text(SHOTGUN[task.id], task.checks) <= 0.25


@pytest.mark.parametrize("task", TEXT_SUITE, ids=lambda t: t.id)
def test_saying_nothing_earns_nothing(task) -> None:
    # An empty answer once collected the "no extra lines" check for free.
    assert score_text("", task.checks) == 0.0


@pytest.mark.parametrize("task", TEXT_SUITE, ids=lambda t: t.id)
def test_formatting_noise_does_not_punish_a_correct_answer(task) -> None:
    # Markdown emphasis is the single most common way a correct answer arrives.
    bolded = "\n".join(f"**{line}**" for line in CORRECT[task.id].splitlines())

    assert score_text(bolded, task.checks) == 1.0


def test_text_goals_declare_a_non_code_task_type() -> None:
    # The whole point is calibration coverage beyond `code`.
    for task in TEXT_SUITE:
        assert task.task_type != "code"
        assert task.checks

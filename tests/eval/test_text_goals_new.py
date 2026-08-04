"""The write and research rubrics, attacked before they were trusted.

The module they live in records why: the first draft of the `write` rubric scored a
lazy answer 100% because its RULES block was the answer key, and the research rubric
leaked 83%. These pin the two attacks that actually landed on THIS draft — a shotgun
answer that names every candidate, and an empty one that harvests every negative check
for free — so a later edit cannot quietly reopen either.
"""

from __future__ import annotations

import pytest
from eval.tasks_text import (
    RESEARCH_CHECKS,
    RESEARCH_GOAL,
    WRITE_CHECKS,
    WRITE_GOAL,
)

GOOD_WRITE = """We're sorry — signing in did not work for a period this morning.

Between 08:52 and 09:31 UTC on 14 March, customers were unable to sign in: a
disruption of 39 minutes. If you were already signed in, your session kept working
and you were not affected. Service has been fully restored.

The cause was an expired security credential, which has been replaced. We are adding
automated alerting that warns us before such credentials expire."""

GOOD_RESEARCH = """For the settings file, use configparser: it reads that
sections-with-key-value format directly. For the diff, use difflib: unified_diff gives
the usual patch-style output with no external process. For the measurement, use timeit:
it runs the callable repeatedly and handles the loop for you."""

SHOTGUN = (
    "configparser or json or tomllib or yaml; difflib or filecmp or subprocess; "
    "timeit or time.perf_counter or cProfile or profile. Any of these could work "
    "depending on your needs and preferences."
) * 2


def _score(out: str, checks) -> float:
    return sum(1 for c in checks if c.passes(out) != c.negative) / len(checks)


@pytest.mark.parametrize(
    ("checks", "answer"),
    [(WRITE_CHECKS, GOOD_WRITE), (RESEARCH_CHECKS, GOOD_RESEARCH)],
)
def test_a_correct_answer_reaches_the_ceiling(checks, answer) -> None:
    """A rubric nobody can satisfy grades the rubric, not the model."""
    assert _score(answer, checks) == 1.0


@pytest.mark.parametrize(
    ("checks", "goal"), [(WRITE_CHECKS, WRITE_GOAL), (RESEARCH_CHECKS, RESEARCH_GOAL)]
)
def test_echoing_the_prompt_scores_badly(checks, goal) -> None:
    """The attack that beat the first draft of this suite: if the prompt contains what
    the checks look for, copying it is a winning strategy."""
    assert _score(goal, checks) <= 0.55


def test_a_shotgun_answer_does_not_profit_from_naming_everything() -> None:
    """Measured at 0.83 before the fix. Three positives outweighed one negative, so
    spraying candidates paid; exclusivity is folded into each positive now."""
    assert _score(SHOTGUN, RESEARCH_CHECKS) <= 0.40


@pytest.mark.parametrize("checks", [WRITE_CHECKS, RESEARCH_CHECKS])
def test_an_empty_answer_cannot_harvest_the_negative_checks(checks) -> None:
    """Nothing forbidden is present when nothing is present, so every negative check
    passes for free. It scored the same as copying the prompt until positives that
    require real content were added."""
    assert _score("", checks) <= 0.35


@pytest.mark.parametrize("checks", [WRITE_CHECKS, RESEARCH_CHECKS])
def test_the_rubric_has_teeth_in_both_directions(checks) -> None:
    """A rubric of only positives cannot punish padding, and one of only negatives
    cannot reward substance."""
    assert any(c.negative for c in checks)
    assert any(not c.negative for c in checks)

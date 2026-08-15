"""Instrument check for `httpkit_method`, the second wide goal.

Its numbers were reproduced independently before it joined the suite rather than taken
from the design that proposed it: golden 1.0000, untouched tree 0.0000, prose-only
0.0000, and a drop table whose cheapest file costs three change cases. The shared gate
in `test_repo_suite_gate.py` re-runs all of that; what lives here is the truncation
ladder, which is the property this goal exists for.
"""

from __future__ import annotations

import re

import pytest
from eval.harness import _score_reference, repo_files_for
from eval.tasks_repo_http import GOLDEN, HTTPKIT_TASK

_BLOCKS = re.split(r"\n\n(?=```)", GOLDEN)
_NAMES = [re.search(r"# path: (\S+)", b).group(1) for b in _BLOCKS]


def _score(answer: str) -> float:
    return _score_reference(
        answer, HTTPKIT_TASK.reference_test, repo_files_for(answer, HTTPKIT_TASK.repo)
    )[0]


def test_the_golden_answer_scores_exactly_one() -> None:
    assert _score(GOLDEN) == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="empty"),
        pytest.param("Sure, I will add PATCH support.", id="prose-only"),
    ],
)
def test_an_answer_that_changes_nothing_scores_exactly_zero(answer: str) -> None:
    assert _score(answer) == 0.0


def test_the_score_reads_as_how_many_files_landed() -> None:
    """Truncation is monotone here, and that is the point of a wide goal.

    Handing back the first N files of a correct answer scores in even steps, so the
    number says HOW MUCH of the change arrived rather than merely that something was
    wrong. The measured failure this goal exists to see is exactly that shape: a single
    call emits complete-looking files and silently drops parts it did not edit.
    """
    scores = [_score("\n\n".join(_BLOCKS[:n])) for n in range(len(_BLOCKS) + 1)]

    assert scores[0] == 0.0
    assert scores[-1] == 1.0
    assert scores == sorted(scores), scores
    # Strictly increasing: no prefix is free, so no file is decoration.
    assert all(b > a for a, b in zip(scores, scores[1:], strict=False)), scores


def test_the_keystone_file_is_the_most_expensive_to_drop() -> None:
    """`methods.py` is what every other module reads. Losing it must be visibly worse
    than losing a leaf, or the score flattens every kind of incompleteness into one
    number and stops telling you which happened."""

    def without(name: str) -> float:
        return _score("\n\n".join(b for i, b in enumerate(_BLOCKS) if _NAMES[i] != name))

    assert without("methods.py") < 0.5
    assert without("retry.py") > 0.8

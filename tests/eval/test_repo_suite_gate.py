"""One gate over every goal in REPO_SUITE, so a new goal cannot skip it.

Each repo goal has its own test module with its own mutation ladder. This is the part
that must hold for ALL of them, present and future:

    the golden answer scores exactly 1.000
    an untouched tree scores exactly 0.000
    dropping any one file of the golden answer costs something

Every one of those three has fired on a real draft here — an unsatisfiable goal, a free
point worth 0.0769, and two files whose omission cost nothing because their fallbacks
happened to agree with the requirement. Adding a goal to the suite without a golden
fails `test_every_suite_goal_has_a_golden`, which is deliberate: a goal nobody can
demonstrate a correct answer for is a goal nobody can trust a score from.
"""

from __future__ import annotations

import pytest
from eval.repo_goal import gate_findings
from eval.tasks_repo import REPO_SUITE
from tests.eval.test_repo_config_goal import GOLDEN as DEPLOYCTL_GOLDEN
from tests.eval.test_repo_goals import GOLDEN as SHOPCART_GOLDEN
from tests.eval.test_repo_wide_goal import GOLDEN as RECORDKIT_GOLDEN

GOLDENS = {
    "shopcart_shipping": SHOPCART_GOLDEN,
    "deployctl_layers": DEPLOYCTL_GOLDEN,
    "recordkit_field": RECORDKIT_GOLDEN,
}


def test_every_suite_goal_has_a_golden() -> None:
    assert {t.id for t in REPO_SUITE} == set(GOLDENS)


@pytest.mark.parametrize("task", REPO_SUITE, ids=lambda t: t.id)
def test_the_goal_passes_the_three_checks(task) -> None:
    findings = gate_findings(task, GOLDENS[task.id])

    assert findings == [], "\n".join(findings)

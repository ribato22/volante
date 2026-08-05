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
from eval.tasks_repo_authkit import GOLDEN as AUTHKIT_GOLDEN
from eval.tasks_repo_feedkit import GOLDEN as FEEDKIT_GOLDEN
from eval.tasks_repo_gradekit import GOLDEN as GRADEKIT_GOLDEN
from eval.tasks_repo_http import GOLDEN as HTTPKIT_GOLDEN
from eval.tasks_repo_unitkit import GOLDEN as UNITKIT_GOLDEN
from tests.eval.test_repo_config_goal import GOLDEN as DEPLOYCTL_GOLDEN
from tests.eval.test_repo_goals import GOLDEN as SHOPCART_GOLDEN
from tests.eval.test_repo_wide_goal import GOLDEN as RECORDKIT_GOLDEN

GOLDENS = {
    "shopcart_shipping": SHOPCART_GOLDEN,
    "deployctl_layers": DEPLOYCTL_GOLDEN,
    "recordkit_field": RECORDKIT_GOLDEN,
    "httpkit_method": HTTPKIT_GOLDEN,
    "unitkit_unit": UNITKIT_GOLDEN,
    "authkit_role": AUTHKIT_GOLDEN,
    "feedkit_source": FEEDKIT_GOLDEN,
    "gradekit_kind": GRADEKIT_GOLDEN,
}


def test_every_suite_goal_has_a_golden() -> None:
    assert {t.id for t in REPO_SUITE} == set(GOLDENS)


@pytest.mark.parametrize("task", REPO_SUITE, ids=lambda t: t.id)
def test_the_goal_passes_the_three_checks(task) -> None:
    findings = gate_findings(task, GOLDENS[task.id])

    assert findings == [], "\n".join(findings)


# --- the gate must actually reject things ------------------------------------
#
# A gate that has never refused anything is decoration. Each check below is fed a goal
# that violates exactly one rule, and the finding must name it.


def _wide_task():
    from eval.tasks_repo_wide import RECORDKIT_TASK

    return RECORDKIT_TASK


def test_the_gate_rejects_a_golden_that_cannot_reach_the_ceiling() -> None:
    broken = GOLDENS["recordkit_field"].replace('"priority": 8', '"priority": 99')
    assert broken != GOLDENS["recordkit_field"], "the mutation did not apply"

    findings = gate_findings(_wide_task(), broken)

    assert any("not 1.000" in f for f in findings), findings


def test_the_gate_rejects_a_file_whose_omission_is_nearly_free() -> None:
    """The bar is TWO change cases, not "something".

    Testing only `cost > 0` let a file worth a single case count as load-bearing, and
    a goal built of such files claims a width it does not have. The check is run
    against a golden with one file removed: the REMAINING files must each still be
    worth two, and `summary.py` — the cheapest at exactly two — is what makes this a
    real boundary rather than a comfortable one.
    """
    import re

    golden = GOLDENS["recordkit_field"]
    blocks = re.split(r"\n\n(?=```)", golden)
    thin = "\n\n".join(
        b for b in blocks if "# path: summary.py" not in b and "# path: export.py" not in b
    )

    findings = gate_findings(_wide_task(), thin)

    # It cannot reach the ceiling AND the remaining drops are cheaper, so both fire.
    assert any("not 1.000" in f for f in findings), findings


def test_the_gate_rejects_a_prompt_that_omits_the_starting_files() -> None:
    """The defect none of the three score checks can see.

    A goal whose prompt does not carry the tree is attemptable only by the arm that
    reads the disk. The other two score ~0 for a harness reason that reads exactly
    like a capability gap — which is the single most expensive mistake this eval can
    make, and it has made it before.
    """
    import dataclasses

    blind = dataclasses.replace(_wide_task(), goal="Add a priority field, please.")

    findings = gate_findings(blind, GOLDENS["recordkit_field"])

    assert any("prompt does not contain" in f for f in findings), findings

"""One builder for every repo-shaped goal, and one gate they all have to pass.

Each repo goal was rendering its own reference runner from its own case tables. Three
goals is three copies of the same twenty lines; six is six places for the product
formula, the nonce-tagged output line, or the change/regression split to drift apart
silently. The runner is mechanical, so it belongs in one place.

The GATE is the more important half. Every defect this suite has found in its own goals
was caught by one of three checks, each of which costs no API calls:

    golden scores exactly 1.000     — or the goal is unsatisfiable
    untouched tree scores 0.000     — or there are free points
    dropping any one file costs     — or the goal's claimed width is decoration

All three have fired on real drafts. `gate_repo_goal` runs them together so a new goal
cannot join the suite without facing them.
"""

from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING, Any

from eval.tasks import EvalTask, RepoSpec

_N_CHANGE_RE = _re.compile(r"^_N_CHANGE = (\d+)$", _re.M)

if TYPE_CHECKING:
    from collections.abc import Sequence

# (label, module, fn, args, expected) — the shape every case table uses. `module` is a
# module name inside the repo, usually the hidden probe; `args` and `expected` must be
# JSON-serialisable, because they cross the RPC boundary to the solution server. A
# tuple in the starting files comes back as a LIST, so a case expecting a tuple fails
# against a perfectly correct answer.
Case = tuple[str, str, str, list[Any], Any]

_RUNNER = """\
import json, sys

CHANGE = {change!r}
REGRESSION = {regression!r}

# Counts as literals so the gate can read them back without re-parsing the tables.
# Without them it could only ask "did dropping this file cost ANYTHING", which is a
# weaker bar than the one this suite claims to hold goals to.
_N_CHANGE = {n_change}
_N_REGRESSION = {n_regression}


def _ok(mod, fn, args, expected):
    try:
        return call_in(mod, fn, *args) == expected
    except Exception:
        return False


def main():
    cp = sum(1 for _l, m, f, a, e in CHANGE if _ok(m, f, a, e))
    rp = sum(1 for _l, m, f, a, e in REGRESSION if _ok(m, f, a, e))
    # The PRODUCT: breaking what existed scales the whole score down, and changing
    # nothing leaves it at zero however tidy the untouched project still is.
    print(_TAG + json.dumps({{
        "passed": cp * rp,
        "total": len(CHANGE) * len(REGRESSION),
        "change": [cp, len(CHANGE)],
        "regression": [rp, len(REGRESSION)],
    }}))


if __name__ == "__main__":
    main()
    sys.exit(0)
"""


def render_runner(change: Sequence[Case], regression: Sequence[Case]) -> str:
    """The hidden reference runner for a repo goal, rendered from its case tables."""
    if not change or not regression:
        raise ValueError("a repo goal needs both change and regression cases")
    return _RUNNER.format(
        change=list(change),
        regression=list(regression),
        n_change=len(change),
        n_regression=len(regression),
    )


def build_repo_task(
    *,
    task_id: str,
    goal: str,
    starting_files: Sequence[tuple[str, str]],
    probe_files: Sequence[tuple[str, str]] = (),
    change: Sequence[Case],
    regression: Sequence[Case],
) -> EvalTask:
    """An `EvalTask` for a repo goal, with its runner rendered from the tables."""
    return EvalTask(
        id=task_id,
        goal=goal,
        reference_test=render_runner(change, regression),
        repo=RepoSpec(starting_files=tuple(starting_files), probe_files=tuple(probe_files)),
    )


def file_block(starting_files: Sequence[tuple[str, str]]) -> str:
    """The starting tree as prompt text.

    The FULL source goes in the prompt, never a summary. The agentic arm gets the tree
    on disk; if the other arms only got a description, the regression factor would be
    measuring how well a model reconstructs prose rather than whether it broke code it
    was given, and an arm comparison over unequal context means nothing.
    """
    return "\n\n".join(
        f"### {path}\n```python\n{body.rstrip()}\n```" for path, body in starting_files
    )


def gate_findings(task: EvalTask, golden: str) -> list[str]:
    """Every way `task` fails the three checks, as plain sentences. Empty means clean.

    Returns findings rather than raising so a caller can see ALL of them at once. A gate
    that stops at the first problem hides the other two, and these arrive together:
    the draft that had free points usually also had a file whose omission was free.
    """
    import re

    from eval.harness import _score_reference, repo_files_for

    def score(answer: str) -> float:
        return _score_reference(answer, task.reference_test, repo_files_for(answer, task.repo))[0]

    findings: list[str] = []

    top = score(golden)
    if top != 1.0:
        findings.append(
            f"the golden answer scores {top:.4f}, not 1.000 — the goal asks for "
            "something its own reference answer does not deliver, so no model can "
            "reach the ceiling and every arm is measured against a moving target"
        )

    floor = score("")
    if floor != 0.0:
        findings.append(
            f"an empty answer scores {floor:.4f}, not 0.000 — some change case is "
            "already satisfied by the untouched tree, which pays a model for work it "
            "did not do; move that case to the regression table or conjoin it with a "
            "positive control"
        )

    # A goal whose prompt does not carry the starting tree cannot be attempted by the
    # two arms that only see the prompt. Both would score ~0 for a HARNESS reason that
    # reads exactly like a capability gap — the fourth defect class, and the only one
    # none of the three score checks can see.
    for path, body in task.repo.starting_files:
        marker = next(
            (
                ln.strip()
                for ln in body.splitlines()
                if ln.strip() and not ln.lstrip().startswith(('"""', "#", "from", "import"))
            ),
            "",
        )
        if marker and marker not in task.goal:
            findings.append(
                f"the prompt does not contain {path} — the baseline and orchestration "
                "arms only ever see the prompt, so they would be asked to modify code "
                "they were never shown while the agentic arm reads it off disk. That "
                "is not an arm comparison, it is three different questions"
            )
            break

    # A prompt that never states the answer FORMAT is a fifth defect class, and none
    # of the checks above can see it. Measured: httpkit's request ended at the
    # requirement, every prompt-only arm emitted unlabelled fences, and both scored
    # 0.00 three times running while the agentic arm — which writes to disk and needs
    # no format — "won" the goal at 0.27. That reads as a capability result and is not.
    if "# path:" not in task.goal:
        findings.append(
            "the prompt never states the answer format — nothing tells the model to "
            "label each block with a `# path:` first line, so the extractor attributes "
            "no files and every arm that answers in TEXT scores 0.00 while the agentic "
            "arm, which writes to disk, appears to win"
        )

    # "costs at least TWO change cases", which is the bar this suite actually claims.
    # Testing only "costs something" let a file worth one case count as load-bearing.
    n_change = int(_N_CHANGE_RE.search(task.reference_test).group(1))
    change_only = task.reference_test.replace('"passed": cp * rp,', '"passed": cp,').replace(
        '"total": len(CHANGE) * len(REGRESSION),', '"total": len(CHANGE),'
    )

    def change_cases(answer: str) -> float:
        from eval.harness import _score_reference as _sr

        return _sr(answer, change_only, repo_files_for(answer, task.repo))[0] * n_change

    blocks = re.split(r"\n\n(?=```)", golden)
    named = [
        (i, m.group(1)) for i, b in enumerate(blocks) if (m := re.search(r"#\s*path:\s*(\S+)", b))
    ]
    full = change_cases(golden)
    for index, path in named:
        partial = "\n\n".join(b for i, b in enumerate(blocks) if i != index)
        cost = full - change_cases(partial)
        if cost < 2.0 - 1e-9:
            findings.append(
                f"dropping {path} costs {cost:.0f} change case(s), under two — either "
                "its fallback agrees with the requirement, or too few cases reach it. "
                "A file whose omission is nearly free is width the goal claims and "
                "does not have"
            )
    return findings


# The answer-format block every repo goal states, in one place so it cannot drift.
#
# The first version said only "the complete contents of every file you changed", and
# models truncated anyway: measured across three goals, a plain single call dropped
# 5 to 13 functions per answer while believing it was finished. The second sentence is
# what stopped it — function losses went to zero on every run of every goal. It names
# the specific wrong thing rather than restating the requirement, which is the whole
# difference between the two versions.
ANSWER_FORMAT = """

Answer with the complete contents of every file you changed. Each block must be that
file's COMPLETE current contents — every function and constant it has now, including
the ones you did not touch. A file reduced to the part you changed is not a shorter
answer, it is a broken file.

Put each one in its own ```python block whose FIRST LINE is a comment naming the file:

```python
# path: example.py
...the whole file...
```

Files you did not change do not need to be included.
"""

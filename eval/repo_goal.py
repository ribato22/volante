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

from typing import TYPE_CHECKING, Any

from eval.tasks import EvalTask, RepoSpec

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
    return _RUNNER.format(change=list(change), regression=list(regression))


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
        repo=RepoSpec(
            starting_files=tuple(starting_files), probe_files=tuple(probe_files)
        ),
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
        return _score_reference(
            answer, task.reference_test, repo_files_for(answer, task.repo)
        )[0]

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

    blocks = re.split(r"\n\n(?=```)", golden)
    named = [(i, m.group(1)) for i, b in enumerate(blocks)
             if (m := re.search(r"#\s*path:\s*(\S+)", b))]
    for index, path in named:
        partial = "\n\n".join(b for i, b in enumerate(blocks) if i != index)
        cost = top - score(partial)
        if cost <= 0.0:
            findings.append(
                f"dropping {path} costs nothing — the goal's claimed width includes a "
                "file no case depends on, so an answer that omits it scores the same "
                "as one that does not. Either the fallback agrees with the requirement "
                "or no case reaches this file"
            )
    return findings

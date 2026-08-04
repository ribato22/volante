"""Check an answer by RUNNING it, not by asking a model whether it is right.

Every attempt in this project to have a model judge its own output failed on
measurement. Its difficulty labels give the one goal where decomposition is worth
+0.289 the same profile as goals a single call already aces. Told it may abstain on a
simple goal, it never abstains. Shown its own answer and asked to check it against the
goal, it replies ``OK`` to work scoring 0.417.

Execution does not ask. Derive ``assert`` statements from the GOAL, run them against
the answer in the sandbox Volante already ships, and report what happened. Across 11
code goals, 2 runs each, that produced **0 false negatives in 22** — it never approved
a wrong answer, and the mean score whenever every check passed was exactly 1.000.

WHAT IT DOES NOT DO, and the measurements are the reason:

* It does not gate. 10 of those 22 runs were false POSITIVES — correct answers whose
  derived checks failed — and the failing fraction does not separate the cases
  (a wrong answer failed 25% of its checks; a perfect one failed 17%).
* It does not repair. Handing the failures back for one repair pass produced 0
  improvements in 16 attempts and 3 regressions, one of them 1.000 -> 0.000: a working
  module rewritten into a broken one on the strength of a single wrong assertion.

So this reports, and the caller decides. It returns WHICH checks failed rather than a
verdict, deliberately: at a 45% false-positive rate a pass/fail badge misleads, while
"here is the check I derived and here is what happened" is information a reader can
judge in seconds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_PY_OPEN = re.compile(r"```[^\S\r\n]*python[^\S\r\n]*\r?\n", re.IGNORECASE)
# A close must start its own line; a ``` mid-line is part of the text.
_FENCE_CLOSE = re.compile(r"(?m)^[^\S\r\n]*```")

# It forbids the COMPUTATION, not the derivation — a distinction the failures dictated.
# Reading them one by one, almost none was the CODE being wrong:
# `column_widths(...) == [4, 5, 5]` where the answer is [4, 4, 5]; `wrap_text("a"*10, 5)`
# expecting ten single characters; a bare `from_roman(to_roman(n))` naming a loop
# variable that does not exist. The model was working out its own expected values and
# getting them wrong, and 57% of correct answers were flagged for it.
#
# Forbidding invention outright fixed that (57% -> 0%) and broke something worse: with
# only literal worked examples allowed, `resolve` kept ONE check, passed it at a score
# of 0.875, and produced the false negative this detector exists to never produce. It
# also left 12% of goals unverifiable.
#
# Allowing direct substitution while forbidding calculation keeps both ends. Measured
# across 8 goals, each run against its own answer AND a deliberately broken copy:
# false positives 0/8, false negatives 0/8, nothing inconclusive.
DERIVE_PROMPT = (
    "Below is a programming goal. Do NOT solve it.\n\n"
    "Write ONLY a Python block of `assert` statements, and obey these rules:\n"
    "- You may write an assertion when the goal's rules decide the answer by DIRECT "
    "SUBSTITUTION — copying a worked example, or applying one stated rule to one "
    "input.\n"
    "- You must NOT write one when you would have to COMPUTE the answer: no arithmetic, "
    "no counting, no widths, no sorting or merging in your head, no multi-step "
    "transformations. If you would have to work it out, skip it.\n"
    "- Every name you use must be a function the goal names. No loop variables, no "
    "helpers, no placeholders.\n"
    "- Assume the functions are already defined in scope. No imports, no definitions, "
    "no prose.\n\n"
    "An assertion you had to calculate is worse than one you did not write.\n\n"
    "GOAL:\n{goal}"
)

# Below this many grounded assertions, the goal simply did not state enough for a
# verdict. Reporting "passed" on one assertion is how grounding broke the property that
# made this worth having: with the rule alone, `resolve` kept ONE check, passed it, and
# scored 0.875 — a false negative, the one outcome that must never happen. The third
# state is what makes precision affordable: say "not enough evidence", not "fine".
_MIN_GROUNDED_CHECKS = 3

# Each assertion runs on its own so one failure cannot hide the rest, and the reason is
# captured so a reader can see whether the CHECK or the CODE was wrong. Runs inside the
# sandbox, which is what the sandbox is for: these assertions are model-written.
_RUNNER = '''
import json
_failed = []
for _src in _ASSERTS:
    try:
        eval(compile(_src, "<check>", "exec"), globals())
    except Exception as _exc:
        _failed.append([_src, type(_exc).__name__ + ": " + str(_exc)[:160]])
print("VOLANTE_CHECKS " + json.dumps({"failed": _failed, "total": len(_ASSERTS)}))
'''

_MARKER = "VOLANTE_CHECKS "


@dataclass(frozen=True)
class CheckReport:
    """What the derived checks did. `total == 0` means verification did not run."""

    total: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None

    @property
    def ran(self) -> bool:
        return self.total > 0 or self.error is not None

    @property
    def inconclusive(self) -> bool:
        """Too few grounded checks to say anything. NOT a pass."""
        return (
            self.error is None
            and not self.failed
            and 0 < self.total < _MIN_GROUNDED_CHECKS
        )

    @property
    def all_passed(self) -> bool:
        """Every check passed AND there were enough of them to mean it."""
        return (
            self.total >= _MIN_GROUNDED_CHECKS
            and not self.failed
            and self.error is None
        )

    def summary(self) -> str:
        if self.error is not None:
            return f"checks could not run: {self.error}"
        if self.total == 0:
            return "no checks derived — the goal states no worked example to check"
        if self.inconclusive:
            return (
                f"only {self.total} check(s) could be grounded in the goal — not enough "
                "to verify; state an expected result and it can be checked"
            )
        if not self.failed:
            return f"{self.total}/{self.total} derived checks passed"
        return f"{self.total - len(self.failed)}/{self.total} derived checks passed"


def extract_python(answer: str) -> str:
    """The first fenced python block, closed where the code actually PARSES.

    The closing fence is chosen rather than taken, for a measured reason: a ``` inside a
    string literal — which is what a README embedded in a module looks like — closed the
    block early, and the truncated code could not import. In the eval scorer that turned
    working solutions into 0.000. Candidates are tried in order, so a block whose first
    close is correct behaves exactly as before.
    """
    import ast

    opening = _PY_OPEN.search(answer)
    if opening is None:
        return answer
    body = answer[opening.end() :]
    first: str | None = None
    for close in _FENCE_CLOSE.finditer(body):
        candidate = body[: close.start()]
        if first is None:
            first = candidate
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        return candidate
    # Nothing parsed: hand back the first candidate so broken code stays visibly broken
    # rather than being silently replaced by prose.
    return first if first is not None else body


def parse_assertions(derived: str) -> list[str]:
    """The `assert` lines out of a derivation, ignoring whatever else it wrote."""
    body = extract_python(derived)
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith("assert ")
    ]


def build_program(solution: str, assertions: list[str]) -> str:
    """The program the sandbox runs: the answer, then its checks, one at a time."""
    return f"{solution}\n\n_ASSERTS = {assertions!r}\n{_RUNNER}"


def read_report(stdout: str, stderr: str, assertions: list[str]) -> CheckReport:
    """Turn a sandbox run into a report.

    A missing marker means the answer did not even import, which is not "no checks
    failed" — it is the strongest failure there is, and it must not read as success.
    """
    match = re.search(_MARKER + r"(\{.*\})", stdout or "")
    if match is None:
        tail = (stderr or "").strip().splitlines()
        return CheckReport(
            total=len(assertions),
            failed=[],
            error=tail[-1][:200] if tail else "solution did not run",
        )
    payload = json.loads(match.group(1))
    return CheckReport(
        total=int(payload["total"]),
        failed=[(src, why) for src, why in payload["failed"]],
    )

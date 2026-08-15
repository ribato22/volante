"""Instrument check for `recordkit_field` — the wide goal, and the only one where
decomposition has a structural case.

Two design defects were measured out of this goal before any model saw it, and both
are the same mistake in different clothes: the goal claimed six independent pieces and
did not have them.

  * The first draft's modules all iterated `FIELDS` generically, so adding the field in
    ONE file made three others work for free. The golden solution needed three files,
    not six, and "width" was decoration.
  * Once each module had its own per-field table, dropping `render.py` and `export.py`
    still cost NOTHING — the fallbacks I had chosen (`ALIGN.get(name, "left")`,
    `EXPORT_AS.get(name)`) happened to be the right answer for `priority`. A default
    that agrees with the requirement is not a requirement.

`priority` now differs from every fallback: right-aligned where the default is left,
shouted where the default is as-typed, lower-cased where the default is bare strip.
Every one of the six files has to be opened.
"""

from __future__ import annotations

import re

import pytest
from eval.harness import _score_reference, repo_files_for
from eval.tasks_repo_wide import CHANGE, RECORDKIT_TASK

GOLDEN = '''```python
# path: fields.py
FIELDS = ("id", "owner", "status", "priority")

LABELS = {"id": "ID", "owner": "Owner", "status": "Status", "priority": "Priority"}

WIDTHS = {"id": 6, "owner": 12, "status": 10, "priority": 8}
```

```python
# path: parse.py
from fields import FIELDS

NORMALISE = {
    "id": "strip",
    "owner": "strip",
    "status": "lower",
    "priority": "lower",
}


class MalformedLine(ValueError):
    """A line did not carry one value per field."""


def _apply(how, value):
    if how == "lower":
        return value.strip().lower()
    return value.strip()


def parse_line(line):
    parts = line.split("|")
    if len(parts) != len(FIELDS):
        raise MalformedLine(
            f"expected {len(FIELDS)} values, got {len(parts)}: {line!r}"
        )
    return {
        name: _apply(NORMALISE.get(name, "strip"), raw)
        for name, raw in zip(FIELDS, parts)
    }


def parse(text):
    return [parse_line(line) for line in text.splitlines() if line.strip()]
```

```python
# path: validate.py
STATUSES = ("open", "closed", "blocked")
PRIORITIES = ("low", "normal", "high")


def check_id(value):
    if not value.isdigit():
        return f"id must be digits, got {value!r}"
    return None


def check_owner(value):
    if not value:
        return "owner must not be empty"
    return None


def check_status(value):
    if value not in STATUSES:
        return f"status must be one of {', '.join(STATUSES)}, got {value!r}"
    return None


def check_priority(value):
    if value not in PRIORITIES:
        return f"priority must be one of {', '.join(PRIORITIES)}, got {value!r}"
    return None


RULES = {
    "id": check_id,
    "owner": check_owner,
    "status": check_status,
    "priority": check_priority,
}


def errors(record):
    found = []
    for name, rule in RULES.items():
        message = rule(record.get(name, ""))
        if message is not None:
            found.append(message)
    return found
```

```python
# path: render.py
from fields import FIELDS, LABELS, WIDTHS

ALIGN = {
    "id": "right",
    "owner": "left",
    "status": "left",
    "priority": "right",
}


def _pad(name, text):
    width = WIDTHS[name]
    return text.rjust(width) if ALIGN.get(name, "left") == "right" else text.ljust(width)


def header():
    return " ".join(_pad(name, LABELS[name]) for name in FIELDS)


def row(record):
    return " ".join(_pad(name, str(record.get(name, ""))) for name in FIELDS)


def table(records):
    return "\\n".join([header(), *(row(r) for r in records)])
```

```python
# path: export.py
from fields import FIELDS

EXPORT_AS = {
    "id": "as_is",
    "owner": "as_is",
    "status": "upper",
    "priority": "upper",
}


def _rendered(name, value):
    return str(value).upper() if EXPORT_AS.get(name) == "upper" else str(value)


def header():
    return ",".join(FIELDS)


def line(record):
    return ",".join(_rendered(name, record.get(name, "")) for name in FIELDS)


def to_csv(records):
    return "\\n".join([header(), *(line(r) for r in records)])
```

```python
# path: summary.py
from collections import Counter


def by_status(records):
    return dict(Counter(r.get("status", "") for r in records))


def by_owner(records):
    return dict(Counter(r.get("owner", "") for r in records))


def by_priority(records):
    return dict(Counter(r.get("priority", "") for r in records))


def totals(records):
    return {
        "status": by_status(records),
        "owner": by_owner(records),
        "priority": by_priority(records),
    }
```'''


_BLOCKS = re.split(r"\n\n(?=```python)", GOLDEN)
_NAMES = [re.search(r"# path: (\S+)", b).group(1) for b in _BLOCKS]


def _score(answer: str) -> float:
    return _score_reference(
        answer,
        RECORDKIT_TASK.reference_test,
        repo_files_for(answer, RECORDKIT_TASK.repo),
    )[0]


def test_the_golden_answer_scores_exactly_one() -> None:
    assert _score(GOLDEN) == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="empty"),
        pytest.param("Sure, I'll add the priority field.", id="prose-only"),
    ],
)
def test_an_answer_that_changes_nothing_scores_exactly_zero(answer: str) -> None:
    assert _score(answer) == 0.0


def test_the_golden_answer_really_touches_six_files() -> None:
    """The premise, asserted rather than assumed.

    A wide goal that a three-file answer can ace is a narrow goal with extra prose,
    and it cannot say anything about whether splitting work helps.
    """
    assert len(_NAMES) == 6
    assert set(_NAMES) == {p for p, _ in RECORDKIT_TASK.repo.starting_files}


@pytest.mark.parametrize("dropped", range(6), ids=lambda i: f"without-file-{i}")
def test_dropping_any_single_file_costs_something(dropped: int) -> None:
    """Completeness under width IS the measurement here.

    Every individual piece is pattern completion — the surrounding code demonstrates
    each one three times over. What a wide change provokes is not a wrong answer but
    an INCOMPLETE one: a model that summarises, truncates, or simply forgets the sixth
    file. If omitting a file were free, this goal would be blind to exactly the failure
    it exists to see.
    """
    partial = "\n\n".join(b for i, b in enumerate(_BLOCKS) if i != dropped)

    score = _score(partial)

    assert score < 1.0 - 1e-9, f"dropping {_NAMES[dropped]} cost nothing"
    assert score < _score(GOLDEN)


def test_dropping_the_keystone_file_is_the_most_expensive() -> None:
    """`fields.py` is what every other module reads. Losing it should be visibly
    worse than losing a leaf, and the score should say so rather than flattening
    every kind of incompleteness into one number."""
    without_fields = "\n\n".join(b for i, b in enumerate(_BLOCKS) if _NAMES[i] != "fields.py")
    without_leaf = "\n\n".join(b for i, b in enumerate(_BLOCKS) if _NAMES[i] != "summary.py")

    assert _score(without_fields) < 0.2
    assert _score(without_leaf) > 0.7


def test_the_starting_project_passes_every_format_independent_regression() -> None:
    """A regression suite over a tree that was already broken measures nothing.

    Only PART of this suite can be checked that way, and the reason is the goal itself:
    the wire format is what changed, so seven of the twelve regression cases can only
    be exercised through a four-field line the old parser rightly refuses. Their
    PROPERTY is still regression -- did adding a field break the three that were
    already there -- but the untouched tree cannot demonstrate it.

    The five that take a record directly do not touch the format at all, and those must
    pass before the change or the regression factor is measuring nothing.
    """
    from eval.tasks_repo_wide import REGRESSION

    independent = [c for c in REGRESSION if c[2] in ("errors_for", "parse_rejects")]
    assert len(independent) == 5, "the format-independent set changed; re-derive it"

    runner = (
        RECORDKIT_TASK.reference_test.replace(
            f"REGRESSION = {REGRESSION!r}", f"REGRESSION = {independent!r}"
        )
        .replace('"passed": cp * rp,', '"passed": rp,')
        .replace('"total": len(CHANGE) * len(REGRESSION),', '"total": len(REGRESSION),')
    )

    score, measured = _score_reference("", runner, repo_files_for("", RECORDKIT_TASK.repo))

    assert (score, measured) == (1.0, True)


def test_every_touched_file_is_reachable_from_the_change_cases() -> None:
    """A file nobody's case depends on is a file the goal cannot tell you was missed."""
    assert len(CHANGE) >= len(_NAMES) * 2

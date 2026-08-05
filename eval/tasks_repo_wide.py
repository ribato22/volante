"""The third repo-shaped goal, and the only one where decomposition has a case.

`shopcart_shipping` and `deployctl_layers` are both sequential changes to one or two
files. Asking whether orchestration helps on those is asking a question whose answer is
fixed before the measurement: there is nothing to split.

This one is WIDE. One new field has to land in six files, and the six pieces are
genuinely independent — different files, no ordering between them, no shared reasoning.
Six parallel workers could each own one. If orchestration cannot win here it cannot win
anywhere, which makes this goal the one that actually tests the engine's premise.

The discriminator is therefore not depth but COMPLETENESS UNDER WIDTH. Every individual
piece is pattern completion — three fields already do exactly this, and the lesson
`deployctl_layers` paid for is that models are excellent at copying a visible pattern.
What is not easy is emitting the sixth file at all. Single calls truncate, summarise,
and quietly drop the last item; that failure mode is real, it is what a wide change
provokes, and nothing in this eval had ever measured it.
"""

from __future__ import annotations

from eval.repo_goal import build_repo_task, file_block

_FIELDS = '''\
"""Which columns a record has, in the order they appear on the wire and on screen."""

FIELDS = ("id", "owner", "status")

LABELS = {
    "id": "ID",
    "owner": "Owner",
    "status": "Status",
}

WIDTHS = {
    "id": 6,
    "owner": 12,
    "status": 10,
}
'''

_PARSE = '''\
"""Records arrive as pipe-separated lines: `id|owner|status`."""

from fields import FIELDS

# How each field is normalised once it is off the wire. A field with no entry here is
# left exactly as typed, which is nearly always the wrong thing for a closed
# vocabulary: `Open` and `open` become two different statuses.
NORMALISE = {
    "id": "strip",
    "owner": "strip",
    "status": "lower",
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
    """Every non-blank line. Blank lines are skipped, malformed ones are not."""
    return [parse_line(line) for line in text.splitlines() if line.strip()]
'''

_VALIDATE = '''\
"""One rule per field. Rules are independent and each returns a message or None."""

STATUSES = ("open", "closed", "blocked")


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


RULES = {
    "id": check_id,
    "owner": check_owner,
    "status": check_status,
}


def errors(record):
    """Every problem with `record`, in field order. Empty list means it is fine."""
    found = []
    for name, rule in RULES.items():
        message = rule(record.get(name, ""))
        if message is not None:
            found.append(message)
    return found
'''

_RENDER = '''\
"""Fixed-width rendering, one line per record."""

from fields import FIELDS, LABELS, WIDTHS

# Which way each column sits. Numbers read right-aligned; words read left. A field with
# no entry falls back to left, which is not an error and not what you wanted either.
ALIGN = {
    "id": "right",
    "owner": "left",
    "status": "left",
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
'''

_SUMMARY = '''\
"""Counts, one tally per field worth tallying."""

from collections import Counter


def by_status(records):
    return dict(Counter(r.get("status", "") for r in records))


def by_owner(records):
    return dict(Counter(r.get("owner", "") for r in records))


def totals(records):
    """Every tally this module knows how to produce, keyed by field name."""
    return {"status": by_status(records), "owner": by_owner(records)}
'''

_EXPORT = '''\
"""CSV export. Quoting is deliberately minimal — the values never contain commas."""

from fields import FIELDS

# How each field is written out. The warehouse system upper-cases statuses and we match
# it; anything without an entry goes out as typed.
EXPORT_AS = {
    "id": "as_is",
    "owner": "as_is",
    "status": "upper",
}


def _rendered(name, value):
    return str(value).upper() if EXPORT_AS.get(name) == "upper" else str(value)


def header():
    return ",".join(FIELDS)


def line(record):
    return ",".join(_rendered(name, record.get(name, "")) for name in FIELDS)


def to_csv(records):
    return "\\n".join([header(), *(line(r) for r in records)])
'''

STARTING_FILES = (
    ("fields.py", _FIELDS),
    ("parse.py", _PARSE),
    ("validate.py", _VALIDATE),
    ("render.py", _RENDER),
    ("summary.py", _SUMMARY),
    ("export.py", _EXPORT),
)

_PROBE = '''\
import export
import parse as parse_mod
import render
import summary
import validate


def parsed(text):
    return parse_mod.parse(text)


def field_of(text, index, name):
    return parse_mod.parse(text)[index].get(name)


def errors_for(record):
    return validate.errors(record)


def table_of(text):
    return render.table(parse_mod.parse(text))

def header_line():
    return render.header()


def csv_of(text):
    return export.to_csv(parse_mod.parse(text))


def totals_of(text):
    return summary.totals(parse_mod.parse(text))


def priority_rule_discriminates():
    """True when the priority rule accepts a good value AND rejects a bad one.

    Two halves in ONE call on purpose. "a good priority is not an error" as its own
    case is satisfied by a project with no priority rule at all -- an empty answer
    passes it -- which is exactly the free point `rejects()` was written to close.
    """
    good = {"id": "1", "owner": "ana", "status": "open", "priority": "low"}
    bad = dict(good, priority="urgent")
    return validate.errors(good) == [] and len(validate.errors(bad)) == 1


def parse_rejects(line):
    """True when parse WORKS on a good line and REJECTS this one.

    The control call is the point: a parser that raises on everything -- including a
    module that fails at import -- would satisfy a bare "it raised" check for free.
    """
    good = "1|ana|open|high"
    try:
        parse_mod.parse_line(good)
    except Exception:
        try:
            parse_mod.parse_line("1|ana|open")
        except Exception:
            return False
    try:
        parse_mod.parse_line(line)
    except Exception:
        return True
    return False
'''

PROBE_FILES = (("_probe.py", _PROBE),)

_FILE_BLOCK = file_block(STARTING_FILES)

RECORDKIT_GOAL = f"""\
Here is our little record toolkit. It works today.

{_FILE_BLOCK}

WHAT I NEED
-----------
We are adding a fourth field, `priority`. It arrives last on the wire, so a line now
reads `id|owner|status|priority`.

It has to work everywhere the other three do:

* it parses out of a line like the others, and like `status` it is a fixed vocabulary,
  so `HIGH` and `high` must not end up as two different things;
* it validates -- the allowed values are `low`, `normal` and `high`, and anything else
  is an error worded like the other rules;
* it renders in the table, last, under the heading `Priority`, in a column 8 wide,
  sitting the way `id` sits rather than the way the word columns sit;
* it exports to CSV, last, shouted the way `status` is;
* and `totals()` gains a `priority` tally alongside the two that are already there.

Everything the toolkit already does must keep working exactly as it does now.

Answer with the complete contents of every file you changed. Put each one in its own
```python block whose FIRST LINE is a comment naming the file, like:

```python
# path: fields.py
...the whole file...
```

Files you did not change do not need to be included.
"""

# --- the hidden acceptance suite --------------------------------------------
#
# The CHANGE cases are grouped by which FILE they depend on, because that grouping is
# the measurement: a model that emits five of the six files loses one whole group, and
# the score says which. Completeness under width is what this goal exists to see.

_L1 = "1|ana|open|high"
_L2 = "2|bo|closed|low"
_L3 = "3|cy|blocked|normal"
_TEXT = f"{_L1}\n{_L2}\n{_L3}"

CHANGE = [
    # parse.py
    ("priority parses off the line", "_probe", "field_of", [_TEXT, 0, "priority"], "high"),
    ("priority parses on later lines", "_probe", "field_of", [_TEXT, 2, "priority"], "normal"),
    # parse.py's own table: without an entry the value comes through as typed.
    ("priority is normalised like the other closed vocabulary",
     "_probe", "field_of", ["5|eve|OPEN|HIGH", 0, "priority"], "high"),
    ("priority is normalised on later rows too",
     "_probe", "field_of", ["1|ana|open|low\n5|eve|open|Normal", 1, "priority"], "normal"),
    ("a line with only three values is still refused",
     "_probe", "parse_rejects", ["9|dee|open"], True),

    # validate.py
    ("a bad priority is an error",
     "_probe", "errors_for", [{"id": "1", "owner": "ana", "status": "open",
                               "priority": "urgent"}],
     ["priority must be one of low, normal, high, got 'urgent'"]),
    ("the priority rule both accepts and rejects",
     "_probe", "priority_rule_discriminates", [], True),
    ("priority errors come after the others",
     "_probe", "errors_for", [{"id": "x", "owner": "ana", "status": "open",
                               "priority": "urgent"}],
     ["id must be digits, got 'x'",
      "priority must be one of low, normal, high, got 'urgent'"]),
    # fields.py + render.py
    ("the table heading gains Priority", "_probe", "header_line", [],
     "    ID Owner        Status     Priority"),
    # render.py's own table: a field with no ALIGN entry falls back to left, which is
    # right here -- so the case that bites is the one where the WIDTH is applied.
    ("the priority column is padded to its width", "_probe", "table_of", ["5|eve|open|low"],
     "    ID Owner        Status     Priority\n     5 eve          open            low"),
    ("the table row gains the priority column", "_probe", "table_of", [_L1],
     "    ID Owner        Status     Priority\n     1 ana          open           high"),
    # export.py
    ("the csv header gains priority", "_probe", "csv_of", [_L1],
     "id,owner,status,priority\n1,ana,OPEN,HIGH"),
    # export.py's own table: `status` is shouted, `priority` is not. A model that
    # copies the status entry gets HIGH here instead of high.
    ("priority is exported shouted like status", "_probe", "csv_of", ["5|eve|open|high"],
     "id,owner,status,priority\n5,eve,OPEN,HIGH"),
    ("the csv keeps field order across rows", "_probe", "csv_of", [f"{_L1}\n{_L2}"],
     "id,owner,status,priority\n1,ana,OPEN,HIGH\n2,bo,CLOSED,LOW"),
    # summary.py
    ("totals gains a priority tally", "_probe", "totals_of", [_TEXT],
     {"status": {"open": 1, "closed": 1, "blocked": 1},
      "owner": {"ana": 1, "bo": 1, "cy": 1},
      "priority": {"high": 1, "low": 1, "normal": 1}}),
    ("the priority tally counts repeats",
     "_probe", "totals_of", [f"{_L1}\n4|dee|open|high"],
     {"status": {"open": 2}, "owner": {"ana": 1, "dee": 1}, "priority": {"high": 2}}),
]

REGRESSION = [
    # Refused before the change (not 3 values) and after (not 4). Filed as a CHANGE
    # case it handed an untouched tree a free point, which is what put this goal's
    # floor at 0.0559.
    ("a line with too many values is still refused",
     "_probe", "parse_rejects", ["9|dee|open|high|extra"], True),
    ("id still parses", "_probe", "field_of", [_TEXT, 0, "id"], "1"),
    ("owner still parses", "_probe", "field_of", [_TEXT, 1, "owner"], "bo"),
    ("status still parses", "_probe", "field_of", [_TEXT, 2, "status"], "blocked"),
    ("blank lines are still skipped", "_probe", "parsed", [f"{_L1}\n\n  \n{_L2}"],
     [{"id": "1", "owner": "ana", "status": "open", "priority": "high"},
      {"id": "2", "owner": "bo", "status": "closed", "priority": "low"}]),
    ("values are still stripped", "_probe", "field_of", ["  7 | eve | open | low ", 0, "owner"],
     "eve"),
    ("the id rule still fires",
     "_probe", "errors_for", [{"id": "x", "owner": "ana", "status": "open",
                               "priority": "low"}], ["id must be digits, got 'x'"]),
    ("the owner rule still fires",
     "_probe", "errors_for", [{"id": "1", "owner": "", "status": "open",
                               "priority": "low"}], ["owner must not be empty"]),
    ("the status rule still fires",
     "_probe", "errors_for", [{"id": "1", "owner": "ana", "status": "done",
                               "priority": "low"}],
     ["status must be one of open, closed, blocked, got 'done'"]),
    ("a clean record still has no errors",
     "_probe", "errors_for", [{"id": "1", "owner": "ana", "status": "open",
                               "priority": "normal"}], []),
    ("existing column widths are unchanged", "_probe", "table_of", [_L2],
     "    ID Owner        Status     Priority\n     2 bo           closed          low"),
    ("by_status still counts", "_probe", "totals_of", [f"{_L1}\n4|dee|open|high"],
     {"status": {"open": 2}, "owner": {"ana": 1, "dee": 1}, "priority": {"high": 2}}),
]


RECORDKIT_TASK = build_repo_task(
    task_id="recordkit_field",
    goal=RECORDKIT_GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)

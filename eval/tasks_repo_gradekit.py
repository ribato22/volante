"""Wide repo goal: gradekit_kind (gradekit)

Six per-item tables, one new item, and the new item differs from EVERY module's
fallback — the property two earlier drafts lacked, where a default that agreed with
the requirement made a file free to omit.

Gated before it joined the suite: golden exactly 1.000, untouched tree exactly 0.000,
the prompt carries the whole starting tree exactly once, and dropping any single file
costs at least two change cases.

Same archetype as the other wide goals, and that is recorded rather than hidden: five
instances of one shape ("add one item to N per-item tables") are not five independent
samples of repo work, so a cross-goal interval over them is narrower than it looks.
"""

from __future__ import annotations

from eval.repo_goal import build_repo_task, file_block

_F0 = (
    '"""Which assessments a course is made of, in the order they appear on a transcript."""\n'
    "\n"
    'KINDS = ("quiz", "essay", "exam")\n'
    "\n"
    "LABELS = {\n"
    '    "quiz": "Quiz",\n'
    '    "essay": "Essay",\n'
    '    "exam": "Exam",\n'
    "}\n"
    "\n"
    "# How wide each column is on a printed transcript. There is deliberately no default: a\n"
    "# kind missing from here is a KeyError, not a squashed column nobody notices.\n"
    "WIDTHS = {\n"
    '    "quiz": 6,\n'
    '    "essay": 7,\n'
    '    "exam": 6,\n'
    "}\n"
)

_F1 = (
    '"""What each assessment contributes to the final mark."""\n'
    "\n"
    "from scheme import KINDS\n"
    "\n"
    "# Points of weight per kind -- not percentages. The total is normalised by whatever the\n"
    "# student actually sat, so a kind with no entry here weighs nothing and is silently\n"
    "# deleted from the result rather than being reported as missing.\n"
    "WEIGHTS = {\n"
    '    "quiz": 20,\n'
    '    "essay": 30,\n'
    '    "exam": 50,\n'
    "}\n"
    "\n"
    "\n"
    "def weight_of(kind):\n"
    "    return WEIGHTS.get(kind, 0)\n"
    "\n"
    "\n"
    "def weighted_total(marks):\n"
    '    """The final mark: every kind present, weighted, normalised, rounded to 2dp."""\n'
    "    present = [kind for kind in KINDS if kind in marks]\n"
    "    numerator = sum(weight_of(kind) * marks[kind] for kind in present)\n"
    "    denominator = sum(weight_of(kind) for kind in present)\n"
    "    if not denominator:\n"
    "        return 0.0\n"
    "    return round(numerator / denominator, 2)\n"
)

_F2 = (
    '"""How a raw score becomes the mark that goes on the record."""\n'
    "\n"
    "import math\n"
    "\n"
    "# One policy per kind. A kind with no entry is recorded exactly as it was computed,\n"
    "# which is only ever right for a mark that was already on the scale -- anything a\n"
    "# marker averaged arrives with the tail of a division still on it.\n"
    "ROUNDING = {\n"
    '    "quiz": "whole",   # nearest whole mark, .5 goes up\n'
    '    "essay": "half",   # nearest half mark\n'
    '    "exam": "whole",\n'
    "}\n"
    "\n"
    "\n"
    "def round_mark(kind, raw):\n"
    '    how = ROUNDING.get(kind, "exact")\n'
    '    if how == "whole":\n'
    "        return float(math.floor(raw + 0.5))\n"
    '    if how == "half":\n'
    "        return math.floor(raw * 2 + 0.5) / 2\n"
    "    return float(raw)\n"
)

_F3 = (
    '"""Whether a mark clears the bar, per kind."""\n'
    "\n"
    "# The mark each kind has to reach. A kind with no entry has no bar at all, so every\n"
    "# mark clears it -- which is what you want for something we do not grade and a lie for\n"
    "# anything we do.\n"
    "THRESHOLDS = {\n"
    '    "quiz": 40.0,\n'
    '    "essay": 50.0,\n'
    '    "exam": 50.0,\n'
    "}\n"
    "\n"
    "# Whether failing this kind sinks the course on its own. A kind with no entry can be\n"
    '# carried by a good overall total -- that is what "not a hurdle" means here.\n'
    "MUST_PASS = {\n"
    '    "quiz": False,\n'
    '    "essay": False,\n'
    '    "exam": True,\n'
    "}\n"
    "\n"
    "\n"
    "def outcome(kind, mark):\n"
    '    return "pass" if mark >= THRESHOLDS.get(kind, 0.0) else "fail"\n'
    "\n"
    "\n"
    "def report(marks):\n"
    '    """One verdict per kind the student sat."""\n'
    "    return {kind: outcome(kind, mark) for kind, mark in marks.items()}\n"
    "\n"
    "\n"
    "def hurdles_cleared(marks):\n"
    '    """True when every kind that must be passed on its own was passed."""\n'
    "    return all(\n"
    '        outcome(kind, mark) == "pass"\n'
    "        for kind, mark in marks.items()\n"
    "        if MUST_PASS.get(kind, False)\n"
    "    )\n"
)

_F4 = (
    '"""The printed transcript: one column per kind, headings then marks."""\n'
    "\n"
    "from scheme import KINDS, LABELS, WIDTHS\n"
    "\n"
    "# Which way each column sits. Marks read right-aligned; a kind with no entry falls back\n"
    "# to left, which is not an error and not what you wanted either.\n"
    "ALIGN = {\n"
    '    "quiz": "right",\n'
    '    "essay": "right",\n'
    '    "exam": "right",\n'
    "}\n"
    "\n"
    "# How many decimals the mark is shown with. A kind with no entry shows none, which\n"
    "# quietly hides the part of the mark a student turns up to argue about.\n"
    "DECIMALS = {\n"
    '    "quiz": 0,\n'
    '    "essay": 1,\n'
    '    "exam": 0,\n'
    "}\n"
    "\n"
    "\n"
    "def _cell(kind, text):\n"
    "    width = WIDTHS[kind]\n"
    '    return text.rjust(width) if ALIGN.get(kind, "left") == "right" else text.ljust(width)\n'
    "\n"
    "\n"
    "def _mark(kind, value):\n"
    '    return f"{value:.{DECIMALS.get(kind, 0)}f}"\n'
    "\n"
    "\n"
    "def header():\n"
    '    return " ".join(_cell(kind, LABELS[kind]) for kind in KINDS)\n'
    "\n"
    "\n"
    "def row(marks):\n"
    '    return " ".join(_cell(kind, _mark(kind, marks.get(kind, 0.0))) for kind in KINDS)\n'
    "\n"
    "\n"
    "def render(marks):\n"
    '    return "\\n".join([header(), row(marks)])\n'
)

_F5 = (
    '"""The registry feed. One field per kind, in scheme order."""\n'
    "\n"
    "from scheme import KINDS\n"
    "\n"
    "# The code each kind goes out under. The registry accepts exactly two upper-case\n"
    "# letters and bounces the whole row otherwise, so a kind with no entry here -- which\n"
    "# goes out under its own long name -- takes the rest of the row down with it.\n"
    "CODES = {\n"
    '    "quiz": "QZ",\n'
    '    "essay": "ES",\n'
    '    "exam": "EX",\n'
    "}\n"
    "\n"
    "\n"
    "def code_for(kind):\n"
    "    return CODES.get(kind, kind)\n"
    "\n"
    "\n"
    "def header():\n"
    '    return ",".join(code_for(kind) for kind in KINDS)\n'
    "\n"
    "\n"
    "def line(marks):\n"
    '    """Only the kinds the student sat, in scheme order."""\n'
    '    return ",".join(\n'
    '        f"{code_for(kind)}={marks[kind]:g}" for kind in KINDS if kind in marks\n'
    "    )\n"
    "\n"
    "\n"
    "def feed(rows):\n"
    '    return "\\n".join([header(), *(line(row) for row in rows)])\n'
)

STARTING_FILES = (
    ("scheme.py", _F0),
    ("weights.py", _F1),
    ("rounding.py", _F2),
    ("passfail.py", _F3),
    ("transcript.py", _F4),
    ("export.py", _F5),
)

_P0 = (
    "import export\n"
    "import passfail\n"
    "import rounding\n"
    "import scheme\n"
    "import transcript\n"
    "import weights\n"
    "\n"
    "\n"
    "def kinds():\n"
    "    return list(scheme.KINDS)\n"
    "\n"
    "\n"
    "def kind_at(index):\n"
    "    return scheme.KINDS[index]\n"
    "\n"
    "\n"
    "def heading():\n"
    "    return transcript.header()\n"
    "\n"
    "\n"
    "def sheet(marks):\n"
    "    return transcript.render(marks)\n"
    "\n"
    "\n"
    "def rounded(kind, raw):\n"
    "    return rounding.round_mark(kind, raw)\n"
    "\n"
    "\n"
    "def weight(kind):\n"
    "    return weights.weight_of(kind)\n"
    "\n"
    "\n"
    "def total(marks):\n"
    "    return weights.weighted_total(marks)\n"
    "\n"
    "\n"
    "def verdicts(marks):\n"
    "    return passfail.report(marks)\n"
    "\n"
    "\n"
    "def hurdles(marks):\n"
    "    return passfail.hurdles_cleared(marks)\n"
    "\n"
    "\n"
    "def feed_line(marks):\n"
    "    return export.line(marks)\n"
    "\n"
    "\n"
    "def feed_header():\n"
    "    return export.header()\n"
    "\n"
    "\n"
    "def feed_of(rows):\n"
    "    return export.feed(rows)\n"
    "\n"
    "\n"
    "def practical_bar_discriminates():\n"
    '    """True when the practical bar PASSES a clear mark and FAILS one below it.\n'
    "\n"
    '    Two halves in ONE call on purpose. "a good practical mark is a pass" as its own\n'
    "    case is satisfied by a project that has no practical bar at all -- the fallback\n"
    "    threshold is 0.0, so an untouched tree passes it for free. Measured: the positive\n"
    "    half alone returns True on the starting tree. Conjoining it with the reject half\n"
    "    is what closes that free point.\n"
    '    """\n'
    "    return (\n"
    '        passfail.outcome("practical", 60.0) == "pass"\n'
    '        and passfail.outcome("practical", 59.5) == "fail"\n'
    "    )\n"
    "\n"
    "\n"
    "def quarter_rounding_discriminates():\n"
    '    """True when a raw practical mark snaps DOWN and UP to the nearest quarter.\n'
    "\n"
    "    One direction alone is passed by any policy that happens to move the same way on\n"
    '    that one number -- copying the essay\'s "half" gets 81.4 -> 81.5 right by accident.\n'
    "    Both directions in one call pin the step size itself.\n"
    '    """\n'
    "    return (\n"
    '        rounding.round_mark("practical", 81.3) == 81.25\n'
    '        and rounding.round_mark("practical", 81.4) == 81.5\n'
    "    )\n"
)

PROBE_FILES = (("_probe.py", _P0),)

_G0 = (
    '"""Which assessments a course is made of, in the order they appear on a transcript."""\n'
    "\n"
    'KINDS = ("quiz", "essay", "exam", "practical")\n'
    "\n"
    "LABELS = {\n"
    '    "quiz": "Quiz",\n'
    '    "essay": "Essay",\n'
    '    "exam": "Exam",\n'
    '    "practical": "Practical",\n'
    "}\n"
    "\n"
    "# How wide each column is on a printed transcript. There is deliberately no default: a\n"
    "# kind missing from here is a KeyError, not a squashed column nobody notices.\n"
    "WIDTHS = {\n"
    '    "quiz": 6,\n'
    '    "essay": 7,\n'
    '    "exam": 6,\n'
    '    "practical": 10,\n'
    "}\n"
)

_G1 = (
    '"""What each assessment contributes to the final mark."""\n'
    "\n"
    "from scheme import KINDS\n"
    "\n"
    "# Points of weight per kind -- not percentages. The total is normalised by whatever the\n"
    "# student actually sat, so a kind with no entry here weighs nothing and is silently\n"
    "# deleted from the result rather than being reported as missing.\n"
    "WEIGHTS = {\n"
    '    "quiz": 20,\n'
    '    "essay": 30,\n'
    '    "exam": 50,\n'
    '    "practical": 25,\n'
    "}\n"
    "\n"
    "\n"
    "def weight_of(kind):\n"
    "    return WEIGHTS.get(kind, 0)\n"
    "\n"
    "\n"
    "def weighted_total(marks):\n"
    '    """The final mark: every kind present, weighted, normalised, rounded to 2dp."""\n'
    "    present = [kind for kind in KINDS if kind in marks]\n"
    "    numerator = sum(weight_of(kind) * marks[kind] for kind in present)\n"
    "    denominator = sum(weight_of(kind) for kind in present)\n"
    "    if not denominator:\n"
    "        return 0.0\n"
    "    return round(numerator / denominator, 2)\n"
)

_G2 = (
    '"""How a raw score becomes the mark that goes on the record."""\n'
    "\n"
    "import math\n"
    "\n"
    "# One policy per kind. A kind with no entry is recorded exactly as it was computed,\n"
    "# which is only ever right for a mark that was already on the scale -- anything a\n"
    "# marker averaged arrives with the tail of a division still on it.\n"
    "ROUNDING = {\n"
    '    "quiz": "whole",       # nearest whole mark, .5 goes up\n'
    '    "essay": "half",       # nearest half mark\n'
    '    "exam": "whole",\n'
    '    "practical": "quarter",  # nearest quarter mark -- four stations, averaged\n'
    "}\n"
    "\n"
    "\n"
    "def round_mark(kind, raw):\n"
    '    how = ROUNDING.get(kind, "exact")\n'
    '    if how == "whole":\n'
    "        return float(math.floor(raw + 0.5))\n"
    '    if how == "half":\n'
    "        return math.floor(raw * 2 + 0.5) / 2\n"
    '    if how == "quarter":\n'
    "        return math.floor(raw * 4 + 0.5) / 4\n"
    "    return float(raw)\n"
)

_G3 = (
    '"""Whether a mark clears the bar, per kind."""\n'
    "\n"
    "# The mark each kind has to reach. A kind with no entry has no bar at all, so every\n"
    "# mark clears it -- which is what you want for something we do not grade and a lie for\n"
    "# anything we do.\n"
    "THRESHOLDS = {\n"
    '    "quiz": 40.0,\n'
    '    "essay": 50.0,\n'
    '    "exam": 50.0,\n'
    '    "practical": 60.0,\n'
    "}\n"
    "\n"
    "# Whether failing this kind sinks the course on its own. A kind with no entry can be\n"
    '# carried by a good overall total -- that is what "not a hurdle" means here.\n'
    "MUST_PASS = {\n"
    '    "quiz": False,\n'
    '    "essay": False,\n'
    '    "exam": True,\n'
    '    "practical": True,\n'
    "}\n"
    "\n"
    "\n"
    "def outcome(kind, mark):\n"
    '    return "pass" if mark >= THRESHOLDS.get(kind, 0.0) else "fail"\n'
    "\n"
    "\n"
    "def report(marks):\n"
    '    """One verdict per kind the student sat."""\n'
    "    return {kind: outcome(kind, mark) for kind, mark in marks.items()}\n"
    "\n"
    "\n"
    "def hurdles_cleared(marks):\n"
    '    """True when every kind that must be passed on its own was passed."""\n'
    "    return all(\n"
    '        outcome(kind, mark) == "pass"\n'
    "        for kind, mark in marks.items()\n"
    "        if MUST_PASS.get(kind, False)\n"
    "    )\n"
)

_G4 = (
    '"""The printed transcript: one column per kind, headings then marks."""\n'
    "\n"
    "from scheme import KINDS, LABELS, WIDTHS\n"
    "\n"
    "# Which way each column sits. Marks read right-aligned; a kind with no entry falls back\n"
    "# to left, which is not an error and not what you wanted either.\n"
    "ALIGN = {\n"
    '    "quiz": "right",\n'
    '    "essay": "right",\n'
    '    "exam": "right",\n'
    '    "practical": "right",\n'
    "}\n"
    "\n"
    "# How many decimals the mark is shown with. A kind with no entry shows none, which\n"
    "# quietly hides the part of the mark a student turns up to argue about.\n"
    "DECIMALS = {\n"
    '    "quiz": 0,\n'
    '    "essay": 1,\n'
    '    "exam": 0,\n'
    '    "practical": 2,\n'
    "}\n"
    "\n"
    "\n"
    "def _cell(kind, text):\n"
    "    width = WIDTHS[kind]\n"
    '    return text.rjust(width) if ALIGN.get(kind, "left") == "right" else text.ljust(width)\n'
    "\n"
    "\n"
    "def _mark(kind, value):\n"
    '    return f"{value:.{DECIMALS.get(kind, 0)}f}"\n'
    "\n"
    "\n"
    "def header():\n"
    '    return " ".join(_cell(kind, LABELS[kind]) for kind in KINDS)\n'
    "\n"
    "\n"
    "def row(marks):\n"
    '    return " ".join(_cell(kind, _mark(kind, marks.get(kind, 0.0))) for kind in KINDS)\n'
    "\n"
    "\n"
    "def render(marks):\n"
    '    return "\\n".join([header(), row(marks)])\n'
)

_G5 = (
    '"""The registry feed. One field per kind, in scheme order."""\n'
    "\n"
    "from scheme import KINDS\n"
    "\n"
    "# The code each kind goes out under. The registry accepts exactly two upper-case\n"
    "# letters and bounces the whole row otherwise, so a kind with no entry here -- which\n"
    "# goes out under its own long name -- takes the rest of the row down with it.\n"
    "CODES = {\n"
    '    "quiz": "QZ",\n'
    '    "essay": "ES",\n'
    '    "exam": "EX",\n'
    '    "practical": "PR",\n'
    "}\n"
    "\n"
    "\n"
    "def code_for(kind):\n"
    "    return CODES.get(kind, kind)\n"
    "\n"
    "\n"
    "def header():\n"
    '    return ",".join(code_for(kind) for kind in KINDS)\n'
    "\n"
    "\n"
    "def line(marks):\n"
    '    """Only the kinds the student sat, in scheme order."""\n'
    '    return ",".join(\n'
    '        f"{code_for(kind)}={marks[kind]:g}" for kind in KINDS if kind in marks\n'
    "    )\n"
    "\n"
    "\n"
    "def feed(rows):\n"
    '    return "\\n".join([header(), *(line(row) for row in rows)])\n'
)

GOLDEN_FILES = (
    ("scheme.py", _G0),
    ("weights.py", _G1),
    ("rounding.py", _G2),
    ("passfail.py", _G3),
    ("transcript.py", _G4),
    ("export.py", _G5),
)

GOLDEN = "\n\n".join(
    f"```python\n# path: {path}\n{body.rstrip()}\n```" for path, body in GOLDEN_FILES
)

_REQUEST = (
    "Here is gradekit, our marking toolkit. The six files above are the whole of it, and it\n"
    "works today.\n"
    "\n"
    "WHAT I NEED\n"
    "-----------\n"
    "The board has approved a fourth assessment kind, `practical`. It comes last, after the\n"
    "exam, everywhere the other three appear.\n"
    "\n"
    "It has to work everywhere they do:\n"
    "\n"
    "* it carries 25 points of weight, alongside the points the other three carry, and the\n"
    "  final mark normalises over it like everything else;\n"
    "* a practical mark is an average over four stations, so it always lands on a quarter --\n"
    "  a raw practical score is recorded snapped to the NEAREST QUARTER MARK, the way an\n"
    "  essay is snapped to the nearest half;\n"
    "* on the transcript it is headed `Practical`, in a column 10 wide, sitting the way the\n"
    "  other mark columns sit, and shown to TWO decimals -- the quarters are the whole point\n"
    "  of it;\n"
    "* its bar is 60, higher than anything else, and it is a HURDLE: failing the practical\n"
    "  sinks the course on its own, the way failing the exam does;\n"
    "* in the registry feed it goes out under the code `PR`.\n"
    "\n"
    "Everything gradekit already does must keep working exactly as it does now.\n"
    "\n"
    "Answer with the complete contents of every file you changed. Each block must be that\n"
    "file's COMPLETE current contents — every function and constant it has now, including\n"
    "the ones you did not touch. A file reduced to the part you changed is not a shorter\n"
    "answer, it is a broken file. Put each one in its own\n"
    "```python block whose FIRST LINE is a comment naming the file, like:\n"
    "\n"
    "```python\n"
    "# path: scheme.py\n"
    "...the whole file...\n"
    "```\n"
    "\n"
    "Files you did not change do not need to be included."
)

# The FULL tree goes in the prompt: the two arms that only ever see the
# prompt would otherwise be asked to change code they were never shown.
GOAL = file_block(STARTING_FILES) + "\n\n" + _REQUEST

CHANGE = [
    (
        "practical joins the scheme, last",
        "_probe",
        "kinds",
        [],
        ["quiz", "essay", "exam", "practical"],
    ),
    (
        "the final mark counts the practical",
        "_probe",
        "total",
        [{"quiz": 80, "essay": 60.5, "exam": 70, "practical": 81.25}],
        71.57,
    ),
    ("the practical weight is 25", "_probe", "weight", ["practical"], 25),
    (
        "a practical-only student has a final mark at all",
        "_probe",
        "total",
        [{"practical": 81.25}],
        81.25,
    ),
    (
        "a raw practical mark snaps to the nearest quarter",
        "_probe",
        "rounded",
        ["practical", 81.3],
        81.25,
    ),
    ("practical rounding snaps both ways", "_probe", "quarter_rounding_discriminates", [], True),
    (
        "a raw practical mark snaps up to a quarter a half would miss",
        "_probe",
        "rounded",
        ["practical", 81.2],
        81.25,
    ),
    (
        "the practical bar is above the others",
        "_probe",
        "verdicts",
        [{"exam": 55, "practical": 55}],
        {"exam": "pass", "practical": "fail"},
    ),
    ("the practical bar both passes and fails", "_probe", "practical_bar_discriminates", [], True),
    (
        "a failed practical sinks the course on its own",
        "_probe",
        "hurdles",
        [{"quiz": 90, "essay": 90, "exam": 90, "practical": 55}],
        False,
    ),
    (
        "the transcript heading gains Practical",
        "_probe",
        "heading",
        [],
        "  Quiz   Essay   Exam  Practical",
    ),
    (
        "the practical mark is shown to two decimals in its own column",
        "_probe",
        "sheet",
        [{"quiz": 80, "essay": 60.5, "exam": 70, "practical": 81.25}],
        "  Quiz   Essay   Exam  Practical\n    80    60.5     70      81.25",
    ),
    (
        "a whole practical mark still shows its decimals",
        "_probe",
        "sheet",
        [{"quiz": 80, "essay": 60.5, "exam": 70, "practical": 75}],
        "  Quiz   Essay   Exam  Practical\n    80    60.5     70      75.00",
    ),
    ("the feed header gains the practical code", "_probe", "feed_header", [], "QZ,ES,EX,PR"),
    (
        "the practical goes out last under its code",
        "_probe",
        "feed_line",
        [{"quiz": 80, "essay": 60.5, "exam": 70, "practical": 81.25}],
        "QZ=80,ES=60.5,EX=70,PR=81.25",
    ),
    (
        "the feed keeps kind order across rows",
        "_probe",
        "feed_of",
        [[{"quiz": 80, "practical": 75}, {"exam": 70, "practical": 60}]],
        "QZ,ES,EX,PR\nQZ=80,PR=75\nEX=70,PR=60",
    ),
]

REGRESSION = [
    ("the first kind is still the quiz", "_probe", "kind_at", [0], "quiz"),
    ("the exam is still third", "_probe", "kind_at", [2], "exam"),
    ("the exam still weighs 50", "_probe", "weight", ["exam"], 50),
    (
        "a course without a practical still totals the old way",
        "_probe",
        "total",
        [{"quiz": 80, "essay": 60, "exam": 70}],
        69,
    ),
    ("a quiz mark still rounds to the nearest whole", "_probe", "rounded", ["quiz", 7.5], 8),
    ("an essay mark still rounds to the nearest half", "_probe", "rounded", ["essay", 7.3], 7.5),
    (
        "an exam mark still rounds down below the halfway point",
        "_probe",
        "rounded",
        ["exam", 7.49],
        7,
    ),
    (
        "the old bars still fire",
        "_probe",
        "verdicts",
        [{"quiz": 39, "essay": 50, "exam": 49.9}],
        {"quiz": "fail", "essay": "pass", "exam": "fail"},
    ),
    ("a bad quiz is still carried", "_probe", "hurdles", [{"quiz": 10, "exam": 80}], True),
    ("a bad exam still sinks the course", "_probe", "hurdles", [{"exam": 49}], False),
    (
        "the essay column still shows one decimal and the exam none",
        "_probe",
        "sheet",
        [{"quiz": 80, "essay": 60.5, "exam": 70, "practical": 81.25}],
        "  Quiz   Essay   Exam  Practical\n    80    60.5     70      81.25",
    ),
    (
        "a feed row for the old kinds is unchanged",
        "_probe",
        "feed_line",
        [{"quiz": 80, "essay": 60, "exam": 70}],
        "QZ=80,ES=60,EX=70",
    ),
    ("a feed row for one old kind is unchanged", "_probe", "feed_line", [{"essay": 60}], "ES=60"),
]

TASK = build_repo_task(
    task_id="gradekit_kind",
    goal=GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)

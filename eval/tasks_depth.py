"""A depth-graded code goal — and the record of a design premise that measurement killed.

WHAT THIS GOAL IS FOR. The nine goals in `tasks.py` cannot separate the arms: baseline
scores 1.00 on seven of them, so on most of the suite there is no headroom to win.
This goal was built to create that headroom, on a mechanism that had been measured
rather than assumed.

THE PREMISE. Asked for 24 functions with `max_tokens=8192`, gpt-4o-mini emits ~2,500
tokens and declares all 24 complete — it does not truncate, it DILUTES. That was
measured directly, same function, same spec, temperature 0:

    written alone           1,225 characters   5 of 5 stated requirements met
    written as 1 of 24        359 characters   2 of 5 stated requirements met

Alone it validated its input, expanded tabs and normalised unicode dashes. Among
twenty-three siblings it dropped all three and kept the happy path. The inference:
grade the stated edge-case requirements instead of the happy path, at a scale where
dilution bites, and baseline should fall well below 1.00.

WHAT ACTUALLY HAPPENED. It does not. Baseline (gpt-4o-mini, temperature 0, k=3):

    12 functions, 42 cases    0.968
    24 functions, 87 cases    0.963

Doubling the load moved the score by 0.005. The dilution is real as a WRITING effect
and does not become a SCORING effect: at 24 functions the model spends ~430 characters
each, and for utilities of this kind 430 characters is still correct code. The earlier
2-of-5 figure came from a hand-graded rubric on a deliberately fiddly spec, not from a
program that has to run. Requirement count is not the variable; per-unit difficulty is.
Scaling the count of individually-easy functions cannot open headroom no matter how
many are added, because a well-known utility is the single most-represented pattern in
the model's training data.

So this goal does NOT solve the headroom problem, and the number above is the reason
to stop pulling on this thread rather than escalate to 48 functions. It is kept
because it is still a sound instrument: harder than the existing suite, graded on
validation and boundaries rather than the happy path, with a verified ceiling
(a correct implementation scores exactly 1.000) and a verified floor (an empty module
scores exactly 0.000, a one-function stub 1/87).

Kept OUT of `EVAL_SUITE` deliberately: the nine-goal comparison has five historical
artifacts, and silently changing what "the suite" means would make every published
number incomparable with the ones before it. Run with `--suite depth`.

Run the orchestration arm here with `--synthesis assemble`. The product IS the module,
and a summarising synthesis would funnel twenty-four separately-built functions back
through one bounded call — reintroducing exactly the compression the decomposition
paid to avoid.
"""

from __future__ import annotations

from eval.tasks import EvalTask

GUARDKIT_GOAL: str = """Implement a Python module providing the twenty-four functions below.

Every function must be COMPLETE: a docstring, full input validation, and correct
handling of every stated edge case. No stubs, no `pass`, no `...`, no "implementation
omitted", no helper left undefined. Each function must stand alone — do not rely on a
shared helper defined elsewhere in the module.

1.  `clamp(value, lo, hi)` -> the value confined to [lo, hi]. Raise `ValueError` if
    `lo > hi`. Accept ints and floats. Return the bound itself when the value is
    outside it. Raise `ValueError` if any argument is NaN.

2.  `parse_bool(text: str) -> bool` -> accept "true", "false", "yes", "no", "on",
    "off", "1", "0", case-insensitively and with surrounding whitespace stripped.
    Raise `ValueError` for anything else, including the empty string.

3.  `truncate(text: str, limit: int, suffix: str = "...") -> str` -> return `text`
    unchanged when it already fits in `limit` characters; otherwise cut it and append
    `suffix` so the RESULT is at most `limit` characters. Raise `ValueError` when
    `limit` is smaller than `len(suffix)`.

4.  `chunk(items: list, size: int) -> list[list]` -> split into consecutive lists of
    `size`, the last one shorter if it must be. Raise `ValueError` when `size < 1`.
    An empty input gives an empty list.

5.  `dedupe(items: list) -> list` -> keep the FIRST occurrence of each value and
    preserve that order. Raise `TypeError` when an item is unhashable.

6.  `normalise_spaces(text: str) -> str` -> collapse every run of whitespace,
    including tabs and newlines AND the non-breaking space U+00A0, into one ordinary
    space, then strip the ends.

7.  `parse_duration(text: str) -> int` -> read "1h30m", "45s", "2d", "90m" and return
    the total in SECONDS. Units are d, h, m, s. Raise `ValueError` on an empty string,
    an unknown unit, a missing number, or a negative value.

8.  `format_bytes(n: int) -> str` -> 1024-based, using the units B, KiB, MiB, GiB,
    TiB. Whole bytes carry no decimal ("512 B"); every larger unit carries exactly one
    ("1.5 KiB"). Raise `ValueError` when `n` is negative.

9.  `slug(text: str) -> str` -> lowercase; runs of spaces and underscores become one
    hyphen; every character that is not a lowercase letter, digit or hyphen is
    removed; consecutive hyphens collapse; leading and trailing hyphens are stripped.
    Raise `ValueError` when nothing survives.

10. `mask_secret(text: str, keep: int = 4) -> str` -> replace every character except
    the last `keep` with "*". Raise `ValueError` when `keep < 0`. When `keep` is at
    least the length of the text, mask the whole thing rather than revealing it.

11. `parse_range(text: str) -> tuple[int, int]` -> "3-7" gives (3, 7) and a bare "5"
    gives (5, 5). Raise `ValueError` when the ends are reversed, when either side is
    not an integer, or when the string is empty. Surrounding whitespace is allowed.

12. `pluralise(word: str, n: int) -> str` -> return `word` unchanged when `n == 1`.
    Otherwise: a word ending in a consonant followed by "y" becomes "ies"; a word
    ending in s, x, z, ch or sh takes "es"; anything else takes "s".

13. `title_case(text: str) -> str` -> capitalise each word, EXCEPT that "of", "the",
    "and", "in", "a" stay lowercase unless they are the first word. Collapse runs of
    spaces. Raise `ValueError` when the text has no words.

14. `roman(n: int) -> str` -> the Roman numeral for 1 to 3999, using the subtractive
    forms (4 is IV, 9 is IX, 40 is XL, 900 is CM). Raise `ValueError` outside that
    range, including for 0 and negatives.

15. `parse_csv_line(line: str) -> list[str]` -> split one CSV line on commas. A field
    wrapped in double quotes may contain commas, and a doubled quote inside it means
    one literal quote. The empty string gives `[""]`. Raise `ValueError` when a quote
    is never closed.

16. `hamming(a: str, b: str) -> int` -> how many positions differ. Raise `ValueError`
    when the lengths differ. Two empty strings give 0.

17. `ordinal(n: int) -> str` -> "1st", "2nd", "3rd", "4th" — and note 11, 12 and 13
    take "th" while 21 takes "st". Raise `ValueError` for a negative `n`.

18. `flatten(nested: list) -> list` -> flatten lists to ANY depth. A string is NOT
    flattened into characters. Raise `TypeError` when the argument itself is not a
    list. An empty list gives an empty list.

19. `word_wrap(text: str, width: int) -> list[str]` -> break into lines of at most
    `width` characters at spaces, never mid-word — unless a single word is longer than
    `width`, which is split hard. Raise `ValueError` when `width < 1`.

20. `parse_version(text: str) -> tuple[int, int, int]` -> "1.2.3" gives (1, 2, 3) and
    "1.2" gives (1, 2, 0). Raise `ValueError` on an empty string, a non-numeric part,
    or more than three parts.

21. `safe_div(a, b, default=None)` -> `a / b`. When `b` is 0, return `default` — but
    raise `ZeroDivisionError` when `default` is None. The result is always a float.

22. `strip_accents(text: str) -> str` -> "café" becomes "cafe" and "naïve" becomes
    "naive": decompose and drop the combining marks. Characters with no decomposition
    (CJK, Cyrillic, emoji) pass through unchanged. The empty string gives "".

23. `interval_merge(intervals: list) -> list` -> merge overlapping AND touching
    (start, end) pairs and return them sorted by start. Raise `ValueError` when any
    interval has start > end. An empty list gives an empty list.

24. `base_n(n: int, base: int) -> str` -> render a non-negative int in `base`, using
    the digits 0-9 then a-z. 0 gives "0". Raise `ValueError` when `base` is outside
    2 to 36, and when `n` is negative.

Reply with the complete module."""


# Hidden reference cases. Each function is checked on its STATED REQUIREMENTS —
# the validation, the boundaries, the unicode — not on the happy path, because the
# happy path is what a diluted implementation still gets right.
GUARDKIT_REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

MISSING = object()


def value(fn, *args):
    """The solution's return value, or a sentinel that equals no expected value."""
    try:
        return call_solution(fn, *args)
    except Exception:
        return MISSING


def rejects(fn, good, bad):
    """True when the function WORKS on `good` and REJECTS `bad`.

    The control call is not decoration. call_solution raises RuntimeError both when
    the solution's function raised and when it does not exist at all, so a bare
    "did it raise?" check hands a free point to a module that defined nothing —
    across twelve functions that is a third of this goal's score, awarded for
    producing no code. Requiring the function to first succeed on valid input makes
    the point mean "it validates", which is the requirement being measured.
    """
    try:
        call_solution(fn, *good)
    except Exception:
        return False
    try:
        call_solution(fn, *bad)
    except Exception:
        return True
    return False


CASES = [
    # clamp
    ("clamp", lambda: value("clamp", 5, 1, 10) == 5),
    ("clamp", lambda: value("clamp", -3, 1, 10) == 1),
    ("clamp", lambda: value("clamp", 99, 1, 10) == 10),
    ("clamp", lambda: rejects("clamp", (5, 1, 10), (5, 10, 1))),
    ("clamp", lambda: rejects("clamp", (5, 1, 10), (float("nan"), 1, 10))),
    # parse_bool
    ("parse_bool", lambda: value("parse_bool", "  YES ") is True),
    ("parse_bool", lambda: value("parse_bool", "off") is False),
    ("parse_bool", lambda: value("parse_bool", "1") is True),
    ("parse_bool", lambda: rejects("parse_bool", ("true",), ("",))),
    ("parse_bool", lambda: rejects("parse_bool", ("true",), ("maybe",))),
    # truncate
    ("truncate", lambda: value("truncate", "hello", 10) == "hello"),
    ("truncate", lambda: len(value("truncate", "abcdefghij", 5) or "") == 5),
    ("truncate", lambda: rejects("truncate", ("hello", 10), ("abcdefghij", 2))),
    # chunk
    ("chunk", lambda: value("chunk", [1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]),
    ("chunk", lambda: value("chunk", [], 3) == []),
    ("chunk", lambda: rejects("chunk", ([1, 2], 1), ([1, 2], 0))),
    # dedupe
    ("dedupe", lambda: value("dedupe", [3, 1, 3, 2, 1]) == [3, 1, 2]),
    ("dedupe", lambda: rejects("dedupe", ([1, 1],), ([[1], [2]],))),
    # normalise_spaces
    ("normalise_spaces", lambda: value("normalise_spaces", "  a\\t\\tb\\n\\nc  ") == "a b c"),
    ("normalise_spaces", lambda: value("normalise_spaces", "a\\u00a0\\u00a0b") == "a b"),
    # parse_duration
    ("parse_duration", lambda: value("parse_duration", "1h30m") == 5400),
    ("parse_duration", lambda: value("parse_duration", "45s") == 45),
    ("parse_duration", lambda: value("parse_duration", "2d") == 172800),
    ("parse_duration", lambda: rejects("parse_duration", ("45s",), ("",))),
    ("parse_duration", lambda: rejects("parse_duration", ("45s",), ("10x",))),
    # format_bytes
    ("format_bytes", lambda: value("format_bytes", 512) == "512 B"),
    ("format_bytes", lambda: value("format_bytes", 1536) == "1.5 KiB"),
    ("format_bytes", lambda: rejects("format_bytes", (512,), (-1,))),
    # slug
    ("slug", lambda: value("slug", "Hello   World_again!") == "hello-world-again"),
    ("slug", lambda: rejects("slug", ("hi",), ("!!!",))),
    # mask_secret
    ("mask_secret", lambda: value("mask_secret", "abcdefgh", 3) == "*****fgh"),
    ("mask_secret", lambda: value("mask_secret", "abc", 5) == "***"),
    ("mask_secret", lambda: rejects("mask_secret", ("abcdefgh", 3), ("abc", -1))),
    # parse_range
    ("parse_range", lambda: list(value("parse_range", "3-7") or []) == [3, 7]),
    ("parse_range", lambda: list(value("parse_range", " 5 ") or []) == [5, 5]),
    ("parse_range", lambda: rejects("parse_range", ("3-7",), ("9-2",))),
    ("parse_range", lambda: rejects("parse_range", ("3-7",), ("a-b",))),
    # pluralise
    ("pluralise", lambda: value("pluralise", "box", 1) == "box"),
    ("pluralise", lambda: value("pluralise", "box", 2) == "boxes"),
    ("pluralise", lambda: value("pluralise", "city", 3) == "cities"),
    ("pluralise", lambda: value("pluralise", "day", 3) == "days"),
    ("pluralise", lambda: value("pluralise", "dish", 2) == "dishes"),
    # title_case
    ("title_case", lambda: value("title_case", "the lord of the rings")
     == "The Lord of the Rings"),
    ("title_case", lambda: value("title_case", "a  tale   of  two") == "A Tale of Two"),
    ("title_case", lambda: rejects("title_case", ("hi",), ("   ",))),
    # roman
    ("roman", lambda: value("roman", 4) == "IV"),
    ("roman", lambda: value("roman", 1994) == "MCMXCIV"),
    ("roman", lambda: value("roman", 3999) == "MMMCMXCIX"),
    ("roman", lambda: rejects("roman", (4,), (0,))),
    ("roman", lambda: rejects("roman", (4,), (4000,))),
    # parse_csv_line
    ("parse_csv_line", lambda: value("parse_csv_line", 'a,"b,c",d') == ["a", "b,c", "d"]),
    ("parse_csv_line", lambda: value("parse_csv_line", '"say ""hi"""') == ['say "hi"']),
    ("parse_csv_line", lambda: value("parse_csv_line", "") == [""]),
    ("parse_csv_line", lambda: rejects("parse_csv_line", ("a,b",), ('a,"b',))),
    # hamming
    ("hamming", lambda: value("hamming", "karolin", "kathrin") == 3),
    ("hamming", lambda: value("hamming", "", "") == 0),
    ("hamming", lambda: rejects("hamming", ("ab", "cd"), ("abc", "ab"))),
    # ordinal
    ("ordinal", lambda: value("ordinal", 2) == "2nd"),
    ("ordinal", lambda: value("ordinal", 11) == "11th"),
    ("ordinal", lambda: value("ordinal", 13) == "13th"),
    ("ordinal", lambda: value("ordinal", 21) == "21st"),
    ("ordinal", lambda: rejects("ordinal", (2,), (-1,))),
    # flatten
    ("flatten", lambda: value("flatten", [1, [2, [3, [4]]]]) == [1, 2, 3, 4]),
    ("flatten", lambda: value("flatten", ["ab", ["cd"]]) == ["ab", "cd"]),
    ("flatten", lambda: value("flatten", []) == []),
    ("flatten", lambda: rejects("flatten", ([1],), ("nope",))),
    # word_wrap
    ("word_wrap", lambda: value("word_wrap", "the quick brown fox", 10)
     == ["the quick", "brown fox"]),
    ("word_wrap", lambda: value("word_wrap", "supercalifragilistic", 6)
     == ["superc", "alifra", "gilist", "ic"]),
    ("word_wrap", lambda: rejects("word_wrap", ("hi", 5), ("hi", 0))),
    # parse_version
    ("parse_version", lambda: list(value("parse_version", "1.2.3") or []) == [1, 2, 3]),
    ("parse_version", lambda: list(value("parse_version", "1.2") or []) == [1, 2, 0]),
    ("parse_version", lambda: rejects("parse_version", ("1.2.3",), ("1.2.3.4",))),
    ("parse_version", lambda: rejects("parse_version", ("1.2.3",), ("1.x.3",))),
    # safe_div
    ("safe_div", lambda: value("safe_div", 7, 2) == 3.5),
    ("safe_div", lambda: value("safe_div", 4, 2) == 2.0),
    ("safe_div", lambda: value("safe_div", 1, 0, 99) == 99),
    ("safe_div", lambda: rejects("safe_div", (1, 2), (1, 0))),
    # strip_accents
    ("strip_accents", lambda: value("strip_accents", "caf\\u00e9") == "cafe"),
    ("strip_accents", lambda: value("strip_accents", "na\\u00efve") == "naive"),
    ("strip_accents", lambda: value("strip_accents", "\\u4e2d\\u6587") == "\\u4e2d\\u6587"),
    ("strip_accents", lambda: value("strip_accents", "") == ""),
    # interval_merge
    ("interval_merge", lambda: [list(p) for p in (value(
        "interval_merge", [[1, 3], [2, 6], [8, 10]]) or [])] == [[1, 6], [8, 10]]),
    ("interval_merge", lambda: [list(p) for p in (value(
        "interval_merge", [[1, 2], [2, 3]]) or [])] == [[1, 3]]),
    ("interval_merge", lambda: value("interval_merge", []) == []),
    ("interval_merge", lambda: rejects("interval_merge", ([[1, 2]],), ([[5, 1]],))),
    # base_n
    ("base_n", lambda: value("base_n", 255, 16) == "ff"),
    ("base_n", lambda: value("base_n", 5, 2) == "101"),
    ("base_n", lambda: value("base_n", 0, 8) == "0"),
    ("base_n", lambda: rejects("base_n", (255, 16), (255, 37))),
    ("base_n", lambda: rejects("base_n", (255, 16), (-1, 16))),
]


def main() -> None:
    total = len(CASES)
    passed = 0
    for _name, check in CASES:
        try:
            if check():
                passed += 1
        except Exception:
            pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


main()
sys.exit(0)
'''


# --------------------------------------------------------------------------- #
# The `resolve` goal — the first shape in this project with MEASURED headroom AND a
# measured decomposition win.
#
# Six hypotheses about what defeats a single temperature-0 pass were probed against
# this harness. Four died. Diagnosis-is-harder-than-writing scored baseline 1.000 and
# is simply false at this model and scale; cross-cutting invariants 0.933; training-
# data gravity 0.860; constraint satisfaction 0.822. Two survived, and this is the
# cleaner one — the other (tracing a stack machine) is confounded, because baseline
# fails it partly by running out of output tokens mid-trace rather than by reasoning
# wrongly.
#
# WHAT BREAKS. Seven precedence rules that genuinely overlap, so most inputs are
# matched by two or more rules and the answer depends on which one WINS. The model
# reproduces each rule correctly in isolation and composes them wrongly. Concretely,
# and this is worth stating because it is not subtle: 19 of 28 baseline runs produced
# code that gets the WORKED EXAMPLE PRINTED IN THE GOAL wrong —
# resolve(["allow:a/b/**", "deny:*/*/*"], "a/b/c") returned "allow" instead of "deny".
# None of them factored the pattern matcher into its own function; matching is inlined
# into `resolve`, so when matching is wrong the error is spread through the whole
# function rather than sitting in one place.
#
# WHAT DECOMPOSITION DOES, measured against the same runner, same model, temperature
# 0, sub-prompts copied character-for-character out of this goal (only the interface
# each part must expose is added), assembled by a third call:
#
#     n=28 paired          baseline    decomposed
#     mean                    0.565         0.790     (+0.225, paired t=4.73, p<0.001)
#     wins                                  22/28
#     BEST case               0.938         0.938     <- identical
#     fell below 0.50         19/28          0/28
#
# Read that table carefully, because the mean overstates it. Decomposition does not
# raise the ceiling — the best single pass matches the best decomposed run exactly.
# It removes the failure mode. Asking for the matcher as its own deliverable is what
# makes the matcher correct; the gain is isolation of a concern, not more capability.
#
# One honest caveat about absolute numbers: between two batches run hours apart,
# BOTH arms moved together (baseline 0.706 -> 0.509, decomposed 0.865 -> 0.760).
# Only within-batch comparisons mean anything here, and the advantage held in both
# (+0.159 and +0.251).
#
# STILL UNMEASURED, and the reason no claim about Volante itself is made above: this
# is a MANUAL decomposition, standing in for what a planner would produce. Whether
# Volante's own Supervisor emits this split is a separate question, and the only
# thing that answers it is the real three-arm run.

RESOLVE_GOAL: str = """\
Implement a Python function `resolve(rules: list[str], path: str) -> str` in solution.py.

Given an access-control rule list, it returns the EFFECT of the single rule that wins for `path`,
written exactly as that rule writes it: one of "allow", "deny", "allow!", "deny!".

INPUT FORMAT.
`path` is a non-empty string of one or more segments joined by "/", e.g. "a/b/c". Every path
segment is a plain literal (lowercase letters and digits); a path never contains a wildcard,
and never has a leading or trailing "/".

Each element of `rules` is a string "EFFECT:PATTERN". EFFECT is exactly one of
"allow", "deny", "allow!", "deny!". PATTERN is one or more segments joined by "/", where each
segment is either a literal, or "*", or "**". "**" may appear only as the LAST segment of a
pattern (a pattern may be just "**"). Rules are given in no particular order, and their order
in the list must NOT affect the answer.

MATCHING.
- A literal pattern segment matches exactly that path segment.
- "*" matches exactly ONE path segment, any literal. It never matches across a "/", and it
  never matches zero segments.
- "**" as the final pattern segment matches ONE OR MORE remaining path segments. It never
  matches zero segments: the pattern "a/**" matches "a/b" and "a/b/c" but does NOT match "a".
- A pattern with no "**" matches only paths with exactly the same number of segments.

PRECEDENCE. Several rules will match at once. Apply these seven rules, in this order, to decide
which one wins.

R1. Rules whose pattern does not match `path` are discarded and play no further part.
R2. If no rule matches, return "deny" (deny by default).
R3. Any matching pattern that contains NO "**" beats EVERY matching pattern that contains "**",
    whatever their effects and whatever their literal counts. Discard the "**" patterns whenever
    at least one non-"**" pattern matched.
R4. Among the rules that survive R3, the winner is the one with the most LITERAL segments in its
    pattern ("*" and "**" segments are not literals). Discard the rest.
R5. If two or more rules survive R4 and disagree, "deny" beats "allow". If the tie is between the
    plain and the "!" form of the same effect, the "!" form is the winner.
R6. EXCEPTION to R5: at such a tie, "allow!" beats "deny". R6 only ever applies at a tie left by
    R4 — an "allow!" rule that lost on R3 or R4 stays lost, and a plain "deny" that is strictly
    more specific than an "allow!" still wins.
R7. EXCEPTION to R6: at such a tie, "deny!" beats "allow!".

The return value is the winning rule's effect string verbatim, "!" included when the winning rule
was written with one.

Worked example: resolve(["allow:a/b/**", "deny:*/*/*"], "a/b/c") returns "deny", because both
patterns match but "*/*/*" has no "**" and therefore wins under R3, even though "a/b/**" has more
literal segments.

Reply with the complete solution.py. Also add a pytest test module (functions named test_*)
covering the precedence rules, and a short README."""


RESOLVE_REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

# 48 cases, TWELVE per possible answer. The balance is the point: with four effects and
# a lopsided case list, `return "deny"` scored 0.423 — 42% of the goal for one line that
# reads nothing. Every constant answer now scores exactly 0.25, so the score measures
# resolution rather than a guess at the most common outcome.
#
# Every case past the first block has AT LEAST TWO matching rules, so it is decided by
# precedence rather than by matching. That is where a single pass fails: it reproduces
# each rule correctly in isolation and then composes them wrongly.
CASES = [
    (["allow:a/b/c"], "a/b/c", "allow"),
    ([], "a", "deny"),
    (["allow:x/**"], "a/b", "deny"),
    (["allow:a/**"], "a", "deny"),
    (["allow:a/**"], "a/b", "allow"),
    (["allow:a/*"], "a/b/c", "deny"),
    (["allow:a/b/**", "deny:*/*/*"], "a/b/c", "deny"),
    (["deny:a/b/c/**", "allow:a/b/c/*"], "a/b/c/d", "allow"),
    (["deny:a/b/**", "allow:a/b/c"], "a/b/c", "allow"),
    (["allow:**", "deny:a/**", "allow!:a/b/c"], "a/b/c", "allow!"),
    (["deny!:a/**", "allow:a/b/c"], "a/b/c", "allow"),
    (["allow:a/**", "deny:a/b/**"], "a/b/c", "deny"),
    (["deny:a/**", "allow:a/b/**"], "a/b/c/d", "allow"),
    (["allow:a/*/**", "deny:a/b/**"], "a/b/c/d", "deny"),
    (["allow:**", "deny:a/*/c/**"], "a/b/c/d/e", "deny"),
    (["allow:*/**", "deny:a/b/**"], "a/b", "allow"),
    (["allow!:a/*/*", "deny:a/b/c"], "a/b/c", "deny"),
    (["allow:a/b/*", "deny:a/*/c"], "a/b/c", "deny"),
    (["deny:a/*/c", "allow!:a/b/*"], "a/b/c", "allow!"),
    (["allow!:a/b/*", "deny!:a/*/c"], "a/b/c", "deny!"),
    (["allow:a/b/*", "deny:a/*/c", "allow!:*/b/c"], "a/b/c", "allow!"),
    (["allow:a/b/*", "deny:a/*/c", "allow!:*/b/c", "deny!:a/b/*"], "a/b/c", "deny!"),
    (["allow:a/b/*", "allow:a/*/c"], "a/b/c", "allow"),
    (["deny!:a/b/**", "allow!:a/*/**"], "a/b/c", "deny!"),
    (["deny:*/*/*", "allow:a/b/**"], "a/b/c", "deny"),
    (["allow!:a/b/*", "deny:a/*/c"], "a/b/c", "allow!"),
    (["allow!:*/*/c", "allow:*/b/c", "deny!:a/b/c/*"], "x/b/c", "allow"),
    (["allow!:*/*", "allow!:a/b/c/d", "allow:a/*"], "a/b", "allow"),
    (["allow!:a/b/c/*", "allow:a/b/c/d", "deny:a/**"], "a/b/c/d", "allow"),
    (["allow:a/b/*", "deny:*/*/*", "deny:a/*"], "a/b/c", "allow"),
    (["allow!:*/*/*", "allow:a/b/*", "deny!:a/b/c/**", "deny:a/b/c"], "a/b/c", "deny"),
    (["allow!:a/*/c", "allow!:a/b/c/d", "deny!:*/b/c", "deny:*/*/c"], "a/y/c", "allow!"),
    (["allow!:*/*/c", "allow:*/b/c", "deny:*/*/*"], "a/y/c", "allow!"),
    (["allow!:*/b/*", "allow:a/b/*", "deny:*/*/c"], "x/b/c", "allow!"),
    (["allow!:*/b/*", "allow!:a/b/*", "allow!:a/b/c/*", "allow:a/b/c/*"], "a/b/c", "allow!"),
    (["allow!:a/**", "allow!:a/*/*", "allow:*/*/*"], "a/y/c", "allow!"),
    (["allow!:*/b/c", "allow!:a/*/c", "allow:*/*/*"], "a/b/c", "allow!"),
    (["allow!:**", "allow!:*/*", "allow!:a/b/c", "allow:*/*"], "a/b", "allow!"),
    (["allow!:a/*/*", "allow!:a/b/**", "allow:a/b/*", "deny!:*/*/*"], "a/y/c", "allow!"),
    (["deny!:a/b/*", "deny:*/*/c", "deny:*/b/*", "deny:a/*"], "a/b/c", "deny!"),
    (["allow!:*/*/*", "allow:*/b/*", "allow:a/b/*", "deny!:*/b/c"], "a/b/c", "deny!"),
    (["allow:a/b/c/d", "deny!:*/*/c", "deny:a/*/*"], "a/y/c", "deny!"),
    (["allow:*/*/c", "deny!:*/*", "deny!:*/b/c", "deny:*/**"], "x/b/c", "deny!"),
    (["allow!:**", "allow!:a/*/*", "deny!:a/b/*"], "a/b/c", "deny!"),
    (["deny!:a/b/c/d", "deny:**", "deny:a/b/c/d"], "a/b/c/d", "deny!"),
    (["deny!:*/b/c", "deny!:a/*/c"], "a/b/c", "deny!"),
    (["allow:a/b/c/*", "deny!:*/*/c", "deny:*/*/*", "deny:a/*/c"], "x/b/c", "deny!"),
    (["allow!:*/b/*", "allow:a/**", "deny!:*/b/c"], "a/b/c", "deny!"),
]


def main() -> None:
    total = len(CASES)
    passed = 0
    for rules, path, expected in CASES:
        try:
            if call_solution("resolve", rules, path) == expected:
                passed += 1
        except Exception:
            pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


main()
sys.exit(0)
'''

# Its own suite. Adding it to EVAL_SUITE would silently redefine what every
# historical comparison measured.
DEPTH_SUITE: list[EvalTask] = [
    EvalTask("guardkit", GUARDKIT_GOAL, GUARDKIT_REFERENCE_TEST),
    EvalTask("resolve", RESOLVE_GOAL, RESOLVE_REFERENCE_TEST),
]

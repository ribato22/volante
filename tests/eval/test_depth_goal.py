"""The depth goal is only an instrument if its ceiling is reachable and its floor is real.

Two failure modes make an eval goal silently worthless, and neither shows up in a
score: a runner nobody can satisfy (every arm loses to the grader, not to the task),
and a runner that pays for nothing (an empty answer collects points, so every arm
floats on a free baseline). The second nearly shipped here — `call_solution` raises
RuntimeError both when the solution's function raised AND when it does not exist, so
a plain "did it raise?" check awarded a module that defined nothing 12 of 42 points.

These tests pin both ends against a known-correct implementation, so a later edit to
the goal text or the cases cannot quietly break the instrument.
"""

from __future__ import annotations

from eval.harness import score_code
from eval.tasks_depth import DEPTH_SUITE, GUARDKIT_REFERENCE_TEST

GOLDEN = """
import math, re
from collections.abc import Hashable

def clamp(value, lo, hi):
    for v in (value, lo, hi):
        if isinstance(v, float) and math.isnan(v):
            raise ValueError("NaN")
    if lo > hi:
        raise ValueError("lo > hi")
    return lo if value < lo else hi if value > hi else value

_BOOLS = {"true": True, "yes": True, "on": True, "1": True,
          "false": False, "no": False, "off": False, "0": False}

def parse_bool(text):
    key = text.strip().lower()
    if key not in _BOOLS:
        raise ValueError(f"not a boolean: {text!r}")
    return _BOOLS[key]

def truncate(text, limit, suffix="..."):
    if limit < len(suffix):
        raise ValueError("limit smaller than suffix")
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix

def chunk(items, size):
    if size < 1:
        raise ValueError("size must be >= 1")
    return [items[i : i + size] for i in range(0, len(items), size)]

def dedupe(items):
    seen = set()
    out = []
    for item in items:
        if not isinstance(item, Hashable):
            raise TypeError("unhashable")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

def normalise_spaces(text):
    return re.sub(r"[\\s\\u00a0]+", " ", text).strip()

_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

def parse_duration(text):
    text = text.strip()
    if not text:
        raise ValueError("empty")
    parts = re.findall(r"(\\d+)([dhms])", text)
    if not parts or "".join(a + b for a, b in parts) != text:
        raise ValueError(f"bad duration: {text!r}")
    return sum(int(n) * _UNITS[u] for n, u in parts)

def format_bytes(n):
    if n < 0:
        raise ValueError("negative")
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        size /= 1024.0
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
    raise ValueError("unreachable")

def slug(text):
    s = re.sub(r"[\\s_]+", "-", text.lower())
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError("empty slug")
    return s

def mask_secret(text, keep=4):
    if keep < 0:
        raise ValueError("keep must be >= 0")
    if keep >= len(text):
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]

def parse_range(text):
    text = text.strip()
    if not text:
        raise ValueError("empty")
    if "-" in text[1:]:
        i = text.index("-", 1)
        lo, hi = text[:i].strip(), text[i + 1 :].strip()
    else:
        lo = hi = text
    try:
        lo_i, hi_i = int(lo), int(hi)
    except ValueError:
        raise ValueError(f"bad range: {text!r}")
    if lo_i > hi_i:
        raise ValueError("reversed")
    return (lo_i, hi_i)

def pluralise(word, n):
    if n == 1:
        return word
    if len(word) > 1 and word[-1] == "y" and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"

_SMALL = {"of", "the", "and", "in", "a"}

def title_case(text):
    words = text.split()
    if not words:
        raise ValueError("no words")
    return " ".join(
        w.capitalize() if i == 0 or w.lower() not in _SMALL else w.lower()
        for i, w in enumerate(words)
    )

_ROMAN = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
          (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]

def roman(n):
    if not 1 <= n <= 3999:
        raise ValueError("out of range")
    out = []
    for v, s in _ROMAN:
        while n >= v:
            out.append(s); n -= v
    return "".join(out)

def parse_csv_line(line):
    fields, cur, i, n = [], [], 0, len(line)
    while i < n:
        if line[i] == '"':
            i += 1
            while True:
                if i >= n:
                    raise ValueError("unterminated quote")
                if line[i] == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        cur.append('"'); i += 2; continue
                    i += 1; break
                cur.append(line[i]); i += 1
        elif line[i] == ",":
            fields.append("".join(cur)); cur = []; i += 1
        else:
            cur.append(line[i]); i += 1
    fields.append("".join(cur))
    return fields

def hamming(a, b):
    if len(a) != len(b):
        raise ValueError("unequal length")
    return sum(1 for x, y in zip(a, b) if x != y)

def ordinal(n):
    if n < 0:
        raise ValueError("negative")
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

def flatten(nested):
    if not isinstance(nested, list):
        raise TypeError("not a list")
    out = []
    for item in nested:
        if isinstance(item, list):
            out.extend(flatten(item))
        else:
            out.append(item)
    return out

def word_wrap(text, width):
    if width < 1:
        raise ValueError("width must be >= 1")
    lines, cur = [], ""
    for word in text.split():
        while len(word) > width:
            if cur:
                lines.append(cur); cur = ""
            lines.append(word[:width]); word = word[width:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur); cur = word
    if cur:
        lines.append(cur)
    return lines

def parse_version(text):
    text = text.strip()
    if not text:
        raise ValueError("empty")
    parts = text.split(".")
    if len(parts) > 3:
        raise ValueError("too many parts")
    nums = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"bad part: {p!r}")
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

def safe_div(a, b, default=None):
    if b == 0:
        if default is None:
            raise ZeroDivisionError("division by zero")
        return default
    return float(a) / float(b)

def strip_accents(text):
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(c)
    )

def interval_merge(intervals):
    pairs = []
    for iv in intervals:
        start, end = iv[0], iv[1]
        if start > end:
            raise ValueError("start > end")
        pairs.append((start, end))
    pairs.sort()
    merged = []
    for start, end in pairs:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

def base_n(n, base):
    if not 2 <= base <= 36:
        raise ValueError("base out of range")
    if n < 0:
        raise ValueError("negative")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, base)
        out.append(_DIGITS[r])
    return "".join(reversed(out))
"""


def _fenced(code: str) -> str:
    return "```python\n" + code + "\n```"


def test_correct_implementation_reaches_the_ceiling():
    """Every case is satisfiable at once. A goal no one can score 1.0 on grades the
    grader, not the model."""
    assert score_code(_fenced(GOLDEN), GUARDKIT_REFERENCE_TEST) == 1.0


def test_empty_module_scores_exactly_zero():
    """No free points. This is the regression for the `rejects` control call: without
    it an empty module collected every "must raise" case."""
    assert score_code("```python\n```", GUARDKIT_REFERENCE_TEST) == 0.0


def test_one_stub_function_scores_one_case():
    """A single happy-path function earns a single happy-path case — the score tracks
    depth, not the presence of a module."""
    stub = "def clamp(value, lo, hi):\n    return value\n"
    score = score_code(_fenced(stub), GUARDKIT_REFERENCE_TEST)
    assert 0.0 < score < 0.05


def test_depth_suite_is_not_the_published_suite():
    """The nine-goal artifacts must stay comparable; this goal ships beside them."""
    from eval.tasks import EVAL_SUITE

    assert [t.id for t in DEPTH_SUITE] == ["guardkit"]
    assert "guardkit" not in {t.id for t in EVAL_SUITE}

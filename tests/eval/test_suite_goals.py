from __future__ import annotations

import pytest
from eval.harness import score_code
from eval.tasks import EVAL_SUITE

# "Validasi si validator": fixtures known-good & broken disalin VERBATIM dari
# artifact terverifikasi sebagai konstanta di sini, sehingga test self-contained
# dan reproducible (TIDAK membaca /tmp/eval_goals_verified.json saat run). Tiap
# reference_test HARUS memberi 1.0 ke solusi benar dan < 1.0 ke solusi rusak;
# kalau tidak, eval-nya bohong.

# slugify tak punya fixture di artifact -> good/broken ditulis tangan di sini.
SLUGIFY_GOOD = """\
import re


def slugify(text):
    s = text.lower()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")
"""

# BUG: kembalikan input apa adanya -> gagal hampir semua case.
SLUGIFY_BROKEN = """\
def slugify(text):
    return text
"""

ROMAN_GOOD = (
    'from __future__ import annotations\n'
    '\n'
    '_TO_ROMAN = [\n'
    '    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),\n'
    '    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),\n'
    '    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),\n'
    ']\n'
    '_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}\n'
    '\n'
    '\n'
    'def to_roman(n: int) -> str:\n'
    '    if isinstance(n, bool) or not isinstance(n, int):\n'
    '        raise ValueError("n must be an int")\n'
    '    if n < 1 or n > 3999:\n'
    '        raise ValueError("n must be in 1..3999")\n'
    '    out = []\n'
    '    for value, sym in _TO_ROMAN:\n'
    '        while n >= value:\n'
    '            out.append(sym)\n'
    '            n -= value\n'
    '    return "".join(out)\n'
    '\n'
    '\n'
    'def from_roman(s: str) -> int:\n'
    '    s = s.upper()\n'
    '    total = 0\n'
    '    length = len(s)\n'
    '    for i, ch in enumerate(s):\n'
    '        v = _VALUES[ch]\n'
    '        if i + 1 < length and _VALUES[s[i + 1]] > v:\n'
    '            total -= v\n'
    '        else:\n'
    '            total += v\n'
    '    return total\n'
)

ROMAN_BROKEN = (
    'from __future__ import annotations\n'
    '\n'
    '_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}\n'
    '\n'
    '\n'
    'def to_roman(n: int) -> str:\n'
    '    # SUBTLY WRONG: purely additive, no subtractive pairs -> to_roman(4) == "IIII".\n'
    '    result = ""\n'
    '    result += "M" * (n // 1000)\n'
    '    n %= 1000\n'
    '    result += "D" * (n // 500)\n'
    '    n %= 500\n'
    '    result += "C" * (n // 100)\n'
    '    n %= 100\n'
    '    result += "L" * (n // 50)\n'
    '    n %= 50\n'
    '    result += "X" * (n // 10)\n'
    '    n %= 10\n'
    '    result += "V" * (n // 5)\n'
    '    n %= 5\n'
    '    result += "I" * n\n'
    '    return result\n'
    '\n'
    '\n'
    'def from_roman(s: str) -> int:\n'
    '    s = s.upper()\n'
    '    total = 0\n'
    '    length = len(s)\n'
    '    for i, ch in enumerate(s):\n'
    '        v = _VALUES[ch]\n'
    '        if i + 1 < length and _VALUES[s[i + 1]] > v:\n'
    '            total -= v\n'
    '        else:\n'
    '            total += v\n'
    '    return total\n'
)

CALC_GOOD = (
    'from __future__ import annotations\n'
    '\n'
    '\n'
    'def evaluate(expr: str) -> float:\n'
    '    tokens = _tokenize(expr)\n'
    '    parser = _Parser(tokens)\n'
    '    value = parser.parse_expression()\n'
    '    if parser.pos != len(tokens):\n'
    '        raise ValueError("unexpected trailing tokens")\n'
    '    return float(value)\n'
    '\n'
    '\n'
    'def _tokenize(expr: str) -> list:\n'
    '    tokens = []\n'
    '    i = 0\n'
    '    n = len(expr)\n'
    '    while i < n:\n'
    '        ch = expr[i]\n'
    '        if ch.isspace():\n'
    '            i += 1\n'
    '            continue\n'
    '        if ch in "+-*/()":\n'
    '            tokens.append(ch)\n'
    '            i += 1\n'
    '            continue\n'
    '        if ch.isdigit() or ch == ".":\n'
    '            j = i\n'
    '            while j < n and (expr[j].isdigit() or expr[j] == "."):\n'
    '                j += 1\n'
    '            tokens.append(float(expr[i:j]))\n'
    '            i = j\n'
    '            continue\n'
    '        raise ValueError(f"bad char: {ch!r}")\n'
    '    return tokens\n'
    '\n'
    '\n'
    'class _Parser:\n'
    '    def __init__(self, tokens: list) -> None:\n'
    '        self.tokens = tokens\n'
    '        self.pos = 0\n'
    '\n'
    '    def _peek(self):\n'
    '        if self.pos < len(self.tokens):\n'
    '            return self.tokens[self.pos]\n'
    '        return None\n'
    '\n'
    '    def _next(self):\n'
    '        tok = self.tokens[self.pos]\n'
    '        self.pos += 1\n'
    '        return tok\n'
    '\n'
    "    # expression := term (('+' | '-') term)*\n"
    '    def parse_expression(self) -> float:\n'
    '        value = self.parse_term()\n'
    '        while self._peek() in ("+", "-"):\n'
    '            op = self._next()\n'
    '            rhs = self.parse_term()\n'
    '            value = value + rhs if op == "+" else value - rhs\n'
    '        return value\n'
    '\n'
    "    # term := factor (('*' | '/') factor)*\n"
    '    def parse_term(self) -> float:\n'
    '        value = self.parse_factor()\n'
    '        while self._peek() in ("*", "/"):\n'
    '            op = self._next()\n'
    '            rhs = self.parse_factor()\n'
    '            value = value * rhs if op == "*" else value / rhs\n'
    '        return value\n'
    '\n'
    "    # factor := number | '(' expression ')' | ('+'|'-') factor\n"
    '    def parse_factor(self) -> float:\n'
    '        tok = self._peek()\n'
    '        if tok == "(":\n'
    '            self._next()\n'
    '            value = self.parse_expression()\n'
    '            if self._peek() != ")":\n'
    '                raise ValueError("expected )")\n'
    '            self._next()\n'
    '            return value\n'
    '        if tok == "+":\n'
    '            self._next()\n'
    '            return self.parse_factor()\n'
    '        if tok == "-":\n'
    '            self._next()\n'
    '            return -self.parse_factor()\n'
    '        if isinstance(tok, float):\n'
    '            return self._next()\n'
    '        raise ValueError("unexpected token")\n'
)

CALC_BROKEN = (
    'from __future__ import annotations\n'
    '\n'
    '\n'
    'def evaluate(expr: str) -> float:\n'
    '    tokens = _tokenize(expr)\n'
    '    return float(_eval_ltr(tokens, 0)[0])\n'
    '\n'
    '\n'
    'def _tokenize(expr: str) -> list:\n'
    '    tokens = []\n'
    '    i = 0\n'
    '    n = len(expr)\n'
    '    while i < n:\n'
    '        ch = expr[i]\n'
    '        if ch.isspace():\n'
    '            i += 1\n'
    '            continue\n'
    '        if ch in "+-*/()":\n'
    '            tokens.append(ch)\n'
    '            i += 1\n'
    '            continue\n'
    '        if ch.isdigit() or ch == ".":\n'
    '            j = i\n'
    '            while j < n and (expr[j].isdigit() or expr[j] == "."):\n'
    '                j += 1\n'
    '            tokens.append(float(expr[i:j]))\n'
    '            i = j\n'
    '            continue\n'
    '        raise ValueError(f"bad char: {ch!r}")\n'
    '    return tokens\n'
    '\n'
    '\n'
    '# BUG: evaluates strictly left-to-right ignoring * / precedence.\n'
    'def _eval_ltr(tokens: list, pos: int):\n'
    '    def read_operand(p):\n'
    '        if tokens[p] == "(":\n'
    '            val, p = _eval_ltr(tokens, p + 1)\n'
    '            # skip closing paren\n'
    '            return val, p + 1\n'
    '        return tokens[p], p + 1\n'
    '\n'
    '    value, pos = read_operand(pos)\n'
    '    while pos < len(tokens) and tokens[pos] in ("+", "-", "*", "/"):\n'
    '        op = tokens[pos]\n'
    '        rhs, pos = read_operand(pos + 1)\n'
    '        if op == "+":\n'
    '            value = value + rhs\n'
    '        elif op == "-":\n'
    '            value = value - rhs\n'
    '        elif op == "*":\n'
    '            value = value * rhs\n'
    '        else:\n'
    '            value = value / rhs\n'
    '    return value, pos\n'
)

CSV_STATS_GOOD = (
    'from __future__ import annotations\n'
    '\n'
    '\n'
    'def column_stats(csv_text: str) -> dict:\n'
    '    lines = [ln for ln in csv_text.splitlines() if ln != ""]\n'
    '    if not lines:\n'
    '        return {}\n'
    '    header = [h.strip() for h in lines[0].split(",")]\n'
    '    rows = [ln.split(",") for ln in lines[1:]]\n'
    '    result: dict = {}\n'
    '    for i, col in enumerate(header):\n'
    '        values: list[float] = []\n'
    '        numeric = True\n'
    '        for row in rows:\n'
    '            if i >= len(row):\n'
    '                numeric = False\n'
    '                break\n'
    '            try:\n'
    '                values.append(float(row[i].strip()))\n'
    '            except (ValueError, TypeError):\n'
    '                numeric = False\n'
    '                break\n'
    '        if numeric and values:\n'
    '            result[col] = {\n'
    '                "min": float(min(values)),\n'
    '                "max": float(max(values)),\n'
    '                "mean": sum(values) / len(values),\n'
    '            }\n'
    '    return result\n'
)

CSV_STATS_BROKEN = (
    'from __future__ import annotations\n'
    '\n'
    '\n'
    'def column_stats(csv_text: str) -> dict:\n'
    '    lines = [ln for ln in csv_text.splitlines() if ln != ""]\n'
    '    if not lines:\n'
    '        return {}\n'
    '    header = [h.strip() for h in lines[0].split(",")]\n'
    '    rows = [ln.split(",") for ln in lines[1:]]\n'
    '    result: dict = {}\n'
    '    for i, col in enumerate(header):\n'
    '        values: list[float] = []\n'
    '        numeric = True\n'
    '        for row in rows:\n'
    '            if i >= len(row):\n'
    '                numeric = False\n'
    '                break\n'
    '            try:\n'
    '                values.append(float(row[i].strip()))\n'
    '            except (ValueError, TypeError):\n'
    '                numeric = False\n'
    '                break\n'
    '        if numeric and values:\n'
    '            result[col] = {\n'
    '                "min": float(min(values)),\n'
    '                "max": float(max(values)),\n'
    '                # BUG: integer division truncates the mean (2.5 -> 2.0, 1.5 -> 1.0)\n'
    '                "mean": float(sum(values) // len(values)),\n'
    '            }\n'
    '    return result\n'
)

JSON_FLATTEN_GOOD = (
    'def flatten(d: dict) -> dict:\n'
    '    result: dict = {}\n'
    '    for key, value in d.items():\n'
    '        if isinstance(value, dict):\n'
    '            for sub_key, sub_value in flatten(value).items():\n'
    '                result[f"{key}.{sub_key}"] = sub_value\n'
    '        else:\n'
    '            result[key] = value\n'
    '    return result\n'
)

JSON_FLATTEN_BROKEN = (
    'def flatten(d: dict) -> dict:\n'
    '    # SUBTLY WRONG: flattens only one level deep instead of recursing.\n'
    '    result: dict = {}\n'
    '    for key, value in d.items():\n'
    '        if isinstance(value, dict):\n'
    '            for sub_key, sub_value in value.items():\n'
    '                result[f"{key}.{sub_key}"] = sub_value\n'
    '        else:\n'
    '            result[key] = value\n'
    '    return result\n'
)

FIXTURES: dict[str, tuple[str, str]] = {
    "slugify": (SLUGIFY_GOOD, SLUGIFY_BROKEN),
    "roman": (ROMAN_GOOD, ROMAN_BROKEN),
    "calc": (CALC_GOOD, CALC_BROKEN),
    "csv_stats": (CSV_STATS_GOOD, CSV_STATS_BROKEN),
    "json_flatten": (JSON_FLATTEN_GOOD, JSON_FLATTEN_BROKEN),
}


def test_fixtures_cover_every_suite_goal():
    assert {t.id for t in EVAL_SUITE} == set(FIXTURES)


@pytest.mark.parametrize("task", EVAL_SUITE, ids=lambda t: t.id)
def test_reference_test_scores_known_good_as_perfect(task):
    good, _ = FIXTURES[task.id]
    assert score_code(good, task.reference_test) == 1.0


@pytest.mark.parametrize("task", EVAL_SUITE, ids=lambda t: t.id)
def test_reference_test_scores_broken_below_one(task):
    _, broken = FIXTURES[task.id]
    assert score_code(broken, task.reference_test) < 1.0



TEXTKIT_GOOD = '''\
import re


def word_frequencies(text):
    out = {}
    for word in re.split(r"[^A-Za-z]+", text.lower()):
        if word:
            out[word] = out.get(word, 0) + 1
    return out


def parse_duration(s):
    seconds = 0
    for value, unit in re.findall(r"(\\d+)([hms])", s):
        seconds += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return seconds


def column_widths(rows):
    if not rows:
        return []
    return [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
'''

# BUG: setiap fungsi mengembalikan nilai kosong -> hanya lolos case yang memang kosong.
TEXTKIT_BROKEN = '''\
def word_frequencies(text):
    return {}


def parse_duration(s):
    return 0


def column_widths(rows):
    return []
'''

LEDGER_GOOD = '''\
def parse_entry(line):
    date, label, amount = line.split("|")
    return {"date": date, "label": label.strip(), "amount": float(amount)}


def convert(amount, rate):
    return round(amount * rate, 2)


def summarize(lines, rate):
    entries = [parse_entry(line) for line in lines]
    total = round(sum(entry["amount"] for entry in entries), 2)
    return {"count": len(entries), "total": total, "converted": convert(total, rate)}
'''

# BUG: label tak di-strip dan konversi tak dibulatkan -> gagal case whitespace & pembulatan.
LEDGER_BROKEN = '''\
def parse_entry(line):
    date, label, amount = line.split("|")
    return {"date": date, "label": label, "amount": float(amount)}


def convert(amount, rate):
    return amount * rate


def summarize(lines, rate):
    entries = [parse_entry(line) for line in lines]
    total = sum(entry["amount"] for entry in entries)
    return {"count": len(entries), "total": total, "converted": convert(total, rate)}
'''

FIXTURES["textkit"] = (TEXTKIT_GOOD, TEXTKIT_BROKEN)
FIXTURES["ledger"] = (LEDGER_GOOD, LEDGER_BROKEN)


TOOLBELT_GOOD = '''\
def wrap_text(s, width):
    words = s.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def merge_intervals(pairs):
    if not pairs:
        return []
    out = []
    for lo, hi in sorted(pairs):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def base_convert(n, base):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    out = ""
    while n:
        n, r = divmod(n, base)
        out = digits[r] + out
    return sign + out


def parse_semver(s):
    core, _, pre = s.partition("-")
    major, minor, patch = (int(x) for x in core.split("."))
    return {"major": major, "minor": minor, "patch": patch, "prerelease": pre or None}


def run_length_encode(s):
    if not s:
        return ""
    out, prev, count = "", s[0], 1
    for ch in s[1:]:
        if ch == prev:
            count += 1
        else:
            out += prev + (str(count) if count > 1 else "")
            prev, count = ch, 1
    return out + prev + (str(count) if count > 1 else "")
'''

# BUG: wrap lupa memperhitungkan spasi pemisah, dan RLE menempelkan hitungan
# bahkan untuk run tunggal -> sebagian kasus gagal, bukan semuanya (skor bergradasi).
TOOLBELT_BROKEN = '''\
def wrap_text(s, width):
    words = s.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        if len(cur) + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def merge_intervals(pairs):
    if not pairs:
        return []
    out = []
    for lo, hi in sorted(pairs):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def base_convert(n, base):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    out = ""
    while n:
        n, r = divmod(n, base)
        out = digits[r] + out
    return sign + out


def parse_semver(s):
    core, _, pre = s.partition("-")
    major, minor, patch = (int(x) for x in core.split("."))
    return {"major": major, "minor": minor, "patch": patch, "prerelease": pre or None}


def run_length_encode(s):
    if not s:
        return ""
    out, prev, count = "", s[0], 1
    for ch in s[1:]:
        if ch == prev:
            count += 1
        else:
            out += prev + (str(count) if count >= 1 else "")
            prev, count = ch, 1
    return out + prev + (str(count) if count >= 1 else "")
'''

FIXTURES["toolbelt"] = (TOOLBELT_GOOD, TOOLBELT_BROKEN)


DEBUG_GAUNTLET_GOOD = '''\
def session_minutes(rows):
    totals = {}
    for user, start, end in rows:
        if not user.strip():
            continue
        start_h, start_m = start.split(":")
        end_h, end_m = end.split(":")
        begin = int(start_h) * 60 + int(start_m)
        finish = int(end_h) * 60 + int(end_m)
        if finish <= begin:
            finish += 24 * 60
        totals[user] = totals.get(user, 0) + (finish - begin)
    return totals
'''

# The buggy implementation handed to the model in the goal itself: minutes added to
# hours instead of scaled, midnight crossings skipped, whitespace names kept. Copying
# it without finding the bugs must score well below 1.0 (measured: 0.2).
DEBUG_GAUNTLET_BROKEN = '''\
def session_minutes(rows):
    totals = {}
    for user, start, end in rows:
        if not user:
            continue
        start_h, start_m = start.split(":")
        end_h, end_m = end.split(":")
        begin = int(start_h) * 60 + int(start_m)
        finish = int(end_h) + int(end_m)
        if finish < begin:
            continue
        totals[user] = totals.get(user, 0) + (finish - begin)
    return totals
'''

FIXTURES["debug_gauntlet"] = (DEBUG_GAUNTLET_GOOD, DEBUG_GAUNTLET_BROKEN)

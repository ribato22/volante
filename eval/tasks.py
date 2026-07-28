from __future__ import annotations

from dataclasses import dataclass

SLUGIFY_GOAL: str = (
    "Implement a Python function slugify(text: str) -> str that: "
    "lowercases the input; converts runs of spaces and underscores to a single "
    "hyphen; removes every character that is not a lowercase letter, digit, or "
    "hyphen; collapses consecutive hyphens into one; and strips leading and "
    "trailing hyphens. "
    "Also add a pytest test module (functions named test_*) covering these rules "
    "and write a short README describing the function and how to run the tests."
)

# Runner referensi tersembunyi. harness.score_code menuliskannya ke tmp dir
# sandbox sebagai `reference_runner.py`, lalu menjalankannya di subprocess
# terisolasi (env bersih tanpa *_API_KEY/*_KEY). Ia mengimpor `slugify` dari
# `solution` (kode yang dinilai) dan mencetak satu baris JSON
# {"passed": int, "total": int} ke stdout via `_TAG`. Disimpan sebagai string
# privat agar solusi yang di-generate tidak bisa mengimpor & meng-echo jawaban.
#
# Kanal hasil ber-nonce: `_TAG` TIDAK didefinisikan di sini — score_code
# meng-inject preamble tepercaya yang membaca nonce dari stdin (sebelum `import
# solution`) dan men-set `_TAG = "AIORCH_RESULT:<nonce>:"`. Hanya baris ber-tag
# itu yang dipercaya score_code, sehingga solusi tak bisa memalsukan skor lewat
# stdout injection naif. (Runner memakai `_TAG` sebagai global yang disediakan
# preamble; ia dieksekusi HANYA lewat score_code, bukan standalone.)
REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

CASES = [
    ("Hello World", "hello-world"),
    ("under_score", "under-score"),
    ("Multiple   Spaces", "multiple-spaces"),
    ("Trailing!!!", "trailing"),
    ("Mix_of Both__here", "mix-of-both-here"),
    ("---edge---", "edge"),
    ("CAPS lock", "caps-lock"),
    ("a1b2 c3", "a1b2-c3"),
]


def main() -> None:
    total = len(CASES)
    passed = 0
    for text_in, expected in CASES:
        try:
            if call_solution("slugify", text_in) == expected:
                passed += 1
        except Exception:
            pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


if __name__ == "__main__":
    main()
    sys.exit(0)
'''


@dataclass(frozen=True)
class EvalTask:
    """Satu goal eval: instruksi komposit + runner referensi tersembunyi.

    reference_test adalah SUMBER Python (stdlib saja) yang mengimpor nama
    yang diharapkan dari `solution`, menjalankan sekumpulan case, dan mencetak
    satu baris `_TAG + json({"passed": int, "total": int})`. `_TAG` di-inject
    score_code (kanal ber-nonce); runner tak dijalankan standalone."""

    id: str
    goal: str
    reference_test: str


# Suite 5 goal komposit (Fase eval-suite). slugify dipertahankan sebagai
# goal pertama; empat goal berikut ditranskripsi VERBATIM dari artifact
# terverifikasi (known-good -> 1.0, broken < 1.0). Tiap reference_test bersifat
# privat (tersembunyi) supaya solusi generated tak bisa meng-echo jawaban.

ROMAN_GOAL = (
    'Implement two functions in solution.py. to_roman(n: int) -> str converts an '
    'integer in the range 1..3999 to its standard Roman numeral as an uppercase '
    'string. from_roman(s: str) -> int parses an uppercase Roman numeral back to '
    'its integer value. Use standard subtractive notation for the pairs IV, IX, '
    'XL, XC, CD, and CM — for example to_roman(1) == "I", to_roman(4) == "IV", '
    'to_roman(9) == "IX", to_roman(40) == "XL", to_roman(58) == "LVIII", '
    'to_roman(90) == "XC", to_roman(400) == "CD", to_roman(1994) == "MCMXCIV", '
    'and to_roman(3999) == "MMMCMXCIX". The two functions must be inverses so '
    'that from_roman(to_roman(n)) == n for every n in 1..3999. Also add a pytest '
    'test module (functions named test_*) covering the conversion rules, the '
    'subtractive cases, and round-trip behaviour, and write a short README '
    'describing the two functions and how to run the tests.'
)

ROMAN_REFERENCE_TEST = (
    'from __future__ import annotations\n'
    '\n'
    'import json\n'
    'import sys\n'
    '\n'
    'TO_ROMAN_CASES = [\n'
    '    (1, "I"),\n'
    '    (2, "II"),\n'
    '    (3, "III"),\n'
    '    (4, "IV"),\n'
    '    (9, "IX"),\n'
    '    (14, "XIV"),\n'
    '    (40, "XL"),\n'
    '    (58, "LVIII"),\n'
    '    (90, "XC"),\n'
    '    (400, "CD"),\n'
    '    (500, "D"),\n'
    '    (944, "CMXLIV"),\n'
    '    (1994, "MCMXCIV"),\n'
    '    (2421, "MMCDXXI"),\n'
    '    (3999, "MMMCMXCIX"),\n'
    ']\n'
    '\n'
    'FROM_ROMAN_CASES = [\n'
    '    ("I", 1),\n'
    '    ("IV", 4),\n'
    '    ("IX", 9),\n'
    '    ("XL", 40),\n'
    '    ("LVIII", 58),\n'
    '    ("XC", 90),\n'
    '    ("CD", 400),\n'
    '    ("MCMXCIV", 1994),\n'
    '    ("MMMCMXCIX", 3999),\n'
    ']\n'
    '\n'
    'ROUNDTRIP_NS = [1, 4, 9, 40, 58, 90, 400, 944, 1994, 2421, 3999]\n'
    '\n'
    '\n'
    'def main() -> None:\n'
    '    total = len(TO_ROMAN_CASES) + len(FROM_ROMAN_CASES) + len(ROUNDTRIP_NS)\n'
    '    passed = 0\n'
    '    for n, expected in TO_ROMAN_CASES:\n'
    '        try:\n'
    '            if call_solution("to_roman", n) == expected:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    for s, expected in FROM_ROMAN_CASES:\n'
    '        try:\n'
    '            if call_solution("from_roman", s) == expected:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    for n in ROUNDTRIP_NS:\n'
    '        try:\n'
    '            if call_solution("from_roman", call_solution("to_roman", n)) == n:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    print(_TAG + json.dumps({"passed": passed, "total": total}))\n'
    '\n'
    '\n'
    'if __name__ == "__main__":\n'
    '    main()\n'
    '    sys.exit(0)\n'
)

CALC_GOAL = (
    'Implement a Python function evaluate(expr: str) -> float in solution.py that '
    'evaluates an arithmetic expression string supporting the binary operators + '
    '- * / and parentheses ( ). It must honor standard operator precedence (* and '
    '/ bind tighter than + and -) and left-to-right associativity for operators '
    'of equal precedence, support nested parentheses to any depth, and accept '
    'optional surrounding whitespace and integer/decimal number literals. It must '
    'also handle a leading unary minus/plus (e.g. "-3+5"). You MUST NOT use '
    "Python's eval(), exec(), ast.literal_eval(), or any similar "
    'dynamic-evaluation shortcut; instead parse and compute the value yourself '
    '(e.g. a recursive-descent parser or the shunting-yard algorithm). Examples: '
    '"1+2*3" -> 7, "(1+2)*3" -> 9, "10/4" -> 2.5, "2*(3+4)-5" -> 9, "2+3*4-1" -> '
    '13, "((1+2)*(3+4))" -> 21. The return value must be a float (compared with a '
    'small floating-point tolerance). Also add a pytest test module (functions '
    'named test_*) covering precedence, associativity, parentheses/nesting, '
    'division producing non-integers, and unary minus, and write a short README '
    'describing the function and how to run the tests (e.g. `pytest`).'
)

CALC_REFERENCE_TEST = (
    'from __future__ import annotations\n'
    '\n'
    'import json\n'
    'import sys\n'
    '\n'
    '# (expression, expected_value)\n'
    'CASES = [\n'
    '    ("1+2*3", 7.0),\n'
    '    ("(1+2)*3", 9.0),\n'
    '    ("10/4", 2.5),\n'
    '    ("2*(3+4)-5", 9.0),\n'
    '    ("2+3*4-1", 13.0),\n'
    '    ("((1+2)*(3+4))", 21.0),\n'
    '    ("2-3-4", -5.0),        # left-associative subtraction\n'
    '    ("100/10/2", 5.0),      # left-associative division\n'
    '    ("-3+5", 2.0),          # unary minus\n'
    '    ("2*3+4*5", 26.0),      # precedence on both sides\n'
    '    ("(2+3)*(4-1)/5", 3.0), # mixed nesting\n'
    '    ("7", 7.0),             # single number\n'
    ']\n'
    '\n'
    'TOL = 1e-9\n'
    '\n'
    '\n'
    'def main() -> None:\n'
    '    total = len(CASES)\n'
    '    passed = 0\n'
    '    for expr_in, expected in CASES:\n'
    '        try:\n'
    '            got = call_solution("evaluate", expr_in)\n'
    '            if isinstance(got, (int, float)) and abs(float(got) - expected) < TOL:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    print(_TAG + json.dumps({"passed": passed, "total": total}))\n'
    '\n'
    '\n'
    'if __name__ == "__main__":\n'
    '    main()\n'
    '    sys.exit(0)\n'
)

CSV_STATS_GOAL = (
    'Implement a Python function column_stats(csv_text: str) -> dict in '
    'solution.py that parses CSV text where the first line is the header row and '
    'every following non-empty line is a data row (comma-separated). For each '
    'column whose data values are ALL numeric (each value parses as a float — '
    'integers, decimals, and negatives all count), return an entry mapping the '
    'header name to a dict {"min": float, "max": float, "mean": float}, where '
    'min/max are the smallest/largest values and mean is the arithmetic average '
    'using true (floating-point) division. Any column that has at least one '
    'non-numeric value must be ignored entirely (excluded from the result). All '
    'numbers in the returned dicts must be Python floats; the mean must never be '
    'truncated by integer division. For example, '
    'column_stats("a,b,name\\n1,10,x\\n2,20,y\\n3,30,z") returns {"a": {"min": 1.0, '
    '"max": 3.0, "mean": 2.0}, "b": {"min": 10.0, "max": 30.0, "mean": 20.0}} '
    '(the "name" column is ignored). Also add a pytest test module (functions '
    'named test_*) covering these rules — including the all-numeric case, the '
    'mixed/ignored non-numeric column case, non-integer means, float and negative '
    'values — and write a short README describing the function and how to run the '
    'tests.'
)

CSV_STATS_REFERENCE_TEST = (
    'from __future__ import annotations\n'
    '\n'
    'import json\n'
    'import math\n'
    'import sys\n'
    '\n'
    'CASES = [\n'
    '    (\n'
    '        "a,b,name\\n1,10,x\\n2,20,y\\n3,30,z",\n'
    '        {"a": {"min": 1.0, "max": 3.0, "mean": 2.0},\n'
    '         "b": {"min": 10.0, "max": 30.0, "mean": 20.0}},\n'
    '    ),\n'
    '    (\n'
    '        "n\\n1\\n2",\n'
    '        {"n": {"min": 1.0, "max": 2.0, "mean": 1.5}},\n'
    '    ),\n'
    '    (\n'
    '        "v\\n1\\n2\\n2",\n'
    '        {"v": {"min": 1.0, "max": 2.0, "mean": 5.0 / 3.0}},\n'
    '    ),\n'
    '    (\n'
    '        "p\\n1.5\\n2.5\\n3.5",\n'
    '        {"p": {"min": 1.5, "max": 3.5, "mean": 2.5}},\n'
    '    ),\n'
    '    (\n'
    '        "x,y\\n1,foo\\n2,bar",\n'
    '        {"x": {"min": 1.0, "max": 2.0, "mean": 1.5}},\n'
    '    ),\n'
    '    (\n'
    '        "t,val\\n-3,a\\n-1,b\\n-2,c",\n'
    '        {"t": {"min": -3.0, "max": -1.0, "mean": -2.0}},\n'
    '    ),\n'
    '    (\n'
    '        "label\\nfoo\\nbar\\nbaz",\n'
    '        {},\n'
    '    ),\n'
    ']\n'
    '\n'
    '\n'
    'def _close(result, expected):\n'
    '    if not isinstance(result, dict):\n'
    '        return False\n'
    '    if set(result.keys()) != set(expected.keys()):\n'
    '        return False\n'
    '    for col, stats in expected.items():\n'
    '        got = result.get(col)\n'
    '        if not isinstance(got, dict):\n'
    '            return False\n'
    '        if set(got.keys()) != set(stats.keys()):\n'
    '            return False\n'
    '        for key, val in stats.items():\n'
    '            g = got.get(key)\n'
    '            try:\n'
    '                if not math.isclose(float(g), float(val), rel_tol=1e-9, abs_tol=1e-9):\n'
    '                    return False\n'
    '            except (TypeError, ValueError):\n'
    '                return False\n'
    '    return True\n'
    '\n'
    '\n'
    'def main() -> None:\n'
    '    total = len(CASES)\n'
    '    passed = 0\n'
    '    for csv_text, expected in CASES:\n'
    '        try:\n'
    '            if _close(call_solution("column_stats", csv_text), expected):\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    print(_TAG + json.dumps({"passed": passed, "total": total}))\n'
    '\n'
    '\n'
    'if __name__ == "__main__":\n'
    '    main()\n'
    '    sys.exit(0)\n'
)

JSON_FLATTEN_GOAL = (
    'Implement a Python function flatten(d: dict) -> dict in solution.py that '
    'flattens an arbitrarily nested dictionary into a single-level dictionary '
    'with dot-separated keys. Rules: recurse into every nested dict value, '
    'joining the parent key and child key with a single "." (dot) separator, '
    'applying this at every depth (not just one level); values that are not dicts '
    '(scalars such as int/str/None, and lists) are kept as-is as leaf values; an '
    'empty input dict returns an empty dict {}. Examples: '
    '{"a":{"b":1,"c":2},"d":3} -> {"a.b":1,"a.c":2,"d":3}; {"x":{"y":{"z":9}}} -> '
    '{"x.y.z":9}; {"a":1} -> {"a":1}; {} -> {}. Also add a pytest test module '
    '(functions named test_*) covering these rules and edge cases (deep nesting, '
    'list/scalar leaves, empty dict), and write a short README describing the '
    'function and how to run the tests.'
)

JSON_FLATTEN_REFERENCE_TEST = (
    'from __future__ import annotations\n'
    '\n'
    'import json\n'
    'import sys\n'
    '\n'
    'CASES = [\n'
    '    ({"a": {"b": 1, "c": 2}, "d": 3}, {"a.b": 1, "a.c": 2, "d": 3}),\n'
    '    ({"x": {"y": {"z": 9}}}, {"x.y.z": 9}),\n'
    '    ({"a": 1}, {"a": 1}),\n'
    '    ({}, {}),\n'
    '    ({"a": {"b": [1, 2]}, "c": 3}, {"a.b": [1, 2], "c": 3}),\n'
    '    ({"p": {"q": {"r": {"s": 5}}}, "t": "x"}, {"p.q.r.s": 5, "t": "x"}),\n'
    '    ({"k": {"m": 1}, "k2": 2}, {"k.m": 1, "k2": 2}),\n'
    '    ({"n": {"o": None, "p": 0}}, {"n.o": None, "n.p": 0}),\n'
    ']\n'
    '\n'
    '\n'
    'def main() -> None:\n'
    '    total = len(CASES)\n'
    '    passed = 0\n'
    '    for d_in, expected in CASES:\n'
    '        try:\n'
    '            if call_solution("flatten", d_in) == expected:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    print(_TAG + json.dumps({"passed": passed, "total": total}))\n'
    '\n'
    '\n'
    'if __name__ == "__main__":\n'
    '    main()\n'
    '    sys.exit(0)\n'
)


EVAL_SUITE: list[EvalTask] = [
    EvalTask("slugify", SLUGIFY_GOAL, REFERENCE_TEST),
    EvalTask("roman", ROMAN_GOAL, ROMAN_REFERENCE_TEST),
    EvalTask("calc", CALC_GOAL, CALC_REFERENCE_TEST),
    EvalTask("csv_stats", CSV_STATS_GOAL, CSV_STATS_REFERENCE_TEST),
    EvalTask("json_flatten", JSON_FLATTEN_GOAL, JSON_FLATTEN_REFERENCE_TEST),
]


# --- Goal multi-bagian (menguji tesis orkestrasi) -----------------------------
# Lima goal di atas adalah kata satu-fungsi: dekomposisi tak bisa membantu di sana,
# sementara orkestrasi tetap membayar panggilan planning + sintesis. Dua goal berikut
# punya komponen yang BENAR-BENAR terpisah, sehingga fan-out paralel punya peluang
# nyata — satu-satunya kondisi di mana pertanyaan "orkestrasi vs satu model kuat"
# bisa dijawab, bukan diasumsikan.

TEXTKIT_GOAL = (
    'Implement three INDEPENDENT functions in solution.py. '
    '(1) word_frequencies(text: str) -> dict[str, int] lowercases the text, splits it '
    'on every character that is not an ASCII letter, discards empty pieces, and returns '
    'a dict mapping each word to how many times it occurs — for example '
    'word_frequencies("Cat, cat; DOG") == {"cat": 2, "dog": 1}. '
    '(2) parse_duration(s: str) -> int converts a compact duration into whole seconds. '
    'The string is a concatenation of one or more parts, each an integer followed by '
    'the unit h, m, or s, always in that order and each unit used at most once — '
    'parse_duration("1h30m") == 5400, parse_duration("45s") == 45, '
    'parse_duration("2h5s") == 7205, parse_duration("0m") == 0. '
    '(3) column_widths(rows: list[list[str]]) -> list[int] returns, for each column '
    'index, the length of the longest string in that column across all rows; every row '
    'has the same number of columns, and an empty rows list returns an empty list — '
    'column_widths([["a", "bbb"], ["cc", "d"]]) == [2, 3]. '
    'The three functions are unrelated: none of them may call another. Also add a pytest '
    'test module (functions named test_*) covering all three, and a short README.'
)

TEXTKIT_REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

WORD_CASES = [
    ("Cat, cat; DOG", {"cat": 2, "dog": 1}),
    ("", {}),
    ("a1b2c", {"a": 1, "b": 1, "c": 1}),
    ("Hello---hello  HELLO", {"hello": 3}),
]
DURATION_CASES = [
    ("1h30m", 5400),
    ("45s", 45),
    ("2h5s", 7205),
    ("0m", 0),
    ("10m30s", 630),
    ("3h", 10800),
]
WIDTH_CASES = [
    ([["a", "bbb"], ["cc", "d"]], [2, 3]),
    ([], []),
    ([["", "xy"]], [0, 2]),
    ([["one"], ["three"], ["to"]], [5]),
]


def main() -> None:
    total = len(WORD_CASES) + len(DURATION_CASES) + len(WIDTH_CASES)
    passed = 0
    for text_in, expected in WORD_CASES:
        try:
            if call_solution("word_frequencies", text_in) == expected:
                passed += 1
        except Exception:
            pass
    for text_in, expected in DURATION_CASES:
        try:
            if call_solution("parse_duration", text_in) == expected:
                passed += 1
        except Exception:
            pass
    for rows, expected in WIDTH_CASES:
        try:
            if call_solution("column_widths", rows) == expected:
                passed += 1
        except Exception:
            pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


main()
sys.exit(0)
'''

LEDGER_GOAL = (
    'Implement three functions in solution.py that build on each other. '
    '(1) parse_entry(line: str) -> dict parses a pipe-separated ledger line '
    '"DATE|LABEL|AMOUNT" into {"date": str, "label": str, "amount": float}; the label '
    'is stripped of surrounding whitespace and the amount may be negative — '
    'parse_entry("2026-07-27| coffee |-3.50") == '
    '{"date": "2026-07-27", "label": "coffee", "amount": -3.5}. '
    '(2) convert(amount: float, rate: float) -> float multiplies the amount by the rate '
    'and rounds the result to 2 decimal places with Python\'s built-in round — '
    'convert(-3.5, 2.0) == -7.0, convert(10.0, 0.333) == 3.33. '
    '(3) summarize(lines: list[str], rate: float) -> dict uses BOTH of the above to '
    'return {"count": int, "total": float, "converted": float}, where count is the number '
    'of lines, total is the sum of the parsed amounts rounded to 2 decimals with round, '
    'and converted is convert(total, rate). An empty list returns '
    '{"count": 0, "total": 0.0, "converted": 0.0}. Also add a pytest test module '
    '(functions named test_*) covering all three, and a short README.'
)

LEDGER_REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

ENTRY_CASES = [
    (
        "2026-07-27| coffee |-3.50",
        {"date": "2026-07-27", "label": "coffee", "amount": -3.5},
    ),
    ("2026-01-01|rent|1200", {"date": "2026-01-01", "label": "rent", "amount": 1200.0}),
    (
        "2026-02-02|  two words  |0.25",
        {"date": "2026-02-02", "label": "two words", "amount": 0.25},
    ),
]
CONVERT_CASES = [
    ((-3.5, 2.0), -7.0),
    ((10.0, 0.333), 3.33),
    ((0.0, 5.0), 0.0),
]
SUMMARY_CASES = [
    (([], 2.0), {"count": 0, "total": 0.0, "converted": 0.0}),
    (
        (["2026-01-01|a|1.5", "2026-01-02|b|2.5"], 2.0),
        {"count": 2, "total": 4.0, "converted": 8.0},
    ),
    (
        (["2026-01-01|a|-1.0", "2026-01-02|b|0.5"], 1.0),
        {"count": 2, "total": -0.5, "converted": -0.5},
    ),
]


def main() -> None:
    total = len(ENTRY_CASES) + len(CONVERT_CASES) + len(SUMMARY_CASES)
    passed = 0
    for line, expected in ENTRY_CASES:
        try:
            if call_solution("parse_entry", line) == expected:
                passed += 1
        except Exception:
            pass
    for (amount, rate), expected in CONVERT_CASES:
        try:
            if call_solution("convert", amount, rate) == expected:
                passed += 1
        except Exception:
            pass
    for (lines, rate), expected in SUMMARY_CASES:
        try:
            if call_solution("summarize", lines, rate) == expected:
                passed += 1
        except Exception:
            pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


main()
sys.exit(0)
'''

EVAL_SUITE.extend(
    [
        EvalTask("textkit", TEXTKIT_GOAL, TEXTKIT_REFERENCE_TEST),
        EvalTask("ledger", LEDGER_GOAL, LEDGER_REFERENCE_TEST),
    ]
)


# --- Goal berskala luas (menghindari efek langit-langit) -----------------------
# Tujuh goal di atas semuanya dijawab sempurna (1.00) oleh baseline satu panggilan
# pada gpt-4o-mini: tak ada ruang bagi arm mana pun untuk menang, sehingga suite tak
# bisa menguji tesisnya. Goal berikut menaikkan LUAS-nya, bukan kesulitan tiap bagian:
# lima fungsi independen dengan total ~30 kasus tepi. Satu respons harus memuat semua
# implementasi + tes + README dalam satu anggaran output, sementara dekomposisi bisa
# memberi tiap bagian panggilannya sendiri. Skornya bergradasi (kasus lolos / total),
# jadi selisih kecil pun terukur — bukan lulus/gagal.

TOOLBELT_GOAL = (
    'Implement five INDEPENDENT functions in solution.py. None may call another. '
    '(1) wrap_text(s: str, width: int) -> list[str] performs greedy word wrapping: '
    'split the text on whitespace, then fill lines with as many words as fit without '
    'exceeding width characters (words are joined by a single space, and a word longer '
    'than width goes on a line of its own, never split). Empty or whitespace-only input '
    'returns an empty list — wrap_text("a bb ccc", 6) == ["a bb", "ccc"]. '
    '(2) merge_intervals(pairs: list[tuple[int, int]]) -> list[tuple[int, int]] sorts and '
    'merges intervals that overlap OR merely touch, returning them ascending — '
    'merge_intervals([(3, 5), (1, 2), (2, 4)]) == [(1, 5)]; an empty list returns []. '
    '(3) base_convert(n: int, base: int) -> str renders n in the given base from 2 to 36 '
    'using digits 0-9 then lowercase a-z, with a leading "-" for negatives and "0" for '
    'zero — base_convert(255, 16) == "ff", base_convert(-10, 2) == "-1010". '
    '(4) parse_semver(s: str) -> dict parses "MAJOR.MINOR.PATCH" with an optional '
    '"-PRERELEASE" suffix into {"major": int, "minor": int, "patch": int, '
    '"prerelease": str | None} — parse_semver("1.2.3-rc.1") == {"major": 1, "minor": 2, '
    '"patch": 3, "prerelease": "rc.1"}; without a suffix prerelease is None. '
    '(5) run_length_encode(s: str) -> str collapses runs of the same character into the '
    'character followed by its count ONLY when the run is longer than one — '
    'run_length_encode("aaabbc") == "a3b2c"; an empty string returns "". '
    'Also add a pytest test module (functions named test_*) covering all five, and a '
    'short README describing each function.'
)

TOOLBELT_REFERENCE_TEST: str = '''\
from __future__ import annotations

import json
import sys

WRAP = [
    (("a bb ccc", 6), ["a bb", "ccc"]),
    (("", 5), []),
    (("   ", 5), []),
    (("supercalifragilistic", 5), ["supercalifragilistic"]),
    (("one two three four", 9), ["one two", "three", "four"]),
    (("a  b   c", 3), ["a b", "c"]),
]
MERGE = [
    (([(3, 5), (1, 2), (2, 4)],), [(1, 5)]),
    (([],), []),
    (([(1, 2), (4, 5)],), [(1, 2), (4, 5)]),
    (([(1, 10), (2, 3)],), [(1, 10)]),
    (([(5, 6), (1, 5)],), [(1, 6)]),
]
BASE = [
    ((255, 16), "ff"),
    ((-10, 2), "-1010"),
    ((0, 8), "0"),
    ((35, 36), "z"),
    ((7, 2), "111"),
    ((1295, 36), "zz"),
]
SEMVER = [
    (("1.2.3-rc.1",), {"major": 1, "minor": 2, "patch": 3, "prerelease": "rc.1"}),
    (("0.0.0",), {"major": 0, "minor": 0, "patch": 0, "prerelease": None}),
    (("10.20.30-beta",), {"major": 10, "minor": 20, "patch": 30, "prerelease": "beta"}),
    (("2.0.1",), {"major": 2, "minor": 0, "patch": 1, "prerelease": None}),
]
RLE = [
    (("aaabbc",), "a3b2c"),
    (("",), ""),
    (("abc",), "abc"),
    (("aaaaaaaaaaaa",), "a12"),
    (("aabbaa",), "a2b2a2"),
]


def main() -> None:
    groups = [
        ("wrap_text", WRAP),
        ("merge_intervals", MERGE),
        ("base_convert", BASE),
        ("parse_semver", SEMVER),
        ("run_length_encode", RLE),
    ]
    total = sum(len(cases) for _, cases in groups)
    passed = 0
    for name, cases in groups:
        for args, expected in cases:
            try:
                got = call_solution(name, *args)
                if name == "merge_intervals":
                    got = [tuple(x) for x in got]
                if got == expected:
                    passed += 1
            except Exception:
                pass
    print(_TAG + json.dumps({"passed": passed, "total": total}))


main()
sys.exit(0)
'''

EVAL_SUITE.append(EvalTask("toolbelt", TOOLBELT_GOAL, TOOLBELT_REFERENCE_TEST))

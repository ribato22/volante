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
    try:
        from solution import slugify
    except Exception:
        # Termasuk SyntaxError saat import solution -> skor akhirnya 0.0.
        print(_TAG + json.dumps({"passed": 0, "total": total}))
        return
    if not callable(slugify):
        print(_TAG + json.dumps({"passed": 0, "total": total}))
        return
    passed = 0
    for text_in, expected in CASES:
        try:
            if slugify(text_in) == expected:
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
    '    try:\n'
    '        from solution import from_roman, to_roman\n'
    '    except Exception:\n'
    '        # Termasuk SyntaxError/ImportError saat import solution -> skor 0.0.\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    if not (callable(to_roman) and callable(from_roman)):\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    passed = 0\n'
    '    for n, expected in TO_ROMAN_CASES:\n'
    '        try:\n'
    '            if to_roman(n) == expected:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    for s, expected in FROM_ROMAN_CASES:\n'
    '        try:\n'
    '            if from_roman(s) == expected:\n'
    '                passed += 1\n'
    '        except Exception:\n'
    '            pass\n'
    '    for n in ROUNDTRIP_NS:\n'
    '        try:\n'
    '            if from_roman(to_roman(n)) == n:\n'
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
    '    try:\n'
    '        from solution import evaluate\n'
    '    except Exception:\n'
    '        # Termasuk SyntaxError saat import solution -> skor akhirnya 0.0.\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    if not callable(evaluate):\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    passed = 0\n'
    '    for expr_in, expected in CASES:\n'
    '        try:\n'
    '            got = evaluate(expr_in)\n'
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
    '    try:\n'
    '        from solution import column_stats\n'
    '    except Exception:\n'
    '        # Termasuk SyntaxError saat import solution -> skor akhirnya 0.0.\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    if not callable(column_stats):\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    passed = 0\n'
    '    for csv_text, expected in CASES:\n'
    '        try:\n'
    '            if _close(column_stats(csv_text), expected):\n'
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
    '    try:\n'
    '        from solution import flatten\n'
    '    except Exception:\n'
    '        # Termasuk SyntaxError saat import solution -> skor akhirnya 0.0.\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    if not callable(flatten):\n'
    '        print(_TAG + json.dumps({"passed": 0, "total": total}))\n'
    '        return\n'
    '    passed = 0\n'
    '    for d_in, expected in CASES:\n'
    '        try:\n'
    '            if flatten(d_in) == expected:\n'
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

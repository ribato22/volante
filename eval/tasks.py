from __future__ import annotations

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
# {"passed": int, "total": int} ke stdout. Disimpan sebagai string privat agar
# solusi yang di-generate tidak bisa mengimpor & meng-echo jawaban yang benar.
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
        print(json.dumps({"passed": 0, "total": total}))
        return
    if not callable(slugify):
        print(json.dumps({"passed": 0, "total": total}))
        return
    passed = 0
    for text_in, expected in CASES:
        try:
            if slugify(text_in) == expected:
                passed += 1
        except Exception:
            pass
    print(json.dumps({"passed": passed, "total": total}))


if __name__ == "__main__":
    main()
    sys.exit(0)
'''

"""Instrument check for `deployctl_layers`, and it costs no API calls.

The lesson this goal paid for, before any of these tests existed: a convention
demonstrated three times in the file being edited is not a trap, it is a TEMPLATE.
`read_flags` gives every flag `default=None` with a comment saying why, and all four
models measured -- down to an 8B one -- copied it verbatim. All four scored 1.000. The
"trap" trapped nobody, because pattern completion is the thing models are best at.

What discriminates is a rule the surrounding code cannot demonstrate: a dry run forces
`retries` to 0 AFTER the merge while `origin` still names the layer that supplied the
value. Nothing in this project does anything of that shape, so there is nothing to copy.
"""

from __future__ import annotations

import pytest
from eval.harness import _score_reference, repo_files_for
from eval.tasks_repo_config import CHANGE, DEPLOYCTL_TASK

GOLDEN = '''```python
# path: spec.py
"""Which settings exist and what type each one carries."""

SPEC = {
    "region": "str",
    "timeout": "int",
    "verbose": "bool",
    "replicas": "int",
    "dry_run": "bool",
    "retries": "int",
}

DEFAULTS = {
    "region": "eu-west-1",
    "timeout": 30,
    "verbose": False,
    "replicas": 2,
    "dry_run": False,
    "retries": 3,
}
```

```python
# path: layers.py
"""The four layers, lowest priority first: default, file, env, flag."""

import argparse

from coerce import coerce
from spec import DEFAULTS, SPEC

ENV_PREFIX = "DEPLOYCTL_"
LAYERS = ("default", "file", "env", "flag")


def read_defaults():
    return dict(DEFAULTS)


def read_file(text):
    found = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip()
        if name in SPEC:
            found[name] = coerce(name, raw.strip())
    return found


def read_env(env):
    found = {}
    for name in SPEC:
        key = ENV_PREFIX + name.upper()
        if key in env:
            found[name] = coerce(name, env[key])
    return found


def read_flags(argv):
    parser = argparse.ArgumentParser(prog="deployctl", add_help=False)
    parser.add_argument("--region", default=None)
    parser.add_argument("--timeout", default=None)
    parser.add_argument("--verbose", action="store_true", default=None)
    parser.add_argument("--replicas", default=None)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    parser.add_argument("--retries", default=None)
    namespace, _rest = parser.parse_known_args(argv)
    found = {}
    for name in SPEC:
        value = getattr(namespace, name, None)
        if value is not None:
            found[name] = coerce(name, value)
    return found


def resolve(argv, env, file_text):
    sources = {
        "default": read_defaults(),
        "file": read_file(file_text),
        "env": read_env(env),
        "flag": read_flags(argv),
    }
    settings = {}
    origin = {}
    for layer in LAYERS:
        for name, value in sources[layer].items():
            settings[name] = value
            origin[name] = layer
    if settings.get("dry_run"):
        # After the merge, and origin is deliberately left alone: the audit log asks
        # where the number CAME FROM, which is not the same question as what it is now.
        settings["retries"] = 0
    return {"settings": settings, "origin": origin}
```'''


def _score(answer: str) -> float:
    return _score_reference(
        answer, DEPLOYCTL_TASK.reference_test,
        repo_files_for(answer, DEPLOYCTL_TASK.repo),
    )[0]


def test_the_golden_answer_scores_exactly_one() -> None:
    assert _score(GOLDEN) == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="empty"),
        pytest.param("Sure, I'll add those two settings.", id="prose-only"),
    ],
)
def test_an_answer_that_changes_nothing_scores_exactly_zero(answer: str) -> None:
    assert _score(answer) == 0.0


_TWO_CASES = 2 / len(CHANGE)

_NO_INTERACTION = GOLDEN.replace(
    '''    if settings.get("dry_run"):
        # After the merge, and origin is deliberately left alone: the audit log asks
        # where the number CAME FROM, which is not the same question as what it is now.
        settings["retries"] = 0
''',
    "",
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        # The idiomatic argparse spelling, which the file being edited argues against
        # three times over. It costs the most of anything here -- but measured against
        # real models, none of them reached for it.
        pytest.param(
            GOLDEN.replace(
                'parser.add_argument("--dry-run", dest="dry_run", '
                'action="store_true", default=None)',
                'parser.add_argument("--dry-run", dest="dry_run", action="store_true")'),
            0.7407, id="dry-run-flag-claims-a-value-nobody-passed"),
        # "Tidying" the existing verbose flag the same way breaks a setting the request
        # never mentioned -- the regression factor is what sees it.
        pytest.param(
            GOLDEN.replace(
                'parser.add_argument("--verbose", action="store_true", default=None)',
                'parser.add_argument("--verbose", action="store_true")'),
            0.8571, id="verbose-flag-tidied-into-the-idiom"),
        pytest.param(_NO_INTERACTION, 0.9259, id="the-interacting-rule-ignored"),
        pytest.param(
            GOLDEN.replace(
                '        settings["retries"] = 0',
                '        settings["retries"] = 0\n        origin["retries"] = "default"'),
            0.9259, id="the-override-rewrites-the-origin-it-was-told-to-keep"),
        pytest.param(
            GOLDEN.replace('    "retries": "int",\n}', "}"),
            0.6296, id="retries-never-declared-in-SPEC"),
        pytest.param(
            GOLDEN.replace('"dry_run": False,', '"dry_run": True,'),
            0.6667, id="dry_run-default-inverted"),
    ],
)
def test_each_criterion_costs_at_least_two_cases(answer: str, expected: float) -> None:
    score = _score(answer)

    assert score == pytest.approx(expected, abs=0.001)
    assert score <= 1.0 - _TWO_CASES + 1e-9, (
        f"this violation costs {1.0 - score:.4f}, under two cases"
    )


def test_the_mutations_all_actually_apply() -> None:
    """A mutation whose pattern silently missed measures nothing and looks fine.

    This project has already published a meaningless FN 0/7 from a mutation test whose
    regex never matched, so the mutations check that they changed the text at all.
    """
    assert _NO_INTERACTION != GOLDEN


def test_the_starting_project_works_before_the_change() -> None:
    regression_only = DEPLOYCTL_TASK.reference_test.replace(
        '"passed": cp * rp,', '"passed": rp,'
    ).replace('"total": len(CHANGE) * len(REGRESSION),', '"total": len(REGRESSION),')

    score, measured = _score_reference(
        "", regression_only, repo_files_for("", DEPLOYCTL_TASK.repo)
    )

    assert (score, measured) == (1.0, True)

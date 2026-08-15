"""The second repo-shaped goal: a layered configuration CLI.

`shopcart_shipping` measures whether a model breaks what it was not asked to touch.
This one measures something else entirely — whether a model NOTICED how the project
already works before adding to it.

The trap is real and it is written down in the repo, not in the request. `read_flags`
gives every flag `default=None`, because a non-None value is how that layer says "the
user actually typed this". Reach for argparse's `action="store_true"` and its default
`False` claims a value the user never gave, which then silently outranks the
environment. `DEPLOYCTL_DRY_RUN=1` stops working and nothing errors.

A model that reads `read_flags` gets it. A model that answers from the request alone
writes the idiomatic thing and is wrong.
"""

from __future__ import annotations

from eval.repo_goal import build_repo_task, file_block

_SPEC = '''\
"""Which settings exist and what type each one carries."""

SPEC = {
    "region": "str",
    "timeout": "int",
    "verbose": "bool",
    "replicas": "int",
}

DEFAULTS = {
    "region": "eu-west-1",
    "timeout": 30,
    "verbose": False,
    "replicas": 2,
}
'''

_COERCE = '''\
"""Turning text from a file, the environment or a flag into a typed value."""

from spec import SPEC


class BadValue(ValueError):
    """A setting was given something it cannot be."""


TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


def coerce(name, raw):
    """`raw` as the type `name` declares. Raises BadValue rather than guessing."""
    if name not in SPEC:
        raise BadValue(f"unknown setting {name!r}")
    kind = SPEC[name]
    if isinstance(raw, bool) and kind == "bool":
        return raw
    if kind == "int":
        if isinstance(raw, bool):
            raise BadValue(f"{name} must be a whole number, got {raw!r}")
        try:
            return int(str(raw).strip())
        except ValueError:
            raise BadValue(f"{name} must be a whole number, got {raw!r}") from None
    if kind == "bool":
        text = str(raw).strip().lower()
        if text in TRUE:
            return True
        if text in FALSE:
            return False
        raise BadValue(f"{name} must be true or false, got {raw!r}")
    return str(raw)
'''

_LAYERS = '''\
"""The four layers, lowest priority first: default, file, env, flag."""

import argparse

from coerce import coerce
from spec import DEFAULTS, SPEC

ENV_PREFIX = "DEPLOYCTL_"
LAYERS = ("default", "file", "env", "flag")


def read_defaults():
    return dict(DEFAULTS)


def read_file(text):
    """`name = value` per line, `#` starts a comment. Unknown names are ignored."""
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
    """DEPLOYCTL_<NAME>. Coerced HERE, before the merge, so a bad value in the
    environment is an error even when a flag would have overridden it — a typo you
    cannot see is worse than one that stops you."""
    found = {}
    for name in SPEC:
        key = ENV_PREFIX + name.upper()
        if key in env:
            found[name] = coerce(name, env[key])
    return found


def read_flags(argv):
    """Only the flags the user ACTUALLY passed.

    Every flag defaults to None, and that is load-bearing: a non-None value is this
    layer's way of saying the user typed it. Give a flag argparse's own default --
    False for `store_true`, 0 for `type=int` -- and it will claim a value nobody gave,
    silently outranking the environment. Nothing errors; the setting just stops
    listening to DEPLOYCTL_*.
    """
    parser = argparse.ArgumentParser(prog="deployctl", add_help=False)
    parser.add_argument("--region", default=None)
    parser.add_argument("--timeout", default=None)
    parser.add_argument("--verbose", action="store_true", default=None)
    parser.add_argument("--replicas", default=None)
    namespace, _rest = parser.parse_known_args(argv)
    found = {}
    for name in SPEC:
        value = getattr(namespace, name, None)
        if value is not None:
            found[name] = coerce(name, value)
    return found


def resolve(argv, env, file_text):
    """Every setting, plus which layer decided it.

    Returns {"settings": {...}, "origin": {name: layer}}.
    """
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
    return {"settings": settings, "origin": origin}
'''

STARTING_FILES = (
    ("spec.py", _SPEC),
    ("coerce.py", _COERCE),
    ("layers.py", _LAYERS),
)

_PROBE = '''\
from layers import resolve


def setting(argv, env, text, name):
    return resolve(argv, env, text)["settings"].get(name)


def origin(argv, env, text, name):
    return resolve(argv, env, text)["origin"].get(name)


def rejects(argv, env, text):
    """True when resolve WORKS on a good call and REJECTS this one.

    The control call is the whole point: a `resolve` that raises on everything --
    including one that fails at import -- would satisfy a bare "it raised" check for
    free, which is how an earlier goal in this suite handed an empty answer 12 of 42.
    """
    try:
        resolve([], {}, "")
    except Exception:
        return False
    try:
        resolve(argv, env, text)
    except Exception:
        return True
    return False


def is_bool(argv, env, text, name):
    """Typed, not merely truthy: "0" is a string and it is True."""
    return isinstance(resolve(argv, env, text)["settings"].get(name), bool)


def is_int(argv, env, text, name):
    value = resolve(argv, env, text)["settings"].get(name)
    return isinstance(value, int) and not isinstance(value, bool)
'''

PROBE_FILES = (("_probe.py", _PROBE),)

_FILE_BLOCK = file_block(STARTING_FILES)

DEPLOYCTL_GOAL = f"""\
Here is the configuration layer of our deploy tool. It works today.

{_FILE_BLOCK}

WHAT I NEED
-----------
Two new settings, please:

1. `dry_run` — a true/false setting, off by default. Flag `--dry-run`, environment
   variable `DEPLOYCTL_DRY_RUN`, and `dry_run = ...` in the config file.

2. `retries` — a whole number, 3 by default. Flag `--retries`, environment variable
   `DEPLOYCTL_RETRIES`, and `retries = ...` in the config file.

3. A dry run never retries. When `dry_run` ends up true, `retries` must come out 0 --
   whatever any layer said. But `origin` must still name the layer that supplied the
   retries value, because the audit log reads it to explain where a number came from.

The first two must behave like the settings we already have: same layer order, same
typing, same reporting of which layer decided a value.

Everything the project already does must keep working exactly as it does now.

Answer with the complete contents of every file you changed. Each block must be that
file's COMPLETE current contents — every function and constant it has now, including
the ones you did not touch. A file reduced to the part you changed is not a shorter
answer, it is a broken file. Put each one in its own
```python block whose FIRST LINE is a comment naming the file, like:

```python
# path: layers.py
...the whole file...
```

Files you did not change do not need to be included.
"""

# --- the hidden acceptance suite --------------------------------------------

CHANGE = [
    # --- dry_run reaches every layer
    ("dry_run default", "_probe", "setting", [[], {}, "", "dry_run"], False),
    ("dry_run from file", "_probe", "setting", [[], {}, "dry_run = true", "dry_run"], True),
    (
        "dry_run from env",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_DRY_RUN": "1"}, "", "dry_run"],
        True,
    ),
    ("dry_run from flag", "_probe", "setting", [["--dry-run"], {}, "", "dry_run"], True),
    # THE TRAP. `action="store_true"` without `default=None` makes the flag layer
    # claim False and outrank the environment. Nothing errors; the env just stops
    # working. The answer is in `read_flags`, not in the request.
    (
        "dry_run: env wins when no flag was passed",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_DRY_RUN": "yes"}, "", "dry_run"],
        True,
    ),
    (
        "dry_run: env beats file when no flag was passed",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_DRY_RUN": "on"}, "dry_run = false", "dry_run"],
        True,
    ),
    (
        "dry_run origin is env, not flag",
        "_probe",
        "origin",
        [[], {"DEPLOYCTL_DRY_RUN": "1"}, "", "dry_run"],
        "env",
    ),
    # A second case per default: with one, getting the default wrong cost 1/20 and a
    # criterion worth almost nothing is a criterion the goal does not really test.
    (
        "dry_run default survives other settings being given",
        "_probe",
        "setting",
        [["--retries", "5"], {}, "", "dry_run"],
        False,
    ),
    (
        "dry_run origin is default when nobody set it",
        "_probe",
        "origin",
        [[], {}, "", "dry_run"],
        "default",
    ),
    (
        "dry_run is a real bool, not a string",
        "_probe",
        "is_bool",
        [[], {"DEPLOYCTL_DRY_RUN": "0"}, "", "dry_run"],
        True,
    ),
    (
        "dry_run reads false words",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_DRY_RUN": "off"}, "dry_run = true", "dry_run"],
        False,
    ),
    # --- retries reaches every layer
    ("retries default", "_probe", "setting", [[], {}, "", "retries"], 3),
    ("retries from file", "_probe", "setting", [[], {}, "retries = 7", "retries"], 7),
    ("retries from env", "_probe", "setting", [[], {"DEPLOYCTL_RETRIES": "9"}, "", "retries"], 9),
    ("retries from flag", "_probe", "setting", [["--retries", "5"], {}, "", "retries"], 5),
    (
        "retries: flag beats env",
        "_probe",
        "setting",
        [["--retries", "5"], {"DEPLOYCTL_RETRIES": "9"}, "", "retries"],
        5,
    ),
    (
        "retries is a real int, not a string",
        "_probe",
        "is_int",
        [[], {"DEPLOYCTL_RETRIES": "9"}, "", "retries"],
        True,
    ),
    # Deliberately NOT --dry-run: that now forces retries to 0, so using it here
    # would make the case contradict the rule three entries below it.
    (
        "retries default survives other settings being given",
        "_probe",
        "setting",
        [["--region", "us-east-1"], {}, "", "retries"],
        3,
    ),
    ("retries origin is file", "_probe", "origin", [[], {}, "retries = 7", "retries"], "file"),
    # Coercion happens per layer BEFORE the merge, so a bad value in the environment
    # is an error even when a flag would have overridden it.
    (
        "a bad retries in the environment is refused",
        "_probe",
        "rejects",
        [[], {"DEPLOYCTL_RETRIES": "soon"}, ""],
        True,
    ),
    (
        "a bad retries in the environment is refused even when a flag overrides it",
        "_probe",
        "rejects",
        [["--retries", "5"], {"DEPLOYCTL_RETRIES": "soon"}, ""],
        True,
    ),
    # The interaction. It cannot be pattern-matched from the file being edited,
    # because nothing in this project does anything like it: the override lands AFTER
    # the merge, and `origin` has to survive it.
    (
        "a dry run forces retries to 0",
        "_probe",
        "setting",
        [["--dry-run", "--retries", "5"], {}, "", "retries"],
        0,
    ),
    (
        "a dry run forces retries to 0 from the environment too",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_DRY_RUN": "1", "DEPLOYCTL_RETRIES": "9"}, "", "retries"],
        0,
    ),
    (
        "a dry run still reports where retries came from",
        "_probe",
        "origin",
        [["--dry-run", "--retries", "5"], {}, "", "retries"],
        "flag",
    ),
    (
        "a dry run still reports the env origin for retries",
        "_probe",
        "origin",
        [[], {"DEPLOYCTL_DRY_RUN": "1", "DEPLOYCTL_RETRIES": "9"}, "", "retries"],
        "env",
    ),
    (
        "without a dry run retries is untouched",
        "_probe",
        "setting",
        [["--retries", "5"], {}, "", "retries"],
        5,
    ),
    (
        "a bad dry_run in the file is refused",
        "_probe",
        "rejects",
        [[], {}, "dry_run = maybe"],
        True,
    ),
]

REGRESSION = [
    ("region default", "_probe", "setting", [[], {}, "", "region"], "eu-west-1"),
    (
        "region from flag",
        "_probe",
        "setting",
        [["--region", "us-east-1"], {}, "", "region"],
        "us-east-1",
    ),
    ("timeout from env", "_probe", "setting", [[], {"DEPLOYCTL_TIMEOUT": "45"}, "", "timeout"], 45),
    ("timeout is an int", "_probe", "is_int", [[], {}, "timeout = 45", "timeout"], True),
    ("replicas from file", "_probe", "setting", [[], {}, "replicas = 4", "replicas"], 4),
    # The SAME trap, already working. A model that "simplifies" read_flags into
    # ordinary store_true breaks this one on its way past.
    (
        "verbose: env still wins when no flag was passed",
        "_probe",
        "setting",
        [[], {"DEPLOYCTL_VERBOSE": "1"}, "", "verbose"],
        True,
    ),
    ("verbose from flag", "_probe", "setting", [["--verbose"], {}, "", "verbose"], True),
    (
        "verbose origin is env",
        "_probe",
        "origin",
        [[], {"DEPLOYCTL_VERBOSE": "true"}, "", "verbose"],
        "env",
    ),
    (
        "flag still beats env for region",
        "_probe",
        "origin",
        [["--region", "ap-south-1"], {"DEPLOYCTL_REGION": "eu-west-1"}, "", "region"],
        "flag",
    ),
    (
        "env still beats file for timeout",
        "_probe",
        "origin",
        [[], {"DEPLOYCTL_TIMEOUT": "45"}, "timeout = 10", "timeout"],
        "env",
    ),
    (
        "comments and blank lines in the file are ignored",
        "_probe",
        "setting",
        [[], {}, "# nothing\n\n  \nregion = zz-1", "region"],
        "zz-1",
    ),
    (
        "unknown names in the file are ignored",
        "_probe",
        "rejects",
        [[], {}, "colour = blue"],
        False,
    ),
    (
        "a bad timeout in the environment is refused",
        "_probe",
        "rejects",
        [[], {"DEPLOYCTL_TIMEOUT": "soon"}, ""],
        True,
    ),
    (
        "a bad verbose in the file is refused",
        "_probe",
        "rejects",
        [[], {}, "verbose = maybe"],
        True,
    ),
]


DEPLOYCTL_TASK = build_repo_task(
    task_id="deployctl_layers",
    goal=DEPLOYCTL_GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)

"""The calibration workflow has to produce ids the runtime actually registers.

`--calibrate` writes a profile keyed by model id, and the Registry looks it up by
exact id — and rejects the whole file if any key is unknown. So an id that differs by
one character does not degrade, it stops the process:

    ValueError: quality profiles reference unknown models:
        ['openai_compat/openai/gpt-4o-mini', ...]

That is what the documented three-step workflow did. `eval.calibrate_models` built
`f"{prefix.lower()}/{wire}"` — `openai_compat/...` with an underscore — while
bootstrap registers `openai-compat/...` with a hyphen. Measuring worked, calibrating
worked, and pointing Volante at the result failed at startup every time.
"""

from __future__ import annotations

from eval.calibrate_models import model_id_for

from volante.bootstrap import _openai_compat_from_env


def _bootstrap_id(env: dict[str, str]) -> str:
    slot = _openai_compat_from_env(env, "OPENAI_COMPAT")
    assert slot is not None
    return slot[0].id


def test_measured_id_matches_the_id_the_runtime_registers() -> None:
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://x/v1",
        "OPENAI_COMPAT_KEY": "k",
        "OPENAI_COMPAT_MODEL": "openai/gpt-4o-mini",
    }

    assert model_id_for("OPENAI_COMPAT", "openai/gpt-4o-mini", env) == _bootstrap_id(env)


def test_an_explicit_name_is_honoured_by_both_sides() -> None:
    """`*_NAME` is how a user names a model; calibration must follow the same rule or
    the profile it writes describes a model that does not exist."""
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://x/v1",
        "OPENAI_COMPAT_KEY": "k",
        "OPENAI_COMPAT_MODEL": "openai/gpt-4o-mini",
        "OPENAI_COMPAT_NAME": "my-fast-model",
    }

    assert model_id_for("OPENAI_COMPAT", "openai/gpt-4o-mini", env) == "my-fast-model"
    assert _bootstrap_id(env) == "my-fast-model"


def test_a_numbered_slot_matches_too() -> None:
    env = {
        "OPENAI_COMPAT_2_BASE_URL": "https://y/v1",
        "OPENAI_COMPAT_2_KEY": "k",
        "OPENAI_COMPAT_2_MODEL": "openai/gpt-4o",
    }
    slot = _openai_compat_from_env(env, "OPENAI_COMPAT_2")
    assert slot is not None

    assert model_id_for("OPENAI_COMPAT_2", "openai/gpt-4o", env) == slot[0].id

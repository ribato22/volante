"""Assembling an artifact is not the same job as summarising one.

Every orchestration run funnels its workers' output through one synthesis call, so
the final answer can never be larger than that one call may emit. For a summary that
is correct. For a goal whose PRODUCT is an artifact — a module, a suite, a document
in parts — it means the thing orchestration exists to build cannot exceed what a
single model response could have produced anyway, which removes the only structural
reason to decompose the work at all.

The assembler is the other strategy: put the workers' artifacts together mechanically,
with no model call, so the total is bounded by the sum of the workers' budgets rather
than by one of them.
"""

from __future__ import annotations

import pytest

from volante.assembler import ArtifactAssembler
from volante.blackboard import Blackboard
from volante.types import Entry, Task


def _bb(payloads: dict[str, str]) -> Blackboard:
    plan = [Task(id=tid, description="d", type="code", mode="one_shot") for tid in payloads]
    bb = Blackboard(goal="build the module", plan=plan)
    for i, (tid, payload) in enumerate(payloads.items()):
        bb.append(Entry(run_id="r1", task_id=tid, attempt=0, kind="artifact",
                        payload=payload, model_id="m", usage=None, timestamp=float(i)))
    return bb


class _ExplodingProvider:
    """Any model call at all is a failure of the premise."""

    name = "must-not-be-called"

    async def complete(self, req):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("the assembler made a model call")

    async def stream(self, req, on_text):  # pragma: no cover
        raise AssertionError("the assembler made a model call")


async def test_it_assembles_without_calling_a_model() -> None:
    bb = _bb({
        "t0": "Here is the first part:\n```python\ndef alpha():\n    return 1\n```",
        "t1": "And the second:\n```python\ndef beta():\n    return 2\n```",
    })

    out = await ArtifactAssembler().synthesize("build the module", bb)

    assert "def alpha()" in out
    assert "def beta()" in out
    assert "Here is the first part" not in out, "prose around the code came through"


async def test_it_keeps_the_plan_order() -> None:
    # Dependencies are resolved by the planner; assembling out of order would put a
    # caller before the helper it calls.
    bb = _bb({
        "t0": "```python\ndef helper():\n    return 1\n```",
        "t1": "```python\ndef uses_helper():\n    return helper()\n```",
    })

    out = await ArtifactAssembler().synthesize("g", bb)

    assert out.index("def helper()") < out.index("def uses_helper()")


async def test_an_artifact_without_a_fence_is_kept_whole() -> None:
    # A worker that answers with bare code, or with prose for a non-code goal, must
    # not be dropped silently.
    bb = _bb({"t0": "def gamma():\n    return 3\n"})

    assert "def gamma()" in await ArtifactAssembler().synthesize("g", bb)


async def test_the_result_may_exceed_what_one_synthesis_call_could_emit() -> None:
    # THE point. A synthesis call is capped so a non-streaming generation cannot blow
    # the caller's deadline; assembly has no such generation, so five workers each
    # near their own budget produce five workers' worth of output.
    part = "```python\n" + "\n".join(f"def f{i}(): return {i}" for i in range(400)) + "\n```"
    bb = _bb({f"t{n}": part for n in range(5)})

    out = await ArtifactAssembler().synthesize("g", bb)

    assert len(out) // 4 > 8192, "assembly is still bounded by one call's ceiling"


async def test_no_artifacts_is_reported_not_silently_empty() -> None:
    with pytest.raises(ValueError, match="no artifacts"):
        await ArtifactAssembler().synthesize("g", Blackboard(goal="g", plan=[]))


async def test_progress_still_reaches_the_caller() -> None:
    # The CLI and Web UI stream the synthesis phase; assembling must not make that
    # surface go silent.
    seen: list[str] = []
    bb = _bb({"t0": "```python\ndef alpha(): return 1\n```"})

    await ArtifactAssembler().synthesize("g", bb, seen.append)

    assert seen and "def alpha()" in "".join(seen)


async def test_it_satisfies_what_runtime_asks_of_a_synthesizer() -> None:
    # Runtime probes these with getattr and calls them when present; a component that
    # has them but breaks on them would fail at run time, not here.
    a = ArtifactAssembler()
    a.set_call_gate(lambda _model_id: None)
    a.set_model_limits(context_window=200_000, max_output_tokens=64_000)


async def test_files_stay_separated_so_a_readme_cannot_break_the_module() -> None:
    """The defect this exists to prevent, measured: 6 runs of 6 scored 0.000.

    Three workers produce a module, a test module and a README. Stripping the fences
    concatenated the README's prose into the middle of the Python and left nothing
    marking where one file ended — a syntax error every time, in an answer that looks
    complete.
    """
    import ast

    from eval.harness import extract_python

    bb = _bb(
        {
            "t1": "Here you go.\n\n```python\ndef resolve(rules, path):\n    return 'allow'\n```\n",
            "t2": "```python\ndef test_resolve():\n    assert resolve([], 'a') == 'allow'\n```",
            "t3": "```markdown\n# Resolver\n\nThis module resolves rules based on precedence.\n```",
        }
    )

    assembled = await ArtifactAssembler().synthesize("goal", bb)

    ast.parse(extract_python(assembled))
    assert "This module resolves rules" not in extract_python(assembled)
    assert "```markdown" in assembled


async def test_an_unfenced_artifact_is_fenced_rather_than_bled_into_the_next() -> None:
    """A worker that answers bare must not contaminate the file after it."""
    import ast

    from eval.harness import extract_python

    bb = _bb(
        {
            "t1": "```python\nx = 1\n```",
            "t2": "# Notes\n\nJust some prose, no fence at all.",
        }
    )

    assembled = await ArtifactAssembler().synthesize("goal", bb)

    assert ast.parse(extract_python(assembled)) is not None
    assert "Just some prose" not in extract_python(assembled)

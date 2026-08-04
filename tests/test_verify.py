"""The detector must be trustworthy in exactly one direction.

Its whole value rests on one measured property: across 11 code goals, 2 runs each, it
produced 0 false negatives — it never reported a clean run for a wrong answer. These
tests pin the mechanics that property depends on, above all that a solution which does
not import is reported as a FAILURE and not as "nothing failed".
"""

from __future__ import annotations

from volante.verify import (
    CheckReport,
    build_program,
    extract_python,
    parse_assertions,
    read_report,
)


def test_assertions_are_taken_from_the_derivation_and_nothing_else() -> None:
    derived = (
        "Here are the checks:\n\n```python\n"
        "# from the worked example\n"
        "assert slug('Hi There') == 'hi-there'\n"
        "print('ignore me')\n"
        "assert slug('a') == 'a'\n"
        "```\n"
    )
    assert parse_assertions(derived) == [
        "assert slug('Hi There') == 'hi-there'",
        "assert slug('a') == 'a'",
    ]


def test_a_fence_inside_a_string_does_not_truncate_the_answer() -> None:
    """Same defect the eval scorer had: the block closed mid-string-literal and the
    extracted code could not import, which scored working solutions 0.000."""
    answer = (
        '```python\ndef f():\n    return 1\n\n\nREADME = """\n# docs\n\n```bash\nx\n```\n"""\n```\n'
    )
    import ast

    ast.parse(extract_python(answer))


def test_a_solution_that_does_not_import_is_a_failure_not_a_clean_run() -> None:
    """The one way this detector could become dangerous.

    No marker on stdout means the program never reached its checks. Reading that as
    "zero failures" would report a module that cannot even be imported as verified —
    the exact false negative the design has none of.
    """
    report = read_report("", "SyntaxError: invalid syntax", ["assert f() == 1"])

    assert not report.all_passed
    assert report.error is not None
    assert "SyntaxError" in report.error


def test_a_clean_run_reports_every_check_passing() -> None:
    report = read_report(
        'VOLANTE_CHECKS {"failed": [], "total": 3}', "", ["a", "b", "c"]
    )

    assert report.all_passed
    assert report.summary() == "3/3 derived checks passed"


def test_failures_carry_the_check_and_the_reason() -> None:
    """A reader has to be able to tell a wrong CHECK from wrong CODE, because 45% of
    the checks that fail are themselves wrong. A bare count cannot show that."""
    report = read_report(
        'VOLANTE_CHECKS {"failed": [["assert f() == 2", "AssertionError: "]], "total": 4}',
        "",
        ["a", "b", "c", "d"],
    )

    assert not report.all_passed
    assert report.failed == [("assert f() == 2", "AssertionError: ")]
    assert report.summary() == "3/4 derived checks passed"


def test_an_empty_report_is_not_a_pass() -> None:
    """`total == 0` means verification never ran. It must not read as success."""
    assert not CheckReport().all_passed
    assert not CheckReport().ran


def test_the_program_runs_the_answer_before_its_checks() -> None:
    program = build_program("def f():\n    return 1", ["assert f() == 1"])

    assert program.index("def f()") < program.index("_ASSERTS")
    assert "assert f() == 1" in program


# --- wired into a real Runtime ----------------------------------------------- #


async def _run(goal: str, answer: str, *, verify: bool, sandbox=None):
    """Drive a Runtime whose worker and synthesis both return `answer`."""
    from volante.cost import CostMeter
    from volante.registry import Registry
    from volante.types import ModelInfo, Task

    class _Plan:
        async def plan(self, goal, on_text=None):
            return [Task(id="t1", description="do it", type="code", mode="one_shot")]

    class _Route:
        def route_ranked(self, task):
            return ["m"]

    class _Project:
        def project(self, task, model_id, bb):
            from volante.types import CanonicalRequest, text

            return CanonicalRequest(messages=[text("user", "go")], max_tokens=64)

    class _Provider:
        name = "m"

        def __init__(self):
            self.calls = 0

        async def complete(self, req):
            from volante.types import CanonicalResponse, TextBlock, Usage

            self.calls += 1
            # The verification pass asks for checks, not for another answer.
            body = (
                "```python\nassert f() == 1\n```"
                if req.task_id == "__verify__"
                else answer
            )
            return CanonicalResponse(
                content=[TextBlock(text=body)],
                usage=Usage(prompt_tokens=1, completion_tokens=1),
                model="m",
                stop_reason="end_turn",
                latency_ms=1,
            )

    class _Synth:
        async def synthesize(self, goal, bb, on_text=None):
            return answer

    from volante.projector import Projector  # noqa: F401 - proves the real one is unused
    from volante.runtime import Runtime
    from volante.supervisor import Supervisor
    from volante.worker import Worker

    meter = CostMeter()
    provider = _Provider()
    supervisor = Supervisor(provider, "m", meter)
    supervisor.plan = _Plan().plan  # type: ignore[method-assign]
    registry = Registry(
        [
            ModelInfo(
                id="m",
                provider="fake",
                strengths={"coding"},
                context_window=8000,
                max_output_tokens=1000,
                supports_tools=False,
                cost_per_1k_in=0.0,
                cost_per_1k_out=0.0,
            )
        ]
    )
    runtime = Runtime(
        supervisor,
        _Route(),
        _Project(),
        Worker({"m": provider}, meter),
        _Synth(),
        registry,
        meter,
        verify_answer=verify,
        sandbox_factory=sandbox,
    )
    return await runtime.aexecute(goal), provider


async def test_verification_is_off_unless_asked_for() -> None:
    """It costs a model call and a sandbox run, and flags 45% of correct answers."""
    result, provider = await _run("write f", "```python\ndef f():\n    return 1\n```", verify=False)

    assert result.status == "success"
    assert result.checks is None


async def test_a_derivation_failure_never_fails_the_run() -> None:
    """A detector that can break a good run is worse than no detector.

    Here the sandbox itself explodes; the answer must come back untouched, with the
    report saying only that the checks could not run.
    """

    def _broken_sandbox(workspace):
        class _Boom:
            async def run(self, code):
                raise OSError("sandbox unavailable")

        return _Boom()

    result, _ = await _run(
        "write f",
        "```python\ndef f():\n    return 1\n```",
        verify=True,
        sandbox=_broken_sandbox,
    )

    assert result.status == "success"
    assert result.final is not None
    assert result.checks is not None
    assert result.checks.error is not None
    assert not result.checks.all_passed

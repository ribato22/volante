from __future__ import annotations

from volante.tools.base import Tool, ToolRegistry, is_tool_error
from volante.tools.run_python import RunPythonTool
from volante.tools.sandbox import ExecResult
from volante.types import ToolSpec


class _NeverRuns:
    """Stands in for a sandbox on paths that must reject before executing anything."""

    async def run(self, code: str) -> ExecResult:
        raise AssertionError("the tool executed code it should have refused")


class _FailingSandbox:
    async def run(self, code: str) -> ExecResult:
        return ExecResult(
            stdout="", stderr="SystemExit: 1", exit_code=1, timed_out=False
        )


class _Echo:
    name = "echo"
    spec = ToolSpec(name="echo", description="echo", input_schema={"type": "object"})

    async def run(self, args: dict) -> str:
        return str(args)


async def test_tool_protocol_is_satisfied_structurally() -> None:
    t: Tool = _Echo()  # cek struktural terhadap Protocol
    assert t.name == "echo"
    assert await t.run({"a": 1}) == "{'a': 1}"


def test_tool_registry_is_name_to_tool() -> None:
    reg: ToolRegistry = {"echo": _Echo()}
    assert reg["echo"].spec.name == "echo"


async def test_every_built_in_tool_marks_its_refusals_with_the_prefix(tmp_path) -> None:
    # Required-tool enforcement reads this prefix to tell "the tool did its job" from
    # "the tool declined to". A built-in that words a refusal differently would be
    # silently counted as a satisfied capability, which is the bug this guards.
    from volante.tools.fetch_url import FetchUrlTool
    from volante.tools.read_file import ReadFileTool
    from volante.tools.run_python import RunPythonTool

    refusals = [
        await ReadFileTool(tmp_path).run({}),                       # missing argument
        await ReadFileTool(tmp_path).run({"path": "../escape"}),    # policy denial
        await ReadFileTool(tmp_path).run({"path": "absent.txt"}),   # not a file
        await FetchUrlTool(["example.com"]).run({}),                # missing argument
        await FetchUrlTool(["example.com"]).run({"url": "ftp://example.com"}),
        await FetchUrlTool(["example.com"]).run({"url": "http://elsewhere.test/"}),
        await RunPythonTool(_NeverRuns()).run({}),                  # missing argument
    ]
    for refusal in refusals:
        assert is_tool_error(refusal), f"refusal not marked as an error: {refusal!r}"


async def test_code_that_ran_and_failed_is_not_a_tool_error() -> None:
    # The line matters: run_python reporting a non-zero exit means the sandbox DID
    # execute. The capability was obtained; the model's code was wrong. Counting that
    # as a tool refusal would fail tasks whose required tool worked perfectly.
    result = await RunPythonTool(_FailingSandbox()).run({"code": "raise SystemExit(1)"})

    assert not is_tool_error(result)
    assert result.startswith("exit=1")

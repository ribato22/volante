from __future__ import annotations

from baton.tools.base import Tool, ToolRegistry
from baton.types import ToolSpec


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

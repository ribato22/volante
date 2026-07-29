from __future__ import annotations

from typing import Protocol

from volante.types import ToolSpec

# A tool never raises for an ordinary refusal — a policy denial, a malformed
# argument or an I/O failure comes back as a STRING the model can read and correct,
# so one bad call cannot abort the loop. That makes the prefix below the only signal
# distinguishing "the tool did its job" from "the tool declined to", which is what
# required-tool enforcement has to judge. A tool that RAN and reported a failing
# outcome (`run_python` returning `exit=1`) did its job and is not an error here.
TOOL_ERROR_PREFIX = "error: "


def is_tool_error(result: str) -> bool:
    """True when a tool result reports a refusal or failure rather than output."""
    return result.startswith(TOOL_ERROR_PREFIX)


class Tool(Protocol):
    """A host-mediated capability offered to a model.

    ``run`` must not raise for an expected failure: return a string starting with
    ``TOOL_ERROR_PREFIX`` instead, so the model gets a correctable turn and callers
    can tell a refused call from a successful one.
    """

    name: str
    spec: ToolSpec

    async def run(self, args: dict) -> str: ...


ToolRegistry = dict[str, Tool]

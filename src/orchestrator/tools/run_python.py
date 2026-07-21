from __future__ import annotations

from orchestrator.tools.sandbox import Sandbox
from orchestrator.types import ToolSpec


def _cap(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    marker = "\n…[dipotong]…\n"
    keep = max(limit - len(marker), 0)
    head = keep // 2
    tail = keep - head
    return s[:head] + marker + s[len(s) - tail :]


class RunPythonTool:
    name = "run_python"

    def __init__(self, sandbox: Sandbox, max_result_chars: int = 10_000) -> None:
        self.sandbox = sandbox
        self.max_result_chars = max_result_chars
        self.spec = ToolSpec(
            name="run_python",
            description="Execute Python code in a persistent workspace; returns stdout/stderr.",
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        )

    async def run(self, args: dict) -> str:
        code = args.get("code")
        if not isinstance(code, str):
            return "error: 'code' (string) argument is required"
        r = await self.sandbox.run(code)
        exit_label = "timeout" if r.timed_out else str(r.exit_code)
        body = f"exit={exit_label}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        return _cap(body, self.max_result_chars)

from __future__ import annotations

from pathlib import Path

from volante.tools.base import ToolRegistry
from volante.tools.run_python import RunPythonTool
from volante.tools.sandbox import resolve_sandbox_mode, sandbox_for


def build_agentic_tools(
    workspace, *, allowed_domains=None, read_root=None, sandbox_mode=None
) -> ToolRegistry:
    """run_python (when a sandbox is available) + fetch_url/read_file bila config diberikan.

    ``sandbox_mode`` is normally resolved once at bootstrap and passed in, so the tool
    set here matches the tools the planner was told about. When no isolating sandbox is
    available the code-execution tool is simply not built: Volante withholds the capability
    instead of running model-generated code with full host access.
    """
    tools: ToolRegistry = {}
    if sandbox_mode is None:
        sandbox_mode, _ = resolve_sandbox_mode()
    if sandbox_mode != "unavailable":
        tools["run_python"] = RunPythonTool(sandbox_for(workspace, mode=sandbox_mode))
    if allowed_domains:
        from volante.tools.fetch_url import FetchUrlTool

        tools["fetch_url"] = FetchUrlTool(set(allowed_domains))
    if read_root is not None:
        from volante.tools.read_file import ReadFileTool

        tools["read_file"] = ReadFileTool(Path(read_root))
    return tools

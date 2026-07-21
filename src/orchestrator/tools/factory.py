from __future__ import annotations

from pathlib import Path

from orchestrator.tools.base import ToolRegistry
from orchestrator.tools.run_python import RunPythonTool
from orchestrator.tools.sandbox import sandbox_for


def build_agentic_tools(
    workspace, *, allowed_domains=None, read_root=None
) -> ToolRegistry:
    """run_python (via sandbox_for) + fetch_url/read_file bila config diberikan."""
    tools: ToolRegistry = {"run_python": RunPythonTool(sandbox_for(workspace))}
    if allowed_domains:
        from orchestrator.tools.fetch_url import FetchUrlTool

        tools["fetch_url"] = FetchUrlTool(set(allowed_domains))
    if read_root is not None:
        from orchestrator.tools.read_file import ReadFileTool

        tools["read_file"] = ReadFileTool(Path(read_root))
    return tools

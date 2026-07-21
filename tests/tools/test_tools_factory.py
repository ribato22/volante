from __future__ import annotations

from pathlib import Path

from orchestrator.tools.factory import build_agentic_tools


def test_default_run_python_only(tmp_path: Path) -> None:
    tools = build_agentic_tools(tmp_path)
    assert set(tools) == {"run_python"}


def test_adds_fetch_url_and_read_file(tmp_path: Path) -> None:
    tools = build_agentic_tools(
        tmp_path, allowed_domains={"example.com"}, read_root=tmp_path
    )
    assert set(tools) == {"run_python", "fetch_url", "read_file"}
    assert tools["fetch_url"].name == "fetch_url"
    assert tools["read_file"].name == "read_file"

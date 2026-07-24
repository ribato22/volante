"""A Model Context Protocol (MCP) server that exposes Baton as a single tool,
`baton_run`, so an AI assistant running *inside your IDE* (Claude Code, Cursor,
VS Code Copilot agent mode, Windsurf, …) can delegate a goal to Baton and get the
orchestrated final answer back.

The heavy lifting stays in the engine: `run_goal` just drives a fresh Runtime and
maps its `RunResult` to a plain dict; `format_result` renders that for the calling
agent. `mcp` is imported lazily inside `build_server` so `import baton_mcp` works
without the optional `mcp` extra installed (mirroring how `webui` treats fastapi).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _default_runtime_factory(prefer: str | None) -> Callable[[], Any]:
    """Build a runtime factory from the environment, opting into subscription
    CLI-agent providers exactly like the `baton` CLI and the Web UI. Raises if no
    provider is configured, so the MCP client surfaces a clear setup error rather
    than silently running a demo."""
    from baton.bootstrap import build_providers_from_env, make_runtime_factory

    registry, providers, model_id = build_providers_from_env(include_subscription=True)
    return make_runtime_factory(
        registry, providers, model_id, prefer=prefer or "quality"
    )


async def run_goal(
    goal: str,
    *,
    prefer: str | None = None,
    runtime_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Orchestrate ``goal`` end-to-end and return a JSON-serializable result.

    ``runtime_factory`` is injectable for testing; when omitted, providers are read
    from the environment. Returns the two-ledger cost split (``billed_usd`` cash vs
    ``credit_usd`` subscription value) alongside the final answer.
    """
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("goal must be a non-empty string")
    factory = runtime_factory or _default_runtime_factory(prefer)
    runtime = factory()
    res = await runtime.aexecute(goal)
    return {
        "status": res.status,
        "final": res.final,
        "failed_task": res.failed_task,
        "cost_usd": res.cost_usd,
        "billed_usd": res.billed_usd,
        "credit_usd": res.credit_usd,
        "duration_ms": res.duration_ms,
    }


def format_result(result: dict[str, Any]) -> str:
    """Render a `run_goal` result as text for the calling agent: the final answer
    followed by an honest status + cash/plan-credit + duration footer."""
    status = result.get("status")
    final = result.get("final") or ""
    billed = float(result.get("billed_usd") or 0.0)
    credit = float(result.get("credit_usd") or 0.0)
    secs = int(result.get("duration_ms") or 0) / 1000
    footer = (
        f"status: {status} · cash ${billed:.6f} "
        f"· plan credit ${credit:.6f} · {secs:.1f}s"
    )
    if status != "success":
        failed = result.get("failed_task")
        head = f"Baton run {status}" + (f" (failed task: {failed})" if failed else "")
        return f"{head}\n\n{final or '(no output produced)'}\n\n---\n{footer}"
    return f"{final}\n\n---\n{footer}"


def build_server(runtime_factory: Callable[[], Any] | None = None) -> Any:
    """Construct the FastMCP server exposing the `baton_run` tool.

    `mcp` is imported here (not at module top) so the package imports cleanly
    without the `mcp` extra; install it with ``uv sync --extra mcp``.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("baton")

    @server.tool()
    async def baton_run(goal: str, prefer: str | None = None) -> str:
        """Orchestrate GOAL across the configured models. Baton plans a task DAG,
        routes each sub-task to the best-quality model capable of it (by strengths and
        tool support), runs it one-shot or in an agentic tool loop, then synthesizes one
        final answer. Returns that answer plus a status/cost footer. `prefer` optionally
        sets the routing objective (default "quality")."""
        result = await run_goal(goal, prefer=prefer, runtime_factory=runtime_factory)
        return format_result(result)

    return server

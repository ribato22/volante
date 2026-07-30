"""A Model Context Protocol (MCP) server that exposes Volante as a single tool,
`volante_run`, so an AI assistant running *inside your IDE* (Claude Code, Cursor,
VS Code Copilot agent mode, Windsurf, …) can delegate a goal to Volante and get the
orchestrated final answer back.

The heavy lifting stays in the engine: `run_goal` just drives a fresh Runtime and
maps its `RunResult` to a plain dict; `format_result` renders that for the calling
agent. `mcp` is imported lazily inside `build_server` so `import volante_mcp` works
without the optional `mcp` extra installed (mirroring how `webui` treats fastapi).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

# The routing objectives the engine accepts. Declared here as a Literal so FastMCP
# publishes an ENUM in the tool schema: the calling model can then read the legal
# values off the contract instead of guessing "fast" or "cheapest", and a bad value is
# refused by the client before it reaches us. Kept in sync with router._OBJECTIVES by
# a test rather than by an import, because this is a published wire contract — adding
# an objective to the engine should be a deliberate decision to publish it, not an
# automatic one.
Objective = Literal["quality", "cheap", "local", "cash_protect_quota"]
_OBJECTIVES: frozenset[str] = frozenset(("quality", "cheap", "local", "cash_protect_quota"))

# One verified factory per objective, for the life of the server process.
#
# `make_verified_runtime_factory` runs a LIVE plan-gate probe, which on a
# subscription-only setup is a real `claude -p` spawn: measured at 5.03 s and one
# interactive-quota unit. Building it per tool call charged that to EVERY volante_run
# — +33% wall time and +33% quota on top of the actual work — to re-verify what the
# previous call had verified seconds earlier. The engine was already written for
# reuse: bootstrap._runtime_factory's `preflight_pending` charges the gate to the
# first Runtime only, and the Web UI has always reused one factory across runs. The
# MCP server was the only long-lived surface throwing it away.
_factory_cache: dict[str, Callable[[], Any]] = {}
_factory_lock = asyncio.Lock()


def _reset_factory_cache() -> None:
    """Drop the memoised factories. For tests, and for a caller that wants the next
    run to re-verify — e.g. after credentials changed under a long-lived server."""
    _factory_cache.clear()


async def _default_runtime_factory(prefer: str | None) -> Callable[[], Any]:
    """Build (or reuse) a runtime factory from the environment, opting into
    subscription CLI-agent providers exactly like the `volante` CLI and the Web UI.
    Raises if no provider is configured, so the MCP client surfaces a clear setup
    error rather than silently running a demo."""
    from volante.bootstrap import (
        build_providers_from_env,
        make_verified_runtime_factory,
    )

    objective = prefer or "quality"
    cached = _factory_cache.get(objective)
    if cached is not None:
        return cached
    # Under the lock, and re-checked inside it: two concurrent volante_run calls on a
    # cold cache would otherwise each run the live gate, which is the exact cost this
    # cache exists to avoid.
    async with _factory_lock:
        cached = _factory_cache.get(objective)
        if cached is not None:
            return cached
        registry, providers, model_id = build_providers_from_env(
            include_subscription=True
        )
        factory = await make_verified_runtime_factory(
            registry, providers, model_id, prefer=objective
        )
        _factory_cache[objective] = factory
        return factory


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
    # Before the factory, deliberately. Router validated this itself, one line AFTER
    # the awaited live preflight, so a schema-valid guess like "fast" burnt a real
    # interactive-quota call and ~5 s before reporting a name that could have been
    # rejected for free. The error names every legal value so one retry is enough.
    if prefer is not None and prefer not in _OBJECTIVES:
        raise ValueError(
            f"unknown routing objective {prefer!r}; "
            f"expected one of {sorted(_OBJECTIVES)}"
        )
    factory = runtime_factory
    if factory is None:
        factory = await _default_runtime_factory(prefer)
    runtime = factory()
    res = await runtime.aexecute(goal)
    result = {
        "status": res.status,
        "final": res.final,
        "failed_task": res.failed_task,
        "error_code": getattr(res, "error_code", None),
        "error_message": getattr(res, "error_message", None),
        "cost_usd": res.cost_usd,
        "billed_usd": res.billed_usd,
        "credit_usd": res.credit_usd,
        "cost_estimated": getattr(res, "cost_estimated", False),
        "capability_notice": getattr(res, "capability_notice", None),
        "duration_ms": res.duration_ms,
        "subscription_calls": getattr(res, "subscription_calls", 0),
        "routing_decisions": getattr(res, "routing_decisions", {}),
    }
    # Persist to the usage ledger so IDE-delegated runs are auditable later (the Web UI
    # "Usage" dashboard and `volante --usage` read this). Best-effort: never fails a run.
    from volante.observability import record_run

    record_run(result, goal=goal, prefer=prefer, source="mcp")
    return result


def format_result(result: dict[str, Any]) -> str:
    """Render a `run_goal` result as text for the calling agent: the final answer
    followed by an honest status + cash/plan-credit + duration footer."""
    status = result.get("status")
    final = result.get("final") or ""
    billed = float(result.get("billed_usd") or 0.0)
    credit = float(result.get("credit_usd") or 0.0)
    secs = int(result.get("duration_ms") or 0) / 1000
    subscription_calls = int(result.get("subscription_calls") or 0)
    # The estimate marker rides along with the amounts: without it the calling agent
    # would present inferred costs as if the provider had reported them. It marks BOTH
    # amounts (the flag is run-wide, and estimated usage can land in either ledger) and
    # spells the caveat out, because this footer is the agent's only cost signal.
    approx = "~" if result.get("cost_estimated") else ""
    footer = (
        f"status: {status} · cash {approx}${billed:.6f} "
        f"· plan credit {approx}${credit:.6f} "
        f"· subscription calls {subscription_calls} "
        f"· {secs:.1f}s"
    )
    if approx:
        footer += (
            " · amounts marked ~ are estimated: a provider reported no token counts "
            "for at least one call"
        )
    # The calling agent has no access to this server's stderr, so a withheld or
    # unisolated capability has to be stated here or it is invisible.
    notice = result.get("capability_notice")
    if notice:
        footer += f"\nnotice: {notice}"
    decisions = result.get("routing_decisions")
    if isinstance(decisions, dict) and decisions:
        routes: list[str] = []
        for task_id, raw in decisions.items():
            if not isinstance(raw, dict):
                continue
            selected = raw.get("selected_model_id")
            executed = raw.get("executed_model_id") or selected
            objective = raw.get("objective", "quality")
            route = f"{task_id}: {selected}"
            if executed != selected:
                route += f" → {executed}"
            routes.append(f"{route} ({objective})")
        if routes:
            footer += "\nroutes: " + "; ".join(routes)
    if status != "success":
        failed = result.get("failed_task")
        head = f"Volante run {status}" + (f" (failed task: {failed})" if failed else "")
        error = result.get("error_message")
        body = final or error or "(no output produced)"
        return f"{head}\n\n{body}\n\n---\n{footer}"
    return f"{final}\n\n---\n{footer}"


def build_server(runtime_factory: Callable[[], Any] | None = None) -> Any:
    """Construct the FastMCP server exposing the `volante_run` tool.

    `mcp` is imported here (not at module top) so the package imports cleanly
    without the `mcp` extra; install it with ``uv sync --extra mcp``.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("volante")

    @server.tool()
    async def volante_run(goal: str, prefer: Objective | None = None) -> str:
        """Orchestrate GOAL across the configured models. Volante plans a task DAG,
        filters hard capability mismatches, and routes each sub-task to the best
        predicted fit using configured metadata and optional evaluation evidence. It
        runs tasks one-shot or in an agentic tool loop, then synthesizes one final
        answer. Returns that answer plus status, cost, and route evidence.

        `prefer` sets the routing objective and defaults to "quality":
          - "quality"             best predicted fit, price only breaks ties
          - "cheap"               lowest cash cost that still meets the requirements
          - "local"               prefer locally hosted models
          - "cash_protect_quota"  spend cash before subscription quota
        """
        result = await run_goal(goal, prefer=prefer, runtime_factory=runtime_factory)
        return format_result(result)

    return server

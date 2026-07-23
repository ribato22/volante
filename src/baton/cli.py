from __future__ import annotations

import argparse
import asyncio
import sys

from baton.bootstrap import build_providers_from_env, make_runtime_factory
from baton.registry import Registry
from baton.runtime import Runtime
from baton.types import RunResult

# The four routing objectives (§6.2); the CLI default is cash_protect_quota (§7.2).
_PREFER_CHOICES = ("cash_protect_quota", "quality", "local", "cheap")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Orchestrate GOAL across your configured models.",
    )
    parser.add_argument("goal", help="the objective to orchestrate")
    parser.add_argument(
        "--prefer",
        choices=_PREFER_CHOICES,
        default="cash_protect_quota",
        help="routing objective (default: cash_protect_quota)",
    )
    parser.add_argument(
        "--provider",
        "-P",
        default=None,
        help="restrict the planner/synth baseline to this provider name",
    )
    parser.add_argument("--model", default=None, help="override the planner/synth model_id")
    parser.add_argument(
        "--json", action="store_true", help="print the run summary as one JSON line"
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="disable live streaming of plan/worker/synth text",
    )
    return parser.parse_args(argv)


def _build(args: argparse.Namespace) -> tuple[Registry, Runtime]:
    """Build (registry, fresh runtime) from env via bootstrap. The CLI opts into
    subscription providers (include_subscription=True); actual registration still
    requires CLAUDE_CODE_ENABLED / CODEX_ENABLED inside build_providers_from_env,
    which prints the honest "consumes interactive subscription quota" warning
    (§7.2/§9). Not unit-tested (touches env/network); tests monkeypatch this seam."""
    registry, providers, model_id = build_providers_from_env(
        prefer=args.prefer, include_subscription=True
    )
    if args.provider is not None:
        match = next(
            (
                m.id
                for m in registry.all()
                if m.provider == args.provider and m.id in providers
            ),
            None,
        )
        if match is None:
            raise RuntimeError(f"no configured model for provider {args.provider!r}")
        model_id = match
    if args.model is not None:
        if args.model not in providers:
            raise RuntimeError(f"model {args.model!r} is not configured")
        model_id = args.model
    make_runtime = make_runtime_factory(registry, providers, model_id, prefer=args.prefer)
    return registry, make_runtime()


def _summary_lines(result: RunResult, registry: Registry) -> list[str]:
    head = f"status: {result.status}"
    if result.failed_task:
        head += f"  failed_task: {result.failed_task}"
    return [
        head,
        f"billed_usd: ${result.billed_usd:.6f}   credit_usd: ${result.credit_usd:.6f}",
        f"duration_ms: {result.duration_ms}",
    ]


async def _aexecute(
    runtime: Runtime, goal: str, *, stream: bool, collected: list[str]
) -> RunResult:
    if not stream:
        return await runtime.aexecute(goal)

    def on_text(delta: str) -> None:  # planning + synthesis (sequential phases)
        collected.append(delta)
        sys.stdout.write(delta)
        sys.stdout.flush()

    def on_worker(task_id: str, delta: str) -> None:  # parallel workers, labeled
        chunk = f"[{task_id}] {delta}"
        collected.append(chunk)
        sys.stdout.write(chunk)
        sys.stdout.flush()

    return await runtime.aexecute(goal, on_text=on_text, on_worker_text=on_worker)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        registry, runtime = _build(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    collected: list[str] = []
    result = asyncio.run(
        _aexecute(runtime, args.goal, stream=not args.no_stream, collected=collected)
    )
    if result.final:
        print("\n\nFINAL:\n" + str(result.final).strip())
    for line in _summary_lines(result, registry):
        print(line)
    return 0 if result.status == "success" else 1

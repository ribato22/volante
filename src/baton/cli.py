from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from baton.bootstrap import (
    _planner_model_id,
    build_providers_from_env,
    make_runtime_factory,
    verify_claude_plan_gate,
)
from baton.providers.base import LLMProvider
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


def _ensure_planner_gate(
    registry: Registry, providers: dict[str, LLMProvider], planner_id: str
) -> None:
    """§7.1 guard: `make_runtime_factory` picks `planner_id` via `_planner_model_id`,
    which only falls back to a subscription (`plan_included`/`plan_credit`) model when
    NO temperature-controllable card model is available (subscription-only setup). A
    card planner (the normal case) is temperature-controllable and skips the gate
    entirely — no probe call, zero cost/latency added to the common path.

    When the fallback DOES happen, `claude -p`/`codex exec` ignore `temperature` and
    cannot be trusted a priori to emit deterministic, parseable plans. Run ONE live
    probe (`verify_claude_plan_gate`, reusing the real Supervisor parser) before
    trusting it; raise a clear `RuntimeError` on failure instead of silently running
    an ungated planner that can't guarantee valid plans."""
    if registry.get(planner_id).billing == "card":
        return
    ok = asyncio.run(verify_claude_plan_gate(providers[planner_id], planner_id))
    if not ok:
        raise RuntimeError(
            f"subscription planner {planner_id!r} failed the live parse-plan gate (§7.1): "
            "it did not return a plan that survives the supervisor's own parser, so it "
            "cannot be trusted to plan deterministically. Configure a card-billed planner "
            "(e.g. ANTHROPIC_API_KEY, an OPENAI_COMPAT_* slot, or Ollama) and retry."
        )


def _build(args: argparse.Namespace) -> tuple[Registry, Runtime]:
    """Build (registry, fresh runtime) from env via bootstrap. The CLI opts into
    subscription providers (include_subscription=True); actual registration still
    requires CLAUDE_CODE_ENABLED / CODEX_ENABLED inside build_providers_from_env,
    which prints the honest "consumes interactive subscription quota" warning
    (§7.2/§9). Not unit-tested (touches env/network); tests monkeypatch this seam.
    `_ensure_planner_gate` (unit-tested directly) is called here so a subscription-only
    setup fails loudly instead of silently running an ungated `claude -p` planner."""
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
    planner_id = _planner_model_id(registry, providers, model_id)
    _ensure_planner_gate(registry, providers, planner_id)
    make_runtime = make_runtime_factory(registry, providers, model_id, prefer=args.prefer)
    return registry, make_runtime()


def _subscription_models(result: RunResult, registry: Registry) -> int:
    """Number of DISTINCT subscription-billed models observed in usage_total
    (plan_included / plan_credit) -- NOT a call count (e.g. 4 calls to one
    claude-code model still reports 1 here). The exact per-call count is
    intentionally NOT a RunResult field (locked contract §5.3, surfaced only as
    blackboard status entries the CLI can't read); this is the CLI-side proxy
    from usage_total + registry billing."""
    n = 0
    for model_id in result.usage_total:
        try:
            info = registry.get(model_id)
        except ValueError:  # Registry.get raises on unknown model_id
            continue
        if info.billing in ("plan_included", "plan_credit"):
            n += 1
    return n


def _summary_lines(result: RunResult, registry: Registry) -> list[str]:
    head = f"status: {result.status}"
    if result.failed_task:
        head += f"  failed_task: {result.failed_task}"
    return [
        head,
        f"billed_usd: ${result.billed_usd:.6f}   credit_usd: ${result.credit_usd:.6f}",
        f"duration_ms: {result.duration_ms}   "
        f"subscription_models: {_subscription_models(result, registry)}",
    ]


def _summary_json(result: RunResult, registry: Registry) -> str:
    return json.dumps(
        {
            "status": result.status,
            "final": result.final,
            "failed_task": result.failed_task,
            "billed_usd": result.billed_usd,
            "credit_usd": result.credit_usd,
            "cost_usd": result.cost_usd,
            "duration_ms": result.duration_ms,
            "subscription_models": _subscription_models(result, registry),
        }
    )


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


def _handle_broken_pipe() -> int:
    """The reader closed the pipe early (e.g. `baton goal | head`). Redirect stdout to
    os.devnull so the interpreter's shutdown flush doesn't raise a SECOND
    BrokenPipeError (the classic 'Exception ignored in: <...>' noise), then exit
    cleanly -- a closed downstream reader is not a baton failure."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:
        pass  # stdout has no real fd (e.g. under test) -- nothing to redirect
    return 0


def _print_interrupted(collected: list[str]) -> int:
    # Ctrl-C mid-run: there is no RunResult. Print whatever partial text streamed
    # (empty if interrupted before any streaming started, e.g. during _build's
    # planner-gate probe), then exit 130 — never a traceback.
    sys.stdout.write("\n\n[interrupted] partial output:\n")
    sys.stdout.write("".join(collected))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 130


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    collected: list[str] = []
    try:
        registry, runtime = _build(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # _build can itself make a live provider call (the §7.1 planner-gate probe
        # for a subscription-only setup); Ctrl-C there must also exit cleanly.
        return _print_interrupted(collected)
    # --json is machine mode: it must print exactly one parseable JSON line, so it
    # disables streaming regardless of --no-stream (which only matters in text mode).
    stream = not args.no_stream and not args.json
    try:
        result = asyncio.run(
            _aexecute(runtime, args.goal, stream=stream, collected=collected)
        )
        if args.json:
            print(_summary_json(result, registry))
        else:
            if result.final:
                print("\n\nFINAL:\n" + str(result.final).strip())
            for line in _summary_lines(result, registry):
                print(line)
    except KeyboardInterrupt:
        return _print_interrupted(collected)
    except BrokenPipeError:
        # Downstream reader closed early (e.g. `baton goal | head`), possibly mid-stream
        # or mid-summary; a closed pipe is not a baton failure.
        return _handle_broken_pipe()
    except Exception as exc:  # noqa: BLE001 - user-facing entrypoint: no raw tracebacks.
        # Supervisor.plan/Worker/Synthesizer can raise ProviderError (network/auth) or
        # ValueError (planner returned an unparseable/invalid plan) uncaught by Runtime;
        # print a clean one-liner instead of a traceback.
        print(f"baton: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if result.status == "success" else 1

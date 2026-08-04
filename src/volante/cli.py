from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from volante import __version__
from volante.bootstrap import (
    build_providers_from_env,
    make_verified_runtime_factory,
)
from volante.providers.base import LLMProvider
from volante.registry import Registry
from volante.runtime import Runtime
from volante.types import RunResult

# The four routing objectives; the user-facing default is quality.
_PREFER_CHOICES = ("cash_protect_quota", "quality", "local", "cheap")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="volante",
        description="Orchestrate GOAL across your configured models.",
    )
    parser.add_argument(
        "--version",
        action="version",
        # Use the in-package single source of truth (hatch derives the built
        # distribution version from this), so it is independent of the PyPI
        # distribution name (`volante`).
        version=__version__,
    )
    parser.add_argument("goal", nargs="?", help="the objective to orchestrate")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="list every configured/detected model in Volante's routing inventory and exit",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help=(
            "answer in ONE call, with the router still choosing the model. Volante's "
            "own eval says decomposition does not pay — it ties a single call across "
            "the suite for 7.7x the cost — while WHICH MODEL answers is the largest "
            "measured lever by a factor of three. Cannot run tools, so a goal that "
            "needs code executed still wants the orchestrated path"
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "check the answer by RUNNING it: derive assertions from your goal, execute "
            "them against the result in the sandbox, and report which passed. Costs one "
            "extra model call. A clean report is evidence, not a guarantee — measured "
            "against edit-shaped defects it caught 5 of 6 — and a failing check is often "
            "the CHECK being wrong. Read them; do not just count them"
        ),
    )
    parser.add_argument(
        "--usage",
        action="store_true",
        help="show the recent usage ledger (~/.volante/usage.jsonl) and exit",
    )
    parser.add_argument(
        "--calibrate",
        metavar="MEASUREMENTS_JSON",
        default=None,
        help=(
            "convert your measured per-model scores into a quality-profiles file the "
            "router can use as evidence, then exit (see --calibrate-out)"
        ),
    )
    parser.add_argument(
        "--calibrate-out",
        metavar="FILE",
        default="quality-profiles.json",
        help="where --calibrate writes the profiles (default: quality-profiles.json)",
    )
    parser.add_argument(
        "--calibrate-source",
        metavar="LABEL",
        default=None,
        help="provenance label recorded in each profile (default: the input filename)",
    )
    parser.add_argument(
        "--prefer",
        choices=_PREFER_CHOICES,
        default="quality",
        help=(
            "routing objective (default: quality — best predicted task fit from "
            "the configured inventory). cash_protect_quota reserves subscription "
            "quota; local prioritizes local models; cheap prioritizes configured price"
        ),
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
    args = parser.parse_args(argv)
    if args.goal is None and not (args.list_models or args.usage or args.calibrate):
        parser.error("the following arguments are required: goal")
    return args


def _build(
    args: argparse.Namespace,
) -> tuple[Registry, Runtime, dict[str, LLMProvider] | None]:
    """Build (registry, fresh runtime) from env via bootstrap. The CLI opts into
    subscription providers (include_subscription=True); actual registration still
    requires CLAUDE_CODE_ENABLED / CODEX_ENABLED inside build_providers_from_env,
    which prints the honest "consumes interactive subscription quota" warning
    (§7.2/§9). The shared bootstrap factory performs the live compatibility gate
    for subscription-only planners, so CLI, MCP, and Web UI enforce one policy."""
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
    make_runtime = asyncio.run(
        make_verified_runtime_factory(
            registry, providers, model_id, prefer=args.prefer
        )
    )
    runtime = make_runtime()
    runtime.verify_answer = bool(getattr(args, "verify", False))
    # Returned rather than attached to the Runtime: the direct path does not use a
    # Runtime at all, and hanging an attribute on one to smuggle providers across would
    # make two paths look like one.
    direct = providers if getattr(args, "direct", False) else None
    return registry, runtime, direct


def _subscription_models(result: RunResult, registry: Registry) -> int:
    """Number of DISTINCT subscription-billed models observed in usage_total
    (plan_included / plan_credit) -- NOT a call count (e.g. 4 calls to one
    claude-code model still reports 1 here). Physical call count is separately
    exposed as ``RunResult.subscription_calls``."""
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
    # An estimate marker must travel WITH the dollar figures. The flag is per-run, and a
    # provider can report an authoritative dollar total while omitting token counts, so
    # the wording says SOME amounts may be inferred rather than claiming all of them are.
    estimate = (
        "  (estimated — a provider reported no token counts for at least one call)"
        if result.cost_estimated
        else ""
    )
    lines = [
        head,
        f"billed_usd: ${result.billed_usd:.6f}   credit_usd: ${result.credit_usd:.6f}"
        f"{estimate}",
        f"duration_ms: {result.duration_ms}   "
        f"subscription_models: {_subscription_models(result, registry)}   "
        f"subscription_calls: {result.subscription_calls}",
    ]
    checks = getattr(result, "checks", None)
    if checks is not None and checks.ran:
        lines.append(f"checks: {checks.summary()}")
        for src, why in checks.failed[:5]:
            # The check itself, not just a count: 45% of the ones that fail are wrong,
            # and only the text lets a reader tell which kind this is.
            lines.append(f"  ✗ {src}   {why}")
        if len(checks.failed) > 5:
            lines.append(f"  … and {len(checks.failed) - 5} more")
    if result.capability_notice:
        # Print it on success too: a degraded run that never executed code must not read
        # like a full-capability one.
        lines.append(f"notice: {result.capability_notice}")
    if result.error_code:
        lines.append(
            f"error: {result.error_code}"
            + (f" — {result.error_message}" if result.error_message else "")
        )
    for task_id, decision in result.routing_decisions.items():
        selected = decision.get("selected_model_id")
        executed = decision.get("executed_model_id") or selected
        objective = decision.get("objective", "quality")
        candidates = decision.get("candidates", [])
        selected_score = next(
            (
                item.get("score")
                for item in candidates
                if item.get("model_id") == selected
            ),
            None,
        )
        score = f" score={selected_score:.2f}" if isinstance(selected_score, float) else ""
        lines.append(
            f"route[{task_id}]: selected={selected} executed={executed} "
            f"objective={objective}{score}"
        )
    return lines


def _summary_json(result: RunResult, registry: Registry) -> str:
    return json.dumps(
        {
            "status": result.status,
            "final": result.final,
            "failed_task": result.failed_task,
            "billed_usd": result.billed_usd,
            "credit_usd": result.credit_usd,
            "cost_usd": result.cost_usd,
            "cost_estimated": result.cost_estimated,
            "capability_notice": result.capability_notice,
            "duration_ms": result.duration_ms,
            "subscription_models": _subscription_models(result, registry),
            "subscription_calls": result.subscription_calls,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "routing_decisions": result.routing_decisions,
        }
    )


def _inventory_payload(registry: Registry) -> dict[str, object]:
    models: list[dict[str, object]] = []
    for model in registry.all():
        profile = registry.quality_profile(model.id)
        models.append(
            {
                "id": model.id,
                "provider": model.provider,
                "strengths": sorted(model.strengths),
                "tier": model.tier,
                "billing": model.billing,
                "context_window": model.context_window,
                "max_output_tokens": model.max_output_tokens,
                "supports_tools": model.supports_tools,
                "cost_per_1k_in": model.cost_per_1k_in,
                "cost_per_1k_out": model.cost_per_1k_out,
                "quality_profile": (
                    {
                        "source": profile.source,
                        "task_scores": dict(profile.task_scores),
                        "overall_score": profile.overall_score,
                        "reliability_score": profile.reliability_score,
                        "confidence": profile.confidence,
                    }
                    if profile is not None
                    else None
                ),
            }
        )
    return {
        "models": models,
        "count": len(models),
        "availability_note": (
            "Inventory contains explicitly configured or locally detected models; "
            "remote entitlement and live availability are verified only when called."
        ),
    }


def _print_inventory(registry: Registry, *, json_mode: bool) -> None:
    payload = _inventory_payload(registry)
    if json_mode:
        print(json.dumps(payload))
        return
    print(f"Configured model inventory: {payload['count']}")
    models = payload["models"]
    if not isinstance(models, list):  # internal shape guard
        raise TypeError("inventory models must be a list")
    for item in models:
        if not isinstance(item, dict):  # internal shape guard
            raise TypeError("inventory model must be an object")
        profile = item["quality_profile"]
        evidence = (
            f"profile={profile.get('source')}"
            if isinstance(profile, dict)
            else "profile=metadata-only"
        )
        print(
            f"- {item['id']}  provider={item['provider']}  tier={item['tier']}  "
            f"billing={item['billing']}  tools={str(item['supports_tools']).lower()}  "
            f"{evidence}"
        )
    print(str(payload["availability_note"]))


def _print_usage(*, json_mode: bool, limit: int = 20) -> None:
    """Render the recent usage ledger for terminal users — the same records the Web UI
    'Usage' dashboard shows, including goals delegated from an IDE via MCP."""
    from volante.observability import read_runs, summarize, usage_log_path

    path = usage_log_path()
    runs = read_runs(limit=limit)
    summary = summarize(runs)
    if json_mode:
        print(json.dumps({"path": str(path) if path else None, "summary": summary, "runs": runs}))
        return
    if path is None:
        print("Usage logging is disabled (VOLANTE_USAGE_LOG is empty).")
        return
    if not runs:
        print(f"No usage recorded yet at {path}.")
        return
    print(f"Usage ledger: {path}")
    print(
        f"runs: {summary['total_runs']}   "
        f"success: {summary['successes']}/{summary['total_runs']}   "
        f"cash: ${summary['total_billed_usd']:.6f}   "
        f"plan credit: ${summary['total_credit_usd']:.6f}   "
        f"subscription calls: {summary['total_subscription_calls']}"
    )
    if summary.get("estimated_runs"):
        # Say how much of the totals is inferred rather than tagging the whole figure.
        print(
            f"  of which estimated: {summary['estimated_runs']} run(s)   "
            f"~${summary['estimated_billed_usd']:.6f} cash   "
            f"~${summary['estimated_credit_usd']:.6f} plan credit"
        )
    print(f"showing last {len(runs)} (newest first):")
    for run in runs:
        status = run.get("status", "?")
        goal = str(run.get("goal", "")).replace("\n", " ")
        if len(goal) > 60:
            goal = goal[:57] + "..."
        # "~" marks a run whose amounts Volante inferred (provider sent no token usage).
        approx = "~" if run.get("cost_estimated") else " "
        print(
            f"- {run.get('ts', '?')}  [{run.get('source', '?')}]  {status:8}  "
            f"cash {approx}${float(run.get('billed_usd') or 0.0):.4f}  "
            f"credit {approx}${float(run.get('credit_usd') or 0.0):.4f}  "
            f"{int(run.get('duration_ms') or 0)}ms  {goal!r}"
        )


def _run_calibration(args: argparse.Namespace) -> int:
    """Turn measured scores into router evidence. No provider calls, so no cost."""
    from volante.calibrate import CalibrationError, calibrate_file

    source = args.calibrate_source or f"measurements:{os.path.basename(args.calibrate)}"
    replacing = os.path.exists(args.calibrate_out)
    try:
        profiles = calibrate_file(
            args.calibrate, args.calibrate_out, source=source
        )
    except (CalibrationError, OSError) as exc:
        print(f"volante: calibration failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"out": args.calibrate_out, "profiles": profiles}))
        return 0
    verb = "Replaced" if replacing else "Wrote"
    print(f"{verb} {len(profiles)} quality profile(s) in {args.calibrate_out}")
    if replacing:
        print(
            "  (the previous file was overwritten, not merged — models missing from "
            "this run lost their evidence)"
        )
    for model_id, profile in sorted(profiles.items()):
        scores = ", ".join(
            f"{task}={value:.2f}" for task, value in sorted(profile["task_scores"].items())
        )
        reliability = profile.get("reliability_score")
        detail = f"- {model_id}  {scores}  confidence={profile['confidence']:.2f}"
        if reliability is not None:
            detail += f"  reliability={reliability:.2f}"
        print(detail)
    _warn_unknown_profile_models(profiles)
    print(
        f"Point Volante at it to make the router use this evidence:\n"
        f"  export VOLANTE_QUALITY_PROFILES_FILE={args.calibrate_out}"
    )
    return 0


def _warn_unknown_profile_models(profiles: dict) -> None:
    """Warn about ids absent from the configured inventory.

    A profiles file naming a model Volante does not have makes EVERY later `volante` command
    fail at startup (the loader is strict by design), so it is worth catching here rather
    than as a mystery error on an unrelated run.
    """
    try:
        registry, _, _ = build_providers_from_env(include_subscription=True)
    except Exception:  # noqa: BLE001 - no providers configured is fine; skip the check
        return
    known = {model.id for model in registry.all()}
    unknown = sorted(set(profiles) - known)
    if unknown:
        print(
            "warning: these measured models are not in your configured inventory, and "
            "Volante refuses to start while a profiles file names an unknown model: "
            + ", ".join(unknown),
            file=sys.stderr,
        )


async def _aexecute(
    runtime: Runtime,
    goal: str,
    *,
    stream: bool,
    collected: list[str],
    direct_providers: dict[str, LLMProvider] | None = None,
) -> RunResult:
    if direct_providers is not None:
        # One call, router still choosing. Streamed the same way so the two paths look
        # the same to a user comparing them.
        from volante.direct import answer_directly

        def on_direct(delta: str) -> None:
            collected.append(delta)
            sys.stdout.write(delta)
            sys.stdout.flush()

        return await answer_directly(
            goal,
            runtime.router,
            runtime.projector,
            direct_providers,
            runtime.registry,
            runtime.cost_meter,
            call_timeout=runtime.call_timeout,
            on_text=on_direct if stream else None,
        )
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
    """The reader closed the pipe early (e.g. `volante goal | head`). Redirect stdout to
    os.devnull so the interpreter's shutdown flush doesn't raise a SECOND
    BrokenPipeError (the classic 'Exception ignored in: <...>' noise), then exit
    cleanly -- a closed downstream reader is not a volante failure."""
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
    from volante.observability import configure_logging, record_run

    configure_logging()
    args = _parse_args(argv)
    collected: list[str] = []
    if args.usage:
        _print_usage(json_mode=args.json)
        return 0
    if args.calibrate:
        return _run_calibration(args)
    if args.list_models:
        try:
            registry, _, _ = build_providers_from_env(include_subscription=True)
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _print_inventory(registry, json_mode=args.json)
        return 0
    try:
        registry, runtime, direct_providers = _build(args)
    except (RuntimeError, ValueError) as exc:
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
            _aexecute(
                runtime,
                args.goal,
                stream=stream,
                collected=collected,
                direct_providers=direct_providers,
            )
        )
        record_run(result, goal=args.goal, prefer=args.prefer, source="cli")
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
        # Downstream reader closed early (e.g. `volante goal | head`), possibly mid-stream
        # or mid-summary; a closed pipe is not a volante failure.
        return _handle_broken_pipe()
    except Exception as exc:  # noqa: BLE001 - user-facing entrypoint: no raw tracebacks.
        # Supervisor.plan/Worker/Synthesizer can raise ProviderError (network/auth) or
        # ValueError (planner returned an unparseable/invalid plan) uncaught by Runtime;
        # print a clean one-liner instead of a traceback.
        print(f"volante: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if result.status == "success" else 1

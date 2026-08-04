"""Runner manual untuk eval-suite: banding orkestrasi vs baseline atas EVAL_SUITE.

Dijalankan langsung oleh user (butuh API/Ollama nyata): konfigurasi provider
dibaca dari env, lalu run_suite dieksekusi dan laporannya dicetak. `format_report`
bersifat murni (tanpa jaringan) dan itulah bagian yang di-unit-test; `main()` +
`build_providers_from_env()` menyentuh jaringan dan dijalankan manual. Wiring provider
(`build_providers_from_env`, `make_runtime_factory`, helper OpenAI-compat) kini tinggal
di `volante.bootstrap` dan hanya di-re-export di sini agar `run.<name>` tetap resolve.

Contoh::

    ANTHROPIC_API_KEY=sk-... uv run python -m eval.run
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from eval.harness import EVAL_K, run_suite
from eval.tasks import EVAL_SUITE

from volante import __version__
from volante.bootstrap import (
    _all_openai_compat_from_env,  # noqa: F401  re-export utk tests/eval/test_run.py
    _openai_compat_from_env,  # noqa: F401  re-export utk tests/eval/test_run.py
    build_providers_from_env,
    make_runtime_factory,
)


def _pooled_delta(per_goal: list[dict], key: str) -> str | None:
    """One paired interval over every matched run in the suite.

    Pooling the PAIRS rather than the goal means keeps the pairing that makes the
    comparison honest: each pair is a baseline and an orchestration run from the same
    loop iteration, so anything that drifted between iterations moves both and cancels.
    """
    from eval.stats import paired_delta

    base: list[float] = []
    other: list[float] = []
    arm = key.split("_vs_")[0]
    for g in per_goal:
        series = g.get("series")
        if not series or arm not in series:
            return None
        base.extend(series["baseline"])
        other.extend(series[arm])
    if len(base) < 2:
        return None
    delta = paired_delta(base, other)
    verdict = (
        "the interval excludes zero"
        if delta.significant
        else "the interval INCLUDES zero — this is not evidence of a difference"
    )
    return (
        f"{delta.mean:+.3f}  [95% CI {delta.ci_low:+.3f}, {delta.ci_high:+.3f}]  "
        f"n={delta.n} pairs\n"
        f"  {verdict}. Smallest effect this run could have detected: "
        f"±{delta.detectable:.3f}"
    )


def format_report(result: dict) -> str:
    """Render hasil run_suite 3-arm jadi tabel per-goal + agregat + baris VERDICT.

    Kolom composite per arm (baseline/orchestration/agentic) diambil dari
    g["scores"][arm]["composite"]; pemenang per goal dari g["winner"]. Bila
    aggregate.any_estimated True, tambahkan peringatan memuat kata "estimated"
    (sebagian biaya ditaksir karena provider tak mengirim usage)."""
    per_goal = result["per_goal"]
    agg = result["aggregate"]

    header = (
        f"{'GOAL':<14}{'WINNER':<15}"
        f"{'BASE':>7}{'ORCH':>7}{'AGEN':>7}"
    )
    lines: list[str] = [header, "-" * len(header)]
    for g in per_goal:
        s = g["scores"]
        lines.append(
            f"{g['id']:<14}{g['winner']:<15}"
            f"{s['baseline']['composite']:>7.2f}"
            f"{s['orchestration']['composite']:>7.2f}"
            f"{s['agentic']['composite']:>7.2f}"
        )
    lines.append("-" * len(header))
    wins = agg["wins"]
    lines.append(
        f"wins: baseline={wins['baseline']}  "
        f"orchestration={wins['orchestration']}  "
        f"agentic={wins['agentic']}  ties={agg['ties']}"
    )
    cost = agg["cost_total"]
    lines.append(
        f"totals: baseline ${cost['baseline']:.6f}  "
        f"orchestration ${cost['orchestration']:.6f}  "
        f"agentic ${cost['agentic']:.6f}"
    )
    lines.append(f"VERDICT: {str(agg['verdict']).upper()}")

    # The verdict counts goals; it says nothing about whether any single difference is
    # real. Three numbers published from this harness had to be corrected downward
    # because a point estimate read as a finding, so the interval now travels with it.
    pooled = _pooled_delta(per_goal, "orchestration_vs_baseline")
    if pooled is not None:
        lines.append("")
        lines.append("orchestration - baseline, paired over every run:")
        lines.append(f"  {pooled}")
    if agg["any_estimated"]:
        lines.append(
            "WARNING: some costs are estimated (a provider returned no usage); "
            "treat the cost comparison with caution."
        )
    agentic_errors = agg.get("agentic_errors", 0)
    if agentic_errors:
        lines.append(
            f"WARNING: agentic arm failed {agentic_errors} run(s) with a terminal "
            "error (infra/provider or loop-exhausted); its 0.0 scores may reflect "
            "failure, not capability — do not read the verdict as a capability result."
        )
    unmeasured = agg.get("unmeasured_goals", [])
    if unmeasured:
        joined = ", ".join(unmeasured)
        lines.append(
            f"WARNING: goal(s) [{joined}] produced NO trusted result on ANY arm — "
            "the reference runner likely crashed/emitted nothing (a broken scorer, "
            "not a real 0.0). These scores are harness artifacts; fix the runner "
            "before trusting the verdict."
        )
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="eval",
        description="3-arm suite: baseline vs orchestration vs agentic-single.",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        default=None,
        help=(
            "also write the complete result as JSON — a self-describing artifact "
            "(models, k, per-goal scores, costs) so a published number can be traced "
            "back to the run that produced it"
        ),
    )
    parser.add_argument(
        "--k",
        type=int,
        default=EVAL_K,
        help=(
            f"runs per goal per arm (default {EVAL_K}). A small k cannot separate a real "
            "difference from run-to-run noise; raise it before believing a narrow result"
        ),
    )
    parser.add_argument(
        "--suite",
        choices=("eval", "depth"),
        default="eval",
        help=(
            "which goals to run (default eval, the nine-goal suite every published "
            "artifact measured). 'depth' is the guardkit goal, which scores stated "
            "edge-case requirements rather than the happy path"
        ),
    )
    parser.add_argument(
        "--synthesis",
        choices=("summarize", "assemble"),
        default="summarize",
        help=(
            "how the orchestration arm turns artifacts into a final answer (default "
            "summarize). 'assemble' concatenates them with no model call, which is "
            "what a goal whose PRODUCT is the artifact wants — a summarising pass "
            "would funnel every worker's output back through one bounded call"
        ),
    )
    args = parser.parse_args(argv)
    if args.k < 1:
        raise SystemExit("--k must be at least 1")
    if args.suite == "depth":
        from eval.tasks_depth import DEPTH_SUITE

        suite = DEPTH_SUITE
    else:
        suite = EVAL_SUITE

    # The agentic arm executes model-written code, so the suite cannot run without a
    # sandbox. Check BEFORE any provider call: failing here costs nothing, while failing
    # mid-suite would waste the money already spent on the earlier arms.
    from volante.tools.sandbox import resolve_sandbox_mode

    mode, reason = resolve_sandbox_mode()
    if mode == "unavailable":
        raise SystemExit(f"eval needs a code-execution sandbox: {reason}")

    registry, providers, model_id = build_providers_from_env()
    make_runtime = make_runtime_factory(
        registry, providers, model_id, synthesis=args.synthesis
    )
    result = await run_suite(
        suite, make_runtime, providers[model_id], model_id, registry, k=args.k
    )
    print(format_report(result))
    if args.json:
        import json
        from datetime import UTC, datetime

        artifact = {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "volante_version": __version__,
            "planner_model_id": model_id,
            "inventory": sorted(m.id for m in registry.all()),
            "k": args.k,
            # Recorded because they change what the number MEANS: a score is only
            # comparable to another score from the same goals and the same synthesis.
            "suite_name": args.suite,
            "synthesis": args.synthesis,
            "suite": [t.id for t in suite],
            "result": result,
        }
        path = Path(args.json).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")


if __name__ == "__main__":
    asyncio.run(main())

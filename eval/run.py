"""Runner manual untuk eval-suite: banding orkestrasi vs baseline atas EVAL_SUITE.

Dijalankan langsung oleh user (butuh API/Ollama nyata): konfigurasi provider
dibaca dari env, lalu run_suite dieksekusi dan laporannya dicetak. `format_report`
bersifat murni (tanpa jaringan) dan itulah bagian yang di-unit-test; `main()` +
`build_providers_from_env()` menyentuh jaringan dan dijalankan manual. Wiring provider
(`build_providers_from_env`, `make_runtime_factory`, helper OpenAI-compat) kini tinggal
di `baton.bootstrap` dan hanya di-re-export di sini agar `run.<name>` tetap resolve.

Contoh::

    ANTHROPIC_API_KEY=sk-... uv run python -m eval.run
"""

from __future__ import annotations

import asyncio

from eval.harness import run_suite
from eval.tasks import EVAL_SUITE

from baton.bootstrap import (
    _all_openai_compat_from_env,  # noqa: F401  re-export utk tests/eval/test_run.py
    _openai_compat_from_env,  # noqa: F401  re-export utk tests/eval/test_run.py
    build_providers_from_env,
    make_runtime_factory,
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


async def main() -> None:
    registry, providers, model_id = build_providers_from_env()
    make_runtime = make_runtime_factory(registry, providers, model_id)
    result = await run_suite(
        EVAL_SUITE, make_runtime, providers[model_id], model_id, registry
    )
    print(format_report(result))


if __name__ == "__main__":
    asyncio.run(main())

"""Runner manual untuk eval-suite: banding orkestrasi vs baseline atas EVAL_SUITE.

Dijalankan langsung oleh user (butuh API/Ollama nyata): konfigurasi provider
dibaca dari env, lalu run_suite dieksekusi dan laporannya dicetak. `format_report`
bersifat murni (tanpa jaringan) dan itulah bagian yang di-unit-test; `main()` +
`build_providers_from_env()` menyentuh jaringan dan dijalankan manual.

Contoh::

    ANTHROPIC_API_KEY=sk-... uv run python -m eval.run
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from eval.harness import run_suite
from eval.tasks import EVAL_SUITE

from orchestrator.agent import AgenticWorker
from orchestrator.cost import CostMeter
from orchestrator.projector import Projector
from orchestrator.registry import Registry, default_models
from orchestrator.router import Router
from orchestrator.runtime import Runtime
from orchestrator.supervisor import Supervisor
from orchestrator.synthesizer import Synthesizer
from orchestrator.worker import Worker

if TYPE_CHECKING:
    from collections.abc import Callable

    from orchestrator.providers.base import LLMProvider


def format_report(result: dict) -> str:
    """Render hasil run_suite jadi tabel per-goal + baris VERDICT.

    Bila aggregate.any_estimated True, tambahkan peringatan memuat kata
    "estimated" (sebagian biaya ditaksir karena provider tak mengirim usage)."""
    per_goal = result["per_goal"]
    agg = result["aggregate"]

    header = (
        f"{'GOAL':<14}{'WINNER':<15}{'ORCH':>6}{'BASE':>6}"
        f"{'ORCH$':>11}{'BASE$':>11}{'ORCHms':>9}{'BASEms':>9}"
    )
    lines: list[str] = [header, "-" * len(header)]
    for g in per_goal:
        lines.append(
            f"{g['id']:<14}{g['winner']:<15}"
            f"{g['orch_composite']:>6.2f}{g['base_composite']:>6.2f}"
            f"{g['orch_cost']:>11.6f}{g['base_cost']:>11.6f}"
            f"{g['orch_ms']:>9}{g['base_ms']:>9}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"wins: orchestration={agg['orch_wins']}  "
        f"baseline={agg['base_wins']}  ties={agg['ties']}"
    )
    lines.append(
        f"totals: orch ${agg['orch_cost_total']:.6f}  "
        f"base ${agg['base_cost_total']:.6f}"
    )
    lines.append(f"VERDICT: {str(agg['verdict']).upper()}")
    if agg["any_estimated"]:
        lines.append(
            "WARNING: some costs are estimated (a provider returned no usage); "
            "treat the cost comparison with caution."
        )
    return "\n".join(lines)


def build_providers_from_env() -> tuple[Registry, dict[str, LLMProvider], str]:
    """Bangun (registry, providers-by-model_id, baseline_model_id) dari env.

    Membaca ANTHROPIC_API_KEY, MOONSHOT_API_KEY (+ MOONSHOT_BASE_URL), dan
    OLLAMA_BASE_URL. Registry dipangkas hanya ke model yang punya provider agar
    Router tak pernah me-route ke model tanpa backend. Anthropic (bila ada)
    menjadi model baseline (arm kuat); jika tidak, provider pertama yang tersedia.
    Import provider bersifat lazy supaya `import eval.run` tetap ringan/nol-jaringan."""
    from orchestrator.providers.anthropic import AnthropicProvider
    from orchestrator.providers.openai_compat import OpenAICompatProvider

    providers: dict[str, LLMProvider] = {}
    baseline_model_id: str | None = None

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        mid = "anthropic/claude-opus-4-8"
        wire = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
        providers[mid] = AnthropicProvider(api_key=anthropic_key, model=wire)
        baseline_model_id = mid  # arm kuat untuk baseline

    moonshot_key = os.environ.get("MOONSHOT_API_KEY")
    if moonshot_key:
        mid = "kimi/kimi-k2"
        base_url = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
        wire = os.environ.get("MOONSHOT_MODEL", "kimi-k2-0711-preview")
        providers[mid] = OpenAICompatProvider(
            base_url=base_url, api_key=moonshot_key, model=wire
        )
        if baseline_model_id is None:
            baseline_model_id = mid

    ollama_base = os.environ.get("OLLAMA_BASE_URL")
    if ollama_base:
        mid = "ollama/llama3.2"
        wire = os.environ.get("OLLAMA_MODEL", "llama3.2")
        api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
        providers[mid] = OpenAICompatProvider(
            base_url=ollama_base, api_key=api_key, model=wire
        )
        if baseline_model_id is None:
            baseline_model_id = mid

    if not providers:
        raise RuntimeError(
            "No providers configured. Set ANTHROPIC_API_KEY, MOONSHOT_API_KEY, "
            "and/or OLLAMA_BASE_URL before running the eval."
        )
    registry = Registry([m for m in default_models() if m.id in providers])
    assert baseline_model_id is not None  # dijamin oleh guard di atas
    return registry, providers, baseline_model_id


def make_runtime_factory(
    registry: Registry, providers: dict[str, LLMProvider], model_id: str
) -> Callable[[], Runtime]:
    """Factory Runtime segar per pemanggilan (Supervisor non-re-entrant, CostMeter
    per-run) berbagi registry + providers. Supervisor/Synthesizer memakai model
    kuat (model_id); Worker/AgenticWorker memakai peta providers penuh."""

    def make_runtime() -> Runtime:
        cost_meter = CostMeter()
        return Runtime(
            supervisor=Supervisor(providers[model_id], model_id, cost_meter),
            router=Router(registry),
            projector=Projector(registry),
            worker=Worker(providers, cost_meter),
            synthesizer=Synthesizer(providers[model_id], model_id, cost_meter),
            registry=registry,
            cost_meter=cost_meter,
            agentic_worker=AgenticWorker(providers, cost_meter),
        )

    return make_runtime


async def main() -> None:
    registry, providers, model_id = build_providers_from_env()
    make_runtime = make_runtime_factory(registry, providers, model_id)
    result = await run_suite(
        EVAL_SUITE, make_runtime, providers[model_id], model_id, registry
    )
    print(format_report(result))


if __name__ == "__main__":
    asyncio.run(main())

# src/baton/bootstrap.py
"""Provider wiring dari environment: bangun Registry + peta LLMProvider + Runtime factory.

Dipindah dari `eval/run.py` agar bisa dipakai package (CLI `baton`, Web UI, demo) tanpa
menyeret dependensi eval. `eval/run.py` me-re-export simbol-simbol ini sehingga
`run._openai_compat_from_env`/`run.build_providers_from_env` (dipakai
tests/eval/test_run.py) tetap resolve. Semua fungsi di sini murni/offline kecuali
konstruksi provider di `build_providers_from_env` (yang pun offline — provider tak konek
sampai dipanggil)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from baton.agent import AgenticWorker
from baton.cost import CostMeter
from baton.projector import Projector
from baton.registry import Registry, default_models
from baton.router import Router
from baton.runtime import Runtime
from baton.supervisor import Supervisor
from baton.synthesizer import Synthesizer
from baton.types import ModelInfo
from baton.worker import Worker

if TYPE_CHECKING:
    from collections.abc import Callable

    from baton.providers.base import LLMProvider


def _openai_compat_from_env(
    env: dict[str, str], prefix: str = "OPENAI_COMPAT"
) -> tuple[ModelInfo, str, str, str] | None:
    """Parse SATU slot provider OpenAI-compatible generik dari env (Gemini/Groq/
    OpenRouter/DeepSeek/dll. tanpa ubah kode). `prefix` memilih slot: "OPENAI_COMPAT"
    (slot 1) atau "OPENAI_COMPAT_2"/"_3"/… (slot tambahan). Kembalikan (ModelInfo,
    base_url, api_key, wire_model) atau None bila tak dikonfigurasi. Murni (tanpa
    jaringan) agar mudah di-test.

    Aktif bila `{prefix}_BASE_URL` diset. Wajib `{prefix}_MODEL` (nama model di wire).
    Default standar: context 128k, output 8k, tool-capable, biaya 0 (tak menyesatkan
    utk free tier; override bila endpoint berbayar). strengths catch-all {coding,
    reasoning} agar router bisa mengarahkan SEMUA jenis task ke sini.
    """
    base_url = env.get(f"{prefix}_BASE_URL")
    if not base_url:
        return None
    wire = env.get(f"{prefix}_MODEL")
    if not wire:
        raise RuntimeError(
            f"{prefix}_BASE_URL is set but {prefix}_MODEL is missing "
            "(the wire model name, e.g. gemini-2.5-flash)"
        )
    api_key = env.get(f"{prefix}_KEY") or "none"
    model_id = env.get(f"{prefix}_NAME") or f"openai-compat/{wire}"
    info = ModelInfo(
        id=model_id,
        provider="openai_compat",
        strengths={"coding", "reasoning"},  # catch-all: routable utk semua task.type
        context_window=int(env.get(f"{prefix}_CONTEXT", "128000")),
        max_output_tokens=int(env.get(f"{prefix}_MAX_OUTPUT", "8192")),
        supports_tools=env.get(f"{prefix}_TOOLS", "true").strip().lower()
        not in ("false", "0", "no"),
        cost_per_1k_in=float(env.get(f"{prefix}_COST_IN", "0")),
        cost_per_1k_out=float(env.get(f"{prefix}_COST_OUT", "0")),
        tier=int(env.get(f"{prefix}_TIER", "3")),
        billing="card",
    )
    return info, base_url, api_key, wire


def _all_openai_compat_from_env(
    env: dict[str, str],
) -> list[tuple[ModelInfo, str, str, str]]:
    """Kumpulkan SEMUA slot OpenAI-compatible: `OPENAI_COMPAT_*` (slot 1) lalu
    `OPENAI_COMPAT_2_*`, `OPENAI_COMPAT_3_*`, … (bernomor kontigu mulai 2; berhenti di
    gap pertama). Memungkinkan beberapa provider (mis. Gemini + Groq + DeepSeek)
    masing-masing dengan model_id/harga/context sendiri (tanpa numpang slot lain)."""
    slots: list[tuple[ModelInfo, str, str, str]] = []
    first = _openai_compat_from_env(env, "OPENAI_COMPAT")
    if first is not None:
        slots.append(first)
    n = 2
    while (slot := _openai_compat_from_env(env, f"OPENAI_COMPAT_{n}")) is not None:
        slots.append(slot)
        n += 1
    return slots


def build_providers_from_env(
    prefer: str = "quality",
    include_subscription: bool = False,
) -> tuple[Registry, dict[str, LLMProvider], str]:
    """Bangun (registry, providers-by-model_id, baseline_model_id) dari env.

    `prefer` = objektif routing (§6.2: cash_protect_quota|quality|local|cheap);
    diterima di A1 tapi belum mengubah urutan baseline (wiring penuh: Phase A9,
    default "quality" = perilaku sekarang). `include_subscription=False` (default) →
    hanya provider API-key/base-url (perilaku sekarang; tes eval tetap hijau). `True`
    → daftarkan provider langganan ClaudeCode/Codex + peringatan konsumsi kuota,
    DITUNDA ke Phase A7/A8/A9 (provider tsb belum ada); no-op sadar di A1 agar
    signature terkunci sudah bisa dipanggil CLI.

    Membaca ANTHROPIC_API_KEY, satu ATAU lebih slot OpenAI-compatible generik
    (OPENAI_COMPAT_* lalu OPENAI_COMPAT_2_*/_3_*… untuk Gemini/Groq/OpenRouter/DeepSeek
    berbarengan, masing-masing label & harga sendiri), MOONSHOT_API_KEY (+
    MOONSHOT_BASE_URL), dan OLLAMA_BASE_URL. Registry dipangkas hanya ke model yang
    punya provider agar Router tak pernah me-route ke model tanpa backend. Prioritas
    baseline: Anthropic > OPENAI_COMPAT (slot 1 dulu) > Moonshot > Ollama. Import
    provider lazy supaya `import baton.bootstrap` tetap ringan/nol-jaringan."""
    from baton.providers.anthropic import AnthropicProvider
    from baton.providers.openai_compat import OpenAICompatProvider

    providers: dict[str, LLMProvider] = {}
    extra_models: list[ModelInfo] = []
    baseline_model_id: str | None = None

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        mid = "anthropic/claude-opus-4-8"
        wire = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
        providers[mid] = AnthropicProvider(api_key=anthropic_key, model=wire)
        baseline_model_id = mid  # arm kuat untuk baseline

    for info, base_url, api_key, wire in _all_openai_compat_from_env(dict(os.environ)):
        if info.id in providers:
            raise RuntimeError(
                f"duplicate model_id {info.id!r} across OpenAI-compat slots; "
                "set a distinct *_NAME per slot"
            )
        providers[info.id] = OpenAICompatProvider(
            base_url=base_url, api_key=api_key, model=wire
        )
        extra_models.append(info)  # ModelInfo sendiri -> registry (pricing/context benar)
        if baseline_model_id is None:  # slot 1 lebih dulu -> baseline di antara slot compat
            baseline_model_id = info.id

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
            "No providers configured. Set ANTHROPIC_API_KEY, OPENAI_COMPAT_BASE_URL "
            "(+ OPENAI_COMPAT_MODEL/_KEY), MOONSHOT_API_KEY, and/or OLLAMA_BASE_URL "
            "before running the eval."
        )
    registry = Registry(
        [m for m in default_models() if m.id in providers] + extra_models
    )
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

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
import shutil
import sys
from typing import TYPE_CHECKING

from baton.agent import AgenticWorker
from baton.cost import CostMeter
from baton.projector import Projector
from baton.providers.base import LLMProvider, ProviderError
from baton.registry import Registry, default_models
from baton.router import Router
from baton.runtime import Runtime
from baton.supervisor import Supervisor
from baton.synthesizer import Synthesizer
from baton.types import ModelInfo
from baton.worker import Worker

if TYPE_CHECKING:
    from collections.abc import Callable


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


def _detect_cli(binary: str) -> bool:
    """True iff `binary` is on PATH (the 'CLI detected' gate for subscription providers,
    §7.2). Tiny + monkeypatchable so gating tests need no real binary or spawn."""
    return shutil.which(binary) is not None


def _warn_subscription(label: str) -> None:
    """Print the §9 honesty warning to stderr: orchestrating on a subscription CLI agent
    consumes your INTERACTIVE quota (a full run — worse, the eval suite — can burn the
    Claude Code / Codex allowance and trip a mid-run hard-pause)."""
    print(
        f"WARNING: {label} is a subscription CLI agent — this run draws from your "
        "INTERACTIVE subscription quota (not a metered API); a large run or the eval "
        "suite can exhaust the quota and trigger a mid-run hard-pause.",
        file=sys.stderr,
    )


def _register_subscription_providers(
    providers: dict[str, LLMProvider],
    extra_models: list[ModelInfo],
    env: dict[str, str],
    baseline_model_id: str | None,
) -> str | None:
    """Register ClaudeCode/Codex CLI-agent providers, but ONLY when opted in (`*_ENABLED=1`)
    AND the CLI is confirmed available. Each registration prints the §9 warning. The
    registered `ModelInfo` for BOTH legs comes from the existing single-source-of-truth
    seed helpers (`claude_code_model_info()` / `build_codex_model()`) — NOT inlined here —
    so there is exactly one place that knows the shape of each seed (avoids the two
    definitions drifting, e.g. strengths/context_window disagreeing) and the registered
    id follows the configured wire model. Models are `billing="plan_included"` (they draw
    the interactive pool, not per-token cash) and are added AFTER the card providers so
    they never displace a temperature-controllable baseline planner (§7.1). Returns the
    (possibly unchanged) baseline_model_id.

    Detection is intentionally ASYMMETRIC: Codex has a real login-status probe
    (`codex_detected()` = `codex login status` exit 0), purpose-built for this call site,
    so a `codex` binary merely sitting on PATH but not logged in is correctly treated as
    NOT available (a bare PATH hit would register a ~$0-cash provider the router ranks
    first for every hard task, wasting a candidate attempt before reroute). Claude Code has
    no equivalent `claude login status`-style helper yet, so that leg stays on a PATH-only
    `_detect_cli("claude")` check; a not-logged-in `claude` binary self-heals via
    `ClaudeCodeAdapter.classify_error` mapping auth failures to `quota_exhausted`, which
    Runtime reroutes to the next candidate anyway."""
    from baton.providers.cli_agent import CliAgentProvider, subprocess_cli_runner
    from baton.providers.codex import codex_detected

    if env.get("CLAUDE_CODE_ENABLED") == "1" and _detect_cli("claude"):
        from baton.providers.claude_code import ClaudeCodeAdapter, claude_code_model_info

        model = env.get("CLAUDE_CODE_MODEL", "opus")
        tier = int(env.get("CLAUDE_CODE_TIER", "4"))
        max_output = int(env.get("CLAUDE_CODE_MAX_OUTPUT", "4096"))
        info = claude_code_model_info(model, tier=tier, max_output_tokens=max_output)
        mid = info.id
        providers[mid] = CliAgentProvider(
            ClaudeCodeAdapter(),
            model,
            runner=subprocess_cli_runner,
            tier=tier,
            timeout=float(env.get("CLAUDE_CODE_TIMEOUT", "120")),
            max_output=max_output,
            # Default "replace" (live-verified 2026-07-23): with "append", opus as a full
            # Claude Code agent ANSWERS the goal instead of emitting the planner's strict
            # JSON DAG (its base system prompt outranks the appended instruction) -> a real
            # run failed with "planner did not return valid JSON". "replace" swaps in the
            # planner/worker persona so `claude -p` behaves as a raw completion. Override
            # via CLAUDE_CODE_SYSTEM_PROMPT_MODE=append if you want the layered behavior.
            system_prompt_mode=env.get("CLAUDE_CODE_SYSTEM_PROMPT_MODE", "replace"),
        )
        extra_models.append(info)
        _warn_subscription("claude-code")
        if baseline_model_id is None:
            baseline_model_id = mid

    if env.get("CODEX_ENABLED") == "1" and codex_detected():
        from baton.providers.codex import CodexAdapter, build_codex_model

        if env.get("CODEX_TIER") is None:
            raise RuntimeError(
                "CODEX_ENABLED=1 requires CODEX_TIER (no model-name sniffing, §6.1)"
            )
        info = build_codex_model(env)
        # Empty/unset CODEX_MODEL -> CodexAdapter.argv OMITS `--config model=...`
        # entirely (fixed alongside this), so codex exec follows the user's own config.
        model = env.get("CODEX_MODEL", "")
        max_output = int(env.get("CODEX_MAX_OUTPUT", "4096"))
        mid = info.id
        providers[mid] = CliAgentProvider(
            CodexAdapter(),
            model,
            runner=subprocess_cli_runner,
            tier=info.tier,
            timeout=float(env.get("CODEX_TIMEOUT", "120")),
            max_output=max_output,
        )
        extra_models.append(info)
        _warn_subscription("codex")
        if baseline_model_id is None:
            baseline_model_id = mid

    return baseline_model_id


def _no_providers_configured_message(include_subscription: bool) -> str:
    """Actionable "no providers" error for both callers of `build_providers_from_env`:
    the `baton` CLI (include_subscription=True) and the eval suite
    (include_subscription=False, §9 fence — never nudges users toward the eval's own
    subscription-quota fence). Always lists the API/local options; the CLI path ALSO
    lists the subscription CLI-agent opt-ins (§7.2) since those are only wired when
    `include_subscription=True`."""
    msg = (
        "No providers configured. Set one of: ANTHROPIC_API_KEY, OPENAI_COMPAT_BASE_URL "
        "(+ OPENAI_COMPAT_MODEL, optional OPENAI_COMPAT_KEY), MOONSHOT_API_KEY, or "
        "OLLAMA_BASE_URL"
    )
    if include_subscription:
        msg += (
            ", or a subscription CLI agent: CLAUDE_CODE_ENABLED=1 (requires `claude` "
            "logged in) or CODEX_ENABLED=1 CODEX_TIER=3 (requires `codex login`)"
        )
    return msg + ". See the README Usage section."


def build_providers_from_env(
    prefer: str = "quality",
    include_subscription: bool = False,
) -> tuple[Registry, dict[str, LLMProvider], str]:
    """Bangun (registry, providers-by-model_id, baseline_model_id) dari env.

    `prefer` = objektif routing (§6.2: cash_protect_quota|quality|local|cheap), diteruskan
    ke `make_runtime_factory`/`Router(prefer=...)` oleh pemanggil. `include_subscription=False`
    (default, §9 eval fence) → hanya provider API-key/base-url (tes eval tetap hijau, tak
    pernah menyentuh kuota langganan). `True` → SETELAHNYA daftarkan provider langganan
    ClaudeCode/Codex via `_register_subscription_providers` — tapi HANYA per provider bila
    `*_ENABLED=1` DAN CLI-nya terdeteksi di PATH (§7.2); tiap registrasi mencetak peringatan
    konsumsi kuota interaktif (§9). Provider langganan ditambahkan SETELAH baseline card
    ditentukan sehingga tak pernah menggeser baseline.

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

    if include_subscription:
        baseline_model_id = _register_subscription_providers(
            providers, extra_models, dict(os.environ), baseline_model_id
        )

    if not providers:
        raise RuntimeError(_no_providers_configured_message(include_subscription))
    by_id: dict[str, ModelInfo] = {
        m.id: m for m in default_models() if m.id in providers
    }
    for m in extra_models:
        by_id[m.id] = m  # env-configured / subscription models override any default seed
    registry = Registry(list(by_id.values()))
    assert baseline_model_id is not None  # dijamin oleh guard di atas
    return registry, providers, baseline_model_id


def _temperature_controllable(registry: Registry, model_id: str) -> bool:
    """True iff the model honors req.temperature. Subscription CLI agents (billing
    plan_included/plan_credit) ignore temperature (§8.3) and so cannot deliver the
    deterministic temperature=0.0 the planner needs; card-billed API/Ollama/free-tier can."""
    return registry.get(model_id).billing == "card"


def _planner_model_id(
    registry: Registry, providers: dict[str, LLMProvider], baseline_model_id: str
) -> str:
    """Pick the Supervisor/Synthesizer model. Prefer `baseline_model_id` when it is
    temperature-controllable (card); else the highest-tier card model that HAS a provider — so
    planning/synthesis stay deterministic even when `prefer` routes work to subscription (§7.1);
    else fall back to the baseline (subscription-only setup — the CLI runs
    `verify_claude_plan_gate` before trusting claude -p to plan)."""
    if _temperature_controllable(registry, baseline_model_id):
        return baseline_model_id
    card = [m for m in registry.all() if m.id in providers and m.billing == "card"]
    if card:
        return sorted(card, key=lambda m: (-m.tier, m.cost_per_1k_out, m.id))[0].id
    return baseline_model_id


async def verify_claude_plan_gate(
    provider: LLMProvider,
    model_id: str,
    *,
    goal: str = "Plan a single trivial task: print the word hello.",
) -> bool:
    """§7.1 live gate. Run ONE planning probe through `provider` and return True iff the output
    survives the supervisor's OWN parser (`_parse_plan_json` + `_validate`, exercised via
    `Supervisor.plan`). Only then may a subscription CLI agent (`claude -p`, which ignores
    temperature) be trusted to plan; otherwise the caller keeps the API/Ollama planner. Reusing
    the real planner path guarantees the gate's notion of 'valid' matches production. Any
    ProviderError/ValueError => the gate fails closed (returns False)."""
    from baton.cost import CostMeter
    from baton.supervisor import Supervisor

    probe = Supervisor(provider, model_id, CostMeter())  # fresh, single-use; meter discarded
    try:
        await probe.plan(goal)
    except (ProviderError, ValueError):
        return False
    return True


def make_runtime_factory(
    registry: Registry,
    providers: dict[str, LLMProvider],
    model_id: str,
    *,
    prefer: str = "quality",
) -> Callable[[], Runtime]:
    """Factory Runtime segar per pemanggilan (Supervisor non-re-entrant, CostMeter
    per-run) berbagi registry + providers. Supervisor/Synthesizer memakai planner
    temperature-controllable (card) yang dipilih `_planner_model_id` (§7.1), bukan
    langsung `model_id` — Worker/AgenticWorker tetap memakai peta providers penuh
    dan Router memakai `prefer`. Default `prefer="quality"` MATCHES `Router.__init__`'s
    own default: route each task to the strongest capable model (best answer). Pass
    `prefer="cash_protect_quota"` to right-size instead and protect subscription quota."""
    planner_id = _planner_model_id(registry, providers, model_id)

    def make_runtime() -> Runtime:
        cost_meter = CostMeter()
        return Runtime(
            supervisor=Supervisor(providers[planner_id], planner_id, cost_meter),
            router=Router(registry, prefer=prefer),
            projector=Projector(registry),
            worker=Worker(providers, cost_meter),
            synthesizer=Synthesizer(providers[planner_id], planner_id, cost_meter),
            registry=registry,
            cost_meter=cost_meter,
            agentic_worker=AgenticWorker(providers, cost_meter),
        )

    return make_runtime

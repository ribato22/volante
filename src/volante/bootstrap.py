# src/volante/bootstrap.py
"""Provider wiring dari environment: bangun Registry + peta LLMProvider + Runtime factory.

Dipindah dari `eval/run.py` agar bisa dipakai package (CLI `volante`, Web UI, demo) tanpa
menyeret dependensi eval. `eval/run.py` me-re-export simbol-simbol ini sehingga
`run._openai_compat_from_env`/`run.build_providers_from_env` (dipakai
tests/eval/test_run.py) tetap resolve. Semua fungsi di sini murni/offline kecuali
konstruksi provider di `build_providers_from_env` (yang pun offline — provider tak konek
sampai dipanggil)."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from volante.agent import AgenticWorker
from volante.cost import CostMeter
from volante.inventory import (
    apply_model_overrides,
    load_model_overrides,
    load_quality_profiles,
    model_list_from_env,
    parse_model_list,
)
from volante.projector import Projector
from volante.providers.base import LLMProvider, ProviderError
from volante.registry import Registry
from volante.router import NoEligibleModelError, Router
from volante.runtime import Runtime
from volante.supervisor import Supervisor
from volante.synthesizer import Synthesizer
from volante.types import ModelInfo, Task
from volante.worker import Worker

if TYPE_CHECKING:
    from collections.abc import Callable


class NoProvidersConfiguredError(RuntimeError):
    """Raised when no usable API, local, or opted-in subscription provider exists."""


def _metadata_value(env: dict[str, str], key: str, default: str) -> str:
    """Return optional metadata, treating an empty UI-injected value as unset.

    Distribution manifests expose advanced metadata fields as optional form
    inputs. Some clients materialize an untouched optional field as ``""`` rather
    than omitting it, so defaults must apply to empty values too. Provider identity,
    endpoint, key, and model-list parsing deliberately do not use this helper and
    retain their strict contracts.
    """
    value = env.get(key)
    if value is None or not value.strip():
        return default
    return value.strip()


def _bool_from_env(env: dict[str, str], key: str, default: bool) -> bool:
    if key not in env or not env[key].strip():
        return default
    value = env[key].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{key} must be a boolean (1/0, true/false, yes/no, on/off)")


def _strengths_from_env(env: dict[str, str], key: str, default: set[str]) -> set[str]:
    if key not in env or not env[key].strip():
        return set(default)
    values = {part.strip() for part in env[key].split(",")}
    if not values or "" in values:
        raise RuntimeError(f"{key} must be a non-empty comma-separated list")
    return values


def _canonical_names(
    env: dict[str, str],
    *,
    plural_key: str,
    singular_key: str,
    models: list[str],
    default_name: Callable[[str, int], str],
) -> list[str]:
    """Resolve optional canonical ids parallel to a repeatable wire-model list."""

    if env.get(plural_key, "").strip():
        names = parse_model_list(env[plural_key], setting_name=plural_key)
    elif env.get(singular_key, "").strip():
        names = parse_model_list(env[singular_key], setting_name=singular_key)
    else:
        names = [default_name(model, index) for index, model in enumerate(models)]
    if len(names) != len(models):
        raise RuntimeError(
            f"{plural_key}/{singular_key} must contain exactly {len(models)} "
            "canonical id(s), one for each configured model"
        )
    return names


def _anthropic_model_info(wire: str, model_id: str, env: dict[str, str]) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="anthropic",
        strengths=_strengths_from_env(
            env,
            "ANTHROPIC_STRENGTHS",
            {"coding", "reasoning", "long_context"},
        ),
        context_window=int(_metadata_value(env, "ANTHROPIC_CONTEXT", "200000")),
        max_output_tokens=int(_metadata_value(env, "ANTHROPIC_MAX_OUTPUT", "8192")),
        supports_tools=_bool_from_env(env, "ANTHROPIC_TOOLS", True),
        cost_per_1k_in=float(_metadata_value(env, "ANTHROPIC_COST_IN", "0.015")),
        cost_per_1k_out=float(_metadata_value(env, "ANTHROPIC_COST_OUT", "0.075")),
        tier=int(_metadata_value(env, "ANTHROPIC_TIER", "4")),
        billing="card",
    )


def _compat_family_model_info(
    *,
    model_id: str,
    provider: str,
    prefix: str,
    env: dict[str, str],
    context: int,
    max_output: int,
    tools: bool,
    cost_in: float,
    cost_out: float,
    tier: int,
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider=provider,
        strengths=_strengths_from_env(env, f"{prefix}_STRENGTHS", {"coding", "reasoning"}),
        context_window=int(_metadata_value(env, f"{prefix}_CONTEXT", str(context))),
        max_output_tokens=int(_metadata_value(env, f"{prefix}_MAX_OUTPUT", str(max_output))),
        supports_tools=_bool_from_env(env, f"{prefix}_TOOLS", tools),
        cost_per_1k_in=float(_metadata_value(env, f"{prefix}_COST_IN", str(cost_in))),
        cost_per_1k_out=float(_metadata_value(env, f"{prefix}_COST_OUT", str(cost_out))),
        tier=int(_metadata_value(env, f"{prefix}_TIER", str(tier))),
        billing="card",
    )


def _openai_compat_from_env(
    env: dict[str, str], prefix: str = "OPENAI_COMPAT"
) -> tuple[ModelInfo, str, str, str] | None:
    """Parse SATU slot provider OpenAI-compatible generik dari env (Gemini/Groq/
    OpenRouter/DeepSeek/dll. tanpa ubah kode). `prefix` memilih slot: "OPENAI_COMPAT"
    (slot 1) atau "OPENAI_COMPAT_2"/"_3"/… (slot tambahan). Kembalikan (ModelInfo,
    base_url, api_key, wire_model) atau None bila tak dikonfigurasi. Murni (tanpa
    jaringan) agar mudah di-test.

    Aktif bila `{prefix}_BASE_URL` diset. Wajib `{prefix}_MODEL` (nama model di wire).
    Default standar: context 128k, output 8k, tools off, biaya 0 (tak menyesatkan
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
        strengths=_strengths_from_env(env, f"{prefix}_STRENGTHS", {"coding", "reasoning"}),
        context_window=int(_metadata_value(env, f"{prefix}_CONTEXT", "128000")),
        max_output_tokens=int(_metadata_value(env, f"{prefix}_MAX_OUTPUT", "8192")),
        # Tool calling is a hard capability. Arbitrary compatible endpoints must
        # opt in explicitly instead of receiving a false capability declaration.
        supports_tools=_bool_from_env(env, f"{prefix}_TOOLS", False),
        cost_per_1k_in=float(_metadata_value(env, f"{prefix}_COST_IN", "0")),
        cost_per_1k_out=float(_metadata_value(env, f"{prefix}_COST_OUT", "0")),
        tier=int(_metadata_value(env, f"{prefix}_TIER", "3")),
        billing="card",
    )
    return info, base_url, api_key, wire


def _all_openai_compat_from_env(
    env: dict[str, str],
) -> list[tuple[ModelInfo, str, str, str]]:
    """Kumpulkan SEMUA slot OpenAI-compatible: `OPENAI_COMPAT_*` (slot 1) lalu
    `OPENAI_COMPAT_2_*`, `OPENAI_COMPAT_3_*`, … (bernomor kontigu mulai 2; gap
    ditolak agar model tidak diam-diam hilang). Memungkinkan beberapa provider
    (mis. Gemini + Groq + DeepSeek)
    masing-masing dengan model_id/harga/context sendiri (tanpa numpang slot lain)."""
    numbered: set[int] = set()
    pattern = re.compile(r"^OPENAI_COMPAT_(\d+)_")
    for key, value in env.items():
        match = pattern.match(key)
        if match is not None and value.strip():
            numbered.add(int(match.group(1)))
    if numbered:
        if min(numbered) < 2:
            raise RuntimeError(
                "OpenAI-compatible numbered slots start at 2; use OPENAI_COMPAT_* for slot 1"
            )
        highest = max(numbered)
        expected = set(range(2, highest + 1))
        if numbered != expected:
            missing = sorted(expected - numbered)
            raise RuntimeError(
                "OpenAI-compatible numbered slots must be contiguous from 2; "
                f"missing slot(s) {missing}"
            )

    slots: list[tuple[ModelInfo, str, str, str]] = []
    first = _openai_compat_from_env(env, "OPENAI_COMPAT")
    if first is not None:
        slots.append(first)
    for n in sorted(numbered):
        slot = _openai_compat_from_env(env, f"OPENAI_COMPAT_{n}")
        if slot is None:
            raise RuntimeError(
                f"OPENAI_COMPAT_{n}_* is partially configured but "
                f"OPENAI_COMPAT_{n}_BASE_URL is missing"
            )
        slots.append(slot)
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
    from volante.providers.cli_agent import CliAgentProvider, subprocess_cli_runner
    from volante.providers.codex import codex_detected

    if env.get("CLAUDE_CODE_ENABLED") == "1" and _detect_cli("claude"):
        from volante.providers.claude_code import ClaudeCodeAdapter, claude_code_model_info

        models = model_list_from_env(
            env,
            "CLAUDE_CODE_MODELS",
            "CLAUDE_CODE_MODEL",
            default="opus",
        )
        tier = int(_metadata_value(env, "CLAUDE_CODE_TIER", "4"))
        max_output = int(_metadata_value(env, "CLAUDE_CODE_MAX_OUTPUT", "4096"))
        for model in models:
            info = claude_code_model_info(model, tier=tier, max_output_tokens=max_output)
            mid = info.id
            if mid in providers:
                raise RuntimeError(f"duplicate configured model_id {mid!r}")
            providers[mid] = CliAgentProvider(
                ClaudeCodeAdapter(),
                model,
                runner=subprocess_cli_runner,
                tier=tier,
                timeout=float(_metadata_value(env, "CLAUDE_CODE_TIMEOUT", "120")),
                max_output=max_output,
                # Replace the CLI's agent persona with the exact Volante phase contract.
                system_prompt_mode=_metadata_value(
                    env, "CLAUDE_CODE_SYSTEM_PROMPT_MODE", "replace"
                ),
            )
            extra_models.append(info)
            if baseline_model_id is None:
                baseline_model_id = mid
        _warn_subscription("claude-code")

    if env.get("CODEX_ENABLED") == "1" and codex_detected():
        from volante.providers.codex import CodexAdapter, build_codex_model

        if env.get("CODEX_TIER") is None:
            raise RuntimeError("CODEX_ENABLED=1 requires CODEX_TIER (no model-name sniffing, §6.1)")
        if "CODEX_MODELS" in env:
            models = model_list_from_env(env, "CODEX_MODELS", "CODEX_MODEL")
        elif env.get("CODEX_MODEL", "").strip():
            models = model_list_from_env(env, "CODEX_MODELS", "CODEX_MODEL")
        else:
            # Preserve the safe existing default: omit an explicit model so Codex
            # selects its own current default, represented as codex/default.
            models = [""]
        max_output = int(_metadata_value(env, "CODEX_MAX_OUTPUT", "4096"))
        for model in models:
            model_env = dict(env)
            model_env["CODEX_MODEL"] = model
            for key, default in (
                ("CODEX_CONTEXT", "256000"),
                ("CODEX_MAX_OUTPUT", "4096"),
                ("CODEX_COST_IN", "0"),
                ("CODEX_COST_OUT", "0"),
            ):
                model_env[key] = _metadata_value(model_env, key, default)
            info = build_codex_model(model_env)
            mid = info.id
            if mid in providers:
                raise RuntimeError(f"duplicate configured model_id {mid!r}")
            providers[mid] = CliAgentProvider(
                CodexAdapter(),
                model,
                runner=subprocess_cli_runner,
                tier=info.tier,
                timeout=float(_metadata_value(env, "CODEX_TIMEOUT", "120")),
                max_output=max_output,
            )
            extra_models.append(info)
            if baseline_model_id is None:
                baseline_model_id = mid
        _warn_subscription("codex")

    return baseline_model_id


def _no_providers_configured_message(include_subscription: bool) -> str:
    """Actionable "no providers" error for both callers of `build_providers_from_env`:
    the `volante` CLI (include_subscription=True) and the eval suite
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
    provider lazy supaya `import volante.bootstrap` tetap ringan/nol-jaringan."""
    from volante.providers.anthropic import AnthropicProvider
    from volante.providers.openai_compat import OpenAICompatProvider

    env = dict(os.environ)
    providers: dict[str, LLMProvider] = {}
    extra_models: list[ModelInfo] = []
    baseline_model_id: str | None = None

    anthropic_key = env.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        models = model_list_from_env(
            env,
            "ANTHROPIC_MODELS",
            "ANTHROPIC_MODEL",
            default="claude-opus-4-8",
        )
        names = _canonical_names(
            env,
            plural_key="ANTHROPIC_NAMES",
            singular_key="ANTHROPIC_NAME",
            models=models,
            default_name=lambda wire, _index: f"anthropic/{wire}",
        )
        for wire, mid in zip(models, names, strict=True):
            if mid in providers:
                raise RuntimeError(f"duplicate configured model_id {mid!r}")
            providers[mid] = AnthropicProvider(api_key=anthropic_key, model=wire)
            extra_models.append(_anthropic_model_info(wire, mid, env))
            if baseline_model_id is None:
                baseline_model_id = mid

    for info, base_url, api_key, wire in _all_openai_compat_from_env(env):
        if info.id in providers:
            raise RuntimeError(
                f"duplicate model_id {info.id!r} across OpenAI-compat slots; "
                "set a distinct *_NAME per slot"
            )
        providers[info.id] = OpenAICompatProvider(base_url=base_url, api_key=api_key, model=wire)
        extra_models.append(info)  # ModelInfo sendiri -> registry (pricing/context benar)
        if baseline_model_id is None:  # slot 1 lebih dulu -> baseline di antara slot compat
            baseline_model_id = info.id

    moonshot_key = env.get("MOONSHOT_API_KEY")
    if moonshot_key:
        base_url = _metadata_value(env, "MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
        models = model_list_from_env(
            env,
            "MOONSHOT_MODELS",
            "MOONSHOT_MODEL",
            default="kimi-k2-0711-preview",
        )
        names = _canonical_names(
            env,
            plural_key="MOONSHOT_NAMES",
            singular_key="MOONSHOT_NAME",
            models=models,
            default_name=lambda wire, index: (
                "kimi/kimi-k2"
                if len(models) == 1 and wire == "kimi-k2-0711-preview"
                else f"kimi/{wire}"
            ),
        )
        for wire, mid in zip(models, names, strict=True):
            if mid in providers:
                raise RuntimeError(f"duplicate configured model_id {mid!r}")
            providers[mid] = OpenAICompatProvider(
                base_url=base_url, api_key=moonshot_key, model=wire
            )
            extra_models.append(
                _compat_family_model_info(
                    model_id=mid,
                    provider="openai_compat",
                    prefix="MOONSHOT",
                    env=env,
                    context=128_000,
                    max_output=4_096,
                    tools=True,
                    cost_in=0.0012,
                    cost_out=0.0012,
                    tier=3,
                )
            )
            if baseline_model_id is None:
                baseline_model_id = mid

    ollama_base = env.get("OLLAMA_BASE_URL")
    if ollama_base:
        models = model_list_from_env(
            env,
            "OLLAMA_MODELS",
            "OLLAMA_MODEL",
            default="llama3.2",
        )
        names = _canonical_names(
            env,
            plural_key="OLLAMA_NAMES",
            singular_key="OLLAMA_NAME",
            models=models,
            default_name=lambda wire, _index: f"ollama/{wire}",
        )
        api_key = env.get("OLLAMA_API_KEY", "ollama")
        for wire, mid in zip(models, names, strict=True):
            if mid in providers:
                raise RuntimeError(f"duplicate configured model_id {mid!r}")
            providers[mid] = OpenAICompatProvider(base_url=ollama_base, api_key=api_key, model=wire)
            extra_models.append(
                _compat_family_model_info(
                    model_id=mid,
                    provider="ollama",
                    prefix="OLLAMA",
                    env=env,
                    context=8_192,
                    max_output=2_048,
                    tools=False,
                    cost_in=0.0,
                    cost_out=0.0,
                    tier=1,
                )
            )
            if baseline_model_id is None:
                baseline_model_id = mid

    if include_subscription:
        baseline_model_id = _register_subscription_providers(
            providers, extra_models, env, baseline_model_id
        )

    if not providers:
        raise NoProvidersConfiguredError(_no_providers_configured_message(include_subscription))
    by_id: dict[str, ModelInfo] = {}
    for m in extra_models:
        if m.id in by_id:
            raise RuntimeError(f"duplicate configured model_id {m.id!r}")
        by_id[m.id] = m
    inventory_models = apply_model_overrides(
        by_id.values(),
        load_model_overrides(env),
    )
    registry = Registry(
        inventory_models,
        quality_profiles=load_quality_profiles(env),
    )
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


def _phase_model_ids(
    registry: Registry,
    providers: dict[str, LLMProvider],
    primary_model_id: str,
    *,
    phase: str,
) -> list[str]:
    """Return deterministic primary-plus-fallback models for a sequential phase.

    Worker routing can use every user-owned model, including plan-backed CLI
    agents. Planning requires strict JSON and synthesis must remain predictable,
    so automatic phase fallbacks include only card/API/local models that satisfy
    the phase's reasoning hard constraint. A subscription-only primary remains
    available after its explicit live compatibility gate, but unverified
    subscription alternatives are never silently introduced here.
    """

    if phase not in {"planning", "synthesis"}:
        raise ValueError(f"unsupported phase {phase!r}")
    task_type = "analyze" if phase == "planning" else "write"
    task = Task(
        id=f"phase-{phase}",
        description=(
            "Decompose a user goal into a valid execution plan."
            if phase == "planning"
            else "Synthesize completed task artifacts into a final answer."
        ),
        type=task_type,
        mode="one_shot",
        difficulty="hard",
    )
    try:
        ranked = Router(registry, prefer="quality").route_ranked(task)
    except NoEligibleModelError:
        ranked = []
    fallbacks = [
        model_id
        for model_id in ranked
        if model_id in providers
        and registry.get(model_id).billing == "card"
        and model_id != primary_model_id
    ]
    return [primary_model_id, *fallbacks]


async def verify_claude_plan_gate(
    provider: LLMProvider,
    model_id: str,
    *,
    goal: str = "Plan a single trivial task: print the word hello.",
    cost_meter: CostMeter | None = None,
    before_call: Callable[[str], None] | None = None,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> bool:
    """§7.1 live gate. Run one bounded planning probe sequence through ``provider``.

    The production Supervisor may make up to three corrective attempts. The gate
    succeeds only if one response survives the same parser and validator used for
    a real run. Every physical attempt is exposed to the optional call gate and
    meter; provider/validation errors fail closed.
    """
    from volante.supervisor import Supervisor

    meter = cost_meter if cost_meter is not None else CostMeter()
    probe = Supervisor(provider, model_id, meter)
    probe.set_call_gate(before_call)
    if context_window is not None and max_output_tokens is not None:
        probe.set_model_limits(
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        )
    try:
        await probe.plan(goal)
    except (ProviderError, ValueError):
        return False
    return True


async def ensure_subscription_planner_compatible(
    registry: Registry,
    providers: dict[str, LLMProvider],
    baseline_model_id: str,
    *,
    gate_cost_meter: CostMeter | None = None,
    before_gate_call: Callable[[str], None] | None = None,
) -> str:
    """Return the selected planner id after enforcing the live subscription gate.

    API/local planners honor ``temperature=0`` and need no compatibility probe.
    When a subscription CLI is the only available planner, fail closed unless one
    live response survives the production plan parser. Keeping this policy here,
    next to planner selection, prevents CLI/MCP/Web UI entrypoints from drifting.
    """
    planner_id = _planner_model_id(registry, providers, baseline_model_id)
    if _temperature_controllable(registry, planner_id):
        return planner_id
    planner = registry.get(planner_id)
    if await verify_claude_plan_gate(
        providers[planner_id],
        planner_id,
        cost_meter=gate_cost_meter,
        before_call=before_gate_call,
        context_window=planner.context_window,
        max_output_tokens=planner.max_output_tokens,
    ):
        return planner_id
    raise RuntimeError(
        f"subscription planner {planner_id!r} failed the live parse-plan gate: "
        "it did not return a plan that survives the supervisor's own parser, so it "
        "cannot be trusted to plan deterministically. Configure a card-billed planner "
        "(e.g. ANTHROPIC_API_KEY, an OPENAI_COMPAT_* slot, or Ollama) and retry."
    )


def _runtime_factory(
    registry: Registry,
    providers: dict[str, LLMProvider],
    planner_id: str,
    *,
    prefer: str,
    preflight_meter: CostMeter | None = None,
    preflight_subscription_calls: int = 0,
) -> Callable[[], Runtime]:
    """Construct the actual factory after planner compatibility is established."""

    preflight_pending = preflight_subscription_calls > 0
    allowed_domains = [
        value.strip()
        for value in os.environ.get("VOLANTE_FETCH_ALLOWLIST", "").split(",")
        if value.strip()
    ]
    read_root = os.environ.get("VOLANTE_READ_ROOT") or None
    # Resolve the execution sandbox ONCE here, so the planner is told about exactly the
    # tools that will exist at run time. Without an isolating sandbox, code execution is
    # withheld rather than run with full host access (§9 fail-closed).
    from volante.tools.sandbox import resolve_sandbox_mode

    sandbox_mode, sandbox_reason = resolve_sandbox_mode()
    available_tool_names: set[str] = set()
    # The notice travels WITH the result (RunResult.capability_notice), because an IDE
    # MCP client or the Web UI never sees this process's stderr — without it a degraded
    # run (code was never executed) looks identical to a full-capability one.
    capability_notice: str | None = None
    if sandbox_mode == "unavailable":
        capability_notice = (
            f"Code execution (run_python) is disabled: {sandbox_reason}."
        )
        print(f"volante: {capability_notice}", file=sys.stderr)
    else:
        available_tool_names.add("run_python")
        if sandbox_mode == "subprocess":
            capability_notice = (
                "Code execution is running UNISOLATED "
                f"({sandbox_reason}): model-generated code runs as you, with your "
                "network and files. Prefer VOLANTE_SANDBOX=docker."
            )
            print(f"volante: {capability_notice}", file=sys.stderr)
    if allowed_domains:
        available_tool_names.add("fetch_url")
    if read_root is not None:
        available_tool_names.add("read_file")
    planning_model_ids = _phase_model_ids(
        registry,
        providers,
        planner_id,
        phase="planning",
    )
    synthesis_model_ids = _phase_model_ids(
        registry,
        providers,
        planner_id,
        phase="synthesis",
    )

    def make_tools(workspace: Path):
        from volante.tools.factory import build_agentic_tools

        return build_agentic_tools(
            workspace,
            allowed_domains=allowed_domains,
            read_root=read_root,
            sandbox_mode=sandbox_mode,
        )

    def make_runtime() -> Runtime:
        nonlocal preflight_pending
        cost_meter = CostMeter()
        initial_subscription_calls = 0
        # A verified factory may serve multiple Web UI runs. The live gate happened
        # once, so charge it exactly once to the first Runtime created from it.
        if preflight_pending:
            if preflight_meter is not None:
                cost_meter.merge(preflight_meter)
            initial_subscription_calls = preflight_subscription_calls
            preflight_pending = False
        return Runtime(
            supervisor=Supervisor(providers[planner_id], planner_id, cost_meter),
            router=Router(registry, prefer=prefer),
            projector=Projector(registry),
            worker=Worker(providers, cost_meter),
            synthesizer=Synthesizer(providers[planner_id], planner_id, cost_meter),
            registry=registry,
            cost_meter=cost_meter,
            agentic_worker=AgenticWorker(providers, cost_meter),
            initial_subscription_calls=initial_subscription_calls,
            tools_factory=make_tools,
            available_tool_names=available_tool_names,
            phase_providers=providers,
            planning_model_ids=planning_model_ids,
            synthesis_model_ids=synthesis_model_ids,
            capability_notice=capability_notice,
        )

    return make_runtime


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
    own default: route each task to the highest predicted fit. Pass
    `prefer="cash_protect_quota"` to right-size instead and protect subscription quota.

    This synchronous helper is safe when planner selection resolves to an API/local
    model. A subscription-only setup requires a live async compatibility probe, so
    callers must use :func:`make_verified_runtime_factory` instead of bypassing it."""
    planner_id = _planner_model_id(registry, providers, model_id)
    if not _temperature_controllable(registry, planner_id):
        raise RuntimeError(
            "subscription-only planner setup requires the live compatibility gate; "
            "use `await make_verified_runtime_factory(...)`"
        )
    return _runtime_factory(registry, providers, planner_id, prefer=prefer)


async def make_verified_runtime_factory(
    registry: Registry,
    providers: dict[str, LLMProvider],
    model_id: str,
    *,
    prefer: str = "quality",
) -> Callable[[], Runtime]:
    """Build the runtime factory after enforcing planner compatibility.

    This is the canonical entrypoint for user-facing surfaces. It is deliberately
    async because subscription-only configurations require one real provider call;
    card/API/local configurations return immediately without a probe.
    """
    preflight_meter = CostMeter()
    preflight_calls = 0
    raw_cap = os.environ.get("VOLANTE_MAX_SUBSCRIPTION_CALLS", "16")
    try:
        subscription_cap = int(raw_cap)
    except ValueError as exc:
        raise ValueError("VOLANTE_MAX_SUBSCRIPTION_CALLS must be a non-negative integer") from exc
    if subscription_cap < 0:
        raise ValueError("VOLANTE_MAX_SUBSCRIPTION_CALLS must be a non-negative integer")
    candidate_planner = _planner_model_id(registry, providers, model_id)
    if subscription_cap == 0 and not _temperature_controllable(registry, candidate_planner):
        raise RuntimeError(
            "subscription planner preflight is disabled because "
            "VOLANTE_MAX_SUBSCRIPTION_CALLS=0; configure a card/local planner or "
            "raise the cap"
        )

    def reserve_preflight(_model_id: str) -> None:
        nonlocal preflight_calls
        if preflight_calls >= subscription_cap:
            raise ProviderError(
                f"subscription cap {subscription_cap} reached during planner preflight",
                retryable=False,
                quota_exhausted=True,
            )
        preflight_calls += 1

    try:
        planner_id = await ensure_subscription_planner_compatible(
            registry,
            providers,
            model_id,
            gate_cost_meter=preflight_meter,
            before_gate_call=reserve_preflight,
        )
    except RuntimeError as exc:
        detail = (
            f" Planner preflight consumed {preflight_calls} physical subscription "
            "call(s) before failing."
            if preflight_calls
            else ""
        )
        raise RuntimeError(f"{exc}{detail}") from exc
    return _runtime_factory(
        registry,
        providers,
        planner_id,
        prefer=prefer,
        preflight_meter=preflight_meter,
        preflight_subscription_calls=preflight_calls,
    )

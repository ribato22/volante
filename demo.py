"""End-to-end demo of the AI orchestration engine (run MANUALLY; needs real providers).

Set env (or a .env you export) first, then:

    uv run python demo.py            # show detected providers + usage
    uv run python demo.py agentic    # one cross-provider agentic coding task (run_python loop)
    uv run python demo.py orchestrate  # full supervisor->workers->synth, all phases stream live
    uv run python demo.py eval       # 3-arm eval suite (baseline vs orchestration vs agentic)

Env read: ANTHROPIC_API_KEY, MOONSHOT_API_KEY (+MOONSHOT_BASE_URL), OLLAMA_BASE_URL,
a generic OpenAI-compatible slot OPENAI_COMPAT_BASE_URL (+OPENAI_COMPAT_KEY,
OPENAI_COMPAT_MODEL, optional OPENAI_COMPAT_NAME/_CONTEXT/_MAX_OUTPUT/_TOOLS/_COST_IN/
_COST_OUT) for Gemini/Groq/OpenRouter/DeepSeek/etc.,
BATON_SANDBOX=docker (real container isolation; needs Docker up; default subprocess;
legacy AIORCH_SANDBOX name still works),
DEMO_FETCH_ALLOWLIST=example.com,docs.python.org (adds fetch_url tool to the agentic run).

  # e.g. free Google AI Studio Gemini Flash (top of the free tier by intelligence):
  #   OPENAI_COMPAT_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
  #   OPENAI_COMPAT_KEY=<ai-studio-key> OPENAI_COMPAT_MODEL=gemini-2.5-flash \
  #   OPENAI_COMPAT_NAME=google/gemini-flash uv run python demo.py orchestrate

Only the pure helpers (detect_providers / pick_agentic_model / _fmt_turns) are unit-tested;
the two run modes touch the network and are executed by you.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baton.agent import TurnRecord
    from baton.registry import Registry


def detect_providers(env: dict[str, str]) -> list[str]:
    """Nama provider yang terkonfigurasi di env (urut Anthropic, OpenAI-compat generik,
    Kimi, Ollama)."""
    found: list[str] = []
    if env.get("ANTHROPIC_API_KEY"):
        found.append("anthropic")
    # slot generik: OPENAI_COMPAT_BASE_URL (slot 1) atau OPENAI_COMPAT_2_/3_… (tambahan)
    if any(k.startswith("OPENAI_COMPAT") and k.endswith("_BASE_URL") for k in env):
        found.append("openai-compat")
    if env.get("MOONSHOT_API_KEY"):
        found.append("kimi")
    if env.get("OLLAMA_BASE_URL"):
        found.append("ollama")
    return found


def pick_agentic_model(registry: Registry, providers: dict) -> str | None:
    """Model tool-capable pertama yang punya provider; jika tak ada, jatuh ke model
    terkonfigurasi pertama (loop tetap jalan — model mungkin tak memanggil tool)."""
    for m in registry.all():
        if m.supports_tools and m.id in providers:
            return m.id
    for m in registry.all():
        if m.id in providers:
            return m.id
    return None


def _fmt_turns(turns: list[TurnRecord]) -> str:
    lines: list[str] = []
    for t in turns:
        payload = " ".join(t.payload.split())[:140]
        lines.append(f"  [{t.index}] {t.kind:<12} {payload}")
    return "\n".join(lines)


async def demo_agentic() -> None:
    """Satu task agentic (perbaiki kode buggy pakai run_python) di model tool-capable
    apa pun yang terkonfigurasi — Anthropic ATAU Kimi (lintas-penyedia)."""
    from eval.run import build_providers_from_env

    from baton.agent import AgenticWorker
    from baton.cost import CostMeter
    from baton.tools.factory import build_agentic_tools
    from baton.types import CanonicalRequest, text

    registry, providers, _ = build_providers_from_env()
    model_id = pick_agentic_model(registry, providers)
    if model_id is None:
        print("No provider configured. Set ANTHROPIC_API_KEY / MOONSHOT_API_KEY / OLLAMA_BASE_URL.")
        return
    sandbox = os.environ.get("BATON_SANDBOX") or os.environ.get("AIORCH_SANDBOX", "subprocess")
    print(f"Agentic demo — model={model_id}  sandbox={sandbox}\n")

    ws = Path(".runs") / "demo" / uuid.uuid4().hex[:8]
    allow = os.environ.get("DEMO_FETCH_ALLOWLIST")
    domains = {d.strip() for d in allow.split(",") if d.strip()} if allow else None
    tools = build_agentic_tools(ws, allowed_domains=domains)

    goal = (
        "There is a bug: add(a, b) currently returns a - b. In a file named solution.py, "
        "implement add(a, b) correctly so it returns a + b. Also write a pytest test that "
        "asserts add(2, 3) == 5. Use the run_python tool to execute your test and iterate "
        "until it passes. When the test passes, reply with the word DONE."
    )
    cm = CostMeter()
    worker = AgenticWorker(providers, cm)
    mi = registry.get(model_id)
    req = CanonicalRequest(
        messages=[text("user", goal)],
        max_tokens=mi.max_output_tokens,
        temperature=0.0,
        task_id="demo",
    )
    def _emit(s: str) -> None:
        print(s, end="", flush=True)

    print("(streaming live)\n")
    res = await worker.run(req, model_id, tools, on_text=_emit)
    print()  # newline setelah stream

    print("TRANSCRIPT:")
    print(_fmt_turns(res.turns))
    print("\nFINAL:\n" + res.final_text.strip())
    total = res.usage_total.get(model_id)
    if total is not None:
        est = " (estimated)" if total.estimated else ""
        print(
            f"\nusage[{model_id}]: prompt={total.prompt_tokens} "
            f"completion={total.completion_tokens}{est}"
        )
    print(f"cost: ${cm.cost_usd(registry):.6f}")


async def demo_orchestrate() -> None:
    """Orkestrasi penuh (supervisor -> workers paralel -> synthesizer) ter-stream live:
    fase sekuensial (planning + sintesis) via on_text, dan worker PARALEL via
    on_worker_text yang di-prefix `[task_id]` sehingga output antar-task terurai (tak
    bercampur). Butuh provider terkonfigurasi."""
    from eval.run import build_providers_from_env, make_runtime_factory

    try:
        registry, providers, model_id = build_providers_from_env()
    except RuntimeError as exc:
        print(str(exc))
        return
    make_runtime = make_runtime_factory(registry, providers, model_id)
    runtime = make_runtime()
    goal = (
        "Write a short haiku about concurrency, then explain the haiku in one sentence."
    )
    print(f"Orchestrate demo — planner/synth model={model_id}\n")

    def _emit(s: str) -> None:
        print(s, end="", flush=True)

    def _emit_worker(task_id: str, s: str) -> None:
        # Worker paralel: prefix task_id agar output antar-task terurai.
        print(f"[{task_id}] {s}", end="", flush=True)

    print("(planning + workers + synthesis stream live)\n")
    result = await runtime.aexecute(goal, on_text=_emit, on_worker_text=_emit_worker)
    print(f"\n\nSTATUS: {result.status}")
    if result.final:
        print("\nFINAL:\n" + result.final.strip())
    if result.failed_task:
        print(f"failed_task: {result.failed_task}")
    print(f"\ncost: ${result.cost_usd:.6f}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "agentic":
        asyncio.run(demo_agentic())
    elif mode == "orchestrate":
        asyncio.run(demo_orchestrate())
    elif mode == "eval":
        from eval.run import main as eval_main

        asyncio.run(eval_main())
    else:
        found = detect_providers(dict(os.environ))
        print(__doc__ or "")
        print("Detected providers: " + (", ".join(found) if found else "NONE (set env keys above)"))


if __name__ == "__main__":
    main()

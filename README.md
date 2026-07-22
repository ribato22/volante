# Baton

[![CI](https://github.com/ribato/baton/actions/workflows/ci.yml/badge.svg)](https://github.com/ribato/baton/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

**A from-scratch, cross-provider multi-model AI orchestration engine.** A *supervisor* model
decomposes a goal into a task DAG, *routes* each sub-task to the best model — across providers
(Anthropic, any OpenAI-compatible endpoint, Ollama) — runs them one-shot or in an agentic tool
loop, and *synthesizes* a final answer. Built without an orchestration framework (no LangChain /
CrewAI / LiteLLM) as a study of how these systems actually work under the hood.

> One conductor, many players — pass the *baton* from the leader model to the workers and back.

---

## Highlights

- **Supervisor + routing.** An LLM plans a validated, acyclic task DAG; a router sends each task
  to the cheapest model whose strengths (and tool support) match.
- **Cross-provider.** `AnthropicProvider` and a generic `OpenAICompatProvider` speak to Anthropic,
  Google AI Studio (Gemini), Groq, OpenRouter, DeepSeek, Moonshot (Kimi), local Ollama, and any
  other OpenAI-compatible endpoint — no code changes, just env vars.
- **Hybrid one-shot / agentic.** Tasks run as a single call *or* as a model↔tool loop (`run_python`
  in a subprocess sandbox — container-isolated under `AIORCH_SANDBOX=docker` — plus host-mediated
  `fetch_url` / `read_file`).
- **Shared context.** An append-only *blackboard* carries provenance; each task gets a scoped,
  budget-capped projection of only the dependency artifacts it needs.
- **Streaming everywhere.** Live token streaming through the supervisor, workers, and synthesizer,
  with per-task labels for parallel workers and cooperative early-stop.
- **Cost & honesty.** A `CostMeter` tallies per-model usage and cost, and propagates an *estimated*
  flag when a provider returns no usage.
- **Forgery-resistant evaluation.** A 3-arm eval (baseline vs. orchestration vs. single-agent) with
  a scorer that runs untrusted solution code under **process + filesystem separation** so a model
  cannot fake a passing score.
- **Tested.** 330+ tests, zero-network by default (`FakeProvider` + local subprocesses), `ruff`-clean.

## Architecture

```text
                    ┌──────────────┐
   goal ──────────► │  Supervisor  │  plan → validated task DAG (acyclic, typed, one_shot|agentic)
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐   per task: pick cheapest model whose strengths + tool
                    │    Router    │   support match the task type
                    └──────┬───────┘
                           ▼
        ┌───────────── wave execution (asyncio, fan-out cap, fail-fast) ─────────────┐
        │   ┌───────────┐   scoped, budget-capped request (system + task + deps)      │
        │   │ Projector │──────────────────────────────────────────────────────────► │
        │   └───────────┘                                                             │
        │        ▼                              ▼                                      │
        │   ┌─────────┐  one-shot          ┌───────────────┐  model↔tool loop         │
        │   │ Worker  │                    │ AgenticWorker │  (run_python sandbox,     │
        │   └────┬────┘                    └───────┬───────┘   fetch_url, read_file)   │
        │        └──────────────┬──────────────────┘                                   │
        └───────────────────────┼───────────────────────────────────────────────────┘
                                 ▼
                    ┌──────────────────────┐   append-only, provenance, latest-wins
                    │  Blackboard          │◄──────────────────────────────────────
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────┐
                    │ Synthesizer  │  combine artifacts → final answer
                    └──────┬───────┘
                           ▼
                        result  (+ CostMeter totals, usage, duration)
```

| Component | File | Responsibility |
|---|---|---|
| Supervisor | `src/orchestrator/supervisor.py` | Decompose goal → validated task DAG |
| Router | `src/orchestrator/router.py` | Task → model by strengths + tool support (cheapest match) |
| Projector | `src/orchestrator/projector.py` | Scoped, budget-capped request from blackboard artifacts |
| Worker | `src/orchestrator/worker.py` | One-shot model call |
| AgenticWorker | `src/orchestrator/agent.py` | Model↔tool loop with per-turn records |
| Blackboard | `src/orchestrator/blackboard.py` | Append-only shared state with provenance |
| Synthesizer | `src/orchestrator/synthesizer.py` | Artifacts → final answer |
| Runtime | `src/orchestrator/runtime.py` | Orchestrate: plan → waves → synthesize (streaming, fail-fast) |
| Providers | `src/orchestrator/providers/` | Anthropic + OpenAI-compatible adapters (complete/stream/tools) |
| Tools | `src/orchestrator/tools/` | Sandbox / DockerSandbox, run_python, fetch_url, read_file |
| Eval | `eval/` | 5 composite goals, 3-arm comparison, forgery-resistant scorer |

## Quickstart

Requires **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ribato/baton
cd baton
uv sync --dev            # install deps + dev tools
uv run pytest            # 330+ tests, no network
uv run ruff check .      # lint
```

Configure at least one provider (see [Providers](#providers)), then run a demo:

```bash
cp .env.example .env     # fill in one provider, then `set -a; . .env; set +a`

uv run python demo.py               # show detected providers
uv run python demo.py orchestrate   # full supervisor → workers → synth, streamed live
uv run python demo.py agentic       # one cross-provider agentic coding task (run_python loop)
uv run python demo.py eval          # 3-arm eval suite
```

### Example output

`demo.py orchestrate` streams every phase live, then prints the result (illustrative):

```text
Orchestrate demo — planner/synth model=openai/gpt-4o-mini

(planning + workers + synthesis stream live)
[haiku] Threads run as one— / tasks bloom in parallel time, / the join gathers all.

STATUS: success

FINAL:
Threads run as one—
tasks bloom in parallel time,
the join gathers all.

cost: $0.001834
```

`demo.py eval` prints the 3-arm table (`format_report`); read the `VERDICT` with the warnings
(illustrative numbers):

```text
GOAL          WINNER            BASE   ORCH   AGEN
-------------------------------------------------
slugify       orchestration     0.70   1.00   0.85
roman         baseline          1.00   0.85   0.55
calc          orchestration     0.55   0.85   0.70
csv_stats     agentic           0.40   0.55   0.85
json_flatten  orchestration     0.70   1.00   0.85
-------------------------------------------------
wins: baseline=1  orchestration=3  agentic=1  ties=0
totals: baseline $0.0210  orchestration $0.0480  agentic $0.0350
VERDICT: ORCHESTRATION
```

## Providers

Set environment variables for any subset; baseline priority is
**Anthropic > OpenAI-compat > Kimi > Ollama**. See [`.env.example`](.env.example) for the full list.

| Provider | Env | Access |
|---|---|---|
| **Anthropic** (Claude) | `ANTHROPIC_API_KEY` | Paid API (`console.anthropic.com`) |
| **Generic OpenAI-compatible** | `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_MODEL` (+`_KEY`/`_NAME`/`_CONTEXT`/…) | Any OpenAI-compatible endpoint |
| **Moonshot / Kimi** | `MOONSHOT_API_KEY` | Paid API |
| **Ollama** | `OLLAMA_BASE_URL` | **Local & free** |

> **Subscriptions are not APIs.** A `claude.ai` / ChatGPT / Antigravity subscription cannot be used
> here — those are chat products, separate from the programmatic APIs the engine calls.

**Free, high-intelligence option** — Google AI Studio (Gemini Flash), via the generic slot:

```bash
export OPENAI_COMPAT_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export OPENAI_COMPAT_KEY=<ai-studio-key>       # aistudio.google.com/apikey
export OPENAI_COMPAT_MODEL=gemini-2.5-flash
export OPENAI_COMPAT_NAME=google/gemini-flash
uv run python demo.py orchestrate
```

The generic slot defaults to industry-standard values (context 128k, output 8k, tool-capable, cost
0 for free tiers) and registers its own `ModelInfo`, so cost/context accounting is correct.

## Evaluation

`demo.py eval` runs a **3-arm** comparison over 5 composite coding goals: **baseline** (one strong
model, one shot), **orchestration** (the full engine), and **agentic-single** (one model + a
`run_python` loop, no decomposition). Each goal is scored by a hidden reference test.

The scorer runs the model's generated `solution.py` in a subprocess under **process + filesystem
separation**: a trusted runner drives the untrusted solution in a *separate* process that never sees
the expected outputs (nonce-authenticated RPC), so a solution must actually compute correct answers —
it cannot fake a passing score. See
[`docs/superpowers/specs/2026-07-21-eval-process-separation-design.md`](docs/superpowers/specs/2026-07-21-eval-process-separation-design.md).

Read the verdict together with the warnings the harness emits:

- `WARNING: some costs are estimated …` — a provider returned no usage; cost comparison is soft.
- `WARNING: agentic arm failed N run(s) …` — a `0.0` may be infra/provider failure, not capability.
- `WARNING: goal(s) […] produced NO trusted result …` — the reference runner itself is broken; those
  scores are harness artifacts, not real zeros.

## Security & limitations (honest)

This is a study project; its isolation guarantees are deliberately scoped and documented.

- **Agentic sandbox is for self-written goals.** The default subprocess `Sandbox` protects against
  *accidents*, not *adversaries*: on macOS the host network and disk remain reachable. For real
  isolation use `AIORCH_SANDBOX=docker` (runs code in a container with `--network none`, read-only
  root, cgroup limits) — this is the prerequisite for the network-isolation guarantee.
- **External tools are host-mediated.** `fetch_url` (domain allowlist) and `read_file` (root-confined)
  run in the trusted orchestrator so sandboxed code stays network-isolated. Prompt-injection
  containment holds only under the Docker sandbox.
- **Eval scoring is forgery-resistant, best-effort POSIX.** Process + filesystem separation stops a
  solution from faking a score; a solution calling `setsid()` can still escape the `killpg` group
  (the wall-clock timeout still bounds the run). It is process isolation, not a security sandbox for
  arbitrary hostile code.
- **Never put secrets in model context.** Allowlists and the read-file root are the trust boundary.

## Project layout

```text
src/orchestrator/     # engine (importable package: `orchestrator`)
  providers/          # Anthropic + OpenAI-compatible adapters, FakeProvider
  tools/              # Sandbox, DockerSandbox, run_python, fetch_url, read_file
eval/                 # goals, 3-arm harness, forgery-resistant scorer, runner
tests/                # 330+ tests (unit + opt-in integration)
docs/superpowers/     # design specs and implementation plans
demo.py               # end-to-end demo (orchestrate | agentic | eval)
```

## Development

- **Test-driven, zero-network by default.** `uv run pytest` uses `FakeProvider` and local
  subprocesses; integration tests that touch the network/Docker are marked `integration` and skipped
  by default (`uv run pytest -m integration` to opt in).
- **Lint:** `uv run ruff check .` (line length 100; `E,F,I,UP,B`).
- Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Security reports:
  [SECURITY.md](SECURITY.md). Release notes: [CHANGELOG.md](CHANGELOG.md).

## Roadmap

- Run the real 3-arm eval across providers and interpret whether orchestration beats a single model.
- Per-task labelled streaming to a UI; async-generator / backpressure streaming API.
- Ollama tool-calling / streaming integration coverage.

## License

[MIT](LICENSE) © 2026 ribato.

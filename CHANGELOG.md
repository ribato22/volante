# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-22

### Added
- Supervisor + routing engine: goal → validated task DAG → per-task model routing (by strengths and
  tool support) → scoped, budget-capped projection → wave execution (async fan-out, fail-fast) →
  synthesis, with a `CostMeter` (per-model usage/cost, estimated-flag propagation).
- Provider adapters: `AnthropicProvider` and a tool-capable `OpenAICompatProvider`, plus a generic
  `OPENAI_COMPAT_*` slot for any OpenAI-compatible endpoint (Gemini / Groq / OpenRouter / DeepSeek /
  Ollama) with correct model_id, pricing, and context window.
- Hybrid execution: one-shot workers and an agentic model↔tool loop (`run_python` sandbox,
  host-mediated `fetch_url` / `read_file`).
- Isolation: subprocess `Sandbox` (process-group kill, `RLIMIT_CPU`, scrubbed env) and an opt-in
  `DockerSandbox` (`--network none`, read-only root, cgroup limits).
- Streaming across supervisor / workers / synthesizer, with per-task labelled parallel-worker
  streaming and cooperative early-stop.
- Evaluation: 5 composite goals, a 3-arm comparison (baseline / orchestration / agentic-single), and
  a forgery-resistant scorer using process + filesystem separation with a nonce-authenticated RPC.
- Optional Web UI (`webui/`): a FastAPI + Server-Sent-Events app that streams a run live in the
  browser (plan, per-task worker output, synthesis, result); uses real providers or a no-key
  `FakeProvider` demo. Install with the `ui` extra; run via `python -m webui`.
- Project docs: README, LICENSE (MIT), SECURITY, CONTRIBUTING, CI, and design specs under `docs/`.

[Unreleased]: https://github.com/ribato/baton/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ribato/baton/releases/tag/v0.1.0

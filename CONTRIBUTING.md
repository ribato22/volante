# Contributing to Baton

Thanks for your interest! Baton is a from-scratch study of multi-model orchestration, so clarity and
correctness matter more than feature count.

## Development setup

Requires **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest            # full suite, no network
uv run ruff check .      # lint (line length 100; E,F,I,UP,B)
```

Integration tests touch the network or Docker and are skipped by default:

```bash
uv run pytest -m integration     # opt in (needs provider keys / a Docker daemon)
```

## Working style

- **Test-driven.** Add or update tests with every behavior change; keep the suite green and
  zero-network by default (use `FakeProvider` and local subprocesses).
- **Small, focused changes.** Match the surrounding code's naming, comment density, and idioms.
- **No orchestration frameworks.** The engine is intentionally built from scratch; provider adapters
  may use the official `anthropic` / `openai` SDKs behind the `LLMProvider` interface.
- **Be honest about security.** If a change touches isolation or the eval scorer, state the exact
  guarantee and its limits (see [SECURITY.md](SECURITY.md)); don't overclaim.

## Pull requests

1. Branch off `main`.
2. Ensure `uv run pytest` and `uv run ruff check .` are green.
3. Describe *what* changed and *why*; note any new invariant or limitation.
4. One logical change per PR where possible.

## Commit messages

Use conventional prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`) and a concise, imperative
subject that says what and why.

## Reporting bugs

Open a [GitHub issue](https://github.com/ribato22/baton/issues) with a minimal reproduction. For
security issues, follow [SECURITY.md](SECURITY.md) instead.

## Code of Conduct

This project is governed by the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you
agree to uphold it; report unacceptable behavior **privately** via the contact in that document
(not the public issue tracker).

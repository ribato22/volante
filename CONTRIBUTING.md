# Contributing to Volante

Thanks for your interest! Volante is a study of multi-model orchestration, so clarity and
correctness matter more than feature count.

## Development setup

Requires **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/).

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
- **No orchestration frameworks.** The engine is intentionally built without one (no LangChain /
  CrewAI / LiteLLM); provider adapters may use the official `anthropic` / `openai` SDKs behind the
  `LLMProvider` interface.
- **Be honest about security.** If a change touches isolation or the eval scorer, state the exact
  guarantee and its limits (see [SECURITY.md](SECURITY.md)); don't overclaim.

## Reading the `§N` citations

You will find comments citing sections like `(§6.4)` or `(§8.3)`. Those point at the private
design spec this engine was built from, which is **not published**. You are not missing anything
you need: the citation is provenance, not a dependency, and the reasoning it refers to is always
written out in the comment itself. They are dropped as the files around them are rewritten.

If a comment ever leaves you needing the spec to understand the code, that comment is the bug —
please open an issue for it.

## Pull requests

1. Branch off `main`.
2. Ensure `uv run pytest` and `uv run ruff check .` are green.
3. Describe *what* changed and *why*; note any new invariant or limitation.
4. One logical change per PR where possible.

## Commit messages

Use conventional prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`) and a concise, imperative
subject that says what and why.

## Reporting bugs

Open a [GitHub issue](https://github.com/ribato22/volante/issues) with a minimal reproduction. For
security issues, follow [SECURITY.md](SECURITY.md) instead.

## Code of Conduct

This project is governed by the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you
agree to uphold it; report unacceptable behavior **privately** via the contact in that document
(not the public issue tracker).

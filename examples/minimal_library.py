"""Minimal example: use Baton as a library against your OWN configured provider.

Needs at least one provider configured in the environment — see the README
"Providers" section or `.env.example` (ANTHROPIC_API_KEY, OPENAI_COMPAT_BASE_URL,
MOONSHOT_API_KEY, or OLLAMA_BASE_URL). Uses only the public top-level `baton` API
(see README Usage > Library).

Run:

    uv run python examples/minimal_library.py "write a haiku about concurrency"

If no provider is configured this exits with the actionable "No providers
configured" error instead of a traceback — see `examples/fake_provider.py` for a
variant that needs NO keys at all.
"""

from __future__ import annotations

import asyncio
import sys

import baton


async def main() -> int:
    goal = " ".join(sys.argv[1:]) or "write a haiku about concurrency"

    try:
        registry, providers, model_id = baton.build_providers_from_env()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    make_runtime = baton.make_runtime_factory(registry, providers, model_id)
    runtime = make_runtime()

    result = await runtime.aexecute(goal)

    print(f"status: {result.status}")
    if result.final:
        print(f"final:\n{result.final}")
    print(f"billed_usd: {result.billed_usd}  credit_usd: {result.credit_usd}")
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

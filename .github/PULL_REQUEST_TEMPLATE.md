## What & why

<!-- Describe what changed and why. Note any new invariant or limitation this introduces. -->

## Checklist

- [ ] `uv run pytest` is green (zero-network by default; ran `uv run pytest -m integration` too if
      this touches network/Docker/subscription-provider code)
- [ ] `uv run ruff check .` is clean
- [ ] Tests added/updated for the behavior change (see
      [CONTRIBUTING.md](https://github.com/ribato/baton/blob/main/CONTRIBUTING.md) — test-driven,
      small focused changes)
- [ ] `CHANGELOG.md`'s `[Unreleased]` section is updated (Added/Changed/Fixed, matching existing
      style)
- [ ] If this touches isolation/sandboxing or the eval scorer: the exact guarantee and its limits
      are stated honestly (see
      [SECURITY.md](https://github.com/ribato/baton/blob/main/SECURITY.md)) — no overclaiming
- [ ] README updated if user-facing behavior (CLI flags, env vars, Providers) changed

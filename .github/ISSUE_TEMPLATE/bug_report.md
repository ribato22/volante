---
name: Bug report
about: Report something that isn't working as expected
title: "[Bug] "
labels: bug
assignees: ""
---

## Describe the bug

A clear, concise description of what the bug is.

## Reproduction

Minimal steps to reproduce the behavior, ideally a `uv run baton "..."` invocation, a
`demo.py`/library snippet, or a failing `uv run pytest` test.

```bash
# commands / code here
```

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened (include the full error output / traceback if any — `baton` itself should
never print a raw traceback, so if you see one that's part of the bug).

## Environment

- Baton version (`uv run baton --version`):
- Python version (`python --version`):
- OS:
- Provider(s) configured (Anthropic / OpenAI-compat / Kimi / Ollama / Claude Code / Codex — no need
  to share keys):

## Additional context

Anything else relevant (logs, related issues, whether it reproduces with `FakeProvider` / no
network).

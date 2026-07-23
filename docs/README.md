# docs/

This directory holds **internal design and build records**, not user-facing
documentation. If you're looking for how to install, configure, or use Baton, start
with the top-level [README](../README.md) instead.

## docs/superpowers/specs/

Design **decision records** — the "why". Each spec captures the architecture research
and locked design decisions for a feature *before* implementation started (problem
framing, alternatives considered, trade-offs, the decisions that were locked in).
Written once, largely static afterward.

## docs/superpowers/plans/

Detailed implementation **build records** — the "how it actually got built". Each plan
is a verbose, task-by-task log used to drive and track the real implementation
(including checkboxes, exact shell commands run, and commit messages as they happened).
These are historical artifacts of the build process, not maintained after the fact —
expect them to reflect the state of the code *at the time each task was completed*,
which may have since evolved. They are kept as an honest record of how the system was
actually built, not deleted or rewritten after the fact.

## docs/claude-code-live-gate.md

A standalone note on the live Claude Code plan-gate (`verify_claude_plan_gate` in
`src/baton/bootstrap.py`).

---

None of the above is required reading to use Baton as a library, CLI, or Web UI — see
the [top-level README](../README.md) for that.

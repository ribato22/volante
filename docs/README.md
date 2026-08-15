# docs/

This directory holds **internal design and build records**, not user-facing
documentation. If you're looking for how to install, configure, or use Volante, start
with the top-level [README](../README.md) instead.

## docs/claude-code-live-gate.md

A standalone note on the live Claude Code plan-gate (`verify_claude_plan_gate` in
`src/volante/bootstrap.py`).

## docs/p0-p1-review-2026-07-26.md

The professional product/engineering audit, completion matrix, validation evidence,
residual risks, and recommended P2 priorities for the P0/P1 hardening sprint.

## docs/benchmark-story.md

The one concrete benchmark story (2026-08-14): which model answers matters more than
any orchestration (measured on `resolve`: 0.497 → 0.958 by model swap alone), the
three-arm verdict and its limits, the five numbers the project had to correct
downward, the gaps still unmeasured (aggregated latency, wider inventory), and the
60-second demo script every number maps to. All figures cite `eval/artifacts/README.md`.

## docs/release-checklist.md

The coordinated version bump (P2#6): every surface that names the version — Python
package, Smithery pin, MCP registry pin, manifest, changelog, tag — plus the fresh
build/smoke-install/registry-validation sequence and the held decisions (version
number proposal: 0.10.0) that need the maintainer before tagging.

---

None of the above is required reading to use Volante as a library, CLI, or Web UI — see
the [top-level README](../README.md) for that.

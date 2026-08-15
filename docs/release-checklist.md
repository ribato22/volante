# Release checklist

The coordinated version bump the 2026-07-26 audit flagged as P2#6. Every surface that
knows the version is listed; a bump that touches one and misses another ships a broken
pin. Rehearsed for the current 0.9.0 → next release; replace X.Y.Z throughout.

## Surfaces that name the version

| surface | file | edit |
|---|---|---|
| Python package | `src/volante/__init__.py` (`__version__`) | `"0.9.0"` → `"X.Y.Z"` |
| PyPI install pin (Smithery) | `smithery.yaml` | `volante[mcp]==0.9.0` → `==X.Y.Z` |
| PyPI install pin (registry) | `server.json` | `==0.9.0` → `==X.Y.Z` (two places) |
| PyPI install pin (manifest) | `mcpb/manifest.json` | `==0.9.0` → `==X.Y.Z` |
| Changelog | `CHANGELOG.md` | move `[Unreleased]` to `[X.Y.Z] - <date>`, add empty `[Unreleased]` |
| Tag | `vX.Y.Z` | push triggers `release.yml` |

## Sequence

1. All checks green: `ruff check`, `ruff format --check`, `mypy`, full `pytest`
   (currently **1420 passed, 5 deselected**), `git diff --check`.
2. Bump every surface above in ONE commit (`release: X.Y.Z — <summary>`), matching the
   repo's commit style (see `git log --oneline`).
3. Fresh isolated build + smoke install (the audit's residual risk #1):
   - `uvx --from build pyproject-build` (or `uv build`) into an empty output dir;
   - install the wheel into a fresh venv and run a **no-key** end-to-end
     (`python -m volante --help`, a never-called local demo), and the MCPB/plugin
     import check under `volante_mcp/`.
4. Push the tag. `release.yml` builds, produces PEP 740 attestations, and publishes to
   PyPI via OIDC **only after** a trusted publisher exists in the PyPI project settings
   (`repository_owner`, `repository`, workflow `release.yml`, environment `pypi`).
   Verify: `curl https://pypi.org/integrity/volante/X.Y.Z/<filename>/provenance`.
5. Validate the published artifacts: `volante-mcp` registry pin (`server.json`),
   Smithery pin, and the wheel install from PyPI (not the local dist) once surfaced.

## Held decisions before tagging

- **Version number** — proposal: **0.10.0** (new-feature release: depth-aware routing
  estimate, automatic feedback merge + recency, circuit breaker + reliability ledger).
- **PyPI trusted publisher** for `release.yml` — present? unknown; the publish step
  fails harmlessly (build + attestations succeed) until configured.
- **dist/** — the 0.9.0 artifacts predate the Unreleased work; the checklist above
  rebuilds fresh rather than reusing them.
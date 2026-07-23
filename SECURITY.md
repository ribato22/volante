# Security Policy

Baton is a study project. Its isolation mechanisms are real but **deliberately scoped** — read this
before running untrusted goals or exposing it to hostile input.

## Threat model & guarantees

| Surface | Guarantee | Not covered |
|---|---|---|
| Agentic `run_python`, default subprocess `Sandbox` | Protects against *accidents* (own buggy code): process-group kill, `RLIMIT_CPU`, scrubbed env (no `*_API_KEY`) | Not an adversary sandbox — on macOS host network/disk remain reachable |
| Agentic `run_python`, `BATON_SANDBOX=docker` | Real isolation: container, `--network none`, read-only root, `--cap-drop ALL`, `--pids-limit`, non-root user | Requires a trusted Docker daemon |
| `fetch_url` / `read_file` tools | Host-mediated: domain allowlist / root-confined path, no redirects, size cap | Prompt-injection containment holds **only** under the Docker sandbox |
| Eval scorer (`score_code`) | Forgery-resistant: process + filesystem separation + nonce-authenticated RPC — a solution cannot fake a passing score | Best-effort POSIX: a solution calling `setsid()` escapes the `killpg` group (wall-clock timeout still bounds it); not a sandbox for arbitrary hostile code |

**Operating assumptions**

- The agentic loop is intended for **self-written goals**, not adversarial input.
- **Never place secrets in model context.** Allowlists and the read-file root are the trust boundary.
- Run adversarial or untrusted goals only under `BATON_SANDBOX=docker` (the deprecated
  `AIORCH_SANDBOX` name is still read as a fallback).

## Supported versions

The `main` branch is the supported version. This is pre-1.0 software; APIs may change.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, open a private
[GitHub Security Advisory](https://github.com/ribato/baton/security/advisories/new) with a
description, a reproduction, and the impact. You will get an acknowledgement as soon as possible.

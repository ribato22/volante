# Security Policy

Volante is an alpha model-routing and orchestration control plane. Its isolation mechanisms are real
but **deliberately scoped** — read this before running untrusted goals or exposing it to hostile
input.

## Threat model & guarantees

| Surface | Guarantee | Not covered |
|---|---|---|
| Agentic `run_python`, **default** (`VOLANTE_SANDBOX` unset) | Fails closed: Docker is auto-selected when its daemon answers; when none does, `run_python` is **not offered at all** and the planner is told it cannot execute code. An unrecognized `VOLANTE_SANDBOX` value is rejected, never downgraded | Withholding a capability is not a sandbox: it prevents execution rather than containing it |
| Agentic `run_python`, `VOLANTE_SANDBOX=docker` | Real isolation: container, `--network none`, read-only root, `--cap-drop ALL`, `--pids-limit`, non-root user | Requires a trusted Docker daemon |
| Agentic `run_python`, `VOLANTE_SANDBOX=subprocess` (explicit opt-in) | Protects against *accidents* (own buggy code): process-group kill, `RLIMIT_CPU`, scrubbed env (no `*_API_KEY`). Warns on every start | Not an adversary sandbox — host network and disk remain reachable. You are choosing to run model-generated code with your own access |
| `fetch_url` / `read_file` tools | Host-mediated: domain allowlist / root-confined path, no redirects, size cap | Prompt-injection containment holds **only** under the Docker sandbox |
| Eval scorer (`score_code`) | Forgery-resistant: process + filesystem separation + nonce-authenticated RPC — a solution cannot fake a passing score | Best-effort POSIX: a solution calling `setsid()` escapes the `killpg` group (wall-clock timeout still bounds it); not a sandbox for arbitrary hostile code |

**Operating assumptions**

- The agentic loop is intended for **self-written goals**, not adversarial input.
- **Never place secrets in model context.** Allowlists and the read-file root are the trust boundary.
- Run adversarial or untrusted goals only under `VOLANTE_SANDBOX=docker` (the deprecated
  `AIORCH_SANDBOX` name is still read as a fallback).
- The default already fails closed: leave `VOLANTE_SANDBOX` unset unless you have decided to accept
  unisolated execution. `VOLANTE_SANDBOX=subprocess` is that decision — it re-enables code execution
  with your own filesystem and network access, and is not equivalent to the old default.

## Supported versions

The `main` branch is the supported version. This is pre-1.0 software; APIs may change.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, open a private
[GitHub Security Advisory](https://github.com/ribato22/volante/security/advisories/new) with a
description, a reproduction, and the impact. You will get an acknowledgement as soon as possible.

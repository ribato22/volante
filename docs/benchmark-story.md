# The one benchmark story (2026-08-14)

One concrete, reproducible account of what Volante (a transparent, user-owned LLM
router) actually does for you, built entirely from numbers the repo measured and
published in `eval/artifacts/README.md`. Nothing here is a claim in search of data —
every figure cites the run that produced it, and the story's third act is about the
numbers this project had to walk back after re-measurement.

Reading order: this file for the narrative, `eval/artifacts/README.md` for the raw
runs and methods.

## Scene 1 — a router's whole job fits in one question

> Which model answers this goal?

Every arm of every comparison answers the same request. The suite is a fixed set of
coding goals (`eval/tasks.py`), scored by an independent harness (not the model).
"Baseline" means one model, one call, no orchestration. The question the router
answers is which of the configured models gets that call.

## Scene 2 — the lever that matters is the MODEL, not the chains around it

The largest measured difference in this project is not planning, decomposition, or
synthesis — it is *which model answers*. On the same goal, same harness, moving from
`gpt-4o-mini` to `gpt-4o` moved the score **+0.462** — by a factor of three the
biggest single lever ever measured here.

Measured concretely on the goal built to favour orchestration over a single call:

| model | baseline | orchestration | orchestration cost |
|---|---|---|---|
| `openai/gpt-4o-mini` | 0.497 | 0.476 | 2.2x |
| `openai/gpt-4o` | **0.958** | 0.941 | 7.6x |

Orchestration does not beat a single call even where it was *built to win*. Routing to
the right model is worth more than any amount of wrapping around the wrong one.

## Scene 3 — the three-arm comparison: modest, but it is not what routing claims

Over the 9-goal suite, the coarse picture (baseline 0.993, orchestration 0.721,
agentic 0.618, baseline wins 9-0 at about a seventh of the cost) is a statement about
*this suite*, where a single call already scores 1.00 on 7 of 9 goals — there is
basically no headroom for anything else to win. The honest takeaway is not that
baseline "wins", it is that **routing's value is the headroom a goal actually has**, and
that is the model's ceiling, not the router's.

## Scene 4 — the hard-won honesty: five published numbers came DOWN

This project keeps the raw runs precisely so a number cannot outlive its evidence.
Re-measurement already corrected five published figures downward, including a
`p<0.005` difference that pooling showed to be between-batch drift (+0.289 looked,
+0.159 pooled, maybe nothing), three of the four *routing designs* that were built and
then measured out of existence, and an orchestration verdict that flipped between
0.3.0 and 0.4.1 after the losing arms' defects were fixed. What survives repeated
measurement is narrower and stated precisely:

- **The detector** (`--verify`): zero false negatives in 22 runs; it reliably flags
  nothing wrong as right. It is a reliably *detector* and a useless *actuator* (a
  repair pass fed its own failing checks moved a score 0.000).
- **The reliability work**: unparsable output dropped from 2/8 to 0/8, not as an arm
  comparison but as a property of the engine.
- **Circuit breaking**: a model in an outage stops being probed — 30 → 15 calls on a
  hard-failing model over 10 tasks, 300 → 51 over 100, 100 → 10 with no retries; the
  residue is the opening task and one probe per cooldown, by construction.
- **Depth-aware routing**: heuristic inert on 0/12 suite decisions, mean loss
  unchanged (0.1244), mechanism demonstrated on file-naming goals — a null result
  stated as one.
- **Cross-provider calibration**: model ranking by measured quality beats argued tiers;
  a second model family changed the picture, and a k=5 re-check of a k=3 outlier
  (0.704 → 0.795) was sampling variance, not contamination — the ranking did not move.

## Scene 5 — what is NOT yet measured (the honest gaps)

- **Latency, aggregated.** Run results carry `duration_ms`, but no artifact in this
  repo publishes a per-model/task latency table. The "latency" axis of the
  quality/cost/latency Pareto is real but unrecorded — the benchmark story cannot
  claim it until the calibration arms write it.
- **A wider cross-provider inventory.** The measured palette is three OpenAI-family
  models plus one GLM outlier, on one suite family. Reproducing
  `eval/calibrate_models.py --models ... --k 3` with a real inventory is the
  documented, spend-bearing path to the fullest Pareto.
- **Live entitlements.** "Configured vs entitled vs healthy vs rate-limited vs
  depleted" is designed as a concept (and the breaker distincts outages from
  depletion) but not yet probed live.

## Reproducing the story

```bash
# Capability map, per model per task type (REAL calls, REAL spend)
uv run python -m eval.calibrate_models --models gpt-4.1-nano,gpt-4o-mini,gpt-4.1 --k 3

# Three-arm comparison over the coding suite (REAL calls, REAL spend) — see
# eval/run.py --help for suites, k, synthesis mode; results land in eval/artifacts/
uv run python -m eval.run --suite repo --k 5

# Bread-and-butter response to the routing question — one call, verified, direct by
# default (no --direct flag; --orchestrate turns the chain back on)
uv run volante --verify "write slugify(phrase: str) with tests"
```

## The 60-second demo script this story maps to

1. **0–10 s** — "Every solver ships two dials: which model, and how much orchestration
   around it. Volante is a *user-owned* router — the choice is yours, the trace is
   yours, the ledger is yours."
2. **10–30 s** — "Measured, not marketed." Show `resolve` on two models: 0.497 vs
   0.958 on the same goal, same harness (Scene 2 table).
3. **30–45 s** — "Routing decides once and explains itself." Show one
   `--direct --verify` run, the routing trace, and the check output.
4. **45–60 s** — "The wallet is protected too." Show the breaker: a failing model
   skipped after two consecutive failures, cooldown probe, and the bill — one claim,
   spelled out in the run trace.

Search terms the script should read aloud or caption: **user-owned LLM router**,
**cross-provider model routing**, **AI model control plane**.
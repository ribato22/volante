# Benchmark artifacts

Raw output from `eval/`, kept so every number quoted in the README and CHANGELOG can
be checked against the run that produced it. Nothing here is read at runtime — the
files the engine and the docs actually reference (`measurements.json`,
`quality-profiles.json`) stay at the repository root.

## Three-arm comparisons

Each file is one `compare_arms` run: **baseline** (one model, one turn) against
**orchestration** (plan → workers → synthesis) and **agentic** (one model with a
tool loop), over the 9 coding goals in `eval/tasks.py`.

Read them in the order they were produced, not the order the names suggest — the
names are historical and `results-final.json` was **not** the last run:

| order | file | volante | k | verdict | note |
|---|---|---|---|---|---|
| 1 | `results.json` | 0.3.0 | 5 | baseline | first full run |
| 2 | `results-after-fix.json` | 0.3.0 | 5 | baseline | after the agentic-loop livelock fix |
| 3 | `results-k5.json` | 0.3.0 | 5 | baseline | orchestration takes 1 goal |
| 4 | `results-final.json` | 0.3.0 | 5 | baseline | orchestration takes 0 |
| 5 | `results-nudge2.json` | 0.3.0 | 5 | baseline | after the second stall nudge; orchestration takes 1 |
| 6 | `results-0.4.1.json` | 0.4.1 | 5 | baseline | re-run after 38 engine fixes; verdict unchanged, 9-0 |

The 0.4.1 re-run exists because every earlier comparison was produced by 0.3.0, and
several defects fixed since then penalised the arms that lost — codex discarding a
paid correct answer, a garbled CLI reply accepted as complete, a failed required tool
counted as satisfied. The verdict did not move: baseline 0.993, orchestration 0.721,
agentic 0.618, and baseline wins all nine goals at a seventh of the cost.

**Do not read the orchestration means as comparable between runs.** A targeted A/B on
the three goals that moved most found a within-condition standard deviation of 0.43 —
the same goal, the same code, temperature 0, scoring `[0.0, 0.3, 1.0]` on one arm and
`[1.0, 0.0, 0.0]` on the other. The orchestration arm is bimodal: it either completes
the goal or fails it outright. At k=5 nothing finer than about ±0.2 is measurable, so
the 0.919 → 0.721 movement between 0.3.0 and 0.4.1 is very probably noise. What the
suite does support is the coarse result: baseline is consistently near 1.0 with small
variance, and orchestration is not.

Every run reaches the same verdict: **baseline wins**. The README states the limits
that verdict carries — baseline scores 1.00 on 7 of 9 goals, so on most of this
suite there is no headroom to win at all.

## Calibration re-checks

`measurements-glm-recheck.json` — `glm-4.5-flash`, code arm only, k=5, measured
2026-07-30 with the `null` encoding introduced in 0.4.1.

It exists to answer one question: were `glm`'s eight zeros in `measurements.json`
the model failing, or our own client failing? **The model.** 44 of 45 runs were
graded and 1 was unmeasured, so `reliability_score` is 0.978 — the first time that
field has been measurable at all, because before 0.4.1 the harness could not emit
the `null` its own consumer documents.

The score moves 0.704 → 0.795, which is sampling variance between k=3 and k=5 rather
than decontamination, and the ranking does not change: `glm` is still last at code
by a wide margin, and `debug_gauntlet` fails 0/5 — a real, reproducible capability
limit, not an artefact.

## depth suite (2026-07-31): where orchestration loses, and why

`results-depth-0.4.1.json` (`--synthesis assemble`) and `results-depth-summarize.json`
(`--synthesis summarize`), both `--suite depth --k 3`, planner `openai/gpt-4o-mini`.

On the `resolve` goal, scoring the CODE only (the composite mixes in has_tests and
has_readme, which every arm earns and which hide the result):

| | code score |
|---|---|
| manual decomposition, n=28 | **0.790** |
| baseline | 0.396 – 0.562 |
| a constant answer, reads nothing | 0.250 |
| Volante orchestration, `summarize` | 0.292 |
| Volante orchestration, `assemble` | 0.132 |

A hand-written three-call decomposition of this goal beats baseline by +0.225
(paired t=4.73, p<0.001, 22/28). Volante's own orchestration scores at or below a
constant answer. The gap is the ENGINE, not the goal.

The cause is one `Supervisor.plan()` call away, and it is not subtle:

```
[1] one_shot code   Implement the resolve function in solution.py
[2] one_shot write  Write pytest test cases ...           deps=[1]
[3] one_shot write  Create a README ...                   deps=[1]
```

Task 1 carries the entire difficulty in a single worker call, so it is baseline with
extra steps; tasks 2 and 3 produce artifacts the code score does not measure. The
decomposition that wins splits the *problem* (pattern matcher / precedence resolver /
assemble), which is what makes the matcher correct — 19 of 28 baseline runs get the
goal's own worked example wrong, and none of them factor the matcher out.

CORRECTION, from checking three more goals instead of generalising from one. This was
first written as "the planner decomposes by deliverable, not by sub-problem". That is
wrong. Given `toolbelt` (five named functions) it emits five code tasks plus tests plus
README; given `guardkit` (twenty-four named functions) it emits twenty-four. It
decomposes by sub-problem perfectly well.

What it decomposes by is the goal's own ENUMERATION. `resolve` names ONE function, so
it plans one code task. The separable structure in this goal — pattern matching versus
precedence resolution — is INSIDE that single named deliverable, and nothing in the
planning prompt asks the model to look there. The prompt spends its words on the JSON
schema and the one_shot/agentic choice and says nothing about how to split work.

So the defect is narrower than first stated, and correspondingly the fix is narrower:
the planner never decomposes a single hard deliverable by its internal structure. It is
not choosing deliverables over sub-problems; it is only ever mirroring the list it was
given.

`assemble` is additionally wrong here and its own docstring says why: it does not
reconcile the parts, so concatenating solution + tests + README yields a module that
fails more cases than a one-line stub.

So the honest reading of every "baseline wins" result this project has published is
narrower than it looked. It is not evidence that decomposition does not help. It is
evidence that THIS PLANNER, on goals shaped like a library deliverable, produces a
plan with no isolation benefit to collect.

### The gap is the plan, and only the plan

Before changing anything, the ceiling was measured: feed Volante's OWN synthesis the
ideal decomposition (a matcher artifact and a precedence artifact, produced by the same
model at temperature 0) and score what comes out, n=6.

| | code score |
|---|---|
| ideal plan + `Synthesizer` (a model call) | **0.819** |
| ideal plan + `ArtifactAssembler` (concatenation) | **0.819** |
| manual three-call decomposition, n=28 | 0.790 |
| Volante's real orchestration | 0.292 |

Both strategies produce genuinely different text — 4,996 characters of rewritten module
versus 2,718 characters of concatenation — and score identically, because both preserve
the semantics of the two parts they were given. That is the point: synthesis is not
where anything is lost.

So the whole 0.292 -> 0.819 gap sits in the plan. Nothing downstream needs changing,
and any fix that does not change what `Supervisor.plan()` emits cannot help.

### Attacking the planner gap: what does not work (2026-07-31)

The gap above is entirely in `Supervisor.plan()`. Seven candidate revisions of
`_PLAN_SYSTEM` were written and screened on real plans for six goals — inserted before
the "no prose" sentence so the JSON contract stays closest to the output. They ranged
from abstract principle ("a deliverable named once is not automatically one task") to
difficulty-gated ("before emitting a task you would label hard, check whether it
splits") to concrete shape ("when a function needs a non-trivial helper, plan the
helper as its own task").

**All seven failed.** Not one produced a `resolve` plan with a matching task separate
from a precedence task, and three inflated the trivial controls (`calc` 1→2 code
tasks, `roman` 2→3). Every plan reproduced the three deliverables the goal names in
its closing sentence.

The mechanism, from a direct probe: asked *"list the separable sub-problems — the parts
that could be implemented independently"*, the same model at temperature 0 answers
"Pattern Matching / Rule Filtering / Precedence Handling / Output Formatting" — exactly
the decomposition that wins. **The capability is there; the single planning turn
suppresses it**, because the goal's closing deliverable list dominates the same turn
that would have to do the analysis.

Two-stage planning (analyse, then plan against goal + analysis) does produce the right
split for `resolve`. Two things then went wrong:

- **The analysis stage cannot abstain.** Told to answer `NONE` when the deliverable is
  one straightforward function, it returned four sub-problems for `slugify` (a 5-line
  function) as readily as for `resolve`. It never abstains, on any goal.
- **End to end, n=4, it is not conclusive.** `resolve` moved 0.344 → 0.453 but both
  arms contain 0.000 runs, against a spread that wide. Two-stage produced the two
  highest scores seen (0.958, 0.854) and two zeros.

One assumption of ours was refuted and is worth recording: over-decomposition did NOT
hurt the score. `slugify` scored 1.000 under both one-stage and two-stage planning
despite being split into four code tasks. The cost of the extra calls is real; the
quality penalty we assumed is not there.

What would settle it: higher n, and a diagnosis of the 0.000 runs (a successful run
whose final answer scores nothing suggests the assembled module sometimes lacks a
usable `resolve`, which would be a pipeline defect independent of planning).

Nothing was shipped from this. Recorded so the seven dead candidates are not written
an eighth time.

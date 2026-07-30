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

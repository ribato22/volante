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

### The grading bug was the story (2026-07-31)

Diagnosing the 0.000 runs found a defect in the SCORER, not in Volante. `extract_python`
took the first ```python block and closed it at the first ``` — which, when the model
embeds the requested README inside a triple-quoted string containing a ```bash example,
lands mid-string-literal. The extracted code ends in an unterminated string, fails to
import, and all 48 cases fail. Six saved orchestration outputs, re-scored:

    run 0   0.000 -> 0.396      run 3   1.000 -> 1.000
    run 1   0.000 -> 0.000      run 4   0.000 -> 0.000
    run 2   0.417 -> 0.417      run 5   0.000 -> 0.875
                                mean    0.236 -> 0.448

It is not neutral between the arms. It punishes whoever emits ONE self-contained block
carrying code plus docs — the shape a synthesis pass produces — so it hit orchestration
far harder than baseline and read as a capability difference.

Re-measured on `resolve` with the fixed scorer, same model, temperature 0, n=8:

| arm | mean | stdev | min | max |
|---|---|---|---|---|
| baseline | 0.414 | 0.007 | 0.396 | 0.417 |
| orchestration, current planner | **0.620** | 0.322 | 0.000 | 0.958 |
| orchestration, two-stage planner | 0.604 | 0.340 | 0.000 | 0.917 |

**The direction reverses.** Orchestration was recorded at 0.292 against a baseline of
0.396–0.562; corrected, it is 0.620 against 0.414 — ahead on 6 of 8 runs.

Two honest limits on that. The difference is +0.205 with t=1.81 (df≈7), so it is NOT
significant at n=8: baseline is astonishingly consistent (stdev 0.007) while
orchestration swings from 0.000 to 0.958, and that spread is what costs the p-value.
Settling it needs more n, and reducing the spread is worth more than raising the mean.

And the planner change this investigation set out to build is **unnecessary**: two-stage
planning scores 0.604 against 0.620, a difference of -0.016 (t=-0.09). The seven failed
prompt candidates and the two-stage design were all chasing a gap that the scorer had
manufactured.

Every orchestration number published before commit cd94390 is a LOWER BOUND. The new
extraction returns the first candidate that parses, and falls back to the old behaviour
when none does, so by construction new >= old.

### Reliability, and the first significant win (2026-07-31)

The corrected scorer left orchestration ahead but not significantly: the mean was fine,
the SPREAD was the problem. Two runs in eight emitted a module that does not parse —
for someone who just wants working output, the worst possible failure, because the
answer looks complete and is a Python file with prose in it.

Root cause, from the two failing outputs: asked for a module plus tests plus a README,
the model put all three inside ONE python fence — once as raw markdown after a
`# README.md` comment, once inside a `"""` it never closed because the README carried
its own fence.

Two changes, each measured on `resolve`, n=8, same model, temperature 0:

| | mean | stdev | broken | vs baseline |
|---|---|---|---|---|
| baseline | 0.414 | 0.007 | — | — |
| orchestration, as it was | 0.620 | 0.322 | 2/8 | t=1.81, not significant |
| + file-layout rule | 0.654 | 0.315 | 1/8 | t=2.15, not significant |
| + verify-and-retry | **0.742** | **0.212** | **0/8** | **t=4.38, p<0.005** |

The layout rule helps and cannot guarantee — an instruction is probabilistic. What
closes it is verifying the model's own claim: a block it TAGGED ```python must actually
parse, checked with `ast`, one retry if it does not, and the first answer kept if the
retry is no better. Same shape of guarantee as `ensure_complete_response` — verify what
the provider asserted about its output, do not judge the content.

This is the first time orchestration beats a single strong model on this project at
p<0.005. It was not won by making the answers better; it was won by removing the
failures. Mean rose 0.620 -> 0.742 while stdev fell 0.322 -> 0.212, and it is the
second number that bought the significance.

### Execution verification: safe, not yet economical (2026-07-31)

Every gate that asked the MODEL to judge quality failed — difficulty labels, abstention,
self-check. Execution does not ask. Derive `assert` statements from the goal, run them
in the sandbox Volante already ships, and read the exit code.

First probe, `resolve` and `slugify`, 9 runs: perfect separation. Every `resolve` answer
(scores 0.396–0.458) failed its checks; every `slugify` answer (1.000) passed. On the
same 0.417 answers, the model's own self-check had replied `OK`.

Then the number that decides whether it can be a default — a check that wrongly PASSES a
wrong answer stops the engine and hands over something broken. Across all 11 code goals,
2 runs each:

| | count |
|---|---|
| passed and correct | 8 |
| failed and wrong | 4 |
| false positive (expensive, safe) | 10/22 |
| **FALSE NEGATIVE (dangerous)** | **0/22** |

Mean score when the check passed: **1.000**. The safety property holds — it never
approved a wrong answer.

The economics do not, yet. A 45% false-positive rate would escalate nearly half of
already-perfect answers, which costs more than always orchestrating. Counting individual
assertions shows why: the derived checks are mostly right and contain a minority of
wrong ones (0-29% fail). And the failing FRACTION does not separate the cases —

    resolve   score 0.417 (wrong)    5/20 failed   25%
    calc      score 1.000 (right)    2/12 failed   17%
    csv_stats score 1.000 (right)    1/8  failed   12%
    guardkit  score 0.978 (wrong)    2/26 failed    8%

25% versus 17% is not a threshold, it is an overlap. So "escalate when checks fail" is
the wrong shape.

What the evidence points at instead is the shape 0.5.0 already proved: the `ast` check
did not GATE anything, it fed the error back and repaired once, taking unparsable output
from 2 runs in 8 to 0 in 8. Applied here that means always run the cheap path, always
run the derived checks, and hand any failures to a single repair pass — which tolerates
wrong assertions, because a model shown a bad assertion can dismiss it. Cost is then flat
(~3 calls) rather than a bet on a classifier that does not exist.

Unmeasured, and the reason nothing is built yet: whether a repair pass shown its own
failing assertions actually improves the score, and whether it ever makes it worse.

### The repair pass does not work either (2026-08-04)

Following the evidence from the gate experiment, the proposed shape was: always take the
cheap path, run the derived checks, and hand any failures to one repair pass — tolerant
of wrong assertions, because a model shown a bad check can dismiss it.

Measured across all 11 code goals, 2 runs each, 16 repairs actually triggered:

| policy | mean delta | improved | regressed |
|---|---|---|---|
| A — always take the repair | **-0.126** | **0** | 3 (worst -1.000) |
| B — take it only when fewer assertions fail | +0.000 | 0 | 0 |

**Zero improvements in sixteen repairs.** Policy A is actively harmful: `textkit` went
1.000 -> 0.000 twice, a working module rewritten into a broken one on the strength of a
single wrong assertion. Policy B is safe and is exactly a no-op — the repair never
reduced the failing-assertion count (1->1, 3->3, 20->20, 19->19), so B never takes it and
the extra call buys nothing.

The `resolve` row is the one that matters most. With 19-20 of its assertions failing and
an answer genuinely scoring 0.417, the repair pass still changed nothing. So this is not
merely "the evidence was bad". Shown exactly what fails, the model cannot fix it.

That is consistent with everything else measured about this goal: baseline scores below
0.50 on 15 of 16 runs, its own self-check replies `OK`, and now a repair pass with
concrete failing checks moves it 0.000. **Decomposition remains the only intervention
that has ever improved it** (+0.289, p<0.005).

Four designs are now dead: difficulty-gated planning, planner abstention, model
self-check, and repair-with-evidence. What survives is narrower and worth stating
precisely — execution verification is a reliable DETECTOR and a useless ACTUATOR. It
never approved a wrong answer in 22 runs, and it cannot drive a fix.

### The headroom was model weakness (2026-08-04)

The decisive test, and it goes against this project. `resolve` was built because
baseline failed it and orchestration did not. Run the same goal on a stronger model in
the same family, one batch, n=6 per arm:

| model | baseline | orchestration | orchestration cost |
|---|---|---|---|
| `openai/gpt-4o-mini` | 0.497 | 0.476 | 2.2x |
| `openai/gpt-4o` | **0.958** (stdev 0.000) | 0.941 | 7.6x |

gpt-4o solves it alone and deterministically. Orchestration makes it slightly worse for
7.6x the money. What decomposition was closing is a gap a better model does not have.

And the same batch reversed the headline result on mini: orchestration 0.476 against
baseline 0.497. Pooling every measurement taken after the scorer and reliability fixes:

    orchestration  n=25  0.653   batches: 0.742 · 0.721 · 0.656 · 0.476
    baseline       n=25  0.494   batches: 0.414 · 0.492 · 0.708 · 0.497

The pooled gap is +0.159, not the +0.289 published at p<0.005 from a single batch of 8.
Between-batch drift is larger than the effect. **There is currently no reliable evidence
that orchestration beats a single call on this suite**, and the README has been corrected
to say so.

Two things this does NOT invalidate, because they were never comparisons between arms:
the reliability work (unparsable output 2 in 8 -> 0 in 8) and the detector (`--verify`,
0 false negatives in 22). Those are properties of the engine.

The methodological lesson is the expensive one. Four batches of n=6-8 looked like a
result and were not. On a metric whose between-batch drift is this large, a single batch
cannot support a p-value, and quoting one was an error of ours — the third time in this
project that a published number had to be corrected downward after re-measurement.

### Work larger than one response: the last structural argument also fails (2026-08-04)

Every goal before this fits in a single reply, so decomposition never had a STRUCTURAL
reason to win — only a quality reason, and that one is dead. This is the regime where a
single call cannot compete by construction. Measured on gpt-4o-mini (max_output_tokens
8192): 20 functions -> all 20 in 2,379 tokens; 45 -> all 45 in 5,198; **90 -> eleven
functions in 1,402 tokens and a clean end_turn.** It does not truncate, it gives up.

Instrument validated first: a correct factory implementation scores 1.000, the
eleven-function partial scores 0.133, an empty module 0.000. Compression is deliberately
allowed — the family is regular, so a model that spots the rule and writes a factory
wins in one response, and that would be the better answer.

**Volante failed this goal before producing anything.** The planner enumerated one task
per function, exhausted its own output budget mid-array, and the run died:

    failed_task  __planning__
    error_code   incomplete_output   provider stopped with 'max_tokens'

`_MAX_PLAN_TASKS = 32` existed the whole time and did not help: it VALIDATES the array
after the model writes it, and the model never got that far. Worse, `ensure_complete_
response` was called OUTSIDE the plan retry loop, so the one failure a corrective
follow-up is most likely to fix was the only kind that skipped recovery entirely. Both
are now fixed — the cap is stated in the prompt, and a length failure is retried with an
instruction that names the actual problem.

With the run no longer dying, n=4 paired:

| arm | mean | vs baseline |
|---|---|---|
| baseline | 0.300 | — |
| orchestration, `summarize` | 0.208 | -0.092 [CI -0.231, +0.048] NOT significant |
| orchestration, `assemble` | 0.050 | -0.250 [CI -0.303, -0.197] significant |

So in the one regime built to favour it, orchestration ties at best and loses at worst.
Note also that BOTH arms fail the goal badly — 0.30 against a reachable 1.000. Volume
defeats them both; it does not separate them.

That closes the last structural argument this project had. What survives is unchanged
and does not depend on any comparison between arms: the reliability work, the detector,
the router, and an eval that now reports what it could and could not have detected.

### Verify-then-escalate: the detector is not precise enough to route on (2026-08-04)

The two halves were separately measured and had never been connected: the detector
never approved a wrong answer (0 false negatives in 22), and a stronger model in the
same family is worth +0.462 on a goal the weak one fails. Policy: answer cheap, run the
derived checks, escalate to the strong model only when they fail.

Three goals (two a cheap model aces, one it fails), n=3 each, paired in one session:

| policy | score | cost | vs always-cheap |
|---|---|---|---|
| always cheap | 0.806 | $0.0184 | 1.0x |
| always strong | 0.923 | $0.0951 | 5.2x |
| **escalate** | **0.923** | **$0.0907** | **4.9x** |

Escalation reaches always-strong quality at 94% of always-strong cost. That is not a
policy, it is always-strong with extra steps. The cause is the false-positive rate
already measured at 45%: **escalation fired on 6 of 9 runs**, including every `roman`
run, where the cheap model had already scored 1.000.

One row refutes the premise underneath the whole idea:

    roman   cheap 1.000   strong 0.429   escalate 0.429

The stronger model is not uniformly better, and escalation follows it down. And the
comparison is underpowered anyway: +0.117 [CI -0.166, +0.400], where this design could
only have detected ±0.392 — most of the variance is BETWEEN GOALS, not between
policies, which is a flaw in the measurement as much as in the policy.

So the detector cannot be routed on at 45% false positives. It remains what it was
measured to be and nothing more: a report the user reads, where a failing check is often
the check being wrong and the text is legible enough to tell. Making escalation pay needs
the false-positive rate down around 10%, and THAT is the next number to move — not
another policy on top of a detector this noisy.

### The detector gets precise, by learning to say "not enough evidence" (2026-08-04)

The 45% false-positive rate had killed five policies in a row, so the failures were read
one by one instead of guessed at. Almost none was the CODE being wrong:

    column_widths([["one","two","three"],["four","five","six"]]) == [4, 5, 5]   # is [4,4,5]
    wrap_text("a" * 10, 5) == ["a","a","a","a","a","a","a","a","a","a"]         # is ["aaaaa","aaaaa"]
    from_roman(to_roman(n)) == n                                               # NameError: 'n'

The model was working out its own expected values and getting them wrong. So it is now
told not to calculate at all: copy every expected value from the goal, and write nothing
where the goal states nothing.

That alone traded one failure for a worse one:

| derivation | false positives | FALSE NEGATIVES |
|---|---|---|
| free-form | 4/7 = 57% | 0/2 |
| grounded | 0/7 = 0% | **1/2** |

Grounding cut `resolve` from 21 checks to 1. It passed that one and scored 0.875 — a
false negative, the single outcome that must never happen, and precisely what made the
detector worth having.

The fix is a third state rather than a better threshold. Below three grounded checks the
goal did not state enough to judge, and the honest report is "not enough evidence", not
"fine". Failures still count at any count: thin evidence cannot confirm, but it can
refute. On the same data that gives 0/7 false positives AND 0/2 false negatives, with
two goals honestly marked inconclusive.

Confirmed through the real Runtime, though on a small sample — 3 of 7 runs failed for
unrelated provider errors, leaving four:

    csv_stats  1.000   1 check   -> not enough evidence
    toolbelt   1.000   6 checks  -> passed
    slugify    1.000   5 checks  -> passed
    resolve    0.938   1 check   -> not enough evidence

For a caller who does not write tests, "your goal states no expected result, so this
could not be checked" is more useful than either a false alarm or a false assurance —
and it names the one thing they can do about it.

### Forbid the calculation, not the derivation (2026-08-04)

Grounding assertions in the goal's literal text fixed the false-positive rate and cost
12% of goals their verdict entirely — `csv_stats` kept one check, `resolve` kept one,
and one check cannot confirm anything. The rule was too blunt: it forbade DERIVING an
expected value, when the failures only ever came from COMPUTING one.

The distinction is visible in the failures themselves. The model transcribes correctly
and calculates wrongly:

    slug("Hello World") == "hello-world"          direct substitution — right
    column_widths(...) == [4, 5, 5]               had to count — wrong, is [4,4,5]
    wrap_text("a" * 10, 5) == ["a"] * 10          had to wrap — wrong

So the rule now permits an assertion the goal's stated rules decide by direct
substitution, and forbids any that needs arithmetic, counting, sorting, merging, or a
multi-step transformation.

Measured across 8 goals — and the false-negative test was rebuilt, because it had been
resting on two goals total. Every goal now also runs against a DELIBERATELY BROKEN copy
of its own answer:

| derivation | false positives | inconclusive | FALSE NEGATIVES |
|---|---|---|---|
| literal-only | 0/8 | 1/8 = 12% | 0/8 |
| **direct-substitution** | **0/8** | **0/8** | **0/8** |

The loosened rule dominates: as safe, with nothing left unverifiable.

Stated limit: the injected defect is coarse — the first function returns a constant — so
FN=0 is measured against an easy target. It is a floor on the property, not a proof that
subtle errors are caught. The one subtle case that has been observed (`resolve` at 0.875)
was reported inconclusive rather than passed, which is the correct behaviour but is a
single data point.

An earlier version of this measurement reported FN 0/7 while the mutation silently never
applied — the regex missed return annotations, so every "broken" answer was the original.
The number was meaningless and looked fine. It is recorded here because a measurement
that no-ops is worse than one that fails.

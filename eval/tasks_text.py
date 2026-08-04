"""Text goals for the router's non-code task types.

The coding suite grades by running the model's program against hidden cases. These
goals have no program to run, so they are graded by a stack of independent
mechanical checks — still a pass fraction, still deterministic, and with no LLM
judge, which would cost money per grade and drift between runs.

Each goal here was drafted, then handed to an adversary whose only job was to score
well WITHOUT doing the work, then rewritten against that attack. That step was not
ceremony: the first draft of the `write` rubric scored the lazy output **100%**,
because its RULES block was the answer key — it listed the service names, the
figures and the banned words, so a model could satisfy every check by copying the
prompt. The analyze rubric leaked 64% and the research rubric 83% the same way.

The ground truth for the analyze goal was recomputed from its own inline corpus
before these checks were written, rather than trusted from the design notes.
"""

from __future__ import annotations

import re

from eval.tasks import EvalTask, TextCheck

# --- analyze ------------------------------------------------------------------
# A delivery log with four planted traps: a duplicated id, a blank field, an
# impossible value, and status filtering. Each trap moves a different answer, so a
# model that skips the cleaning rules fails a different subset than one that
# over-applies them — which is what makes the score informative rather than binary.

ANALYZE_KEYS = (
    "MEDIAN_MINUTES", "MEAN_MINUTES", "VAN_DELIVERED", "DRONE_PARCELS",
    "DELIVERED_PARCELS", "CANCEL_RATE", "TOP_DEPOT", "LOW_DEPOT",
    "SLOWER_DEPOT", "SLOWEST_ID",
)

_KEY_LINE = re.compile(r"^\s*(?:[-*+>\u2022]\s*)?([A-Z][A-Z_]{2,})\s*:\s*(.*?)\s*$")
_FENCE = re.compile(r"^```[A-Za-z]{0,12}$")


def _parse(output: str) -> tuple[list[str], dict[str, str], int]:
    """Return (key order, first value per key, junk line count).

    FIRST occurrence wins: a model that prints a draft block and then a corrected
    one must not be rewarded for the second attempt, and the junk counter charges
    it for the extra lines either way.
    """
    order: list[str] = []
    values: dict[str, str] = {}
    junk = 0
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or _FENCE.match(stripped):
            continue
        clean = re.sub(r"[*`#]", "", line).strip()
        if not clean:
            continue
        match = _KEY_LINE.match(clean)
        if match and match.group(1) in ANALYZE_KEYS:
            order.append(match.group(1))
            values.setdefault(match.group(1), match.group(2))
        else:
            junk += 1
    return order, values, junk


def _num(raw: str) -> float | None:
    """A value is one bare number or nothing. Hedges do not parse, by design."""
    n = raw.replace(",", "").rstrip("%").strip("()[]").strip()
    return float(n) if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", n) else None


def _near(key: str, low: float, high: float):
    def check(output: str) -> bool:
        value = _parse(output)[1].get(key)
        if value is None:
            return False
        n = _num(value)
        return n is not None and low <= n <= high

    return check


def _pair(key: str, word: str, low: float, high: float):
    """Two space-separated tokens: a label and a number. Both must be right."""

    def check(output: str) -> bool:
        value = _parse(output)[1].get(key)
        if value is None:
            return False
        parts = value.split()
        if len(parts) != 2 or parts[0].strip(".,").upper() != word:
            return False
        n = _num(parts[1])
        return n is not None and low <= n <= high

    return check


ANALYZE_CHECKS = (
    TextCheck("keys_exact", lambda o: _parse(o)[0] == list(ANALYZE_KEYS)),
    # Negative, and this is the one that stops shotgunning: every hedge,
    # restatement, unit or shown-working line is charged here, so covering all the
    # options costs a check instead of buying one.
    # Also unsatisfied when nothing parsed at all: saying nothing would otherwise
    # collect this check for free, which is the same free lunch as shotgunning.
    TextCheck(
        "no_extra_lines",
        lambda o: _parse(o)[2] > 0 or not _parse(o)[0],
        negative=True,
    ),
    TextCheck("median_minutes", _near("MEDIAN_MINUTES", 74.99, 75.01)),
    # 82.3529 exact, 82.3 truncated and 82.4 rounded all pass; 86.1 (no dedup) and
    # 327.8 (kept the impossible value) do not.
    TextCheck("mean_minutes", _near("MEAN_MINUTES", 82.29, 82.41)),
    TextCheck("van_delivered", _near("VAN_DELIVERED", 7, 7)),
    TextCheck("drone_parcels", _near("DRONE_PARCELS", 101, 101)),
    TextCheck("delivered_parcels", _near("DELIVERED_PARCELS", 1193, 1193)),
    TextCheck("cancel_rate", _near("CANCEL_RATE", 8.69, 8.71)),
    TextCheck("top_depot", _pair("TOP_DEPOT", "EAST", 436, 436)),
    TextCheck("low_depot", _pair("LOW_DEPOT", "WEST", 353, 353)),
    # The planted impossible value inflates EAST's median enough to flip this
    # comparison, so a model that keeps it answers EAST 85.0 and fails.
    TextCheck("slower_depot", _pair("SLOWER_DEPOT", "NORTH", 82.49, 82.51)),
    TextCheck("slowest_id", _pair("SLOWEST_ID", "R21", 160, 160)),
)

ANALYZE_GOAL = '''\
Analyse this delivery log. Use only the data below.

DATA (id,depot,mode,status,parcels,minutes):
R01,NORTH,VAN,DELIVERED,120,95
R02,EAST,BIKE,DELIVERED,40,55
R03,WEST,DRONE,DELIVERED,25,30
R04,NORTH,VAN,CANCELLED,300,0
R05,EAST,VAN,DELIVERED,110,140
R06,WEST,BIKE,DELIVERED,35,70
R07,NORTH,DRONE,DELIVERED,20,
R08,EAST,BIKE,DELAYED,50,180
R09,WEST,VAN,DELIVERED,130,150
R10,NORTH,BIKE,DELIVERED,45,60
R11,EAST,DRONE,DELIVERED,18,25
R12,WEST,VAN,CANCELLED,260,0
R13,NORTH,VAN,DELIVERED,105,115
R14,EAST,VAN,DELIVERED,95,4500
R15,WEST,BIKE,DELIVERED,38,65
R16,NORTH,DRONE,DELIVERED,22,35
R17,EAST,BIKE,DELIVERED,42,85
R18,WEST,DRONE,DELAYED,15,45
R19,NORTH,BIKE,DELIVERED,48,90
R20,EAST,VAN,DELIVERED,115,130
R21,WEST,VAN,DELIVERED,125,160
R22,NORTH,BIKE,DELIVERED,44,75
R09,WEST,VAN,DELIVERED,130,150
R23,EAST,DRONE,DELIVERED,16,20

CLEANING RULES (apply to every calculation):
1. One id appears twice with identical values. Count that record ONCE, everywhere, including denominators.
2. An empty field is missing: drop that record from statistics over THAT field only; it still counts for every other field and as a record.
3. One record has impossible minutes (over 1000) - a data-entry error: drop it from minutes statistics ONLY; its parcels still count and it is still a record.
4. "Delivered" means status DELIVERED. CANCELLED and DELAYED are excluded unless a question says otherwise.

QUESTIONS (over delivered records unless stated):
MEDIAN_MINUTES: median minutes, 1 decimal place.
MEAN_MINUTES: mean minutes, 1 decimal place.
VAN_DELIVERED: number of records with mode VAN.
DRONE_PARCELS: total parcels of records with mode DRONE.
DELIVERED_PARCELS: total parcels of all delivered records.
CANCEL_RATE: CANCELLED records as a percentage of ALL records of every status, 2 decimal places.
TOP_DEPOT: depot with the largest total parcels, then that total.
LOW_DEPOT: depot with the smallest total parcels, then that total.
SLOWER_DEPOT: whichever of NORTH and EAST has the higher median minutes, then that median, 1 decimal place.
SLOWEST_ID: id of the record with the highest minutes, then those minutes.

OUTPUT: exactly 10 lines, one per key, in the order listed, each as `KEY: value` - nothing else. No prose, headings, fences, units, working or extra lines. Numbers are bare (no % or commas). A two-part value is two space-separated tokens (format examples only: "SOUTH 999", "R99 999"). Any alternative, range, hedge or annotation inside a value scores zero for that line.'''

TEXT_SUITE = [
    EvalTask(
        id="delivery_log",
        goal=ANALYZE_GOAL,
        task_type="analyze",
        checks=ANALYZE_CHECKS,
    ),
]


# --- write --------------------------------------------------------------------
# The attack this goal is built against is the one the analyze rubric lost to first:
# a RULES block that lists what the checks look for IS the answer key, and a model
# scores full marks by echoing the prompt. So nothing the checks reward appears in
# the prompt. The duration must be computed from two timestamps, the audience-facing
# wording must be chosen, and the internal identifiers that ARE in the prompt are
# exactly what the negative checks punish for appearing.

WRITE_GOAL = '''\
You are writing the customer-facing status update for an incident that has now ended.

Internal incident record (NOT for publication):

    ticket        INC-4471
    services      auth-edge-7, session-store-b, token-mint
    began         2026-03-14 08:52 UTC
    mitigated     2026-03-14 09:31 UTC
    root cause    a stale certificate in the token-mint pod pool
    impact        customers could not sign in; already-open sessions kept working
    fix           certificate rotated, pool restarted
    follow-up     automated expiry alerting, owner: platform team

Write the public status update. It must:

- open by naming what customers experienced, not what broke internally
- state how long the disruption lasted, in minutes
- say clearly whether customers who were already signed in were affected
- state what is being done so it does not recur
- be at most 120 words

Publish nothing from the internal record that a customer has no use for: no ticket
reference, no service names, no pod or infrastructure detail.
'''


def _words(out: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", out))


def _has_any(out: str, needles: tuple[str, ...]) -> bool:
    low = out.lower()
    return any(n in low for n in needles)


WRITE_CHECKS = (
    # 08:52 -> 09:31 is 39 minutes. It appears nowhere in the prompt: the model has
    # to subtract, which copying cannot do.
    TextCheck("duration_39", lambda o: bool(re.search(r"\b39\b", o))),
    TextCheck(
        "no_wrong_duration",
        lambda o: bool(re.search(r"\b(?:31|52|41|40)\s*minute", o, re.I)),
        negative=True,
    ),
    # Existing sessions kept working — stating this is the one fact that stops the
    # notice reading worse than the incident was.
    TextCheck(
        "existing_sessions_ok",
        lambda o: _has_any(o, ("already signed in", "already logged in",
                               "existing session", "remained signed in",
                               "stayed signed in", "were not affected")),
    ),
    TextCheck("sign_in_named", lambda o: _has_any(o, ("sign in", "sign-in", "log in",
                                                      "login", "signing in"))),
    TextCheck("prevention_named", lambda o: _has_any(o, ("alert", "monitor",
                                                         "expiry", "expiration",
                                                         "renew", "rotat"))),
    TextCheck("within_120_words", lambda o: 0 < _words(o) <= 120),
    # Added after the attack: an EMPTY answer collected every negative check for free
    # — nothing forbidden is present when nothing is present — and scored the same as
    # copying the prompt. Positives that require real content are what put a floor
    # under that, and each of these is something the update genuinely needs.
    TextCheck("window_stated", lambda o: bool(re.search(r"08[:.]52|09[:.]31", o))),
    TextCheck("resolved_stated", lambda o: _has_any(o, ("resolved", "restored",
                                                        "fixed", "back to normal",
                                                        "has ended", "mitigated"))),
    TextCheck("apology_or_thanks", lambda o: _has_any(o, ("sorry", "apolog",
                                                          "thank", "regret"))),
    # The negatives are the whole defence. Every one of these strings IS in the
    # prompt, so a model that pads with the internal record scores worse, not better.
    TextCheck("no_ticket", lambda o: "inc-4471" in o.lower(), negative=True),
    TextCheck(
        "no_service_names",
        lambda o: _has_any(o, ("auth-edge", "session-store", "token-mint")),
        negative=True,
    ),
    TextCheck("no_infra_detail", lambda o: _has_any(o, ("pod", "certificate",
                                                        "cert ")), negative=True),
)


# --- research -----------------------------------------------------------------
# Recall and synthesis, with no network. The three answers are stdlib module names
# that the prompt never says — the needs are described in prose instead, so the
# scoring content cannot be copied. The negative check is the anti-shotgun one:
# naming many candidates per need would otherwise satisfy a positive check by
# accident, which is exactly how the first research rubric leaked 83%.

RESEARCH_GOAL = '''\
A colleague is writing a small command-line tool in Python and wants to use only the
standard library. Recommend exactly one standard-library module for each of the three
needs below, and justify each in one sentence.

1. They need to read a settings file that is written as sections with `key = value`
   lines underneath, the format Python's own tooling has used for decades.
2. They need to compare two versions of a text file and print the differences in the
   usual patch-like form, without shelling out to an external program.
3. They need to run a function repeatedly to measure how long it takes, with the
   timing loop and the repetition handled for them rather than written by hand.

For each need give the module name, then the sentence. Do not list alternatives —
one module per need, the one you would actually use.
'''

_R1 = ("configparser",)
_R2 = ("difflib",)
_R3 = ("timeit",)
# Plausible neighbours a shotgun answer reaches for. Naming these alongside the right
# answer is what "do not list alternatives" forbids, and what a rubric of positive
# checks alone would happily reward.
_R_DECOYS = ("json", "yaml", "toml", "tomllib", "argparse", "csv",
             "filecmp", "subprocess", "time.perf_counter", "cprofile",
             "profile", "datetime")


def _names_module(out: str, wanted: tuple[str, ...]) -> bool:
    low = out.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in wanted)


def _decoys(out: str) -> int:
    low = out.lower()
    return sum(1 for d in _R_DECOYS if re.search(rf"\b{re.escape(d)}\b", low))


# Exclusivity is folded INTO each positive check rather than left to one negative.
# Measured on the first draft: a shotgun answer that named all three right modules
# among eight wrong ones scored 0.83 — three positives are worth more than one
# negative, so naming everything paid. Requiring "the right module AND not a spray"
# makes the positive itself unsatisfiable by spraying, which is the only version the
# goal's own instruction ("do not list alternatives") actually enforces.
RESEARCH_CHECKS = (
    TextCheck("configparser", lambda o: _names_module(o, _R1) and _decoys(o) <= 1),
    TextCheck("difflib", lambda o: _names_module(o, _R2) and _decoys(o) <= 1),
    TextCheck("timeit", lambda o: _names_module(o, _R3) and _decoys(o) <= 1),
    TextCheck("not_a_shotgun", lambda o: _decoys(o) > 1, negative=True),
    # One sentence of justification each was asked for, so the answer must be prose
    # and not three bare words.
    TextCheck("justified", lambda o: _words(o) >= 45),
    TextCheck("not_padded", lambda o: _words(o) <= 400),
)

TEXT_SUITE.extend(
    [
        EvalTask(id="incident_note", goal=WRITE_GOAL, task_type="write",
                 checks=WRITE_CHECKS),
        EvalTask(id="stdlib_pick", goal=RESEARCH_GOAL, task_type="research",
                 checks=RESEARCH_CHECKS),
    ]
)

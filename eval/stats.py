"""Paired analysis, so a published number says how sure it is.

Three numbers in this project were published and then corrected downward. None was a
bug: the eval reported point estimates from a design that could not resolve the effect
it was claiming. From the four batches actually run —

    orchestration  0.742 · 0.721 · 0.656 · 0.476     sd 0.121
    baseline       0.414 · 0.492 · 0.708 · 0.497     sd 0.126
    difference    +0.328 · +0.229 · -0.052 · -0.021  sd 0.187

— the claim made was +0.159. With a paired sd of 0.187 that needs ~11 pairs to detect;
four were run, which could only have seen effects of about +0.26 and up.

Note the third row, because it contradicts the obvious hope: the sd of the DIFFERENCE is
LARGER than either arm's. Between batches the arms drift independently, so pairing at
batch level buys nothing. Whether drift WITHIN one session is common-mode — where
pairing would cancel it — is an open question this harness now measures instead of
assuming, since `run_suite` already runs all three arms inside the same loop iteration.

So the contract here is narrow and honest: report the paired difference, an interval
around it, and the smallest effect the run could have detected. The last one is what
stops "we found nothing" and "we could not have seen anything" from looking identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Two-sided 95% critical values, df 1..30. Hardcoded because the eval has no scipy and
# the normal approximation is badly wrong at the k values actually used (k=3 -> df=2,
# where 1.96 understates the interval by a factor of two).
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}
# One-sided 80% (the power term), same df range.
_T80 = {
    1: 1.376, 2: 1.061, 3: 0.978, 4: 0.941, 5: 0.920, 6: 0.906, 7: 0.896,
    8: 0.889, 9: 0.883, 10: 0.879, 11: 0.876, 12: 0.873, 13: 0.870, 14: 0.868,
    15: 0.866, 16: 0.865, 17: 0.863, 18: 0.862, 19: 0.861, 20: 0.860, 21: 0.859,
    22: 0.858, 23: 0.858, 24: 0.857, 25: 0.856, 26: 0.856, 27: 0.855, 28: 0.855,
    29: 0.854, 30: 0.854,
}


def _crit(table: dict[int, float], df: int, tail: float) -> float:
    if df < 1:
        return float("inf")
    return table.get(df, tail)


@dataclass(frozen=True)
class PairedDelta:
    """The difference between two arms, measured pair by pair."""

    n: int
    mean: float
    ci_low: float
    ci_high: float
    # Smallest true effect this many pairs could have detected, at alpha .05 power .8.
    # Publishing it beside the mean is what stops an underpowered run reading as a null.
    detectable: float

    @property
    def significant(self) -> bool:
        """The interval excludes zero. Not "the mean looks big".

        Requires at least two pairs. With one pair the interval collapses onto the mean
        and every non-zero difference would report as significant — the most confident
        possible statement from the least possible evidence.
        """
        if self.n < 2:
            return False
        return self.ci_low > 0.0 or self.ci_high < 0.0

    def describe(self, label_a: str, label_b: str) -> str:
        if self.n < 2:
            return f"{label_b} vs {label_a}: {self.mean:+.3f} (n={self.n}, no interval)"
        verdict = "significant" if self.significant else "NOT significant"
        return (
            f"{label_b} vs {label_a}: {self.mean:+.3f} "
            f"[95% CI {self.ci_low:+.3f}, {self.ci_high:+.3f}] "
            f"{verdict}; smallest effect {self.n} pairs could detect: "
            f"±{self.detectable:.3f}"
        )


def paired_delta(a: list[float], b: list[float]) -> PairedDelta:
    """`b - a`, pair by pair.

    Pairing is the whole point: run-to-run drift moves both arms together and cancels in
    the difference, while comparing two independent means lets that drift masquerade as
    an effect. That is precisely how +0.289 was published and then failed to reproduce.
    """
    pairs = [y - x for x, y in zip(a, b, strict=False)]
    n = len(pairs)
    if n == 0:
        return PairedDelta(0, 0.0, 0.0, 0.0, float("inf"))
    mean = sum(pairs) / n
    if n < 2:
        return PairedDelta(n, mean, mean, mean, float("inf"))
    var = sum((d - mean) ** 2 for d in pairs) / (n - 1)
    se = math.sqrt(var / n)
    df = n - 1
    half = _crit(_T95, df, 1.96) * se
    detectable = (_crit(_T95, df, 1.96) + _crit(_T80, df, 0.84)) * se
    return PairedDelta(n, mean, mean - half, mean + half, detectable)


def pairs_needed(observed_sd: float, effect: float) -> int:
    """How many pairs it would take to detect `effect`, given the spread just observed.

    For the reader who wants to know what the run they just paid for could have shown,
    and for the one deciding whether a bigger run is worth buying.
    """
    if effect <= 0 or observed_sd <= 0:
        return 0
    return math.ceil(((1.96 + 0.84) * observed_sd / effect) ** 2)

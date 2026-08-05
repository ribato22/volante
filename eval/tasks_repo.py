"""Repo-shaped goals: an existing project, a change request, and a hidden suite.

Every other goal in this eval is one function in one file. Volante's users are not
writing `slugify()` — they are changing a project that already exists, where the
expensive failure is breaking something nobody asked them to touch. Nothing here had
ever measured that, so the suite's negative results (orchestration does not beat one
call; per-task routing does not pay) may simply not transfer: a single-function goal
gives decomposition nothing to decompose.

**The scoring shape is the point.** A repo goal scores

    (change_passed / change_total) * (regression_passed / regression_total)

as a PRODUCT, not a sum. Rewriting the project so the new feature is trivially true
drives the regression factor down and takes the score with it, and doing nothing at
all leaves the change factor at zero. Neither is reachable by being agreeable.
"""

from __future__ import annotations

from eval.tasks import EvalTask, RepoSpec

# --- the starting project ---------------------------------------------------
# Real, runnable, and it WORKS before the change. Half of what this goal measures is
# whether a model breaks something it was not asked to touch, which is only a question
# if the thing worked to begin with.

_MONEY = '''\
"""Money is integer cents everywhere. Never floats — 0.1 + 0.2 is not 0.3."""


def parse(text):
    """'12.34' -> 1234. Two decimal places, no currency symbol."""
    whole, _, frac = str(text).strip().partition(".")
    frac = (frac + "00")[:2]
    return int(whole) * 100 + int(frac)


def fmt(amount):
    """1234 -> '12.34'."""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}{amount // 100}.{amount % 100:02d}"


def apply_pct(amount, percent):
    """`percent` OF `amount`, rounded half-up to the cent.

    Half-up, not banker's rounding: finance signed off on this and the reconciliation
    job depends on it.
    """
    return (amount * percent + 50) // 100
'''

_CATALOG = '''\
"""The product catalogue. `grams` is the shipped weight of ONE unit."""

PRODUCTS = {
    "APL": {"name": "Apple", "price": 150, "grams": 180},
    "MLK": {"name": "Milk", "price": 250, "grams": 1000},
    "OIL": {"name": "Olive oil", "price": 1300, "grams": 750},
    "TEA": {"name": "Tea", "price": 400, "grams": 160},
    "SAF": {"name": "Saffron", "price": 4000, "grams": 20},
}


class UnknownSku(KeyError):
    """Raised for a sku the catalogue does not carry."""


def lookup(sku):
    try:
        return PRODUCTS[sku]
    except KeyError:
        raise UnknownSku(sku) from None
'''

_PRICING = '''\
"""Discounts and tax."""

from money import apply_pct

COUPONS = {"SAVE10": 10, "SAVE25": 25}
TAX_PCT = 9


def discount_for(subtotal, codes):
    """Total discount in cents.

    Coupons COMPOUND: each one applies to what is left after the previous one, so
    SAVE10 twice is 19% off, not 20%. This is deliberate — marketing priced the
    campaign on it. Do not "fix" it.
    """
    remaining = subtotal
    for code in codes:
        percent = COUPONS.get(code)
        if percent is None:
            continue
        remaining -= apply_pct(remaining, percent)
    return subtotal - remaining


def tax_for(amount):
    """Tax on an amount, in cents."""
    return apply_pct(amount, TAX_PCT)


def qualifies(subtotal, codes, threshold):
    """True when this order reaches `threshold`.

    Promotions compare against the GOODS value: subtotal minus discounts, before tax.
    Fulfilment bills us on goods, so a promotion triggered by the taxed total would
    pay out on money we never collected for the goods.
    """
    return subtotal - discount_for(subtotal, codes) >= threshold
'''

_CART = '''\
"""The cart. Amounts are cents; quantities are whole units."""

from catalog import lookup
from pricing import discount_for, qualifies, tax_for

FREE_GIFT_FROM = 10000


class Cart:
    def __init__(self):
        self._lines = []
        self._codes = []

    def add(self, sku, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        lookup(sku)  # raises UnknownSku for a sku we do not carry
        self._lines.append((sku, qty))
        return self

    def coupon(self, code):
        self._codes.append(code)
        return self

    def subtotal(self):
        return sum(lookup(sku)["price"] * qty for sku, qty in self._lines)

    def discount(self):
        return discount_for(self.subtotal(), self._codes)

    def payable(self):
        """What the customer owes for the GOODS, after discounts, before tax."""
        return self.subtotal() - self.discount()

    def tax(self):
        return tax_for(self.payable())

    def free_gift(self):
        """A tote bag on big orders."""
        return qualifies(self.subtotal(), self._codes, FREE_GIFT_FROM)

    def total(self):
        return self.payable() + self.tax()

    def receipt(self):
        """[(label, amount), ...] in the order they are printed."""
        rows = [("SUBTOTAL", self.subtotal())]
        if self.discount():
            rows.append(("DISCOUNT", -self.discount()))
        rows.append(("TAX", self.tax()))
        rows.append(("TOTAL", self.total()))
        return rows
'''

STARTING_FILES = (
    ("money.py", _MONEY),
    ("catalog.py", _CATALOG),
    ("pricing.py", _PRICING),
    ("cart.py", _CART),
)

# --- the hidden probe -------------------------------------------------------
# The runner can only call module-level functions with JSON arguments, and this goal's
# API is a class. Written AFTER the model's files so it cannot be shadowed.
#
# `_value` accepts a method OR a property: a correct implementation that exposes
# `shipping` as a property is not wrong, and grading it 0 would measure Python style
# rather than the change that was asked for.

_PROBE = '''\
from cart import Cart


def _build(items, codes):
    c = Cart()
    for sku, qty in items:
        c.add(sku, qty)
    for code in codes:
        c.coupon(code)
    return c


def _value(obj, name):
    v = getattr(obj, name)
    return v() if callable(v) else v


def value(items, codes, name):
    return _value(_build(items, codes), name)


def receipt(items, codes):
    return [list(row) for row in _build(items, codes).receipt()]


def raises(items, codes, name):
    """True when reading `name` raises — the goal's error behaviour must survive."""
    try:
        _value(_build(items, codes), name)
    except Exception:
        return True
    return False


def add_rejects(sku, qty):
    """True when `add` WORKS on a good call and REJECTS this one.

    The control call is not decoration. A cart whose `add` raises on everything --
    including an empty module that raises on import -- would satisfy a bare "it
    raised" check for free, which is exactly how an earlier goal in this suite handed
    an empty answer 12 of 42 points.
    """
    try:
        Cart().add("APL", 1)
    except Exception:
        return False
    try:
        Cart().add(sku, qty)
    except Exception:
        return True
    return False
'''

PROBE_FILES = (("_probe.py", _PROBE),)

# --- the change request -----------------------------------------------------

# The FULL source of every file goes in the prompt, not a summary of it.
#
# The agentic arm gets the tree on disk; if the other two arms only got a description,
# the regression factor would be measuring how well a model reconstructs my prose
# rather than whether it broke code it was given. Every arm now receives byte-identical
# context, which is the only way an arm comparison means anything.

_FILE_BLOCK = "\n\n".join(
    f"### {path}\n```python\n{body.rstrip()}\n```" for path, body in STARTING_FILES
)

SHOPCART_GOAL = f"""\
Here is a small shopping-cart project of mine. It works today, and it has tests and a
README that I have left out of this message.

{_FILE_BLOCK}

WHAT I NEED
-----------
We are adding shipping. Please:

1. Give `Cart` a `weight_grams()` returning the total shipped weight of everything in
   the cart, and a `shipping()` returning the shipping cost in cents.

2. Shipping is free once the order is worth 5000 or more.

3. Otherwise it goes by total weight:
       up to and including 500 g   ->  499
       up to and including 2000 g  ->  899
       anything heavier            -> 1499

4. Tax is charged on the goods only. Shipping is not taxed, so `tax()` must not change.

5. `total()` becomes payable + tax + shipping.

6. `receipt()` gains a `SHIPPING` row, placed after any DISCOUNT row and BEFORE the TAX
   row, so it reads SUBTOTAL, DISCOUNT (when there is one), SHIPPING, TAX, TOTAL.

Everything the project already does must keep working exactly as it does now.

Answer with the complete contents of every file you changed. Put each one in its own
```python block whose FIRST LINE is a comment naming the file, like:

```python
# path: cart.py
...the whole file...
```

Files you did not change do not need to be included.
"""

# --- the hidden acceptance suite --------------------------------------------
# Two factors, multiplied. Each entry is (module, function, args, expected).
#
# CHANGE cases grade only the shipping feature. REGRESSION cases grade behaviour that
# existed before and was never asked about — including the compounding coupon rule the
# request explicitly tells the model NOT to "fix", which is the single most tempting
# thing to tidy while passing through pricing.py.

CHANGE = [
    # FOUR cases per shipping outcome, and each of the two traps appears at THREE
    # different tariffs. Both properties were forced by measurement, not taste:
    #   * unbalanced, "always charge 1499" scored 0.500 because 1499 happened to be
    #     the right answer most often;
    #   * with one case per trap, getting the threshold wrong cost 1/16 = 0.0625, so
    #     the criterion the goal exists to test was worth almost nothing.
    # SAF exists to make the second property possible at all: without a dense product,
    # high value implies high weight and a trap can only ever land in the heavy tier.
    #
    # --- 499: 500 g or less
    ("499 light", "_probe", "value", [[["TEA", 1]], [], "shipping"], 499),
    ("499 at exactly 500 g", "_probe", "value", [[["APL", 1], ["TEA", 2]], [], "shipping"], 499),
    # payable 4800, but 5232 with tax: thresholding after tax frees this.
    ("499 threshold is BEFORE tax",
     "_probe", "value", [[["SAF", 1], ["TEA", 2]], [], "shipping"], 499),
    # subtotal 5200, payable 4680 after SAVE10: thresholding on subtotal frees this.
    ("499 threshold is AFTER discounts",
     "_probe", "value", [[["SAF", 1], ["TEA", 3]], ["SAVE10"], "shipping"], 499),
    # --- 899: over 500 g, up to 2000 g
    ("899 just over 500 g", "_probe", "value", [[["APL", 2], ["TEA", 1]], [], "shipping"], 899),
    ("899 at exactly 2000 g", "_probe", "value", [[["MLK", 2]], [], "shipping"], 899),
    ("899 threshold is BEFORE tax",
     "_probe", "value", [[["SAF", 1], ["TEA", 1], ["APL", 2]], [], "shipping"], 899),
    ("899 threshold is AFTER discounts",
     "_probe", "value", [[["SAF", 1], ["MLK", 1], ["TEA", 2]], ["SAVE10"], "shipping"], 899),
    # --- 1499: over 2000 g
    ("1499 just over 2000 g",
     "_probe", "value", [[["MLK", 2], ["TEA", 1]], [], "shipping"], 1499),
    ("1499 heavy", "_probe", "value", [[["OIL", 2], ["MLK", 3]], [], "shipping"], 1499),
    ("1499 threshold is BEFORE tax",
     "_probe", "value", [[["OIL", 3], ["MLK", 4]], [], "shipping"], 1499),
    ("1499 threshold is AFTER discounts",
     "_probe", "value", [[["MLK", 24]], ["SAVE25"], "shipping"], 1499),
    # --- free: payable at or above 5000
    ("free at exactly 5000", "_probe", "value", [[["OIL", 2], ["TEA", 6]], [], "shipping"], 0),
    ("free above 5000", "_probe", "value", [[["OIL", 4]], [], "shipping"], 0),
    # The SAVE25 trap cart without its coupon: free. The pair is the cheapest possible
    # demonstration that the threshold reads payable rather than subtotal.
    ("free heavy but expensive", "_probe", "value", [[["MLK", 24]], [], "shipping"], 0),
    ("free and light", "_probe", "value", [[["SAF", 2]], [], "shipping"], 0),
    # --- composite criteria, each on a cart with a DIFFERENT shipping value so that
    # no single constant collects them all.
    ("weight_grams", "_probe", "value", [[["OIL", 3], ["MLK", 4]], [], "weight_grams"], 6250),
    ("total is payable + tax + shipping",
     "_probe", "value", [[["OIL", 2]], [], "total"], 3733),
    ("receipt row order with a discount", "_probe", "receipt", [[["MLK", 24]], ["SAVE10"]],
     [["SUBTOTAL", 6000], ["DISCOUNT", -600], ["SHIPPING", 0],
      ["TAX", 486], ["TOTAL", 5886]]),
    ("receipt row order without a discount", "_probe", "receipt", [[["TEA", 1]], []],
     [["SUBTOTAL", 400], ["SHIPPING", 499], ["TAX", 36], ["TOTAL", 935]]),
]

REGRESSION = [
    ("subtotal", "_probe", "value", [[["OIL", 3], ["MLK", 4]], [], "subtotal"], 4900),
    # Criterion 4 -- "tax() must not change" -- reads like part of the change request
    # and is not. The starting tree already satisfies it, so grading it as a CHANGE
    # case handed an EMPTY answer 1 of 13 and put the whole goal's floor at 0.0769.
    # A model that taxes shipping still fails it; it just fails as a regression, which
    # is what breaking existing behaviour is.
    ("tax is not charged on shipping",
     "_probe", "value", [[["OIL", 3], ["MLK", 4]], [], "tax"], 441),
    # 19% off, not 20%: the rule the request says is deliberate.
    ("coupons still compound",
     "_probe", "value", [[["MLK", 24]], ["SAVE10", "SAVE10"], "discount"], 1140),
    # A second two-coupon case: "tidying" the compounding rule into an additive one is
    # the most tempting change to make while passing through pricing.py, and with a
    # single case it cost 1/13 of one factor. Compound 1690 here; additive gives 1820.
    ("coupons still compound, different pair",
     "_probe", "value", [[["OIL", 4]], ["SAVE25", "SAVE10"], "discount"], 1690),
    ("single coupon", "_probe", "value", [[["MLK", 24]], ["SAVE25"], "discount"], 1500),
    ("unknown coupon ignored",
     "_probe", "value", [[["MLK", 24]], ["NOPE"], "discount"], 0),
    ("payable", "_probe", "value", [[["MLK", 24]], ["SAVE25"], "payable"], 4500),
    ("tax on payable", "_probe", "value", [[["MLK", 24]], ["SAVE25"], "tax"], 405),
    ("money.parse", "money", "parse", ["12.34"], 1234),
    ("money.fmt", "money", "fmt", [1234], "12.34"),
    ("apply_pct rounds half UP", "money", "apply_pct", [105, 10], 11),
    ("apply_pct", "money", "apply_pct", [4900, 9], 441),
    # The existing promotion, and the convention the new one has to follow. 9200 is
    # under the 10000 gift threshold, but 10028 once taxed: an implementation that
    # thresholds on the taxed total hands out a gift nobody earned.
    ("free_gift stays on the goods value",
     "_probe", "value", [[["SAF", 2], ["TEA", 3]], [], "free_gift"], False),
    # 9500 is under 10000 but 10355 taxed: another basis-of-comparison case, so the
    # convention costs more than a single cell to get wrong.
    ("free_gift stays on the goods value, second cart",
     "_probe", "value", [[["SAF", 2], ["MLK", 6]], [], "free_gift"], False),
    # subtotal 12000, payable 9000 after SAVE25: thresholding on subtotal gifts this.
    ("free_gift is measured after discounts",
     "_probe", "value", [[["SAF", 3]], ["SAVE25"], "free_gift"], False),
    ("free_gift above the threshold",
     "_probe", "value", [[["SAF", 3]], [], "free_gift"], True),
    ("unknown sku still rejected", "_probe", "add_rejects", ["ZZZ", 1], True),
    ("non-positive qty still rejected", "_probe", "add_rejects", ["APL", 0], True),
]

# The runner is RENDERED from those tables rather than restating them, so a case can
# only exist in one place. Tests read the tables directly; duplicating them as text
# would let the graded suite and the checked suite drift apart silently, which is the
# same failure mode as a mutation test whose mutation never applied.
SHOPCART_REFERENCE_TEST = f"""\
import json, sys

CHANGE = {CHANGE!r}
REGRESSION = {REGRESSION!r}


def _ok(mod, fn, args, expected):
    try:
        return call_in(mod, fn, *args) == expected
    except Exception:
        return False


def main():
    cp = sum(1 for _l, m, f, a, e in CHANGE if _ok(m, f, a, e))
    rp = sum(1 for _l, m, f, a, e in REGRESSION if _ok(m, f, a, e))
    # The PRODUCT: breaking what existed scales the whole score down, and changing
    # nothing leaves it at zero however tidy the untouched project still is.
    print(_TAG + json.dumps({{
        "passed": cp * rp,
        "total": len(CHANGE) * len(REGRESSION),
        "change": [cp, len(CHANGE)],
        "regression": [rp, len(REGRESSION)],
    }}))


if __name__ == "__main__":
    main()
    sys.exit(0)
"""

SHOPCART_TASK = EvalTask(
    id="shopcart_shipping",
    goal=SHOPCART_GOAL,
    reference_test=SHOPCART_REFERENCE_TEST,
    repo=RepoSpec(starting_files=STARTING_FILES, probe_files=PROBE_FILES),
)

from eval.tasks_repo_config import DEPLOYCTL_TASK  # noqa: E402

REPO_SUITE = [SHOPCART_TASK, DEPLOYCTL_TASK]

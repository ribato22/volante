"""The instrument check for repo-shaped goals, and it costs no API calls.

Every number here was MEASURED before it was asserted, and three of them changed the
GOAL rather than the test:

  * an empty answer scored 0.0769, because "tax() must not change" was filed as a
    CHANGE case when the untouched tree already satisfies it;
  * "always charge 1499" scored 0.500, because 1499 happened to be the right answer
    most often and the composite cases used carts that agreed with it;
  * both threshold criteria -- the entire point of the goal -- cost 1/16 = 0.0625,
    because with this catalogue high value implied high weight, so a trap could only
    ever land in the heavy tier. `SAF` exists to break that coupling.

A goal whose golden solution does not score exactly 1.000, or whose untouched tree does
not score exactly 0.000, is broken. Finding that out here costs nothing; finding it out
from a three-arm run costs a suite of calls and produces a number to retract.
"""

from __future__ import annotations

import collections
import itertools

import pytest
from eval.harness import _score_reference, repo_files_for
from eval.tasks_repo import CHANGE, REGRESSION, SHOPCART_TASK

GOLDEN = """```python
# path: cart.py
from catalog import lookup
from pricing import discount_for, qualifies, tax_for

FREE_GIFT_FROM = 10000
FREE_SHIPPING_FROM = 5000
WEIGHT_TIERS = ((500, 499), (2000, 899))
HEAVY = 1499


class Cart:
    def __init__(self):
        self._lines = []
        self._codes = []

    def add(self, sku, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        lookup(sku)
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
        return self.subtotal() - self.discount()

    def weight_grams(self):
        return sum(lookup(sku)["grams"] * qty for sku, qty in self._lines)

    def shipping(self):
        if self.payable() >= FREE_SHIPPING_FROM:
            return 0
        grams = self.weight_grams()
        for limit, cost in WEIGHT_TIERS:
            if grams <= limit:
                return cost
        return HEAVY

    def tax(self):
        return tax_for(self.payable())

    def free_gift(self):
        return qualifies(self.subtotal(), self._codes, FREE_GIFT_FROM)

    def total(self):
        return self.payable() + self.tax() + self.shipping()

    def receipt(self):
        rows = [("SUBTOTAL", self.subtotal())]
        if self.discount():
            rows.append(("DISCOUNT", -self.discount()))
        rows.append(("SHIPPING", self.shipping()))
        rows.append(("TAX", self.tax()))
        rows.append(("TOTAL", self.total()))
        return rows
```"""

# The threshold-after-tax mutation needs somewhere to read the post-tax figure from.
_WITH_AFTER_TAX = GOLDEN.replace(
    "    def tax(self):",
    "    def after_tax(self):\n        return self.payable() + self.tax()\n\n    def tax(self):",
)

# Only `discount_for` changes. An earlier version of this mutation rewrote the whole
# file and dropped `qualifies`, which cart.py imports -- every case then failed on an
# ImportError and the mutation "cost" 1.0000. A mutation that breaks the import is
# measuring the mutation, not the rule it was meant to isolate.
_ADDITIVE_COUPONS = (
    GOLDEN
    + """

```python
# path: pricing.py
from money import apply_pct

COUPONS = {"SAVE10": 10, "SAVE25": 25}
TAX_PCT = 9


def discount_for(subtotal, codes):
    return sum(apply_pct(subtotal, COUPONS[c]) for c in codes if c in COUPONS)


def tax_for(amount):
    return apply_pct(amount, TAX_PCT)


def qualifies(subtotal, codes, threshold):
    return subtotal - discount_for(subtotal, codes) >= threshold
```"""
)

_TIER_BODY = (
    "        if self.payable() >= FREE_SHIPPING_FROM:\n"
    "            return 0\n"
    "        grams = self.weight_grams()\n"
    "        for limit, cost in WEIGHT_TIERS:\n"
    "            if grams <= limit:\n"
    "                return cost\n"
    "        return HEAVY"
)
_WEIGHT_BODY = '        return sum(lookup(sku)["grams"] * qty for sku, qty in self._lines)'


def _score(answer: str) -> float:
    return _score_reference(
        answer, SHOPCART_TASK.reference_test, repo_files_for(answer, SHOPCART_TASK.repo)
    )[0]


# --- the two ends of the scale ----------------------------------------------


def test_the_golden_answer_scores_exactly_one() -> None:
    assert _score(GOLDEN) == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="empty"),
        pytest.param("Sure! I'll add shipping to the cart.", id="prose-only"),
        pytest.param("```python\nx = 1\n```", id="a-block-naming-no-file"),
    ],
)
def test_an_answer_that_changes_nothing_scores_exactly_zero(answer: str) -> None:
    """The untouched project passes every REGRESSION case by construction.

    The product is what makes that worth nothing: leaving the change factor at zero
    zeroes the score however intact the rest of the tree still is. A weighted sum would
    have paid this answer handsomely for the fourteen things it did not do.
    """
    assert _score(answer) == 0.0


def test_the_starting_project_works_before_the_change() -> None:
    """A regression suite over a tree that was already broken measures nothing."""
    regression_only = SHOPCART_TASK.reference_test.replace(
        '"passed": cp * rp,', '"passed": rp,'
    ).replace('"total": len(CHANGE) * len(REGRESSION),', '"total": len(REGRESSION),')

    score, measured = _score_reference("", regression_only, repo_files_for("", SHOPCART_TASK.repo))

    assert (score, measured) == (1.0, True)


# --- the mutation ladder ----------------------------------------------------

_TWO_CASES = 2 / len(CHANGE)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param(
            _WITH_AFTER_TAX.replace("if self.payable() >= FREE", "if self.after_tax() >= FREE"),
            0.80,
            id="free-shipping-thresholded-AFTER-tax",
        ),
        pytest.param(
            GOLDEN.replace("if self.payable() >= FREE", "if self.subtotal() >= FREE"),
            0.85,
            id="free-shipping-thresholded-on-SUBTOTAL",
        ),
        pytest.param(
            GOLDEN.replace("if grams <= limit", "if grams < limit"),
            0.85,
            id="weight-boundaries-made-exclusive",
        ),
        pytest.param(
            GOLDEN.replace(
                "return tax_for(self.payable())", "return tax_for(self.payable() + self.shipping())"
            ),
            # The only mutation that costs on BOTH factors, which is what taxing
            # shipping is: a new rule applied wrongly AND an old figure changed.
            0.80,
            id="tax-charged-on-shipping-too",
        ),
        pytest.param(
            GOLDEN.replace(
                "self.payable() + self.tax() + self.shipping()", "self.payable() + self.tax()"
            ),
            0.90,
            id="total-forgets-shipping",
        ),
        pytest.param(
            GOLDEN.replace(
                'rows.append(("SHIPPING", self.shipping()))\n'
                '        rows.append(("TAX", self.tax()))',
                'rows.append(("TAX", self.tax()))\n'
                '        rows.append(("SHIPPING", self.shipping()))',
            ),
            0.90,
            id="SHIPPING-row-printed-after-TAX",
        ),
        pytest.param(
            GOLDEN.replace("((500, 499), (2000, 899))", "((500, 899), (2000, 499))"),
            0.50,
            id="the-two-weight-tariffs-swapped",
        ),
        pytest.param(
            GOLDEN.replace('["grams"] * qty for sku, qty', '["grams"] for sku, qty'),
            0.65,
            id="weight-ignores-quantity",
        ),
        # Not a criterion, its opposite: the request says the compounding rule is
        # deliberate, and tidying it into an addition is the most tempting thing to do
        # while passing through pricing.py.
        pytest.param(_ADDITIVE_COUPONS, 0.8889, id="compounding-coupons-tidied-away"),
        # `qualifies` in pricing.py states the convention the request deliberately does
        # NOT: promotions compare against the goods value, before tax. Rebuilding the
        # existing gift rule on the taxed total is what a model does when it answered
        # from the request without reading the file it was editing.
        pytest.param(
            GOLDEN.replace(
                "return qualifies(self.subtotal(), self._codes, FREE_GIFT_FROM)",
                "return self.payable() + self.tax() >= FREE_GIFT_FROM",
            ),
            0.8889,
            id="free_gift-rebuilt-on-the-taxed-total",
        ),
    ],
)
def test_each_criterion_costs_at_least_two_cases(answer: str, expected: float) -> None:
    score = _score(answer)

    assert score == pytest.approx(expected, abs=0.001)
    assert score <= 1.0 - _TWO_CASES + 1e-9, (
        f"this violation costs {1.0 - score:.4f}, under two cases; a criterion worth "
        "almost nothing is a criterion the goal does not really test"
    )


# --- the degenerate space ---------------------------------------------------

_CONSTANT_CEILING = 0.30


def test_no_constant_answer_beats_the_measured_ceiling() -> None:
    """Search the grid, do not argue about one plausible dict.

    Shipping has four outcomes and the table holds four cases each, so a constant's
    floor is a quarter of the pure cases. The measured 0.30 is that floor plus
    `weight_grams`, which a constant can only collect by implementing it correctly --
    real work, correctly paid for.
    """
    template = GOLDEN.replace(_TIER_BODY, "        return {constant}").replace(
        _WEIGHT_BODY, "        return {weight}"
    )
    worst = 0.0
    grid = itertools.product(
        ("0", "499", "899", "1499", "999"),
        ("0", 'sum(lookup(sku)["grams"] * qty for sku, qty in self._lines)'),
    )
    for constant, weight in grid:
        worst = max(worst, _score(template.format(constant=constant, weight=weight)))

    assert worst <= _CONSTANT_CEILING + 1e-9, worst
    # And not zero either: a constant that lands on the right tier for some carts did
    # get those carts right, and a scale that denied it would be lying in the safe
    # direction rather than measuring.
    assert worst > 0.0


def test_the_shipping_outcomes_are_balanced() -> None:
    """Four cases per outcome, checked against the table rather than trusted.

    Balance is not a stylistic preference: it is the thing that bounds the constant,
    and the first unbalanced draft handed "always charge 1499" a score of 0.500.
    """
    tally = collections.Counter(
        expected
        for _label, _mod, fn, _args, expected in CHANGE
        if fn == "value" and isinstance(expected, int) and expected < 2000
    )

    assert sorted(tally) == [0, 499, 899, 1499]
    assert set(tally.values()) == {4}, tally


def test_each_threshold_trap_appears_at_more_than_one_tariff() -> None:
    """A trap that only ever fires in one tier is guessable from the tier alone."""
    for trap in ("BEFORE tax", "AFTER discounts"):
        tariffs = {expected for label, _mod, _fn, _args, expected in CHANGE if trap in label}
        assert len(tariffs) >= 3, (trap, tariffs)


# --- the layout -------------------------------------------------------------


def test_files_the_model_did_not_touch_come_from_the_starting_tree() -> None:
    files = repo_files_for(GOLDEN, SHOPCART_TASK.repo)

    assert "FREE_SHIPPING_FROM" in files["cart.py"]  # the model's version won
    assert "def apply_pct" in files["money.py"]  # untouched, still there
    assert "_probe.py" in files  # harness-owned, always present


def test_a_model_cannot_replace_the_probe_that_grades_it() -> None:
    """Probes are laid over the model's files, never under them.

    Otherwise a file named `_probe.py` replaces the module the runner calls -- the
    `json.py` hole one layer up. Control what the trusted side imports and the score
    stops meaning anything.
    """
    hostile = GOLDEN + (
        "\n\n```python\n# path: _probe.py\ndef value(items, codes, name):\n    return 0\n```\n"
    )

    files = repo_files_for(hostile, SHOPCART_TASK.repo)

    assert "def add_rejects" in files["_probe.py"]
    assert _score(hostile) == 1.0  # the golden cart still scores on its own merits


def test_the_regression_table_is_not_dominated_by_one_module() -> None:
    """Regression spread over the project, not piled onto the file being changed.

    If every regression case ran through cart.py, "did you break something else"
    would be unmeasurable -- the only file anyone touches is the one being changed.
    """
    modules = collections.Counter(mod for _l, mod, _f, _a, _e in REGRESSION)

    assert len(modules) >= 2, modules


# --- the agentic arm --------------------------------------------------------
#
# NON-NEGOTIABLE GATE. `_AGENTIC_SUFFIX` orders the model to write `solution.py`, and
# `run_agentic_single` read back only `ws/solution.py`. A repo goal forbids both: the
# project has its own filenames, and two criteria are about editing the right files.
# Until the golden answer scores 1.000 through the arm's OWN handover path, any
# cross-arm number from this suite would be recording a harness limit as a capability
# gap — which is the single most expensive mistake this eval has made before.


def _agentic_result(answer: str, *, tests: bool = True, readme: bool = True):
    """Reproduce exactly what `run_agentic_single` does for a repo goal."""
    import tempfile
    from pathlib import Path

    from eval.harness import AgenticArmResult, _collect_workspace_repo, extract_files

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        for rel, body in SHOPCART_TASK.repo.starting_files:
            (ws / rel).write_text(body, encoding="utf-8")
        written, _report = extract_files(answer)
        for rel, body in written.items():
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        if tests:
            (ws / "test_cart.py").write_text("def test_x():\n    assert True\n", "utf-8")
        if readme:
            (ws / "README.md").write_text("# cart\n", encoding="utf-8")
        tree = _collect_workspace_repo(ws)
    return AgenticArmResult(
        solution_code="",
        has_tests=tests,
        has_readme=readme,
        usage_total={},
        cost_usd=0.0,
        duration_ms=0,
        repo_files=tree,
    )


def test_the_agentic_arm_can_reach_the_ceiling() -> None:
    from eval.harness import score_agentic

    scores = score_agentic(
        _agentic_result(GOLDEN), SHOPCART_TASK.reference_test, SHOPCART_TASK.repo
    )

    assert scores["code"] == 1.0
    assert scores["composite"] == 1.0
    assert scores["measured"] is True


def test_the_agentic_arm_gets_no_floor_for_tests_and_a_readme() -> None:
    """Tests and a README are worth 0.30 of the composite for a single-file goal.

    A repo goal states the project already HAS both, so every competent model produces
    them and the two flags become constants — a floor under an arm that changed
    nothing. Worse, they would be a floor under ONE arm, and a scale that differs by
    arm cannot compare arms.
    """
    from eval.harness import score_agentic

    scores = score_agentic(
        _agentic_result("", tests=True, readme=True),
        SHOPCART_TASK.reference_test,
        SHOPCART_TASK.repo,
    )

    assert scores["has_tests"] == 1.0 and scores["has_readme"] == 1.0
    assert scores["composite"] == 0.0


def test_the_workspace_read_back_keeps_files_the_model_never_opened() -> None:
    from eval.harness import score_agentic

    res = _agentic_result(GOLDEN)

    assert set(res.repo_files) == {"cart.py", "catalog.py", "money.py", "pricing.py"}
    assert "FREE_SHIPPING_FROM" in res.repo_files["cart.py"]  # edited
    assert "def apply_pct" in res.repo_files["money.py"]  # untouched, still read back
    assert not any(f.startswith("test_") for f in res.repo_files)
    assert score_agentic(res, SHOPCART_TASK.reference_test, SHOPCART_TASK.repo)["code"] == 1.0

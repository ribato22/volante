from __future__ import annotations

from typing import TYPE_CHECKING

from volante.types import Usage

if TYPE_CHECKING:
    from volante.registry import Registry


class CostMeter:
    """Accumulate Usage per model_id + compute USD cost via Registry.

    Keyed by model_id; add() is called AFTER every successful complete().
    """

    def __init__(self) -> None:
        self._totals: dict[str, Usage] = {}
        self._has_estimated: bool = False
        # model_id -> {"prompt", "completion", "usd"}
        self._direct: dict[str, dict[str, float]] = {}

    def add(
        self,
        model_id: str,
        usage: Usage,
        *,
        cost_usd: float | None = None,
    ) -> None:
        current = self._totals.get(model_id)
        if current is None:
            # copy so the caller's own Usage object is not mutated along with it
            self._totals[model_id] = Usage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                estimated=usage.estimated,
            )
        else:
            self._totals[model_id] = Usage(
                prompt_tokens=current.prompt_tokens + usage.prompt_tokens,
                completion_tokens=current.completion_tokens + usage.completion_tokens,
                estimated=current.estimated or usage.estimated,
            )
        if usage.estimated:
            self._has_estimated = True
        if cost_usd is not None:
            # residu-4: record this call's authoritative tokens & dollars in a
            # separate _direct bucket (per-CALL), so costs_usd does not
            # double-count its token*rate. The prompt/completion split is stored
            # separately (not as a combined scalar) so the residual can be priced
            # per-component — a single fraction scalar is wrong when the direct vs
            # fallback split differs and cost_per_1k_in != cost_per_1k_out.
            bucket = self._direct.get(model_id)
            if bucket is None:
                bucket = {"prompt": 0.0, "completion": 0.0, "usd": 0.0}
                self._direct[model_id] = bucket
            bucket["prompt"] += usage.prompt_tokens
            bucket["completion"] += usage.completion_tokens
            bucket["usd"] += cost_usd

    def totals(self) -> dict[str, Usage]:
        # Return independent Usage values as well as an independent mapping. Public
        # callers may mutate a result without corrupting subsequent accounting.
        return {
            model_id: Usage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                estimated=usage.estimated,
            )
            for model_id, usage in self._totals.items()
        }

    def has_estimated(self) -> bool:
        return self._has_estimated

    def merge(self, other: CostMeter) -> None:
        """Merge another meter without losing authoritative per-call costs.

        This is used when a required provider preflight happens before a Runtime
        exists. Replaying only aggregate tokens through ``add`` would incorrectly
        classify every token as authoritative (or none of them), so both ledgers
        are merged at their native granularity.
        """

        if other is self:
            return
        for model_id, usage in other._totals.items():
            current = self._totals.get(model_id)
            if current is None:
                self._totals[model_id] = Usage(
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    estimated=usage.estimated,
                )
            else:
                self._totals[model_id] = Usage(
                    prompt_tokens=current.prompt_tokens + usage.prompt_tokens,
                    completion_tokens=current.completion_tokens
                    + usage.completion_tokens,
                    estimated=current.estimated or usage.estimated,
                )
        self._has_estimated = self._has_estimated or other._has_estimated
        for model_id, source in other._direct.items():
            target = self._direct.setdefault(
                model_id,
                {"prompt": 0.0, "completion": 0.0, "usd": 0.0},
            )
            for field in ("prompt", "completion", "usd"):
                target[field] += source[field]

    def unpriced_models(self, registry: Registry) -> list[str]:
        """Models that consumed tokens while both their rates are zero.

        A model configured with no `COST_IN`/`COST_OUT` prices every call at zero, so
        the run reports `billed_usd: $0.000000` while real money leaves the account.
        That is the one failure mode of this meter that a reader cannot spot — every
        other inaccuracy shows up as a number that looks wrong, and this one looks
        perfect.

        Only models with actual usage are named. A configured-but-unused model has
        nothing to misreport, and warning about it would train the reader to skip the
        warning.
        """
        unpriced: list[str] = []
        for model_id, usage in self._totals.items():
            if usage.prompt_tokens == 0 and usage.completion_tokens == 0:
                continue
            try:
                info = registry.get(model_id)
            except Exception:  # noqa: BLE001 - an unknown model cannot be priced either
                continue
            if info.cost_per_1k_in == 0.0 and info.cost_per_1k_out == 0.0:
                unpriced.append(model_id)
        return sorted(unpriced)

    def costs_usd(self, registry: Registry) -> tuple[float, float]:
        # Two ledgers: (billed, credit). billed = cash (card), credit = plan_*.
        # residu-4 PER-CALL: _totals holds ALL tokens; authoritative calls (_direct)
        # are priced with their own cost_usd, and the rest (residual tokens
        # PER-COMPONENT) with token*rate. The residual is computed separately for
        # prompt & completion (not as a single fraction scalar) because the direct vs
        # fallback split can differ and cost_per_1k_in != cost_per_1k_out — a fraction
        # scalar would misprice that case.
        billed = 0.0
        credit = 0.0
        for model_id, usage in self._totals.items():
            mi = registry.get(model_id)
            direct = self._direct.get(model_id)
            direct_prompt = direct["prompt"] if direct is not None else 0.0
            direct_completion = direct["completion"] if direct is not None else 0.0
            direct_usd = direct["usd"] if direct is not None else 0.0
            residual_prompt = max(usage.prompt_tokens - direct_prompt, 0.0)
            residual_completion = max(usage.completion_tokens - direct_completion, 0.0)
            amount = (
                residual_prompt / 1000 * mi.cost_per_1k_in
                + residual_completion / 1000 * mi.cost_per_1k_out
                + direct_usd
            )
            if mi.billing == "card":
                billed += amount
            else:
                credit += amount
        return billed, credit

    def cost_usd(self, registry: Registry) -> float:
        billed, credit = self.costs_usd(registry)
        return billed + credit

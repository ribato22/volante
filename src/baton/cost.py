from __future__ import annotations

from typing import TYPE_CHECKING

from baton.types import Usage

if TYPE_CHECKING:
    from baton.registry import Registry


class CostMeter:
    """Akumulasi Usage per model_id + hitung biaya USD via Registry.

    Dipakai sebagai key = model_id; add() dipanggil SETELAH tiap complete() sukses.
    """

    def __init__(self) -> None:
        self._totals: dict[str, Usage] = {}
        self._has_estimated: bool = False
        self._direct: dict[str, dict[str, float]] = {}  # model_id -> {"tokens", "usd"}

    def add(self, model_id: str, usage: Usage) -> None:
        current = self._totals.get(model_id)
        if current is None:
            # salin agar objek Usage milik pemanggil tak ikut termutasi
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

    def totals(self) -> dict[str, Usage]:
        # salinan dangkal: pemanggil boleh mengubah dict-nya tanpa merusak state internal
        return dict(self._totals)

    def has_estimated(self) -> bool:
        return self._has_estimated

    def costs_usd(self, registry: Registry) -> tuple[float, float]:
        # Dua-ledger: (billed, credit). billed = cash (card), credit = plan_*.
        # residu-4 PER-CALL: _totals memuat SEMUA token; call otoritatif (_direct)
        # dinilai pakai cost_usd-nya, sisanya (fraksi non-direct) pakai token*rate.
        billed = 0.0
        credit = 0.0
        for model_id, usage in self._totals.items():
            mi = registry.get(model_id)
            total_tokens = usage.prompt_tokens + usage.completion_tokens
            direct = self._direct.get(model_id)
            direct_tokens = direct["tokens"] if direct is not None else 0.0
            direct_usd = direct["usd"] if direct is not None else 0.0
            full_rate = (
                usage.prompt_tokens / 1000 * mi.cost_per_1k_in
                + usage.completion_tokens / 1000 * mi.cost_per_1k_out
            )
            if total_tokens > 0:
                non_direct_fraction = max(total_tokens - direct_tokens, 0.0) / total_tokens
            else:
                non_direct_fraction = 0.0
            amount = full_rate * non_direct_fraction + direct_usd
            if mi.billing == "card":
                billed += amount
            else:
                credit += amount
        return billed, credit

    def cost_usd(self, registry: Registry) -> float:
        billed, credit = self.costs_usd(registry)
        return billed + credit

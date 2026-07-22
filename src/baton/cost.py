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

    def cost_usd(self, registry: Registry) -> float:
        total = 0.0
        for model_id, usage in self._totals.items():
            mi = registry.get(model_id)
            total += usage.prompt_tokens / 1000 * mi.cost_per_1k_in
            total += usage.completion_tokens / 1000 * mi.cost_per_1k_out
        return total

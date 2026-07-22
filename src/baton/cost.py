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
        if cost_usd is not None:
            # residu-4: catat token & dolar otoritatif call ini di bucket _direct
            # terpisah (per-CALL), agar costs_usd tak double-count token*rate-nya.
            # Split prompt/completion disimpan terpisah (bukan skalar gabungan) agar
            # residual bisa dinilai per-komponen — skalar-fraksi tunggal salah bila
            # split direct vs fallback berbeda dan cost_per_1k_in != cost_per_1k_out.
            bucket = self._direct.get(model_id)
            if bucket is None:
                bucket = {"prompt": 0.0, "completion": 0.0, "usd": 0.0}
                self._direct[model_id] = bucket
            bucket["prompt"] += usage.prompt_tokens
            bucket["completion"] += usage.completion_tokens
            bucket["usd"] += cost_usd

    def totals(self) -> dict[str, Usage]:
        # salinan dangkal: pemanggil boleh mengubah dict-nya tanpa merusak state internal
        return dict(self._totals)

    def has_estimated(self) -> bool:
        return self._has_estimated

    def costs_usd(self, registry: Registry) -> tuple[float, float]:
        # Dua-ledger: (billed, credit). billed = cash (card), credit = plan_*.
        # residu-4 PER-CALL: _totals memuat SEMUA token; call otoritatif (_direct)
        # dinilai pakai cost_usd-nya, sisanya (residual token PER-KOMPONEN) pakai
        # token*rate. Residual dihitung terpisah untuk prompt & completion (bukan
        # skalar-fraksi tunggal) karena split direct vs fallback bisa berbeda dan
        # cost_per_1k_in != cost_per_1k_out — skalar-fraksi akan salah nilai di kasus itu.
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

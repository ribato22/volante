from __future__ import annotations

from volante.cost import CostMeter
from volante.providers.base import (
    LLMProvider,
    OnText,
    call_provider,
    ensure_complete_response,
)
from volante.types import CanonicalRequest, CanonicalResponse


class Worker:
    """Menjalankan satu request one-shot terhadap provider yang dipilih router,
    lalu mencatat usage ke CostMeter (key = model_id) setelah complete() sukses."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        cost_meter: CostMeter,
    ) -> None:
        self._providers = providers
        self._cost_meter = cost_meter

    async def run_one_shot(
        self,
        req: CanonicalRequest,
        model_id: str,
        on_text: OnText | None = None,
    ) -> CanonicalResponse:
        try:
            provider = self._providers[model_id]
        except KeyError as exc:
            raise ValueError(
                f"no provider registered for model_id={model_id!r}"
            ) from exc
        # on_text -> streaming; else complete (nol regresi). Runtime meneruskan
        # callback ber-label per-task (lihat _task_cb) sehingga worker paralel pun
        # bisa diurai per-task oleh konsumen.
        resp = await call_provider(provider, req, on_text)
        # Forward provider-authoritative cost_usd (for example CLI-agent
        # total_cost_usd) consistently across every orchestration phase.
        self._cost_meter.add(model_id, resp.usage, cost_usd=resp.cost_usd)
        return ensure_complete_response(resp, phase="worker")

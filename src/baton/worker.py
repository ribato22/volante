from __future__ import annotations

from baton.cost import CostMeter
from baton.providers.base import LLMProvider, OnText, call_provider
from baton.types import CanonicalRequest, CanonicalResponse


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
        # §5.3 single sanctioned exception: forward the provider-authoritative
        # cost_usd (e.g. Claude Code / Codex total_cost_usd) so it lands in the
        # CostMeter's credit ledger instead of being re-derived from token*rate.
        # NOTE: Worker is the ONLY sanctioned cost_usd forwarder (§5.3). Supervisor,
        # Synthesizer, and AgenticWorker intentionally do NOT forward cost_usd (they
        # call cost_meter.add(model_id, resp.usage) without it) -- so in a
        # subscription-only planner/synth setup, the planner/synth cost is
        # token x rate-approximated instead of provider-authoritative. This is
        # acceptable per the locked contract; it's not an oversight.
        self._cost_meter.add(model_id, resp.usage, cost_usd=resp.cost_usd)
        return resp

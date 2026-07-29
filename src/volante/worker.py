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
    """Runs one one-shot request against the provider the router picked, then
    records usage into CostMeter (key = model_id) after complete() succeeds."""

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
        # on_text -> streaming; else complete (zero regression). Runtime passes down a
        # per-task labelled callback (see _task_cb) so that even parallel workers can
        # still be untangled per task by the consumer.
        resp = await call_provider(provider, req, on_text)
        # Forward provider-authoritative cost_usd (for example CLI-agent
        # total_cost_usd) consistently across every orchestration phase.
        self._cost_meter.add(model_id, resp.usage, cost_usd=resp.cost_usd)
        return ensure_complete_response(resp, phase="worker")

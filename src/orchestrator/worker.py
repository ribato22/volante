from __future__ import annotations

from collections.abc import Callable

from orchestrator.cost import CostMeter
from orchestrator.providers.base import LLMProvider
from orchestrator.types import CanonicalRequest, CanonicalResponse


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
        on_text: Callable[[str], None] | None = None,
    ) -> CanonicalResponse:
        try:
            provider = self._providers[model_id]
        except KeyError as exc:
            raise ValueError(
                f"no provider registered for model_id={model_id!r}"
            ) from exc
        # on_text -> streaming; else complete (nol regresi). Catatan: Runtime TIDAK
        # men-stream worker paralel (output antar-task akan bercampur); param ini
        # untuk pemanggil langsung / skenario satu-worker.
        if on_text is not None:
            resp = await provider.stream(req, on_text)
        else:
            resp = await provider.complete(req)
        self._cost_meter.add(model_id, resp.usage)
        return resp

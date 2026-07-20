# src/orchestrator/providers/fake.py
from __future__ import annotations

from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    TextBlock,
    Usage,
)


class FakeProvider:
    def __init__(
        self,
        responses: list[CanonicalResponse] | None = None,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._responses: list[CanonicalResponse] = list(responses) if responses else []
        self._index = 0

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp

        last_text = ""
        if req.messages:
            last = req.messages[-1]
            last_text = "".join(
                block.text for block in last.content if isinstance(block, TextBlock)
            )
        return CanonicalResponse(
            content=[TextBlock(text=last_text)],
            usage=Usage(prompt_tokens=0, completion_tokens=0),
            model=self.name,
            stop_reason="end_turn",
            latency_ms=0,
        )

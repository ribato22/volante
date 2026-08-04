"""One call, with the router still choosing the model.

Volante's own eval says decomposition does not pay: it ties a single call on the
nine-goal suite for 7.7x the money, the reproduced difference on the goal built to give
it headroom is -0.021, and in the regime where one response physically cannot fit the
answer it ties at best. The largest measured lever by a factor of three is which model
answers. So this path keeps the router and drops the rest — and these tests pin the
contract it must not break while doing so.
"""

from __future__ import annotations

import pytest

from volante.cost import CostMeter
from volante.direct import answer_directly
from volante.projector import Projector
from volante.providers.base import ProviderError
from volante.registry import Registry
from volante.router import Router
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    TextBlock,
    Usage,
)


def _model(mid: str, tier: int = 3) -> ModelInfo:
    return ModelInfo(
        id=mid,
        provider="fake",
        strengths={"coding", "reasoning"},
        context_window=8000,
        max_output_tokens=1000,
        supports_tools=False,
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        tier=tier,
    )


class _Provider:
    def __init__(self, name: str, reply: str | Exception) -> None:
        self.name = name
        self._reply = reply
        self.calls = 0
        self.last: CanonicalRequest | None = None

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        self.last = req
        if isinstance(self._reply, Exception):
            raise self._reply
        return CanonicalResponse(
            content=[TextBlock(text=self._reply)],
            usage=Usage(prompt_tokens=3, completion_tokens=5),
            model=self.name,
            stop_reason="end_turn",
            latency_ms=1,
        )


async def _run(providers: dict[str, _Provider], models: list[ModelInfo]):
    registry = Registry(models)
    return await answer_directly(
        "write a slug function",
        Router(registry),
        Projector(registry),
        providers,  # type: ignore[arg-type]
        registry,
        CostMeter(),
    )


async def test_one_model_call_and_the_answer_comes_back() -> None:
    p = _Provider("m", "```python\ndef slug(s): return s\n```")

    result = await _run({"m": p}, [_model("m")])

    assert result.status == "success"
    assert p.calls == 1, "a direct answer is ONE call; more means the path leaked"
    assert "def slug" in result.final


async def test_the_router_still_chooses_and_the_choice_is_recorded() -> None:
    """The router is the half that pays — dropping the trace with the plan would
    remove the one thing this path is keeping."""
    weak, strong = _Provider("weak", "x"), _Provider("strong", "answer")

    result = await _run(
        {"weak": weak, "strong": strong}, [_model("weak", 1), _model("strong", 4)]
    )

    trace = result.routing_decisions["__direct__"]
    assert trace["executed_model_id"] == "strong"
    assert trace["selected_model_id"] == "strong"
    assert weak.calls == 0


async def test_a_failing_model_falls_over_to_the_next_ranked_one() -> None:
    boom = _Provider("strong", ProviderError("upstream down", retryable=False))
    ok = _Provider("weak", "recovered")

    result = await _run(
        {"strong": boom, "weak": ok}, [_model("strong", 4), _model("weak", 1)]
    )

    assert result.status == "success"
    assert result.final == "recovered"
    assert result.routing_decisions["__direct__"]["fallback_events"][0][
        "reason"
    ] == "provider_unavailable"


async def test_a_blank_answer_is_a_candidate_failure_not_an_answer() -> None:
    """Same rule as every other phase: a model that terminates with no text has not
    answered, and the next ranked candidate gets its turn."""
    blank = _Provider("strong", "   ")
    ok = _Provider("weak", "real answer")

    result = await _run(
        {"strong": blank, "weak": ok}, [_model("strong", 4), _model("weak", 1)]
    )

    assert result.final == "real answer"
    assert result.routing_decisions["__direct__"]["fallback_events"][0][
        "reason"
    ] == "invalid_output"


async def test_every_model_failing_returns_a_structured_failure() -> None:
    boom = _Provider("m", ProviderError("nope", retryable=False))

    result = await _run({"m": boom}, [_model("m")])

    assert result.status == "failed"
    assert result.final is None
    assert result.error_message is not None


async def test_cost_is_metered_exactly_as_the_orchestrated_path_does() -> None:
    """A user comparing the two paths compares these numbers; if direct under-reported
    its cost the comparison this whole change rests on would be rigged."""
    p = _Provider("m", "answer")

    result = await _run({"m": p}, [_model("m")])

    assert result.usage_total["m"].completion_tokens == 5
    assert result.duration_ms >= 0


async def test_an_empty_goal_is_rejected_before_any_call() -> None:
    p = _Provider("m", "answer")
    registry = Registry([_model("m")])

    with pytest.raises(ValueError):
        await answer_directly(
            "   ",
            Router(registry),
            Projector(registry),
            {"m": p},  # type: ignore[arg-type]
            registry,
            CostMeter(),
        )
    assert p.calls == 0

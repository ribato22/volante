"""Unit tests for the MCP server's engine-facing logic (no `mcp` dep needed)."""

from __future__ import annotations

import pytest
from volante_mcp.server import _default_runtime_factory, format_result, run_goal


class _FakeResult:
    def __init__(self, **kw):
        self.status = kw.get("status", "success")
        self.final = kw.get("final", "hello world")
        self.failed_task = kw.get("failed_task")
        self.cost_usd = kw.get("cost_usd", 0.25)
        self.billed_usd = kw.get("billed_usd", 0.0)
        self.credit_usd = kw.get("credit_usd", 0.25)
        self.duration_ms = kw.get("duration_ms", 61569)
        self.error_code = kw.get("error_code")
        self.error_message = kw.get("error_message")
        self.subscription_calls = kw.get("subscription_calls", 2)
        self.routing_decisions = kw.get(
            "routing_decisions",
            {
                "research": {
                    "selected_model_id": "provider/strong",
                    "executed_model_id": "provider/strong",
                    "objective": "quality",
                }
            },
        )


class _FakeRuntime:
    def __init__(self, res: _FakeResult) -> None:
        self._res = res
        self.seen_goal: str | None = None

    async def aexecute(self, goal, on_text=None, on_worker_text=None):
        self.seen_goal = goal
        return self._res


async def test_run_goal_maps_runresult_to_dict() -> None:
    res = _FakeResult()
    out = await run_goal("Summarize X", runtime_factory=lambda: _FakeRuntime(res))
    assert out["status"] == "success"
    assert out["final"] == "hello world"
    assert out["billed_usd"] == 0.0
    assert out["credit_usd"] == 0.25
    assert out["duration_ms"] == 61569
    assert out["subscription_calls"] == 2
    assert out["routing_decisions"]["research"]["selected_model_id"] == "provider/strong"


async def test_run_goal_trims_and_forwards_goal() -> None:
    rt = _FakeRuntime(_FakeResult())
    await run_goal("  do the thing  ", runtime_factory=lambda: rt)
    assert rt.seen_goal == "do the thing"


async def test_run_goal_rejects_empty_goal() -> None:
    with pytest.raises(ValueError):
        await run_goal("   ", runtime_factory=lambda: _FakeRuntime(_FakeResult()))


def test_format_result_success_has_answer_and_ledger() -> None:
    s = format_result(
        {
            "status": "success",
            "final": "the answer",
            "billed_usd": 0.0,
            "credit_usd": 0.25,
            "duration_ms": 61569,
            "subscription_calls": 2,
            "routing_decisions": {
                "research": {
                    "selected_model_id": "provider/strong",
                    "executed_model_id": "provider/strong",
                    "objective": "quality",
                }
            },
        }
    )
    assert "the answer" in s
    assert "cash $0.000000" in s
    assert "plan credit $0.250000" in s
    assert "subscription calls 2" in s
    assert "research: provider/strong (quality)" in s
    assert "61.6s" in s


def test_format_result_failure_notes_failed_task() -> None:
    s = format_result(
        {
            "status": "failed",
            "final": None,
            "failed_task": "research",
            "billed_usd": 0.0,
            "credit_usd": 0.0,
            "duration_ms": 10,
            "error_code": "provider_error",
            "error_message": "provider unavailable",
        }
    )
    assert "failed" in s.lower()
    assert "research" in s
    assert "provider unavailable" in s


async def test_default_factory_uses_shared_verified_bootstrap(monkeypatch) -> None:
    sentinel = lambda: _FakeRuntime(_FakeResult())  # noqa: E731
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "volante.bootstrap.build_providers_from_env",
        lambda **kwargs: ("registry", "providers", "baseline"),
    )

    async def _verified(registry, providers, model_id, *, prefer):
        seen.update(
            registry=registry,
            providers=providers,
            model_id=model_id,
            prefer=prefer,
        )
        return sentinel

    monkeypatch.setattr("volante.bootstrap.make_verified_runtime_factory", _verified)
    assert await _default_runtime_factory("cash_protect_quota") is sentinel
    assert seen == {
        "registry": "registry",
        "providers": "providers",
        "model_id": "baseline",
        "prefer": "cash_protect_quota",
    }


def test_format_result_marks_estimated_amounts() -> None:
    # volante_run returns ONLY this text, so the footer is the calling agent's sole cost
    # signal: both amounts must be marked (the flag is run-wide) and the caveat spelled
    # out, rather than a bare marker attached to one ledger.
    marked = format_result(
        {
            "status": "success", "final": "answer", "billed_usd": 0.42,
            "credit_usd": 0.0, "duration_ms": 1000, "subscription_calls": 0,
            "cost_estimated": True,
        }
    )
    assert "cash ~$0.420000" in marked
    assert "plan credit ~$0.000000" in marked
    assert "estimated" in marked


def test_format_result_leaves_authoritative_amounts_unmarked() -> None:
    plain = format_result(
        {
            "status": "success", "final": "answer", "billed_usd": 0.42,
            "credit_usd": 0.0, "duration_ms": 1000, "subscription_calls": 0,
            "cost_estimated": False,
        }
    )
    assert "cash $0.420000" in plain
    assert "~" not in plain
    assert "estimated" not in plain


def test_format_result_surfaces_a_capability_notice() -> None:
    # The IDE agent never sees this server's stderr; without this line a run whose code
    # execution was disabled is indistinguishable from a full-capability one.
    text = format_result(
        {
            "status": "success", "final": "answer", "billed_usd": 0.0,
            "credit_usd": 0.0, "duration_ms": 10, "subscription_calls": 0,
            "capability_notice": "Code execution (run_python) is disabled: no Docker daemon.",
        }
    )
    assert "notice: Code execution (run_python) is disabled" in text


def test_format_result_omits_the_notice_when_fully_capable() -> None:
    text = format_result(
        {
            "status": "success", "final": "answer", "billed_usd": 0.0,
            "credit_usd": 0.0, "duration_ms": 10, "subscription_calls": 0,
        }
    )
    assert "notice:" not in text

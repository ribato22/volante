from __future__ import annotations

import pytest

import baton.cli as cli
from baton.cost import CostMeter
from baton.providers.base import ProviderError
from baton.providers.fake import FakeProvider
from baton.registry import Registry
from baton.runtime import Runtime
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    Task,
    TextBlock,
    Usage,
    text,
)
from baton.worker import Worker


def test_parse_args_defaults() -> None:
    args = cli._parse_args(["build a thing"])
    assert args.goal == "build a thing"
    assert args.prefer == "cash_protect_quota"  # §7.2 CLI default objective
    assert args.provider is None
    assert args.model is None
    assert args.json is False
    assert args.no_stream is False


def test_parse_args_all_flags() -> None:
    args = cli._parse_args(
        ["g", "--prefer", "local", "-P", "ollama", "--model", "m1", "--json", "--no-stream"]
    )
    assert args.prefer == "local"
    assert args.provider == "ollama"
    assert args.model == "m1"
    assert args.json is True
    assert args.no_stream is True


def test_parse_args_rejects_unknown_prefer() -> None:
    with pytest.raises(SystemExit):  # argparse choices guard
        cli._parse_args(["g", "--prefer", "bogus"])


# ---- FakeProvider-backed real Runtime (stubs mirror tests/test_runtime.py) ----
def _resp(txt: str, model: str) -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=txt)],
        usage=Usage(prompt_tokens=1000, completion_tokens=1000),
        model=model,
        stop_reason="end_turn",
        latency_ms=1,
    )


def _model(model_id: str, *, billing: str = "card") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="fake",
        strengths={"coding"},
        context_window=100_000,
        max_output_tokens=4_096,
        supports_tools=False,
        cost_per_1k_in=0.001,
        cost_per_1k_out=0.002,
        billing=billing,  # Phase 2 field; default "card"
    )


class _StubSupervisor:
    def __init__(self, plan: list[Task]) -> None:
        self._plan = plan

    async def plan(self, goal: str, on_text=None) -> list[Task]:
        if on_text is not None:
            on_text("[plan]")
        return list(self._plan)


class _StubRouter:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def route(self, task: Task) -> str:
        return self._mapping[task.id]

    def route_ranked(self, task: Task) -> list[str]:  # Phase 5 runtime calls this
        return [self._mapping[task.id]]


class _StubProjector:
    def project(self, task: Task, model_id: str, bb: object) -> CanonicalRequest:
        return CanonicalRequest(messages=[text("user", task.description)], max_tokens=64)


class _StubSynthesizer:
    async def synthesize(self, goal: str, bb: object, on_text=None) -> str:
        if on_text is not None:
            on_text("[synth]")
        return "FINAL ANSWER"


class _Raiser:
    def __init__(self, name: str, err: Exception) -> None:
        self.name = name
        self._err = err

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        raise self._err


def _runtime(registry, providers, mapping, plan) -> Runtime:
    cm = CostMeter()
    return Runtime(
        _StubSupervisor(plan),
        _StubRouter(mapping),
        _StubProjector(),
        Worker(providers=providers, cost_meter=cm),
        _StubSynthesizer(),
        registry,
        cm,
    )


def _one_task_runtime(*, billing: str = "card"):
    mid = "m1"
    registry = Registry([_model(mid, billing=billing)])
    providers = {mid: FakeProvider(responses=[_resp("art-1", mid)], name=mid)}
    plan = [Task(id="T1", description="do one", type="code", mode="one_shot")]
    return registry, _runtime(registry, providers, {"T1": mid}, plan)


def test_main_no_stream_success_returns_0(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime()
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    code = cli.main(["do one", "--no-stream"])

    assert code == 0
    out = capsys.readouterr().out
    assert "status: success" in out
    # 1000/1000 tokens x (0.001 in + 0.002 out) = 0.003, all on a "card" model.
    assert "billed_usd: $0.003000" in out
    assert "credit_usd: $0.000000" in out
    assert "duration_ms:" in out


def test_main_failed_status_returns_1(monkeypatch, capsys) -> None:
    mid = "m1"
    registry = Registry([_model(mid)])
    providers = {mid: _Raiser(mid, ProviderError("bad", retryable=False, status=400))}
    plan = [Task(id="T1", description="do one", type="code", mode="one_shot")]
    runtime = _runtime(registry, providers, {"T1": mid}, plan)
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    code = cli.main(["do one", "--no-stream"])

    assert code == 1
    out = capsys.readouterr().out
    assert "status: failed" in out
    assert "failed_task: T1" in out


def test_main_streams_plan_worker_and_synth(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime()
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    code = cli.main(["do one"])  # streaming ON by default (no --no-stream)

    assert code == 0
    out = capsys.readouterr().out
    assert "[plan]" in out  # supervisor.plan streamed via on_text
    assert "[T1] art-1" in out  # worker delta labeled by task_id
    assert "[synth]" in out  # synthesizer streamed via on_text

from __future__ import annotations

import json
import tomllib
from pathlib import Path

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


def test_summary_shows_zero_subscription_calls_for_card(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime()  # billing="card"
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    cli.main(["do one", "--no-stream"])

    assert "subscription_calls: 0" in capsys.readouterr().out


def test_summary_counts_plan_included_and_records_credit(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime(billing="plan_included")
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    cli.main(["do one", "--no-stream"])

    out = capsys.readouterr().out
    assert "subscription_calls: 1" in out
    # Honesty invariant (§5.3): subscription-only run bills $0 cash, records credit.
    assert "billed_usd: $0.000000" in out
    assert "credit_usd: $0.003000" in out


def test_main_json_summary(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime()
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    # NOTE: no --no-stream here -- streaming defaults ON, but --json must suppress it
    # on its own (JSON mode = machine mode: exactly one parseable line, no deltas).
    code = cli.main(["do one", "--json"])

    assert code == 0
    out = capsys.readouterr().out
    lines = out.strip("\n").splitlines()
    assert len(lines) == 1  # exactly one line: no "[plan]"/"[T1] art-1"/"[synth]" deltas
    payload = json.loads(lines[0])
    assert payload["status"] == "success"
    assert payload["billed_usd"] == pytest.approx(0.003)
    assert payload["credit_usd"] == pytest.approx(0.0)
    assert payload["subscription_calls"] == 0
    assert payload["final"] == "FINAL ANSWER"


class _InterruptRuntime:
    """Emits some streamed text, then a Ctrl-C mid-run (KeyboardInterrupt)."""

    async def aexecute(self, goal, on_text=None, on_worker_text=None):
        if on_text is not None:
            on_text("partial plan so far")
        raise KeyboardInterrupt


def test_main_keyboard_interrupt_prints_partial_and_returns_130(monkeypatch, capsys) -> None:
    registry = Registry([])
    monkeypatch.setattr(cli, "_build", lambda args: (registry, _InterruptRuntime()))

    code = cli.main(["do one"])  # streaming ON so the partial is collected

    assert code == 130  # 128 + SIGINT
    out = capsys.readouterr().out
    assert "partial plan so far" in out  # streamed before the interrupt
    assert "[interrupted]" in out


class _RaisingRuntime:
    """Simulates Supervisor.plan/Synthesizer.synthesize failures (bad API key,
    network error, or a real card planner returning an unparseable plan) escaping
    Runtime.aexecute unhandled -- main() must not let this raise a raw traceback."""

    async def aexecute(self, goal, on_text=None, on_worker_text=None):
        raise ProviderError("bad api key", retryable=False, status=401)


def test_main_planner_provider_error_returns_nonzero_no_traceback(monkeypatch, capsys) -> None:
    registry = Registry([])
    monkeypatch.setattr(cli, "_build", lambda args: (registry, _RaisingRuntime()))

    code = cli.main(["do one", "--no-stream"])

    assert code != 0
    err = capsys.readouterr().err
    assert "ProviderError" in err
    assert "bad api key" in err


def test_main_keyboard_interrupt_during_build_returns_130(monkeypatch, capsys) -> None:
    # _build (registry/provider wiring + the §7.1 planner-gate probe) can itself run
    # a live provider call; Ctrl-C there must also exit 130, never a traceback.
    def _raise_build(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_build", _raise_build)

    code = cli.main(["do one"])

    assert code == 130
    assert "[interrupted]" in capsys.readouterr().out


# ---- §7.1 guard: subscription planner must pass the live parse-plan gate ----
def test_ensure_planner_gate_skips_probe_for_card_planner() -> None:
    registry = Registry([_model("m1", billing="card")])
    # A provider that raises if called at all -- the gate must not probe a card planner.
    providers = {"m1": _Raiser("m1", ProviderError("must not be called", retryable=False))}

    cli._ensure_planner_gate(registry, providers, "m1")  # no raise


def test_ensure_planner_gate_passes_for_subscription_planner_with_valid_plan() -> None:
    registry = Registry([_model("m1", billing="plan_included")])
    valid = '[{"id":"t1","description":"d","type":"code","mode":"one_shot","depends_on":[]}]'
    providers = {"m1": FakeProvider(responses=[_resp(valid, "m1")], name="m1")}

    cli._ensure_planner_gate(registry, providers, "m1")  # no raise


def test_ensure_planner_gate_raises_for_subscription_planner_that_fails_gate() -> None:
    registry = Registry([_model("m1", billing="plan_included")])
    providers = {"m1": FakeProvider(responses=[_resp("not json", "m1")], name="m1")}

    with pytest.raises(RuntimeError, match="parse-plan gate"):
        cli._ensure_planner_gate(registry, providers, "m1")


def test_console_script_declared() -> None:
    root = Path(__file__).resolve().parents[1]  # repo root (tests/ -> ..)
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["baton"] == "baton.cli:main"
    from baton.cli import main

    assert callable(main)

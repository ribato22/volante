from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import volante.cli as cli
from volante import __version__
from volante.cost import CostMeter
from volante.providers.base import ProviderError
from volante.providers.fake import FakeProvider
from volante.registry import Registry
from volante.runtime import Runtime
from volante.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    RunResult,
    Task,
    TextBlock,
    Usage,
    text,
)
from volante.worker import Worker


def test_parse_args_defaults() -> None:
    args = cli._parse_args(["build a thing"])
    assert args.goal == "build a thing"
    assert args.list_models is False
    assert args.prefer == "quality"  # CLI default objective: strongest capable model
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


def test_parse_args_list_models_needs_no_goal() -> None:
    args = cli._parse_args(["--list-models", "--json"])
    assert args.goal is None
    assert args.list_models is True
    assert args.json is True


def test_parse_args_rejects_unknown_prefer() -> None:
    with pytest.raises(SystemExit):  # argparse choices guard
        cli._parse_args(["g", "--prefer", "bogus"])


def test_version_flag_registered_and_exits_cleanly(capsys: pytest.CaptureFixture) -> None:
    # `action="version"` prints to stdout and raises SystemExit(0) -- verify both,
    # without needing a fully wired provider/runtime (this must work even with
    # zero configured providers, since it's an argparse-level action).
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_args(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_version_flag_exits_zero(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


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


def test_build_uses_shared_verified_bootstrap(monkeypatch) -> None:
    registry, runtime = _one_task_runtime()
    providers = {"m1": object()}
    monkeypatch.setattr(
        cli,
        "build_providers_from_env",
        lambda **kwargs: (registry, providers, "m1"),
    )
    seen: dict[str, object] = {}

    async def _verified(got_registry, got_providers, model_id, *, prefer):
        seen.update(
            registry=got_registry,
            providers=got_providers,
            model_id=model_id,
            prefer=prefer,
        )
        return lambda: runtime

    monkeypatch.setattr(cli, "make_verified_runtime_factory", _verified)
    got_registry, got_runtime = cli._build(cli._parse_args(["do one"]))
    assert got_registry is registry
    assert got_runtime is runtime
    assert seen == {
        "registry": registry,
        "providers": providers,
        "model_id": "m1",
        "prefer": "quality",
    }


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
    assert "route[T1]:" not in out  # legacy stub router has no decision API


def test_main_lists_full_model_inventory_without_running_gate(
    monkeypatch, capsys
) -> None:
    models = [
        _model("provider/a"),
        ModelInfo(
            id="local/b",
            provider="ollama",
            strengths={"coding", "reasoning"},
            context_window=16_384,
            max_output_tokens=2_048,
            supports_tools=False,
            cost_per_1k_in=0,
            cost_per_1k_out=0,
            tier=2,
        ),
    ]
    registry = Registry(models)
    monkeypatch.setattr(
        cli,
        "build_providers_from_env",
        lambda **kwargs: (registry, {model.id: object() for model in models}, models[0].id),
    )

    code = cli.main(["--list-models", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert [item["id"] for item in payload["models"]] == ["provider/a", "local/b"]


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


def test_summary_shows_zero_subscription_models_for_card(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime()  # billing="card"
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    cli.main(["do one", "--no-stream"])

    assert "subscription_models: 0" in capsys.readouterr().out


def test_summary_counts_plan_included_and_records_credit(monkeypatch, capsys) -> None:
    registry, runtime = _one_task_runtime(billing="plan_included")
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))

    cli.main(["do one", "--no-stream"])

    out = capsys.readouterr().out
    # Honest label: DISTINCT subscription-billed models seen, not a call count
    # (4 calls to one claude-code model would still report 1 here).
    assert "subscription_models: 1" in out
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
    assert payload["subscription_models"] == 0
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


class _BrokenStdout:
    """Simulates a reader that closed the pipe early (`volante goal | head`)."""

    def write(self, s: str) -> int:
        raise BrokenPipeError()

    def flush(self) -> None:
        raise BrokenPipeError()

    def fileno(self) -> int:
        raise OSError("no real fd under test")


def test_main_broken_pipe_exits_cleanly_without_raising(monkeypatch) -> None:
    registry, runtime = _one_task_runtime()
    monkeypatch.setattr(cli, "_build", lambda args: (registry, runtime))
    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout())

    code = cli.main(["do one", "--no-stream"])  # must not raise BrokenPipeError

    assert code == 0


def test_main_keyboard_interrupt_during_build_returns_130(monkeypatch, capsys) -> None:
    # _build (registry/provider wiring + the §7.1 planner-gate probe) can itself run
    # a live provider call; Ctrl-C there must also exit 130, never a traceback.
    def _raise_build(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_build", _raise_build)

    code = cli.main(["do one"])

    assert code == 130
    assert "[interrupted]" in capsys.readouterr().out


def test_console_script_declared() -> None:
    root = Path(__file__).resolve().parents[1]  # repo root (tests/ -> ..)
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["volante"] == "volante.cli:main"
    from volante.cli import main

    assert callable(main)


def test_parse_args_usage_needs_no_goal() -> None:
    args = cli._parse_args(["--usage"])
    assert args.goal is None
    assert args.usage is True


def test_main_usage_reports_disabled_ledger(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("VOLANTE_USAGE_LOG", "")
    assert cli.main(["--usage"]) == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_main_usage_lists_recorded_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
) -> None:
    from volante.observability import record_run

    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    record_run({"status": "success", "billed_usd": 0.02}, goal="teach me routing",
               prefer="quality", source="mcp")
    assert cli.main(["--usage"]) == 0
    out = capsys.readouterr().out
    assert "teach me routing" in out
    assert "[mcp]" in out


def test_main_usage_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
) -> None:
    from volante.observability import record_run

    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    record_run({"status": "success"}, goal="g", prefer="quality", source="cli")
    assert cli.main(["--usage", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total_runs"] == 1
    assert payload["runs"][0]["goal"] == "g"


def _result(*, estimated: bool) -> RunResult:
    return RunResult(
        status="success",
        final="hi",
        partial_artifacts={},
        failed_task=None,
        cost_usd=0.003,
        duration_ms=1200,
        billed_usd=0.003,
        credit_usd=0.0,
        cost_estimated=estimated,
    )


def _card_registry() -> Registry:
    return Registry(
        [
            ModelInfo(
                id="m",
                provider="fake",
                strengths={"coding"},
                context_window=1000,
                max_output_tokens=100,
                supports_tools=False,
                cost_per_1k_in=1.0,
                cost_per_1k_out=1.0,
                billing="card",
                tier=3,
            )
        ]
    )


def test_summary_marks_estimated_costs() -> None:
    # The estimate caveat must travel with the dollar figures, not just exist on the
    # RunResult: a reader of the summary would otherwise treat inferred amounts as
    # provider-reported.
    registry = _card_registry()
    marked = cli._summary_lines(_result(estimated=True), registry)[1]
    plain = cli._summary_lines(_result(estimated=False), registry)[1]
    assert "estimated" in marked
    assert "estimated" not in plain


def test_summary_json_exposes_the_estimate_flag() -> None:
    registry = _card_registry()
    marked = json.loads(cli._summary_json(_result(estimated=True), registry))
    plain = json.loads(cli._summary_json(_result(estimated=False), registry))
    assert marked["cost_estimated"] is True
    assert plain["cost_estimated"] is False


def test_usage_listing_marks_estimated_rows_and_totals(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    ledger = tmp_path / "usage.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "ts": "2026-07-27T00:00:00Z", "source": "mcp", "status": "success",
                "goal": "g", "billed_usd": 4.0, "credit_usd": 1.0, "duration_ms": 10,
                "subscription_calls": 0, "cost_estimated": True,
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-07-27T00:00:01Z", "source": "cli", "status": "success",
                "goal": "h", "billed_usd": 0.4, "credit_usd": 0.1, "duration_ms": 10,
                "subscription_calls": 0, "cost_estimated": False,
            }
        )
        + "\n"
    )
    monkeypatch.setenv("VOLANTE_USAGE_LOG", str(ledger))

    assert cli.main(["--usage"]) == 0

    out = capsys.readouterr().out
    assert "cash ~$4.0000" in out  # estimated row is marked
    assert "cash  $0.4000" in out  # authoritative row is not
    assert "of which estimated: 1 run(s)" in out  # the aggregate says so too

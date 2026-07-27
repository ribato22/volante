from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from volante.agent import AgenticWorker
from volante.blackboard import Blackboard
from volante.cost import CostMeter
from volante.projector import Projector
from volante.providers.base import IncompleteOutputError, LLMProvider, ProviderError
from volante.registry import Registry
from volante.router import NoEligibleModelError, Router
from volante.supervisor import Supervisor, validate_plan
from volante.synthesizer import Synthesizer
from volante.tools.base import ToolRegistry
from volante.tools.run_python import RunPythonTool
from volante.tools.sandbox import Sandbox, sandbox_for
from volante.types import (
    TASK_TOOL_NAMES,
    CapabilityUnavailableError,
    ContentBlock,
    Entry,
    InvalidGoalError,
    RunResult,
    Task,
    TextBlock,
    effective_required_tools,
    validate_goal,
)
from volante.worker import Worker

logger = logging.getLogger(__name__)

# billing yang dihitung sebagai kuota langganan (bukan cash) untuk guard per-run.
_SUBSCRIPTION_BILLING = frozenset({"plan_included", "plan_credit"})


def _safe_task_workspace(runs_dir: Path, run_id: str, task_id: str) -> Path:
    """Resolve a task workspace and prove it remains below this run's root."""

    run_root = (runs_dir / run_id).resolve()
    candidate = (run_root / task_id).resolve()
    try:
        relative = candidate.relative_to(run_root)
    except ValueError as exc:
        raise ValueError(
            f"unsafe task workspace for task {task_id!r}: path escapes run root"
        ) from exc
    if not relative.parts:
        raise ValueError(
            f"unsafe task workspace for task {task_id!r}: path equals run root"
        )
    return candidate


def _text_of(content: list[ContentBlock]) -> str:
    return "".join(b.text for b in content if isinstance(b, TextBlock))


def _task_cb(
    on_worker_text: Callable[[str, str], object] | None, task_id: str
) -> Callable[[str], object] | None:
    """Bungkus `on_worker_text(task_id, delta)` menjadi `on_text(delta)` ber-label,
    supaya stream worker paralel bisa diurai per-task oleh konsumen. None -> None
    (tak streaming). Nilai kembalian diteruskan (truthy = early-stop, konsisten
    dengan kontrak on_text). Caveat: seperti streaming ber-retry lain, kegagalan
    retryable di tengah stream akan memancarkan ulang teks parsial (lihat AgenticWorker)."""
    if on_worker_text is None:
        return None

    def cb(delta: str) -> object:
        return on_worker_text(task_id, delta)

    return cb


class Runtime:
    def __init__(
        self,
        supervisor: Supervisor,
        router: Router,
        projector: Projector,
        worker: Worker,
        synthesizer: Synthesizer,
        registry: Registry,
        cost_meter: CostMeter,
        max_retries: int = 2,
        call_timeout: float = 120.0,
        fan_out: int = 3,
        agentic_worker: AgenticWorker | None = None,
        sandbox_factory: Callable[[Path], Sandbox] | None = None,
        runs_dir: Path | None = None,
        agentic_timeout: float = 600.0,
        tools_factory: Callable[[Path], ToolRegistry] | None = None,
        planning_timeout: float = 300.0,
        synthesis_timeout: float = 180.0,
        initial_subscription_calls: int = 0,
        available_tool_names: set[str] | None = None,
        phase_providers: dict[str, LLMProvider] | None = None,
        planning_model_ids: list[str] | None = None,
        synthesis_model_ids: list[str] | None = None,
        capability_notice: str | None = None,
    ) -> None:
        for name, value in (
            ("call_timeout", call_timeout),
            ("agentic_timeout", agentic_timeout),
            ("planning_timeout", planning_timeout),
            ("synthesis_timeout", synthesis_timeout),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if isinstance(fan_out, bool) or not isinstance(fan_out, int) or fan_out < 1:
            raise ValueError("fan_out must be a positive integer")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer")
        if (
            isinstance(initial_subscription_calls, bool)
            or not isinstance(initial_subscription_calls, int)
            or initial_subscription_calls < 0
        ):
            raise ValueError("initial_subscription_calls must be a non-negative integer")
        self.supervisor = supervisor
        self.router = router
        self.projector = projector
        self.worker = worker
        self.synthesizer = synthesizer
        self.registry = registry
        self.cost_meter = cost_meter
        self.max_retries = max_retries
        self.call_timeout = call_timeout
        self.fan_out = fan_out
        self.agentic_worker = agentic_worker
        self.sandbox_factory = sandbox_factory
        self.runs_dir = Path(runs_dir) if runs_dir is not None else Path(".runs")
        self.agentic_timeout = agentic_timeout
        self.tools_factory = tools_factory
        self.planning_timeout = planning_timeout
        self.synthesis_timeout = synthesis_timeout
        self._sub_cap = 16
        self._initial_subscription_calls = initial_subscription_calls
        self._sub_calls = initial_subscription_calls
        if available_tool_names is None:
            # Default to what will ACTUALLY exist. Advertising run_python while the
            # sandbox policy would refuse to build it makes the planner spend a call on
            # a task that can only fail; the probe behind this is cached per process.
            from volante.tools.sandbox import resolve_sandbox_mode

            mode, _ = resolve_sandbox_mode()
            normalized_tool_names = set() if mode == "unavailable" else {"run_python"}
        else:
            normalized_tool_names = set(available_tool_names)
        if any(not isinstance(name, str) for name in normalized_tool_names):
            raise ValueError("available_tool_names must contain only strings")
        unknown_tool_names = normalized_tool_names - TASK_TOOL_NAMES
        if unknown_tool_names:
            raise ValueError(
                "available_tool_names contains unsupported tools: "
                f"{sorted(unknown_tool_names)}"
            )
        self.available_tool_names = normalized_tool_names
        # Carried into every RunResult so a degraded run is visible in-band.
        self.capability_notice = capability_notice
        self._task_errors: dict[str, tuple[str, str]] = {}
        self._routing_decisions: dict[str, dict[str, Any]] = {}
        self._agentic_has_physical_gate = False
        # A Runtime executes exactly one goal (see aexecute): Supervisor.plan is
        # non-re-entrant and the accounting/trace state below is per-run.
        self._used = False
        self._phase_providers = dict(phase_providers or {})
        self._planning_model_ids = self._validate_phase_models(
            "planning_model_ids", planning_model_ids
        )
        self._synthesis_model_ids = self._validate_phase_models(
            "synthesis_model_ids", synthesis_model_ids
        )
        if (
            self._planning_model_ids or self._synthesis_model_ids
        ) and not self._phase_providers:
            raise ValueError(
                "phase_providers is required when phase fallback models are configured"
            )
        for name, component, model_ids in (
            ("planning_model_ids", self.supervisor, self._planning_model_ids),
            ("synthesis_model_ids", self.synthesizer, self._synthesis_model_ids),
        ):
            component_model_id = getattr(component, "_model_id", None)
            if (
                model_ids
                and isinstance(component_model_id, str)
                and component_model_id != model_ids[0]
            ):
                raise ValueError(
                    f"{name}[0] must match the primary component model "
                    f"{component_model_id!r}"
                )

    def _validate_phase_models(
        self,
        name: str,
        model_ids: list[str] | None,
    ) -> tuple[str, ...]:
        if model_ids is None:
            return ()
        if not isinstance(model_ids, list) or not model_ids:
            raise ValueError(f"{name} must be a non-empty list")
        if any(not isinstance(model_id, str) or not model_id for model_id in model_ids):
            raise ValueError(f"{name} must contain non-empty strings")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError(f"{name} must not contain duplicate model ids")
        for model_id in model_ids:
            try:
                self.registry.get(model_id)
            except ValueError as exc:
                raise ValueError(f"{name} contains {model_id!r}: {exc}") from exc
            if model_id not in self._phase_providers:
                raise ValueError(
                    f"{name} contains {model_id!r} without a configured provider"
                )
        return tuple(model_ids)

    def _before_model_call(self, model_id: str) -> None:
        """Atomically reserve one physical subscription-provider attempt.

        The callback is synchronous by design: no await can interleave the
        check/increment pair while parallel tasks share this per-run counter.
        Direct/card calls pass through without consuming the subscription pool.
        """

        if not self._is_subscription(model_id):
            return
        if self._sub_calls >= self._sub_cap:
            raise ProviderError(
                f"subscription cap {self._sub_cap} reached "
                f"({self._sub_calls} physical calls)",
                retryable=False,
                quota_exhausted=True,
            )
        self._sub_calls += 1

    def _configure_model_component(
        self,
        component: object,
        model_id: str,
        *,
        planning: bool,
    ) -> None:
        setter = getattr(component, "set_call_gate", None)
        if callable(setter):
            setter(self._before_model_call)
        limits_setter = getattr(component, "set_model_limits", None)
        if callable(limits_setter):
            model = self.registry.get(model_id)
            limits_setter(
                context_window=model.context_window,
                max_output_tokens=model.max_output_tokens,
            )
        if planning:
            tool_setter = getattr(component, "set_available_tools", None)
            if callable(tool_setter):
                tool_setter(set(self.available_tool_names))

    def _configure_call_controls(self) -> None:
        for component, planning in (
            (self.supervisor, True),
            (self.synthesizer, False),
        ):
            model_id = getattr(component, "_model_id", None)
            if isinstance(model_id, str):
                try:
                    self._configure_model_component(
                        component,
                        model_id,
                        planning=planning,
                    )
                except ValueError:
                    # Custom composition with a Registry that does not know the
                    # component's private model id retains its historical behavior.
                    setter = getattr(component, "set_call_gate", None)
                    if callable(setter):
                        setter(self._before_model_call)
            else:
                setter = getattr(component, "set_call_gate", None)
                if callable(setter):
                    setter(self._before_model_call)
        self._agentic_has_physical_gate = False
        if self.agentic_worker is not None:
            setter = getattr(self.agentic_worker, "set_call_gate", None)
            if callable(setter):
                setter(self._before_model_call)
                self._agentic_has_physical_gate = True

    def _clear_call_controls(self) -> None:
        for component in (self.supervisor, self.synthesizer, self.agentic_worker):
            if component is None:
                continue
            setter = getattr(component, "set_call_gate", None)
            if callable(setter):
                setter(None)
        self._agentic_has_physical_gate = False

    def _initialize_phase_trace(
        self,
        phase: str,
        model_ids: tuple[str, ...],
    ) -> str:
        task_id = f"__{phase}__"
        self._routing_decisions[task_id] = {
            "task_id": task_id,
            "phase": phase,
            "objective": "quality",
            "selected_model_id": model_ids[0],
            "ranked_model_ids": list(model_ids),
            "executed_model_id": None,
            "fallback_events": [],
            "explanation": (
                f"Use configured {phase} primary {model_ids[0]!r}; "
                "fall back in predicted-quality order when that candidate "
                "cannot complete the phase."
            ),
            "caveats": [
                "Phase ordering is a deterministic prediction from configured "
                "metadata/profiles, not an empirical best-model guarantee.",
                "Bootstrap phase fallbacks are limited to deterministic card/API "
                "models; unverified subscription CLI planners are not added.",
            ],
        }
        return task_id

    @staticmethod
    def _phase_fallback_reason(
        error: Exception,
        *,
        phase: str,
    ) -> str | None:
        if isinstance(error, TimeoutError):
            return "provider_timeout"
        if phase == "planning" and isinstance(error, InvalidGoalError):
            return "context_unavailable"
        if phase == "planning" and isinstance(error, ValueError):
            return "invalid_plan"
        if isinstance(error, IncompleteOutputError):
            return error.error_code
        if isinstance(error, ProviderError):
            if error.quota_exhausted:
                return "quota_exhausted"
            if error.candidate_unavailable:
                return "candidate_unavailable"
            if error.provider_unavailable or error.retryable:
                return "provider_unavailable"
            if "empty" in str(error).lower():
                return "invalid_output"
        return None

    @staticmethod
    def _phase_attempt_timeout(
        deadline: float,
        candidates_left: int,
    ) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("phase deadline exhausted")
        # Reserve a fair share of the total phase deadline for every remaining
        # candidate, so one hung primary cannot consume the entire failover window.
        return remaining / candidates_left

    async def _plan_with_fallback(
        self,
        goal: str,
        on_text: Callable[[str], None] | None,
    ) -> list[Task]:
        if not self._planning_model_ids:
            plan = await asyncio.wait_for(
                self.supervisor.plan(goal, on_text),
                timeout=self.planning_timeout,
            )
            validate_plan(plan)
            return plan

        model_ids = self._planning_model_ids
        trace_id = self._initialize_phase_trace("planning", model_ids)
        deadline = asyncio.get_running_loop().time() + self.planning_timeout
        last_error: Exception | None = None
        for index, model_id in enumerate(model_ids):
            component: Supervisor
            if index == 0:
                component = self.supervisor
            else:
                component = Supervisor(
                    self._phase_providers[model_id],
                    model_id,
                    self.cost_meter,
                )
                self._configure_model_component(
                    component,
                    model_id,
                    planning=True,
                )
            try:
                timeout = self._phase_attempt_timeout(
                    deadline,
                    len(model_ids) - index,
                )
                plan = await asyncio.wait_for(
                    component.plan(goal, on_text),
                    timeout=timeout,
                )
                validate_plan(plan)
            except Exception as error:  # noqa: BLE001 - classified phase failover
                last_error = error
                reason = self._phase_fallback_reason(error, phase="planning")
                if reason is None or index + 1 >= len(model_ids):
                    raise
                self._remember_reroute(
                    trace_id,
                    model_id,
                    reason=reason,
                    message=str(error),
                )
                continue
            finally:
                if index > 0:
                    setter = getattr(component, "set_call_gate", None)
                    if callable(setter):
                        setter(None)
            self._remember_executed_model(trace_id, model_id)
            return plan
        assert last_error is not None
        raise last_error

    async def _synthesize_with_fallback(
        self,
        goal: str,
        bb: Blackboard,
        on_text: Callable[[str], None] | None,
    ) -> str:
        if not self._synthesis_model_ids:
            return await asyncio.wait_for(
                self.synthesizer.synthesize(goal, bb, on_text),
                timeout=self.synthesis_timeout,
            )

        model_ids = self._synthesis_model_ids
        trace_id = self._initialize_phase_trace("synthesis", model_ids)
        deadline = asyncio.get_running_loop().time() + self.synthesis_timeout
        last_error: Exception | None = None
        for index, model_id in enumerate(model_ids):
            component: Synthesizer
            if index == 0:
                component = self.synthesizer
            else:
                component = Synthesizer(
                    self._phase_providers[model_id],
                    model_id,
                    self.cost_meter,
                )
                self._configure_model_component(
                    component,
                    model_id,
                    planning=False,
                )
            try:
                timeout = self._phase_attempt_timeout(
                    deadline,
                    len(model_ids) - index,
                )
                final = await asyncio.wait_for(
                    component.synthesize(goal, bb, on_text),
                    timeout=timeout,
                )
            except Exception as error:  # noqa: BLE001 - classified phase failover
                last_error = error
                reason = self._phase_fallback_reason(error, phase="synthesis")
                if reason is None or index + 1 >= len(model_ids):
                    raise
                self._remember_reroute(
                    trace_id,
                    model_id,
                    reason=reason,
                    message=str(error),
                )
                continue
            finally:
                if index > 0:
                    setter = getattr(component, "set_call_gate", None)
                    if callable(setter):
                        setter(None)
            self._remember_executed_model(trace_id, model_id)
            return final
        assert last_error is not None
        raise last_error

    def _remember_task_error(
        self, task_id: str, error: Exception | None, *, code: str | None = None
    ) -> None:
        if error is None:
            self._task_errors[task_id] = (code or "task_failed", "task failed")
            return
        inferred = code
        if inferred is None:
            if isinstance(error, IncompleteOutputError):
                inferred = error.error_code
            elif isinstance(error, CapabilityUnavailableError):
                inferred = "capability_unavailable"
            elif isinstance(error, TimeoutError):
                inferred = "provider_timeout"
            elif isinstance(error, ProviderError) and error.quota_exhausted:
                inferred = "quota_exhausted"
            elif (
                isinstance(error, ProviderError)
                and error.candidate_unavailable
            ):
                inferred = "candidate_unavailable"
            elif (
                isinstance(error, ProviderError)
                and error.provider_unavailable
            ):
                inferred = "provider_unavailable"
            elif isinstance(error, ProviderError) and error.retryable:
                # This method is reached only after the runtime's retry policy has
                # been exhausted, so a still-retryable transport/5xx condition is
                # operationally an unavailable provider for this run.
                inferred = "provider_unavailable"
            elif isinstance(error, ProviderError):
                inferred = "provider_error"
            else:
                inferred = "task_error"
        self._task_errors[task_id] = (
            inferred,
            f"{type(error).__name__}: {error}",
        )

    @staticmethod
    def _routing_input_tokens(task: Task, bb: Blackboard) -> int:
        """Estimate untruncated goal/task/dependency context before routing."""

        artifacts = bb.current_artifacts()
        chars = len(bb.goal) + len(task.description) + 512
        for dependency in task.depends_on:
            if dependency in artifacts:
                chars += len(str(artifacts[dependency])) + len(dependency) + 16
        return max(256, (chars + 3) // 4)

    def _remember_executed_model(
        self,
        task_id: str,
        model_id: str,
        *,
        provider_reported_model_id: str | None = None,
    ) -> None:
        decision = self._routing_decisions.get(task_id)
        if decision is not None:
            # ``model_id`` is Volante's canonical registry/provider-map key. Provider
            # responses often report a different wire alias; treating that alias as
            # a reroute makes the audit trace false.
            decision["executed_model_id"] = model_id
            if provider_reported_model_id and provider_reported_model_id != model_id:
                decision["provider_reported_model_id"] = provider_reported_model_id

    def _remember_reroute(
        self,
        task_id: str,
        model_id: str,
        *,
        reason: str,
        message: str,
    ) -> None:
        decision = self._routing_decisions.get(task_id)
        if decision is None:
            return
        events = decision.setdefault("fallback_events", [])
        if isinstance(events, list):
            events.append(
                {
                    "model_id": model_id,
                    "reason": reason,
                    "message": message,
                }
            )

    @staticmethod
    def _serialize_routing_decision(decision: object) -> dict[str, Any]:
        serialized = decision.to_dict()  # type: ignore[attr-defined]
        candidates = serialized.get("candidates", ())
        rejected: dict[str, list[str]] = {}
        for candidate in candidates:
            if not candidate.get("eligible", False):
                rejected[str(candidate["model_id"])] = list(
                    candidate.get("rejection_reasons", ())
                )
        # Keep an explicit index in addition to the complete candidate records:
        # consumers can answer "why not model X?" without reverse-engineering.
        serialized["rejected_models"] = rejected
        return serialized

    async def _run_task(
        self,
        task: Task,
        bb: Blackboard,
        run_id: str,
        sem: asyncio.Semaphore,
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> bool:
        # Penjaga fail-fast: eksepsi TAK-terduga (KeyError provider di agent,
        # ValueError worker, OSError/FileNotFoundError sandbox, RuntimeError konfig)
        # TIDAK boleh lolos ke asyncio.gather. Kalau lolos: sibling se-wave jadi
        # orphan (subprocess/container bocor) dan tulisan cost/blackboard pasca-gagal
        # tetap terjadi. Di sini direkam sebagai status gagal (replayable) lalu
        # fail-fast lewat return False — bukan crash yang membuang state.
        model_id = "unknown"
        try:
            required_tools = effective_required_tools(task)
            unavailable_tools = required_tools - self.available_tool_names
            if unavailable_tools:
                raise CapabilityUnavailableError(
                    f"task {task.id!r} requires tools that are not enabled for "
                    f"this runtime: {sorted(unavailable_tools)}"
                )
            try:
                decide = getattr(self.router, "route_decision", None)
                if callable(decide):
                    if isinstance(self.router, Router):
                        decision = decide(
                            task,
                            estimated_input_tokens=self._routing_input_tokens(
                                task, bb
                            ),
                        )
                    else:
                        decision = decide(task)
                    model_ids = list(decision.ranked_model_ids)
                    self._routing_decisions[task.id] = (
                        self._serialize_routing_decision(decision)
                    )
                else:
                    model_ids = self.router.route_ranked(task)
            except NoEligibleModelError as exc:
                rejected = getattr(exc, "rejected", None)
                if isinstance(rejected, dict):
                    self._routing_decisions[task.id] = {
                        "task_id": task.id,
                        "selected_model_id": None,
                        "ranked_model_ids": [],
                        "rejected_models": {
                            str(model_id): list(reasons)
                            for model_id, reasons in rejected.items()
                        },
                        "explanation": str(exc),
                    }
                raise CapabilityUnavailableError(
                    f"no configured model satisfies task {task.id!r}: {exc}"
                ) from exc
            model_id = model_ids[0] if model_ids else "unknown"
            return await self._run_task_body(
                task, bb, run_id, sem, model_ids, on_worker_text
            )
        # ProviderError/TimeoutError are normally absorbed per-candidate inside
        # _run_task_body/_run_agentic (they return False on exhaustion), so they should
        # not surface here. But _run_task is gathered without a per-task except in
        # aexecute, so if one ever DID escape it must still convert to a recorded task
        # failure -> structured RunResult, never a raw exception out of aexecute. Letting
        # it fall through to the general handler below closes that contract at the source.
        except Exception as err:  # noqa: BLE001 - konversi jadi kegagalan tercatat
            self._remember_task_error(task.id, err)
            bb.append(
                Entry(
                    run_id=run_id,
                    task_id=task.id,
                    attempt=0,
                    kind="status",
                    payload=f"failed: {type(err).__name__}: {err}",
                    model_id=model_id,
                    usage=None,
                    timestamp=time.time(),
                )
            )
            return False

    async def _run_task_body(
        self,
        task: Task,
        bb: Blackboard,
        run_id: str,
        sem: asyncio.Semaphore,
        model_ids: list[str],
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> bool:
        # Jalur agentic: _run_agentic kini memiliki loop kandidatnya sendiri
        # (proyeksi per-kandidat di dalam) dan mengambil slot fan-out sendiri
        # (hindari akuisisi semaphore ganda -> deadlock).
        if task.mode == "agentic":
            return await self._run_agentic(task, bb, run_id, sem, model_ids, on_worker_text)

        # Stream one-shot ter-label task.id (output paralel terurai per-task).
        worker_cb = _task_cb(on_worker_text, task.id)
        last_err: Exception | None = None
        last_model = model_ids[0]
        async with sem:  # satu slot fan-out per task, membungkus loop kandidat+retry
            for candidate_index, model_id in enumerate(model_ids):
                last_model = model_id
                # RE-PROYEKSI WAJIB per kandidat: req lama ter-scope ke
                # context_window/max_output model sebelumnya -> overflow di model
                # lebih kecil (opus 200k -> kimi 128k -> llama 8k). Proyeksi ulang
                # menjaga budget input & req.max_tokens benar untuk kandidat ini.
                req = self.projector.project(task, model_id, bb)
                req.run_id = run_id
                req.task_id = task.id
                req.attempt = 0
                reroute = False
                reroute_reason: str | None = None
                # attempt 0..max_retries inklusif => (max_retries + 1) percobaan.
                for attempt in range(self.max_retries + 1):
                    req.attempt = attempt
                    try:
                        # Reserve immediately before every provider attempt, not
                        # once per task. Retries therefore consume real quota too.
                        self._before_model_call(model_id)
                        resp = await asyncio.wait_for(
                            self.worker.run_one_shot(req, model_id, worker_cb),
                            timeout=self.call_timeout,
                        )
                        if not _text_of(resp.content).strip():
                            raise ProviderError(
                                "worker model returned empty text output",
                                retryable=False,
                            )
                    except (ProviderError, TimeoutError) as err:
                        last_err = err
                        # Known model/provider unavailability advances immediately;
                        # depleted subscription quota likewise must not back off.
                        if isinstance(err, ProviderError) and (
                            err.quota_exhausted
                            or err.candidate_unavailable
                            or err.provider_unavailable
                        ):
                            reroute = True
                            reroute_reason = (
                                "quota_exhausted"
                                if err.quota_exhausted
                                else (
                                    "candidate_unavailable"
                                    if err.candidate_unavailable
                                    else "provider_unavailable"
                                )
                            )
                            break
                        # Retry di kandidat SAMA hanya untuk transien/timeout.
                        retryable = isinstance(err, TimeoutError) or (
                            isinstance(err, ProviderError) and err.retryable
                        )
                        if retryable and attempt < self.max_retries:
                            await asyncio.sleep(
                                0.5 * 2**attempt + random.uniform(0, 0.25)
                            )
                            continue
                        if retryable:
                            reroute = True
                            reroute_reason = (
                                "provider_timeout"
                                if isinstance(err, TimeoutError)
                                else "provider_unavailable"
                            )
                        break  # semantic/configuration failure -> fail fast
                    now = time.time()
                    bb.append(
                        Entry(
                            run_id=run_id,
                            task_id=task.id,
                            attempt=attempt,
                            kind="artifact",
                            payload=_text_of(resp.content),
                            model_id=model_id,
                            usage=resp.usage,
                            timestamp=now,
                        )
                    )
                    bb.append(
                        Entry(
                            run_id=run_id,
                            task_id=task.id,
                            attempt=attempt,
                            kind="status",
                            payload="success",
                            model_id=model_id,
                            usage=None,
                            timestamp=now,
                        )
                    )
                    self._remember_executed_model(
                        task.id,
                        model_id,
                        provider_reported_model_id=resp.model,
                    )
                    return True
                if reroute and candidate_index + 1 < len(model_ids):
                    assert last_err is not None
                    reason = reroute_reason or "provider_unavailable"
                    self._remember_reroute(
                        task.id,
                        model_id,
                        reason=reason,
                        message=str(last_err),
                    )
                    # Satu entry status ber-alasan per kandidat yang ditinggalkan
                    # (trace blackboard memperlihatkan jalannya reroute).
                    bb.append(
                        Entry(
                            run_id=run_id,
                            task_id=task.id,
                            attempt=req.attempt,
                            kind="status",
                            payload=f"reroute: {reason} on {model_id}: {last_err}",
                            model_id=model_id,
                            usage=None,
                            timestamp=time.time(),
                        )
                    )
                    continue  # coba kandidat berikutnya TANPA sleep
                # Semantic/request failures do not walk the remaining inventory:
                # repeating an invalid request across providers only burns budget.
                break
        # gagal final: rekam str(err) di entry status agar replayable.
        bb.append(
            Entry(
                run_id=run_id,
                task_id=task.id,
                attempt=req.attempt,
                kind="status",
                payload=f"failed: {last_err}",
                model_id=last_model,
                usage=None,
                timestamp=time.time(),
            )
        )
        self._remember_task_error(task.id, last_err)
        return False

    async def _run_agentic(
        self, task, bb, run_id: str, sem, model_ids: list[str], on_worker_text=None
    ) -> bool:
        if self.agentic_worker is None:
            raise RuntimeError(f"task {task.id} is agentic but no agentic_worker configured")
        workspace = _safe_task_workspace(self.runs_dir, run_id, task.id)
        if self.tools_factory is not None:
            tools = self.tools_factory(workspace)
        else:
            factory = self.sandbox_factory or sandbox_for
            sandbox = factory(workspace)
            tools = {"run_python": RunPythonTool(sandbox)}
        required_tools = effective_required_tools(task)
        missing_tools = required_tools - set(tools)
        if missing_tools:
            raise CapabilityUnavailableError(
                f"task {task.id!r} requires unavailable runtime tools: "
                f"{sorted(missing_tools)}"
            )
        worker_cb = _task_cb(on_worker_text, task.id)  # stream agentic ter-label
        last_err: Exception | None = None
        # Satu slot fan-out membungkus SEMUA kandidat (satu task = satu slot). Cap
        # per-provider (Phase 6) di luar lingkup follow-up ini.
        async with sem:
            for candidate_index, model_id in enumerate(model_ids):
                # RE-PROJEKSI WAJIB per kandidat: context_window/max_output berbeda
                # antar-model (opus 200k -> kimi 128k -> llama 8k); req kandidat lama
                # akan overflow di model lebih kecil bila tak diproyeksi ulang.
                req = self.projector.project(task, model_id, bb)
                req.run_id = run_id
                req.task_id = task.id
                req.attempt = 0
                try:
                    # The built-in AgenticWorker gates every physical turn/retry.
                    # Custom workers lacking that hook retain conservative legacy
                    # dispatch-level gating rather than bypassing the cap entirely.
                    if not self._agentic_has_physical_gate:
                        self._before_model_call(model_id)
                    if isinstance(self.agentic_worker, AgenticWorker):
                        execution = self.agentic_worker.run(
                            req,
                            model_id,
                            tools,
                            worker_cb,
                            required_tools=required_tools,
                        )
                    else:
                        execution = self.agentic_worker.run(
                            req, model_id, tools, worker_cb
                        )
                    res = await asyncio.wait_for(
                        execution, timeout=self.agentic_timeout
                    )
                except (ProviderError, TimeoutError) as err:
                    last_err = err
                    # Reroute mid-agentic me-RESTART task dari awal pada kandidat baru:
                    # turn parsial kandidat lama TIDAK direplay & tak dipersist sebagai
                    # TurnRecord (biayanya sudah ter-meter di cost_meter, side-effect
                    # workspace tetap ada). Keterbatasan ini DITERIMA & DIDOKUMENTASIKAN
                    # (§6.4) — dikerjakan setelah jalur one-shot stabil.
                    if (
                        candidate_index + 1 < len(model_ids)
                        and (
                            isinstance(err, TimeoutError)
                            or (
                                isinstance(err, ProviderError)
                                and (
                                    err.quota_exhausted
                                    or err.candidate_unavailable
                                    or err.provider_unavailable
                                    or err.retryable
                                )
                            )
                        )
                    ):
                        if isinstance(err, TimeoutError):
                            reason = "provider_timeout"
                        elif err.quota_exhausted:
                            reason = "quota_exhausted"
                        elif err.candidate_unavailable:
                            reason = "candidate_unavailable"
                        else:
                            reason = "provider_unavailable"
                        self._remember_reroute(
                            task.id,
                            model_id,
                            reason=reason,
                            message=str(err),
                        )
                        bb.append(
                            Entry(
                                run_id=run_id, task_id=task.id, attempt=0, kind="status",
                                payload=(
                                    f"reroute: {model_id} {reason} "
                                    f"({err}) -> next candidate"
                                ),
                                model_id=model_id, usage=None, timestamp=time.time(),
                            )
                        )
                        continue
                    # Semantic/request failures fail immediately. Replaying them
                    # across providers would only duplicate side effects and cost.
                    bb.append(
                        Entry(
                            run_id=run_id, task_id=task.id, attempt=0, kind="status",
                            payload=f"failed: {err}", model_id=model_id, usage=None,
                            timestamp=time.time(),
                        )
                    )
                    self._remember_task_error(task.id, err)
                    return False
                # sukses di kandidat ini: jejak per-turn + artifact + status.
                for tr in res.turns:
                    bb.append(
                        Entry(
                            run_id=run_id, task_id=task.id, attempt=tr.index, kind=tr.kind,
                            payload=tr.payload[:2000], model_id=tr.model_id, usage=tr.usage,
                            timestamp=time.time(),
                        )
                    )
                agg = res.usage_total.get(model_id)
                bb.append(
                    Entry(
                        run_id=run_id, task_id=task.id, attempt=0, kind="artifact",
                        payload=res.final_text, model_id=model_id, usage=agg,
                        timestamp=time.time(),
                    )
                )
                bb.append(
                    Entry(
                        run_id=run_id, task_id=task.id, attempt=0, kind="status",
                        payload="success", model_id=model_id, usage=None, timestamp=time.time(),
                    )
                )
                self._remember_executed_model(task.id, model_id)
                return True
        # Every viable candidate became unavailable -> fail with the last cause.
        bb.append(
            Entry(
                run_id=run_id, task_id=task.id, attempt=0, kind="status",
                payload=f"failed: {last_err}",
                model_id=model_ids[-1] if model_ids else "unknown",
                usage=None, timestamp=time.time(),
            )
        )
        self._remember_task_error(task.id, last_err)
        return False

    def _is_subscription(self, model_id: str) -> bool:
        # Model tak terdaftar (mis. Registry([]) di test) -> perlakukan sebagai
        # 'direct' agar guard tak salah-picu (nol regresi).
        try:
            billing = self.registry.get(model_id).billing
        except ValueError:
            return False
        return billing in _SUBSCRIPTION_BILLING

    def _cleanup_run_workspace(self, run_id: str) -> None:
        runs_root = self.runs_dir.resolve()
        lexical_path = runs_root / run_id
        try:
            if lexical_path.is_symlink():
                lexical_path.unlink()
                return
        except OSError:
            logger.exception("failed to remove run workspace symlink %s", lexical_path)
            return
        run_path = lexical_path.resolve()
        try:
            relative = run_path.relative_to(runs_root)
        except ValueError:
            logger.error("refusing to clean run path outside runs_dir: %s", run_path)
            return
        if len(relative.parts) != 1 or relative.name != run_id:
            logger.error("refusing to clean unexpected run path: %s", run_path)
            return
        try:
            if run_path.exists():
                shutil.rmtree(run_path)
        except OSError:
            logger.exception("failed to clean run workspace %s", run_path)

    @staticmethod
    def _phase_error(
        error: Exception, *, phase: str
    ) -> tuple[str, str]:
        if isinstance(error, TimeoutError):
            code = "phase_timeout"
        elif isinstance(error, InvalidGoalError):
            code = "invalid_goal"
        elif isinstance(error, IncompleteOutputError):
            code = error.error_code
        elif phase == "planning" and isinstance(error, ValueError):
            code = "invalid_plan"
        elif isinstance(error, ProviderError) and error.quota_exhausted:
            code = "quota_exhausted"
        elif isinstance(error, ProviderError) and error.candidate_unavailable:
            code = "candidate_unavailable"
        elif isinstance(error, ProviderError) and (
            error.provider_unavailable or error.retryable
        ):
            code = "provider_unavailable"
        elif isinstance(error, ProviderError):
            code = "provider_error"
        else:
            code = f"{phase}_error"
        return code, f"{type(error).__name__}: {error}"

    def _finalize(
        self,
        bb: Blackboard,
        started: float,
        *,
        status: str,
        final: str | None,
        failed_task: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> RunResult:
        # Cost close-out dipakai KEDUA jalur (sukses & gagal). Dua-ledger:
        # billed_usd = cash keluar (card); credit_usd = nilai konsumsi plan_* (bukan cash).
        billed, credit = self.cost_meter.costs_usd(self.registry)
        return RunResult(
            status=status,
            final=final,
            partial_artifacts=bb.current_artifacts(),
            failed_task=failed_task,
            usage_total=self.cost_meter.totals(),
            cost_usd=billed + credit,
            duration_ms=int((time.perf_counter() - started) * 1000),
            billed_usd=billed,
            credit_usd=credit,
            error_code=error_code,
            error_message=error_message,
            routing_decisions={
                task_id: dict(decision)
                for task_id, decision in self._routing_decisions.items()
            },
            subscription_calls=self._sub_calls,
            cost_estimated=self.cost_meter.has_estimated(),
            capability_notice=self.capability_notice,
        )

    async def aexecute(
        self,
        goal: str,
        on_text: Callable[[str], None] | None = None,
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> RunResult:
        # on_text men-stream fase SEKUENSIAL (planning + sintesis). on_worker_text
        # men-stream teks TIAP task (one-shot & agentic) ter-label task_id, sehingga
        # output worker PARALEL bisa diurai per-task (bukan bercampur). Keduanya
        # None = complete di semua fase (nol regresi).
        started = time.perf_counter()
        run_id = uuid.uuid4().hex
        raw_cap = os.environ.get("VOLANTE_MAX_SUBSCRIPTION_CALLS", "16")
        # Validate into a local first: a second (illegal, concurrent) caller must not
        # overwrite the in-flight run's cap before the re-entrancy guard rejects it.
        try:
            sub_cap = int(raw_cap)
        except ValueError as exc:
            raise ValueError(
                "VOLANTE_MAX_SUBSCRIPTION_CALLS must be a non-negative integer"
            ) from exc
        if sub_cap < 0:
            raise ValueError(
                "VOLANTE_MAX_SUBSCRIPTION_CALLS must be a non-negative integer"
            )
        if self._initial_subscription_calls > sub_cap:
            raise ValueError(
                "preflight subscription calls exceed VOLANTE_MAX_SUBSCRIPTION_CALLS"
            )
        # A Runtime runs exactly one goal. Its components are single-use too
        # (Supervisor.plan is explicitly non-re-entrant) and the cost meter, subscription
        # counter, task errors, and route trace are all per-run, so a second or
        # overlapping run would report another run's numbers. Refuse loudly with an
        # actionable message instead of silently mixing two runs' accounting; callers
        # build a fresh Runtime per goal (that is what the bootstrap factory returns).
        if self._used:
            raise RuntimeError(
                "this Runtime has already run a goal; Runtime is single-use — "
                "call the runtime factory again for each goal"
            )
        self._used = True
        self._sub_cap = sub_cap
        self._sub_calls = self._initial_subscription_calls
        self._task_errors = {}
        self._routing_decisions = {}
        bb = Blackboard(goal, [])
        try:
            try:
                validate_goal(goal)
            except InvalidGoalError as err:
                code, message = self._phase_error(err, phase="planning")
                return self._finalize(
                    bb,
                    started,
                    status="failed",
                    final=None,
                    failed_task="__planning__",
                    error_code=code,
                    error_message=message,
                )
            try:
                self._configure_call_controls()
                plan = await self._plan_with_fallback(goal, on_text)
            except Exception as err:  # noqa: BLE001 - structured phase close-out
                code, message = self._phase_error(err, phase="planning")
                return self._finalize(
                    bb,
                    started,
                    status="failed",
                    final=None,
                    failed_task="__planning__",
                    error_code=code,
                    error_message=message,
                )

            bb = Blackboard(goal, plan)
            sem = asyncio.Semaphore(self.fan_out)
            done: set[str] = set()
            while len(done) < len(plan):
                wave = [
                    t
                    for t in plan
                    if t.id not in done and all(dep in done for dep in t.depends_on)
                ]
                if not wave:
                    return self._finalize(
                        bb,
                        started,
                        status="failed",
                        final=None,
                        failed_task=None,
                        error_code="scheduler_deadlock",
                        error_message="no runnable tasks remain",
                    )
                results = await asyncio.gather(
                    *(
                        self._run_task(t, bb, run_id, sem, on_worker_text)
                        for t in wave
                    )
                )
                for task, ok in zip(wave, results, strict=True):
                    if not ok:
                        code, message = self._task_errors.get(
                            task.id, ("task_failed", "task failed")
                        )
                        # fail-fast antar-wave: sibling se-wave yang sukses tetap tersimpan.
                        return self._finalize(
                            bb,
                            started,
                            status="failed",
                            final=None,
                            failed_task=task.id,
                            error_code=code,
                            error_message=message,
                        )
                    done.add(task.id)
            try:
                final = await self._synthesize_with_fallback(goal, bb, on_text)
            except Exception as err:  # noqa: BLE001 - structured phase close-out
                code, message = self._phase_error(err, phase="synthesis")
                return self._finalize(
                    bb,
                    started,
                    status="failed",
                    final=None,
                    failed_task="__synthesis__",
                    error_code=code,
                    error_message=message,
                )
            return self._finalize(
                bb, started, status="success", final=final, failed_task=None
            )
        finally:
            # Covers success, structured failure, validation exceptions,
            # provider exceptions, and asyncio cancellation.
            try:
                self._clear_call_controls()
            finally:
                self._cleanup_run_workspace(run_id)

    def execute(
        self,
        goal: str,
        on_text: Callable[[str], None] | None = None,
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> RunResult:
        return asyncio.run(self.aexecute(goal, on_text, on_worker_text))

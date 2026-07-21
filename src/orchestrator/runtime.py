from __future__ import annotations

import asyncio
import random
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from orchestrator.agent import AgenticWorker
from orchestrator.blackboard import Blackboard
from orchestrator.cost import CostMeter
from orchestrator.projector import Projector
from orchestrator.providers.base import ProviderError
from orchestrator.registry import Registry
from orchestrator.router import Router
from orchestrator.supervisor import Supervisor
from orchestrator.synthesizer import Synthesizer
from orchestrator.tools.base import ToolRegistry
from orchestrator.tools.run_python import RunPythonTool
from orchestrator.tools.sandbox import Sandbox, sandbox_for
from orchestrator.types import ContentBlock, Entry, RunResult, Task, TextBlock
from orchestrator.worker import Worker


def _text_of(content: list[ContentBlock]) -> str:
    return "".join(b.text for b in content if isinstance(b, TextBlock))


def _task_cb(
    on_worker_text: Callable[[str, str], None] | None, task_id: str
) -> Callable[[str], None] | None:
    """Bungkus `on_worker_text(task_id, delta)` menjadi `on_text(delta)` ber-label,
    supaya stream worker paralel bisa diurai per-task oleh konsumen. None -> None
    (tak streaming). Caveat: seperti streaming ber-retry lain, kegagalan retryable
    di tengah stream akan memancarkan ulang teks parsial (lihat AgenticWorker)."""
    if on_worker_text is None:
        return None

    def cb(delta: str) -> None:
        on_worker_text(task_id, delta)

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
    ) -> None:
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
            model_id = self.router.route(task)
            return await self._run_task_body(
                task, bb, run_id, sem, model_id, on_worker_text
            )
        except (ProviderError, TimeoutError):
            raise  # sudah ditangani di jalur masing-masing; tak akan sampai sini
        except Exception as err:  # noqa: BLE001 - konversi jadi kegagalan tercatat
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
        model_id: str,
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> bool:
        req = self.projector.project(task, model_id, bb)
        req.run_id = run_id
        req.task_id = task.id
        req.attempt = 0
        if task.mode == "agentic":
            return await self._run_agentic(
                task, bb, run_id, sem, model_id, req, on_worker_text
            )
        # Stream one-shot ter-label task.id (output paralel terurai per-task).
        worker_cb = _task_cb(on_worker_text, task.id)
        last_err: Exception | None = None
        async with sem:  # cap fan-out di sekitar seluruh siklus retry task
            # attempt 0..max_retries inklusif => (max_retries + 1) percobaan.
            for attempt in range(self.max_retries + 1):
                req.attempt = attempt
                try:
                    resp = await asyncio.wait_for(
                        self.worker.run_one_shot(req, model_id, worker_cb),
                        timeout=self.call_timeout,
                    )
                except (ProviderError, TimeoutError) as err:
                    last_err = err
                    # Retry HANYA: ProviderError.retryable True, atau timeout call.
                    retryable = isinstance(err, TimeoutError) or (
                        isinstance(err, ProviderError) and err.retryable
                    )
                    if retryable and attempt < self.max_retries:
                        await asyncio.sleep(
                            0.5 * 2**attempt + random.uniform(0, 0.25)
                        )
                        continue
                    break  # non-retryable, atau retry habis
                now = time.time()
                bb.append(
                    Entry(
                        run_id=run_id,
                        task_id=task.id,
                        attempt=attempt,
                        kind="artifact",
                        payload=_text_of(resp.content),
                        model_id=resp.model,
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
                        model_id=resp.model,
                        usage=None,
                        timestamp=now,
                    )
                )
                return True
        # gagal final: rekam str(err) di entry status agar replayable.
        bb.append(
            Entry(
                run_id=run_id,
                task_id=task.id,
                attempt=req.attempt,
                kind="status",
                payload=f"failed: {last_err}",
                model_id=model_id,
                usage=None,
                timestamp=time.time(),
            )
        )
        return False

    async def _run_agentic(
        self, task, bb, run_id: str, sem, model_id: str, req, on_worker_text=None
    ) -> bool:
        if self.agentic_worker is None:
            raise RuntimeError(f"task {task.id} is agentic but no agentic_worker configured")
        workspace = self.runs_dir / run_id / task.id
        if self.tools_factory is not None:
            tools = self.tools_factory(workspace)
        else:
            factory = self.sandbox_factory or sandbox_for
            sandbox = factory(workspace)
            tools = {"run_python": RunPythonTool(sandbox)}
        worker_cb = _task_cb(on_worker_text, task.id)  # stream agentic ter-label
        async with sem:
            try:
                res = await asyncio.wait_for(
                    self.agentic_worker.run(req, model_id, tools, worker_cb),
                    timeout=self.agentic_timeout,
                )
            except (ProviderError, TimeoutError) as err:
                bb.append(
                    Entry(
                        run_id=run_id, task_id=task.id, attempt=0, kind="status",
                        payload=f"failed: {err}", model_id=model_id, usage=None,
                        timestamp=time.time(),
                    )
                )
                return False
        # jejak per-turn (kind baru; view lama tak terpengaruh)
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
        return True

    def _finalize(
        self,
        bb: Blackboard,
        started: float,
        *,
        status: str,
        final: str | None,
        failed_task: str | None,
    ) -> RunResult:
        # Cost close-out dipakai KEDUA jalur (sukses & gagal).
        return RunResult(
            status=status,
            final=final,
            partial_artifacts=bb.current_artifacts(),
            failed_task=failed_task,
            usage_total=self.cost_meter.totals(),
            cost_usd=self.cost_meter.cost_usd(self.registry),
            duration_ms=int((time.perf_counter() - started) * 1000),
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
        plan = await self.supervisor.plan(goal, on_text)
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
                    bb, started, status="failed", final=None, failed_task=None
                )
            results = await asyncio.gather(
                *(self._run_task(t, bb, run_id, sem, on_worker_text) for t in wave)
            )
            for t, ok in zip(wave, results, strict=True):
                if not ok:
                    # fail-fast antar-wave: sibling se-wave yang sukses tetap tersimpan.
                    return self._finalize(
                        bb, started, status="failed", final=None, failed_task=t.id
                    )
                done.add(t.id)
        final = await self.synthesizer.synthesize(goal, bb, on_text)
        shutil.rmtree(self.runs_dir / run_id, ignore_errors=True)
        return self._finalize(
            bb, started, status="success", final=final, failed_task=None
        )

    def execute(
        self,
        goal: str,
        on_text: Callable[[str], None] | None = None,
        on_worker_text: Callable[[str, str], None] | None = None,
    ) -> RunResult:
        return asyncio.run(self.aexecute(goal, on_text, on_worker_text))

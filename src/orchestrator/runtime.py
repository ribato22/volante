from __future__ import annotations

import asyncio
import time
import uuid

from orchestrator.blackboard import Blackboard
from orchestrator.cost import CostMeter
from orchestrator.projector import Projector
from orchestrator.registry import Registry
from orchestrator.router import Router
from orchestrator.supervisor import Supervisor
from orchestrator.synthesizer import Synthesizer
from orchestrator.types import ContentBlock, Entry, RunResult, Task, TextBlock
from orchestrator.worker import Worker


def _text_of(content: list[ContentBlock]) -> str:
    return "".join(b.text for b in content if isinstance(b, TextBlock))


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

    async def _run_task(
        self, task: Task, bb: Blackboard, run_id: str, sem: asyncio.Semaphore
    ) -> bool:
        model_id = self.router.route(task)
        req = self.projector.project(task, model_id, bb)
        req.run_id = run_id
        req.task_id = task.id
        req.attempt = 0
        async with sem:  # cap fan-out: maksimal `fan_out` task in-flight bersamaan
            resp = await asyncio.wait_for(
                self.worker.run_one_shot(req, model_id),
                timeout=self.call_timeout,
            )
        now = time.time()
        bb.append(
            Entry(
                run_id=run_id,
                task_id=task.id,
                attempt=0,
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
                attempt=0,
                kind="status",
                payload="success",
                model_id=resp.model,
                usage=None,
                timestamp=now,
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

    async def aexecute(self, goal: str) -> RunResult:
        started = time.perf_counter()
        run_id = uuid.uuid4().hex
        plan = await self.supervisor.plan(goal)
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
                # tak ada progres (mestinya tak terjadi: plan sudah divalidasi acyclic)
                return self._finalize(
                    bb, started, status="failed", final=None, failed_task=None
                )
            results = await asyncio.gather(
                *(self._run_task(t, bb, run_id, sem) for t in wave)
            )
            for t, ok in zip(wave, results, strict=True):
                if not ok:
                    # fail-fast antar-wave: sibling se-wave yang sukses tetap tersimpan.
                    return self._finalize(
                        bb, started, status="failed", final=None, failed_task=t.id
                    )
                done.add(t.id)
        final = await self.synthesizer.synthesize(goal, bb)
        return self._finalize(
            bb, started, status="success", final=final, failed_task=None
        )

    def execute(self, goal: str) -> RunResult:
        return asyncio.run(self.aexecute(goal))

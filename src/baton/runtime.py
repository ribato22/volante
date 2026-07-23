from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import random
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from baton.agent import AgenticWorker
from baton.blackboard import Blackboard
from baton.cost import CostMeter
from baton.projector import Projector
from baton.providers.base import ProviderError
from baton.registry import Registry
from baton.router import Router
from baton.supervisor import Supervisor
from baton.synthesizer import Synthesizer
from baton.tools.base import ToolRegistry
from baton.tools.run_python import RunPythonTool
from baton.tools.sandbox import Sandbox, sandbox_for
from baton.types import ContentBlock, Entry, RunResult, Task, TextBlock
from baton.worker import Worker

logger = logging.getLogger(__name__)

# billing yang dihitung sebagai kuota langganan (bukan cash) untuk guard per-run.
_SUBSCRIPTION_BILLING = frozenset({"plan_included", "plan_credit"})


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
        self._sub_cap = 4
        self._sub_calls = 0

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
            try:
                model_ids = self.router.route_ranked(task)
            except ValueError:
                if task.mode != "agentic":
                    raise
                # No tool-capable model is configured (common in subscription-only
                # setups: claude -p / codex exec are supports_tools=False). Degrade
                # the agentic task to a one_shot completion — the model answers
                # without the tool loop — instead of failing the whole run. Logged
                # so a user who genuinely needs tool use knows to configure a
                # tool-capable provider (Anthropic API, or an OpenAI-compat slot
                # with tools).
                logger.warning(
                    "task %r is agentic but no tool-capable model is configured; "
                    "running it as one_shot (degraded — configure a tool-capable "
                    "provider for real tool use)",
                    task.id,
                )
                bb.append(
                    Entry(
                        run_id=run_id,
                        task_id=task.id,
                        attempt=0,
                        kind="status",
                        payload=(
                            "degraded: agentic -> one_shot (no tool-capable model "
                            "configured)"
                        ),
                        model_id=None,
                        usage=None,
                        timestamp=time.time(),
                    )
                )
                task = dataclasses.replace(task, mode="one_shot")
                model_ids = self.router.route_ranked(task)
            model_id = model_ids[0] if model_ids else "unknown"
            return await self._run_task_body(
                task, bb, run_id, sem, model_ids, on_worker_text
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
            for model_id in model_ids:
                last_model = model_id
                # RE-PROYEKSI WAJIB per kandidat: req lama ter-scope ke
                # context_window/max_output model sebelumnya -> overflow di model
                # lebih kecil (opus 200k -> kimi 128k -> llama 8k). Proyeksi ulang
                # menjaga budget input & req.max_tokens benar untuk kandidat ini.
                req = self.projector.project(task, model_id, bb)
                req.run_id = run_id
                req.task_id = task.id
                req.attempt = 0
                if self._is_subscription(model_id) and self._sub_calls >= self._sub_cap:
                    # Guard kuota per-run [residu 3]: cap tercapai -> perlakukan
                    # kandidat langganan sebagai quota_exhausted, reroute ke direct
                    # TANPA memanggil provider langganan.
                    last_err = ProviderError(
                        f"subscription cap {self._sub_cap} reached",
                        retryable=False,
                        quota_exhausted=True,
                    )
                    bb.append(
                        Entry(
                            run_id=run_id,
                            task_id=task.id,
                            attempt=0,
                            kind="status",
                            payload=(
                                f"reroute: subscription cap {self._sub_cap} reached "
                                f"({self._sub_calls} calls); skipping {model_id}"
                            ),
                            model_id=model_id,
                            usage=None,
                            timestamp=time.time(),
                        )
                    )
                    continue
                if self._is_subscription(model_id):
                    self._sub_calls += 1  # hitung DISPATCH, bukan sukses (residu-3 fix):
                    # gagal/quota_exhausted tetap makan kuota interaktif nyata, jadi tetap
                    # harus dihitung supaya cap tak dilewati oleh panggilan riil > cap.
                reroute = False
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
                        # HANYA quota_exhausted memajukan ke kandidat berikutnya,
                        # TANPA sleep (Codex hard-pause jam-an -> backoff percuma).
                        if isinstance(err, ProviderError) and err.quota_exhausted:
                            reroute = True
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
                        break  # non-retryable non-quota / retry habis -> GAGAL
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
                if reroute:
                    # Satu entry status ber-alasan per kandidat yang ditinggalkan
                    # (trace blackboard memperlihatkan jalannya reroute).
                    bb.append(
                        Entry(
                            run_id=run_id,
                            task_id=task.id,
                            attempt=req.attempt,
                            kind="status",
                            payload=f"reroute: quota_exhausted on {model_id}: {last_err}",
                            model_id=model_id,
                            usage=None,
                            timestamp=time.time(),
                        )
                    )
                    continue  # coba kandidat berikutnya TANPA sleep
                # Kelengkapan walk [residu 1]: galat non-quota MENGGAGALKAN task,
                # BUKAN menjalari kandidat berikutnya (bug proyeksi/kontrak jangan
                # membakar tiap provider berurutan).
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
        return False

    async def _run_agentic(
        self, task, bb, run_id: str, sem, model_ids: list[str], on_worker_text=None
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
        last_err: Exception | None = None
        # Satu slot fan-out membungkus SEMUA kandidat (satu task = satu slot). Cap
        # per-provider (Phase 6) di luar lingkup follow-up ini.
        async with sem:
            for model_id in model_ids:
                # RE-PROJEKSI WAJIB per kandidat: context_window/max_output berbeda
                # antar-model (opus 200k -> kimi 128k -> llama 8k); req kandidat lama
                # akan overflow di model lebih kecil bila tak diproyeksi ulang.
                req = self.projector.project(task, model_id, bb)
                req.run_id = run_id
                req.task_id = task.id
                req.attempt = 0
                # residu-3: per-run subscription cap juga melindungi jalur agentic
                # (konsumen kuota terberat) — cermin guard one-shot, hitung DISPATCH
                # (bukan sukses) supaya cap tak dilewati panggilan interaktif riil.
                if self._is_subscription(model_id) and self._sub_calls >= self._sub_cap:
                    last_err = ProviderError(
                        f"subscription cap {self._sub_cap} reached",
                        retryable=False,
                        quota_exhausted=True,
                    )
                    bb.append(
                        Entry(
                            run_id=run_id, task_id=task.id, attempt=0, kind="status",
                            payload=(
                                f"reroute: subscription cap {self._sub_cap} reached "
                                f"({self._sub_calls} calls); skipping {model_id}"
                            ),
                            model_id=model_id, usage=None, timestamp=time.time(),
                        )
                    )
                    continue  # skip TANPA memanggil agentic worker; kandidat berikut
                if self._is_subscription(model_id):
                    self._sub_calls += 1  # hitung DISPATCH, konsisten dgn one-shot
                try:
                    res = await asyncio.wait_for(
                        self.agentic_worker.run(req, model_id, tools, worker_cb),
                        timeout=self.agentic_timeout,
                    )
                except (ProviderError, TimeoutError) as err:
                    last_err = err
                    # HANYA quota_exhausted memajukan ke kandidat berikut — TANPA sleep.
                    # Reroute mid-agentic me-RESTART task dari awal pada kandidat baru:
                    # turn parsial kandidat lama TIDAK direplay & tak dipersist sebagai
                    # TurnRecord (biayanya sudah ter-meter di cost_meter, side-effect
                    # workspace tetap ada). Keterbatasan ini DITERIMA & DIDOKUMENTASIKAN
                    # (§6.4) — dikerjakan setelah jalur one-shot stabil.
                    if isinstance(err, ProviderError) and err.quota_exhausted:
                        bb.append(
                            Entry(
                                run_id=run_id, task_id=task.id, attempt=0, kind="status",
                                payload=f"reroute: {model_id} quota_exhausted -> next candidate",
                                model_id=model_id, usage=None, timestamp=time.time(),
                            )
                        )
                        continue
                    # Galat non-quota (timeout/retryable habis/non-retryable) MENGGAGALKAN
                    # task — jangan jalari sisa kandidat (bug kontrak/semantik jangan
                    # membakar tiap provider berurutan).
                    bb.append(
                        Entry(
                            run_id=run_id, task_id=task.id, attempt=0, kind="status",
                            payload=f"failed: {err}", model_id=model_id, usage=None,
                            timestamp=time.time(),
                        )
                    )
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
                return True
        # Semua kandidat quota_exhausted -> gagal (jejak reroute sudah ditulis di atas).
        bb.append(
            Entry(
                run_id=run_id, task_id=task.id, attempt=0, kind="status",
                payload=f"failed: {last_err}",
                model_id=model_ids[-1] if model_ids else "unknown",
                usage=None, timestamp=time.time(),
            )
        )
        return False

    def _is_subscription(self, model_id: str) -> bool:
        # Model tak terdaftar (mis. Registry([]) di test) -> perlakukan sebagai
        # 'direct' agar guard tak salah-picu (nol regresi).
        try:
            billing = self.registry.get(model_id).billing
        except ValueError:
            return False
        return billing in _SUBSCRIPTION_BILLING

    def _finalize(
        self,
        bb: Blackboard,
        started: float,
        *,
        status: str,
        final: str | None,
        failed_task: str | None,
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
        self._sub_cap = int(os.environ.get("BATON_MAX_SUBSCRIPTION_CALLS", "4"))
        self._sub_calls = 0
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

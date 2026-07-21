from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.agent import AgenticWorker
from orchestrator.cost import CostMeter
from orchestrator.providers.base import ProviderError
from orchestrator.tools.run_python import RunPythonTool
from orchestrator.tools.sandbox import sandbox_for
from orchestrator.types import CanonicalRequest, TextBlock, text

if TYPE_CHECKING:
    from collections.abc import Callable

    from eval.tasks import EvalTask

    from orchestrator.providers.base import LLMProvider
    from orchestrator.registry import Registry
    from orchestrator.runtime import Runtime
    from orchestrator.types import CanonicalResponse, RunResult, Usage

EVAL_TEMPERATURE: float = 0.0
EVAL_K: int = 2

# Timeout wall-clock subprocess score_code (kontrak PATCH v2.1 = 15s). Modul-level
# agar test bisa memangkasnya untuk kasus while-True tanpa menunggu 15 detik.
SCORE_TIMEOUT_S: float = 15.0

# S2 hardening: batas CPU child-side (detik). Backstop kedua selain wall-timeout —
# CPU-spin mati via SIGXCPU tanpa menahan slot selama wall penuh.
SCORE_CPU_S: int = 15

# Wrapper dijalankan DI DALAM proses anak (mirror Sandbox): set RLIMIT_CPU sebelum
# menjalankan runner. Menghindari preexec_fn (fork-unsafe). runpy dgn run_name
# '__main__' -> blok `if __name__ == "__main__"` runner tetap tereksekusi.
# `sys.argv[:] = [p]` SEBELUM run_path: runpy hanya menimpa argv[0], jadi tanpa reset
# argv wrapper ('<cpu>', '<runner>') bocor ke sys.argv[1:] runner -> runner yang
# membaca argv (argparse dsb.) rusak. Setelah reset, runner lihat argv bersih [runner],
# identik dgn invokasi lama `python reference_runner.py`.
_SCORE_WRAPPER = (
    "import resource,runpy,sys;"
    "c=int(sys.argv[1]);p=sys.argv[2];"
    "resource.setrlimit(resource.RLIMIT_CPU,(c,c));"
    "sys.argv[:]=[p];"
    "runpy.run_path(p, run_name='__main__')"
)


def _killpg(pgid: int) -> None:
    """SIGKILL seluruh process group `pgid`.

    Dengan start_new_session=True anak menjadi pemimpin grup, jadi pgid == proc.pid.
    Sengaja TIDAK memakai os.getpgid(proc.pid): bila pemimpin (runner) sudah keluar
    jadi zombie — mis. ia mem-fork anak yang menahan pipe lalu exit duluan — getpgid
    melempar ProcessLookupError di macOS dan grup tak akan terbunuh. Memakai pgid
    (== pid) langsung tetap menjangkau anak yang masih hidup di grup itu."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

# Tiga backtick sebagai penanda fence, dibangun tanpa literal agar tak merusak
# blok kode dokumen.
_TRIPLE = chr(96) * 3
_PY_FENCE = re.compile(
    _TRIPLE + r"[^\S\r\n]*python[^\S\r\n]*\r?\n(.*?)" + _TRIPLE,
    re.IGNORECASE | re.DOTALL,
)
_README_HEADING = re.compile(r"(?m)^#{1,6}\s+\S")


@dataclass
class BaselineResult:
    output: str
    usage_total: dict[str, Usage]
    cost_usd: float
    duration_ms: int


def _extract_text(resp: CanonicalResponse) -> str:
    return "".join(b.text for b in resp.content if isinstance(b, TextBlock))


def extract_python(text_in: str) -> str:
    """Isi blok ```python pertama; fallback teks mentah bila tak ada fence."""
    m = _PY_FENCE.search(text_in)
    return m.group(1) if m else text_in


def _clean_env() -> dict[str, str]:
    """Env subprocess: HANYA PATH. Membuang semua *_API_KEY / *_KEY (dan lainnya)
    sehingga kode yang dinilai tak pernah bisa membaca/mengeksfiltrasi kredensial."""
    return {"PATH": os.environ.get("PATH", "")}


def score_code(model_output: str, reference_test: str) -> float:
    """Ekstrak kode, jalankan runner referensi di subprocess terisolasi, kembalikan
    passed/total dari JSON stdout. SyntaxError/timeout/nonzero -> 0.0.

    `reference_test` adalah SUMBER runner (EvalTask.reference_test) yang dites;
    di-param-kan agar suite multi-goal memakai runner berbeda per goal. Dipakai
    SAMA untuk output orkestrasi maupun baseline (ekuitas penilaian).

    Isolasi (S2, sekelas Sandbox): runner + `solution.py` (kode model) jalan dalam
    process group sendiri (`start_new_session=True`) dgn RLIMIT_CPU child-side. SELURUH
    grup di-killpg pada SETIAP jalur keluar (timeout MAUPUN penyelesaian normal) — jadi
    anak fork yang tetap hidup di grup tak menyintasi score_code, bukan hanya kasus
    timeout. Batas kepercayaan tetap best-effort POSIX (setsid oleh solusi bisa lepas
    dari grup). CATATAN: ini isolasi PROSES, bukan autentikasi output — solusi masih
    bisa mencetak JSON palsu saat import (masalah pre-existing, lihat follow-up)."""
    code = extract_python(model_output)
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "solution.py").write_text(code, encoding="utf-8")
        Path(tmp, "reference_runner.py").write_text(reference_test, encoding="utf-8")
        # Popen (bukan subprocess.run): agar timeout bisa killpg SELURUH grup.
        # subprocess.run hanya mem-proc.kill() anak langsung, lalu communicate reap
        # bisa MENGGANTUNG bila anak fork masih memegang pipe stdout.
        proc = subprocess.Popen(
            [sys.executable, "-c", _SCORE_WRAPPER, str(SCORE_CPU_S), "reference_runner.py"],
            cwd=tmp,
            env=_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # grup proses sendiri -> killpg bunuh fork/cucu
        )
        try:
            try:
                out, _err = proc.communicate(timeout=SCORE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                _killpg(proc.pid)
                try:
                    proc.communicate(timeout=5)  # reap grup yang sudah di-SIGKILL
                except subprocess.TimeoutExpired:
                    pass
                return 0.0
            if proc.returncode != 0:
                return 0.0
            try:
                data = json.loads(out.strip().splitlines()[-1])
                total = int(data["total"])
                passed = int(data["passed"])
            except (ValueError, KeyError, IndexError):
                return 0.0
        finally:
            # killpg TIAP jalur keluar: anak fork yang MELEPAS pipe (close fd 1/2)
            # lalu spin membuat runner selesai NORMAL (bukan timeout) -> tanpa ini
            # ia jadi orphan yang menyintasi skor. Grup sudah mati di jalur timeout
            # (killpg kedua ini no-op/ProcessLookupError, ditelan _killpg).
            _killpg(proc.pid)
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, passed / total))


def score_task(output: str, reference_test: str) -> dict[str, float]:
    """Skor komposit berbobot: code .7 / has_tests .15 / has_readme .15.

    `reference_test` diteruskan apa adanya ke score_code (runner per-goal)."""
    code = score_code(output, reference_test)
    has_tests = 1.0 if "def test_" in output else 0.0
    has_readme = (
        1.0
        if ("readme" in output.lower() or _README_HEADING.search(output))
        else 0.0
    )
    composite = 0.7 * code + 0.15 * has_tests + 0.15 * has_readme
    return {
        "code": code,
        "has_tests": has_tests,
        "has_readme": has_readme,
        "composite": composite,
    }


async def run_orchestration(goal: str, runtime: Runtime) -> RunResult:
    """Jalankan engine orkestrasi penuh untuk sebuah goal (via aexecute publik)."""
    return await runtime.aexecute(goal)


async def run_baseline(
    goal: str, provider: LLMProvider, model_id: str, registry: Registry
) -> BaselineResult:
    """Satu model kuat menjawab goal langsung (tanpa orkestrasi), ekuitas terjaga:
    max_tokens = ModelInfo.max_output_tokens (BUKAN 2048), temperature = 0.0,
    dan biaya diukur oleh CostMeter sendiri."""
    meter = CostMeter()
    mi = registry.get(model_id)
    req = CanonicalRequest(
        messages=[text("user", goal)],
        max_tokens=mi.max_output_tokens,
        temperature=EVAL_TEMPERATURE,
        run_id="baseline",
        task_id="baseline",
    )
    start = time.perf_counter()
    resp = await provider.complete(req)
    duration_ms = int((time.perf_counter() - start) * 1000)
    meter.add(model_id, resp.usage)
    return BaselineResult(
        output=_extract_text(resp),
        usage_total=meter.totals(),
        cost_usd=meter.cost_usd(registry),
        duration_ms=duration_ms,
    )


_AGENTIC_SUFFIX = (
    "\n\nWrite your solution to a file named `solution.py` in the current working "
    "directory. Also write a pytest test module (test_*.py) and a short README. Use the "
    "run_python tool to execute your code and tests, and iterate until the tests pass. "
    "When done, briefly summarize."
)


@dataclass
class AgenticArmResult:
    solution_code: str
    has_tests: bool
    has_readme: bool
    usage_total: dict[str, Usage]
    cost_usd: float
    duration_ms: int
    # Non-None bila loop agentic gagal terminal (ProviderError: infra/auth/network
    # ATAU loop-exhausted/over-budget). Membedakan "arm gagal dijalankan" dari
    # "solusi benar-benar buruk (0.0)"; disurface ke report agar verdict tak bias.
    error: str | None = None


def _scan_workspace(ws: Path) -> tuple[bool, bool]:
    has_tests = False
    has_readme = False
    for p in ws.iterdir():
        name = p.name.lower()
        if p.is_file() and (name.startswith("test_") or name.endswith("_test.py")):
            try:
                if "def test_" in p.read_text(encoding="utf-8", errors="ignore"):
                    has_tests = True
            except OSError:
                pass
        if p.is_file() and name.startswith("readme"):
            has_readme = True
    return has_tests, has_readme


async def run_agentic_single(
    goal: str, provider: LLMProvider, model_id: str, registry: Registry
) -> AgenticArmResult:
    """Arm agentic-single: 1 model + loop run_python, TANPA dekomposisi. Model diminta
    menulis solution.py + tests + README di workspace; skor diambil dari file itu."""
    meter = CostMeter()
    worker = AgenticWorker({model_id: provider}, meter, max_iters=8)
    mi = registry.get(model_id)
    start = time.perf_counter()
    error: str | None = None
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        tools = {"run_python": RunPythonTool(sandbox_for(ws))}
        req = CanonicalRequest(
            messages=[text("user", goal + _AGENTIC_SUFFIX)],
            max_tokens=mi.max_output_tokens,
            temperature=EVAL_TEMPERATURE,
            run_id="agentic",
            task_id="agentic",
        )
        try:
            res = await worker.run(req, model_id, tools)
            final_text = res.final_text
            usage_total = res.usage_total
        except ProviderError as exc:
            # Jangan telan diam-diam: catat sebab. Solusi di disk (bila model sempat
            # menulisnya sebelum gagal) tetap dinilai; kalau tak ada, error yang
            # disurface membedakan kegagalan arm dari solusi 0.0 sungguhan.
            final_text = ""
            usage_total = meter.totals()
            error = f"{type(exc).__name__}: {exc}"
        sol = ws / "solution.py"
        if sol.exists():
            solution_code = sol.read_text(encoding="utf-8", errors="ignore")
        else:
            solution_code = extract_python(final_text)
        has_tests, has_readme = _scan_workspace(ws)
    duration_ms = int((time.perf_counter() - start) * 1000)
    return AgenticArmResult(
        solution_code=solution_code,
        has_tests=has_tests,
        has_readme=has_readme,
        usage_total=usage_total,
        cost_usd=meter.cost_usd(registry),
        duration_ms=duration_ms,
        error=error,
    )


def score_agentic(res: AgenticArmResult, reference_test: str) -> dict[str, float]:
    """Skor komposit arm agentic dari workspace (analog score_task, file-aware)."""
    code = score_code(res.solution_code, reference_test)
    ht = 1.0 if res.has_tests else 0.0
    hr = 1.0 if res.has_readme else 0.0
    return {
        "code": code,
        "has_tests": ht,
        "has_readme": hr,
        "composite": 0.7 * code + 0.15 * ht + 0.15 * hr,
    }


def compare(
    orch: RunResult,
    base: BaselineResult,
    orch_score: dict[str, float],
    base_score: dict[str, float],
) -> dict[str, Any]:
    """Kemas metrik terukur dan tentukan pemenang.

    Aturan winner (match pertama menang):
      1. RunResult orkestrasi berstatus "failed" tak bisa menang -> "baseline";
      2. composite lebih tinggi menang;
      3. seri composite -> sisi termurah menang;
      4. selain itu "tie".
    Flag *_estimated diturunkan dari usage_total (Usage.estimated per model)."""
    orch_composite = orch_score["composite"]
    base_composite = base_score["composite"]
    orch_cost = orch.cost_usd
    base_cost = base.cost_usd
    orch_estimated = any(u.estimated for u in orch.usage_total.values())
    base_estimated = any(u.estimated for u in base.usage_total.values())

    if getattr(orch, "status", None) == "failed":
        winner = "baseline"
    elif orch_composite > base_composite:
        winner = "orchestration"
    elif base_composite > orch_composite:
        winner = "baseline"
    elif orch_cost < base_cost:
        winner = "orchestration"
    elif base_cost < orch_cost:
        winner = "baseline"
    else:
        winner = "tie"

    return {
        "orch_cost": orch_cost,
        "base_cost": base_cost,
        "orch_ms": orch.duration_ms,
        "base_ms": base.duration_ms,
        "orch_composite": orch_composite,
        "base_composite": base_composite,
        "orch_estimated": orch_estimated,
        "base_estimated": base_estimated,
        "winner": winner,
    }


def compare_arms(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """N-way: winner = composite tertinggi; seri komposit -> termurah; masih seri -> "tie"."""
    best = max(a["composite"] for a in arms.values())
    top = [name for name, a in arms.items() if a["composite"] == best]
    if len(top) == 1:
        winner = top[0]
    else:
        cheapest = min(arms[name]["cost"] for name in top)
        cheap_top = [name for name in top if arms[name]["cost"] == cheapest]
        winner = cheap_top[0] if len(cheap_top) == 1 else "tie"
    return {"arms": arms, "winner": winner}


def mean_scores(scores: list[dict[str, float]]) -> dict[str, float]:
    """Rata-rata per-kunci dari daftar dict score_task (untuk agregasi k-run)."""
    if not scores:
        return {"code": 0.0, "has_tests": 0.0, "has_readme": 0.0, "composite": 0.0}
    n = len(scores)
    keys = scores[0].keys()
    return {k: sum(s[k] for s in scores) / n for k in keys}


async def run_eval(
    goal: str,
    reference_test: str,
    make_runtime: Callable[[], Runtime],
    provider: LLMProvider,
    model_id: str,
    registry: Registry,
    k: int = EVAL_K,
) -> dict[str, Any]:
    """Jalankan tiap sisi k kali; rata-ratakan skor score_task via mean_scores; compare.

    `reference_test` adalah runner tersembunyi milik goal (EvalTask.reference_test);
    dipakai untuk menilai kedua sisi secara adil. make_runtime() dipanggil per
    iterasi karena aexecute bersifat sekali-jalan."""
    orch_scores: list[dict[str, float]] = []
    base_scores: list[dict[str, float]] = []
    orch_last: RunResult | None = None
    base_last: BaselineResult | None = None
    for _ in range(k):
        orch_last = await run_orchestration(goal, make_runtime())
        base_last = await run_baseline(goal, provider, model_id, registry)
        orch_scores.append(score_task(orch_last.final or "", reference_test))
        base_scores.append(score_task(base_last.output, reference_test))
    return compare(
        orch_last, base_last, mean_scores(orch_scores), mean_scores(base_scores)
    )


async def run_suite(
    suite: list[EvalTask],
    make_runtime: Callable[[], Runtime],
    baseline_provider: LLMProvider,
    model_id: str,
    registry: Registry,
    k: int = EVAL_K,
) -> dict[str, Any]:
    """3-arm: baseline / orchestration / agentic-single (k×/goal), agregat verdict.

    Tiap goal dinilai runner tersembunyinya sendiri (t.reference_test); isolasi
    subprocess score_code tak berubah. Arm agentic memakai baseline_provider +
    model_id yang sama (model identik) demi ekuitas. Kembalikan::

        {"per_goal": [{"id", "winner", "arms",
                       "scores": {baseline, orchestration, agentic}}],
         "aggregate": {"wins": {..}, "ties", "cost_total": {..},
                       "any_estimated", "verdict"}}

    Verdict overall = mayoritas goal; seri -> "tie"."""
    per_goal: list[dict[str, Any]] = []
    for t in suite:
        b_scores: list[dict[str, float]] = []
        o_scores: list[dict[str, float]] = []
        a_scores: list[dict[str, float]] = []
        a_errors: list[str] = []
        b_last: BaselineResult | None = None
        o_last: RunResult | None = None
        a_last: AgenticArmResult | None = None
        for _ in range(k):
            b_last = await run_baseline(t.goal, baseline_provider, model_id, registry)
            o_last = await run_orchestration(t.goal, make_runtime())
            a_last = await run_agentic_single(
                t.goal, baseline_provider, model_id, registry
            )
            b_scores.append(score_task(b_last.output, t.reference_test))
            o_scores.append(score_task(o_last.final or "", t.reference_test))
            a_scores.append(score_agentic(a_last, t.reference_test))
            if a_last.error is not None:
                a_errors.append(a_last.error)
        b_mean = mean_scores(b_scores)
        o_mean = mean_scores(o_scores)
        a_mean = mean_scores(a_scores)
        arms = {
            "baseline": {
                "composite": b_mean["composite"],
                "cost": b_last.cost_usd,
                "ms": b_last.duration_ms,
                "estimated": any(u.estimated for u in b_last.usage_total.values()),
            },
            "orchestration": {
                "composite": o_mean["composite"],
                "cost": o_last.cost_usd,
                "ms": o_last.duration_ms,
                "estimated": any(u.estimated for u in o_last.usage_total.values()),
            },
            "agentic": {
                "composite": a_mean["composite"],
                "cost": a_last.cost_usd,
                "ms": a_last.duration_ms,
                "estimated": any(u.estimated for u in a_last.usage_total.values()),
                # Jumlah iterasi (dari k) yang gagal terminal + sampel sebab.
                # composite-nya bisa 0.0 karena INFRA, bukan kapabilitas.
                "errors": len(a_errors),
                "error_sample": a_errors[0] if a_errors else None,
            },
        }
        cmp = compare_arms(arms)
        per_goal.append({
            "id": t.id,
            "winner": cmp["winner"],
            "arms": cmp["arms"],
            "scores": {
                "baseline": b_mean,
                "orchestration": o_mean,
                "agentic": a_mean,
            },
        })

    names = ("baseline", "orchestration", "agentic")
    wins = {n: sum(1 for g in per_goal if g["winner"] == n) for n in names}
    ties = sum(1 for g in per_goal if g["winner"] == "tie")
    cost_total = {n: sum(g["arms"][n]["cost"] for g in per_goal) for n in names}
    any_estimated = any(
        g["arms"][n]["estimated"] for g in per_goal for n in names
    )
    # Total iterasi agentic yang gagal terminal: bila > 0, sebagian skor 0.0 arm
    # agentic bisa jadi infra/provider, bukan kapabilitas — verdict harus dibaca hati2.
    agentic_errors = sum(g["arms"]["agentic"].get("errors", 0) for g in per_goal)
    best = max(wins.values())
    leaders = [n for n in names if wins[n] == best]
    verdict = leaders[0] if len(leaders) == 1 else "tie"
    return {
        "per_goal": per_goal,
        "aggregate": {
            "wins": wins,
            "ties": ties,
            "cost_total": cost_total,
            "any_estimated": any_estimated,
            "agentic_errors": agentic_errors,
            "verdict": verdict,
        },
    }

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.cost import CostMeter
from orchestrator.types import CanonicalRequest, TextBlock, text

if TYPE_CHECKING:
    from collections.abc import Callable

    from orchestrator.providers.base import LLMProvider
    from orchestrator.registry import Registry
    from orchestrator.runtime import Runtime
    from orchestrator.types import CanonicalResponse, RunResult, Usage

EVAL_TEMPERATURE: float = 0.0
EVAL_K: int = 2

# Timeout wall-clock subprocess score_code (kontrak PATCH v2.1 = 15s). Modul-level
# agar test bisa memangkasnya untuk kasus while-True tanpa menunggu 15 detik.
SCORE_TIMEOUT_S: float = 15.0

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
    di-param-kan agar suite multi-goal memakai runner berbeda per goal. Isolasi
    subprocess tak berubah. Dipakai SAMA untuk output orkestrasi maupun baseline
    (ekuitas penilaian)."""
    code = extract_python(model_output)
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "solution.py").write_text(code, encoding="utf-8")
        Path(tmp, "reference_runner.py").write_text(reference_test, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "reference_runner.py"],
                cwd=tmp,
                env=_clean_env(),
                capture_output=True,
                text=True,
                timeout=SCORE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return 0.0
        if proc.returncode != 0:
            return 0.0
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            total = int(data["total"])
            passed = int(data["passed"])
        except (ValueError, KeyError, IndexError):
            return 0.0
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

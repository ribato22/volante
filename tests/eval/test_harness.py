from __future__ import annotations

import os
import time

import eval.harness as harness
import pytest
from eval.harness import (
    BaselineResult,
    compare,
    extract_python,
    mean_scores,
    run_baseline,
    run_eval,
    run_orchestration,
    score_code,
    score_task,
)
from eval.tasks import REFERENCE_TEST, SLUGIFY_GOAL

from orchestrator.providers.fake import FakeProvider
from orchestrator.registry import Registry
from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    RunResult,
    TextBlock,
    Usage,
)

# Bangun tiga backtick tanpa menaruh literalnya di sumber (jaga fence markdown).
FENCE = "`" * 3

GOOD_CODE = '''\
import re


def slugify(text):
    s = text.lower()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")
'''


def _model(model_id: str = "strong-model") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="anthropic",
        strengths={"coding"},
        context_window=200_000,
        max_output_tokens=4_096,
        supports_tools=True,
        cost_per_1k_in=0.003,
        cost_per_1k_out=0.015,
    )


def _registry() -> Registry:
    return Registry([_model()])


def _code_response() -> CanonicalResponse:
    return CanonicalResponse(
        content=[TextBlock(text=GOOD_CODE)],
        usage=Usage(prompt_tokens=120, completion_tokens=240),
        model="strong-model",
        stop_reason="end_turn",
        latency_ms=1500,
    )


class _SpyProvider:
    """LLMProvider yang merekam request terakhir; tanpa jaringan."""

    name = "spy"

    def __init__(self, response: CanonicalResponse) -> None:
        self._response = response
        self.last_req: CanonicalRequest | None = None

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        self.last_req = req
        return self._response


class _StubRuntime:
    """Runtime stand-in yang mengekspos kontrak PUBLIK `aexecute` (bukan _aexecute)."""

    def __init__(
        self, final: str, *, cost_usd: float = 0.02, duration_ms: int = 2500
    ) -> None:
        self._final = final
        self._cost_usd = cost_usd
        self._duration_ms = duration_ms

    async def aexecute(self, goal: str) -> RunResult:
        return RunResult(
            status="success",
            final=self._final,
            partial_artifacts={},
            failed_task=None,
            usage_total={"orch-model": Usage(50, 80, estimated=False)},
            cost_usd=self._cost_usd,
            duration_ms=self._duration_ms,
        )


def _run_result(**kw) -> RunResult:
    base = dict(
        status="success",
        final="x",
        partial_artifacts={},
        failed_task=None,
        usage_total={},
        cost_usd=0.0,
        duration_ms=0,
    )
    base.update(kw)
    return RunResult(**base)


def _baseline(**kw) -> BaselineResult:
    base = dict(output="", usage_total={}, cost_usd=0.0, duration_ms=0)
    base.update(kw)
    return BaselineResult(**base)


# --- extract_python ---------------------------------------------------------


def test_extract_python_pulls_first_python_fence():
    doc = f"Here is code:\n\n{FENCE}python\nprint('hi')\n{FENCE}\n\nDone.\n"
    assert extract_python(doc) == "print('hi')\n"


def test_extract_python_falls_back_to_raw_text():
    raw = "def slugify(text):\n    return text\n"
    assert extract_python(raw) == raw


# --- score_code (SUBPROCESS TERISOLASI) ------------------------------------


def test_score_code_perfect_fenced_scores_one():
    assert score_code(f"{FENCE}python\n{GOOD_CODE}{FENCE}", REFERENCE_TEST) == 1.0


def test_score_code_markdown_wrapped_extracted_then_positive():
    doc = f"Sure! Here it is:\n\n{FENCE}python\n{GOOD_CODE}{FENCE}\n\nHope it helps."
    assert score_code(doc, REFERENCE_TEST) > 0.0


def test_score_code_partial_between_zero_and_one():
    partial = "def slugify(text):\n    return text.replace(' ', '-')\n"
    score = score_code(partial, REFERENCE_TEST)
    assert 0.0 < score < 1.0


def test_score_code_syntax_error_scores_zero():
    assert score_code("def slugify(text) return x", REFERENCE_TEST) == 0.0


def test_score_code_infinite_loop_times_out_to_zero(monkeypatch):
    # Timeout default 15s; dipangkas agar test cepat, tetap membuktikan while-True -> 0.0.
    monkeypatch.setattr(harness, "SCORE_TIMEOUT_S", 2.0)
    looping = "def slugify(text):\n    while True:\n        pass\n"
    assert score_code(looping, REFERENCE_TEST) == 0.0


# --- S2 hardening: RLIMIT_CPU + killpg grup (mirror Sandbox) ----------------


def test_score_code_rlimit_cpu_kills_before_wall_timeout(monkeypatch):
    # RLIMIT_CPU child-side membunuh CPU-spin di ~SCORE_CPU_S detik CPU, JAUH sebelum
    # wall-timeout besar. Membuktikan batas CPU aktif (bukan cuma wall-timeout).
    monkeypatch.setattr(harness, "SCORE_CPU_S", 1)
    monkeypatch.setattr(harness, "SCORE_TIMEOUT_S", 30.0)  # wall besar; CPU harus menang
    spin_at_import = "x = 0\nwhile True:\n    x += 1\n"
    start = time.perf_counter()
    score = score_code(spin_at_import, REFERENCE_TEST)
    elapsed = time.perf_counter() - start
    assert score == 0.0
    assert elapsed < 15.0  # CPU-limit (~1s) memutus jauh sebelum wall 30s


def test_score_code_timeout_calls_killpg_on_process_group(monkeypatch):
    # Pada wall-timeout, killpg SELURUH grup dipanggil (bukan cuma anak langsung),
    # sehingga fork/proses ter-detach ikut mati.
    monkeypatch.setattr(harness, "SCORE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(harness, "SCORE_CPU_S", 30)  # jangan biarkan CPU-limit menang dulu
    killed: list[int] = []
    real_killpg = harness._killpg

    def spy_killpg(pid: int) -> None:
        killed.append(pid)
        real_killpg(pid)

    monkeypatch.setattr(harness, "_killpg", spy_killpg)
    # while-True pakai sleep: wall-timeout menggigit (CPU rendah) -> killpg.
    looping = "import time\ndef slugify(text):\n    while True:\n        time.sleep(0.01)\n"
    assert score_code(looping, REFERENCE_TEST) == 0.0
    # Jalur timeout memanggil killpg (di cabang except + sekali lagi di finally);
    # yang penting grup benar-benar di-killpg minimal sekali.
    assert len(killed) >= 1


def test_score_code_kills_forked_child_that_holds_pipe(tmp_path, monkeypatch):
    # Skenario fork-bomb inti: solusi mem-fork anak yang menahan pipe stdout terbuka
    # lalu spin. Tanpa killpg-grup, communicate menggantung tak-terhingga (anak
    # memegang pipe) DAN anak jadi orphan. Dengan start_new_session + killpg, run
    # berbatas waktu dan anak ikut mati.
    monkeypatch.setattr(harness, "SCORE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(harness, "SCORE_CPU_S", 30)
    pidfile = tmp_path / "child.pid"
    forking = (
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "    while True:\n"
        "        time.sleep(0.01)\n"
        "def slugify(text):\n"
        "    return text\n"
    )
    start = time.perf_counter()
    score = score_code(forking, REFERENCE_TEST)
    elapsed = time.perf_counter() - start
    assert score == 0.0
    assert elapsed < 10.0  # tidak menggantung: killpg membebaskan communicate
    # Anak (yang menulis pid-nya) sudah mati setelah score_code kembali.
    assert pidfile.exists(), "child seharusnya sempat menulis PID sebelum di-kill"
    child_pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # ProcessLookupError = proses sudah tiada


def test_score_code_still_scores_good_and_bad_after_hardening():
    # Regresi: hardening tak mengubah penilaian solusi normal.
    assert score_code(f"{FENCE}python\n{GOOD_CODE}{FENCE}", REFERENCE_TEST) == 1.0
    assert score_code("def slugify(text) return x", REFERENCE_TEST) == 0.0
    partial = "def slugify(text):\n    return text.replace(' ', '-')\n"
    assert 0.0 < score_code(partial, REFERENCE_TEST) < 1.0


def test_score_code_killpg_runs_on_success_path(monkeypatch):
    # Verifikasi-adversarial temuan #1: killpg harus jalan di jalur SUKSES (bukan
    # cuma cabang timeout), agar anak fork yang tetap hidup di grup ikut mati.
    calls: list[int] = []
    real_killpg = harness._killpg

    def spy_killpg(pgid: int) -> None:
        calls.append(pgid)
        real_killpg(pgid)

    monkeypatch.setattr(harness, "_killpg", spy_killpg)
    # Solusi normal yang selesai SUKSES (bukan timeout) tetap memicu killpg grup.
    assert score_code(f"{FENCE}python\n{GOOD_CODE}{FENCE}", REFERENCE_TEST) == 1.0
    assert len(calls) == 1


def test_score_code_kills_forked_child_on_success_path(tmp_path):
    # End-to-end temuan #1: anak fork yang MELEPAS pipe (close fd 1/2) lalu spin
    # membuat runner selesai NORMAL (skor benar 1.0), tapi anak harus tetap di-SIGKILL.
    readyfile = tmp_path / "child.ready"
    pidfile = tmp_path / "child.pid"
    forking = (
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        f"    open({str(readyfile)!r}, 'w').write('1')\n"
        "    os.close(1)\n"
        "    os.close(2)\n"
        "    while True:\n"
        "        time.sleep(0.05)\n"
        "else:\n"
        # Parent (runner import) menunggu anak siap dulu -> pidfile dijamin ada
        # sebelum score_code kembali (hilangkan race).
        f"    while not os.path.exists({str(readyfile)!r}):\n"
        "        time.sleep(0.01)\n"
        + GOOD_CODE
    )
    score = score_code(forking, REFERENCE_TEST)
    assert score == 1.0  # jalur sukses: skor tetap benar
    assert pidfile.exists()
    child_pid = int(pidfile.read_text())
    deadline = time.perf_counter() + 5.0
    alive = True
    while time.perf_counter() < deadline:
        try:
            os.kill(child_pid, 0)
            time.sleep(0.02)
        except ProcessLookupError:
            alive = False
            break
    assert alive is False, "anak fork di grup harus di-SIGKILL saat score_code kembali"


def test_score_code_runner_sees_clean_argv():
    # Verifikasi-adversarial temuan #2: wrapper tak boleh membocorkan arg-nya ke
    # sys.argv runner. Runner yang membaca argv harus melihat argv bersih (len 1),
    # identik dengan invokasi lama `python reference_runner.py`.
    argv_probe = (
        "import json, sys\n"
        "extra = sys.argv[1:]\n"  # harus kosong; '15'/duplikat = kebocoran wrapper
        "print(_TAG + json.dumps({'passed': 0 if extra else 1, 'total': 1}))\n"
        "sys.exit(0)\n"
    )
    # Solusi apa pun; skor 1.0 HANYA jika runner melihat argv bersih.
    assert score_code("x = 1\n", argv_probe) == 1.0


# --- kanal hasil ber-nonce (anti-forgery naif + sinyal 'tak-terukur' H2) -----


def test_score_reference_naive_forgery_rejected_and_unmeasured():
    # Forgery naif: solusi cetak JSON sempurna + os._exit(0) SAAT import (sebelum
    # runner mengemit). Tanpa nonce ia tak bisa membuat baris ber-tag -> ditolak.
    forgery = (
        "import json, os\n"
        "print(json.dumps({'passed': 999, 'total': 999}))\n"
        "os._exit(0)\n"
    )
    score, measured = harness._score_reference(forgery, REFERENCE_TEST)
    assert score == 0.0
    assert measured is False  # tak ada hasil tepercaya -> bukan 0.0 sungguhan


def test_score_reference_forgery_cannot_guess_tag_even_reading_env():
    # Solusi coba menebak format tag tanpa nonce (env pun tak memuat nonce).
    forgery = (
        "import json, os\n"
        "print('AIORCH_RESULT::' + json.dumps({'passed': 999, 'total': 999}))\n"
        "os._exit(0)\n"
    )
    score, measured = harness._score_reference(forgery, REFERENCE_TEST)
    assert score == 0.0
    assert measured is False


def test_score_reference_broken_runner_is_unmeasured_not_zero():
    # H2: runner referensi rusak (crash sebelum emit) -> TAK terukur, bukan 0.0 palsu.
    broken_runner = "raise RuntimeError('scorer bug: import failed')\n"
    score, measured = harness._score_reference(GOOD_CODE, broken_runner)
    assert score == 0.0
    assert measured is False


def test_score_reference_runner_emitting_untagged_is_unmeasured():
    # Runner yang mencetak JSON TANPA _TAG (tak ikut kontrak) -> tak dipercaya.
    untagged = (
        "import json\n"
        "print(json.dumps({'passed': 8, 'total': 8}))\n"
    )
    score, measured = harness._score_reference(GOOD_CODE, untagged)
    assert score == 0.0
    assert measured is False


def test_score_reference_syntax_error_solution_is_measured_zero():
    # Solusi rusak (SyntaxError) DINILAI: runner menangkap import gagal & mengemit
    # passed=0 ber-tag -> measured True, skor 0.0 (0.0 sungguhan, bukan artefak).
    score, measured = harness._score_reference("def slugify(text) return x", REFERENCE_TEST)
    assert score == 0.0
    assert measured is True


def test_score_reference_good_solution_is_measured_one():
    score, measured = harness._score_reference(
        f"{FENCE}python\n{GOOD_CODE}{FENCE}", REFERENCE_TEST
    )
    assert score == 1.0
    assert measured is True


def test_score_task_carries_measured_flag():
    ok = score_task(f"{FENCE}python\n{GOOD_CODE}{FENCE}", REFERENCE_TEST)
    assert ok["measured"] is True
    forged = score_task(
        "import json, os\nprint(json.dumps({'passed':9,'total':9}))\nos._exit(0)\n",
        REFERENCE_TEST,
    )
    assert forged["measured"] is False
    assert forged["code"] == 0.0


def _minimal_runner(head: str) -> str:
    # Runner valid yang mengemit _TAG; `head` bervariasi (docstring/komentar/blank
    # sebelum `from __future__`) untuk menguji titik-sisip preamble.
    return (
        head
        + "from __future__ import annotations\n"
        + "import json, sys\n"
        + "try:\n"
        + "    from solution import slugify\n"
        + "    ok = 1 if slugify('Hi There') == 'hi-there' else 0\n"
        + "except Exception:\n"
        + "    ok = 0\n"
        + "print(_TAG + json.dumps({'passed': ok, 'total': 1}))\n"
        + "sys.exit(0)\n"
    )


@pytest.mark.parametrize(
    "head",
    ['"""Reference runner."""\n', "# a comment\n", "\n", "# c\n\n", ""],
)
def test_score_reference_preamble_injected_after_future_import(head):
    # Regresi verifikasi-adversarial: preamble harus disisipkan SETELAH `from
    # __future__` walau ada docstring/komentar/blank sebelumnya — kalau tidak,
    # `from __future__` tergeser dari baris-1 -> SyntaxError -> semua arm 0.0 palsu.
    runner = _minimal_runner(head)
    score, measured = harness._score_reference(
        f"{FENCE}python\n{GOOD_CODE}{FENCE}", runner
    )
    assert measured is True, f"runner dgn head={head!r} harus valid & terukur"
    assert score == 1.0


def test_score_reference_robust_to_partial_stdout_from_solution():
    # Verifikasi-adversarial (note): solusi menulis stdout parsial TANPA newline saat
    # import -> menempel di depan baris ber-tag runner. Scan berbasis rfind(tag) tetap
    # menemukan hasil tepercaya -> solusi benar tetap terukur 1.0 (bukan 0.0 palsu).
    solution = "import sys\nsys.stdout.write('hi')\n" + GOOD_CODE
    score, measured = harness._score_reference(solution, REFERENCE_TEST)
    assert score == 1.0
    assert measured is True


def test_mean_scores_ignores_measured_bool():
    # mean_scores hanya merata-ratakan kunci numerik; `measured` (bool) dilewati.
    s1 = {"code": 1.0, "has_tests": 1.0, "has_readme": 0.0, "composite": 0.85, "measured": True}
    s2 = {"code": 0.0, "has_tests": 1.0, "has_readme": 0.0, "composite": 0.15, "measured": False}
    avg = mean_scores([s1, s2])
    assert "measured" not in avg
    assert avg["code"] == 0.5
    assert avg["composite"] == pytest.approx(0.5)


def test_clean_env_strips_api_keys_keeps_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak2")
    monkeypatch.setenv("SOME_SECRET_KEY", "leak3")
    env = harness._clean_env()
    assert "PATH" in env
    assert not any(k.endswith("_API_KEY") for k in env)
    assert not any(k.endswith("_KEY") for k in env)


# --- score_task (komposit .7 / .15 / .15) ----------------------------------


def test_score_task_full_composite():
    body = (
        f"# slugify\n\n{FENCE}python\n{GOOD_CODE}{FENCE}\n\n"
        "def test_slugify():\n    assert True\n\n"
        "## README\nRun with pytest.\n"
    )
    result = score_task(body, REFERENCE_TEST)
    assert result["code"] == 1.0
    assert result["has_tests"] == 1.0
    assert result["has_readme"] == 1.0
    assert result["composite"] == pytest.approx(1.0)


def test_score_task_code_only_weighting():
    body = f"{FENCE}python\n{GOOD_CODE}{FENCE}\n"
    result = score_task(body, REFERENCE_TEST)
    assert result["code"] == 1.0
    assert result["has_tests"] == 0.0
    assert result["has_readme"] == 0.0
    assert result["composite"] == pytest.approx(0.7)  # 0.7*code only


def test_score_task_detects_tests_and_readme_independently():
    only_tests = "def test_x():\n    assert True\n"
    r1 = score_task(only_tests, REFERENCE_TEST)
    assert r1["has_tests"] == 1.0
    assert r1["has_readme"] == 0.0

    only_readme = "# My Project\nSome description of the tool.\n"
    r2 = score_task(only_readme, REFERENCE_TEST)
    assert r2["has_tests"] == 0.0
    assert r2["has_readme"] == 1.0


# --- run_baseline (equity: max_tokens dari ModelInfo, temp 0.0, CostMeter) --


async def test_run_baseline_returns_baseline_result():
    provider = FakeProvider(responses=[_code_response()])
    result = await run_baseline(SLUGIFY_GOAL, provider, "strong-model", _registry())
    assert isinstance(result, BaselineResult)
    assert result.output == GOOD_CODE
    assert set(result.usage_total) == {"strong-model"}
    assert result.usage_total["strong-model"].prompt_tokens == 120
    # 120/1000*0.003 + 240/1000*0.015 = 0.00036 + 0.0036
    assert result.cost_usd == pytest.approx(0.00396)
    assert result.duration_ms >= 0
    assert score_code(result.output, REFERENCE_TEST) == 1.0


async def test_run_baseline_uses_model_max_output_and_temp_zero():
    provider = _SpyProvider(_code_response())
    result = await run_baseline(SLUGIFY_GOAL, provider, "strong-model", _registry())
    assert isinstance(result, BaselineResult)
    assert provider.last_req is not None
    assert provider.last_req.max_tokens == 4_096  # dari ModelInfo, BUKAN 2048
    assert provider.last_req.max_tokens != 2048
    assert provider.last_req.temperature == 0.0  # EVAL_TEMPERATURE


# --- run_orchestration (delegasi ke aexecute PUBLIK) -----------------------


async def test_run_orchestration_delegates_to_public_aexecute():
    calls: list[str] = []

    class _Rt:
        async def aexecute(self, goal: str) -> RunResult:
            calls.append(goal)
            return RunResult(
                status="success",
                final=GOOD_CODE,
                partial_artifacts={},
                failed_task=None,
                usage_total={},
                cost_usd=0.01,
                duration_ms=100,
            )

    out = await run_orchestration(SLUGIFY_GOAL, _Rt())
    assert calls == [SLUGIFY_GOAL]
    assert isinstance(out, RunResult)
    assert score_code(out.final, REFERENCE_TEST) == 1.0


# --- compare (dict shape + *_estimated + winner by composite/cost) ---------


def test_compare_dict_shape_and_cost_tiebreak():
    orch = _run_result(cost_usd=0.01, duration_ms=3200)
    base = _baseline(cost_usd=0.05, duration_ms=1400)
    score = {"code": 1.0, "has_tests": 1.0, "has_readme": 1.0, "composite": 0.9}
    result = compare(orch, base, score, dict(score))
    assert set(result) == {
        "orch_cost",
        "base_cost",
        "orch_ms",
        "base_ms",
        "orch_composite",
        "base_composite",
        "orch_estimated",
        "base_estimated",
        "winner",
    }
    assert result["orch_cost"] == 0.01
    assert result["base_cost"] == 0.05
    assert result["orch_ms"] == 3200
    assert result["base_ms"] == 1400
    assert result["orch_composite"] == 0.9
    assert result["base_composite"] == 0.9
    assert result["orch_estimated"] is False
    assert result["base_estimated"] is False
    # composite seri -> sisi termurah (orch 0.01 < base 0.05) menang.
    assert result["winner"] == "orchestration"


def test_compare_winner_by_composite_over_cost():
    win = compare(
        _run_result(cost_usd=0.10), _baseline(cost_usd=0.01),
        {"composite": 0.9}, {"composite": 0.4},
    )
    assert win["winner"] == "orchestration"  # composite lebih tinggi kalahkan biaya
    lose = compare(
        _run_result(cost_usd=0.01), _baseline(cost_usd=0.10),
        {"composite": 0.3}, {"composite": 0.8},
    )
    assert lose["winner"] == "baseline"


def test_compare_failed_orchestration_loses():
    orch = _run_result(status="failed", final=None, failed_task="t1", cost_usd=0.001)
    base = _baseline(cost_usd=0.05)
    result = compare(orch, base, {"composite": 1.0}, {"composite": 0.5})
    assert result["winner"] == "baseline"  # orkestrasi gagal tak bisa menang


def test_compare_estimated_flags_from_usage_total():
    orch = _run_result(usage_total={"m": Usage(10, 20, estimated=True)})
    base = _baseline(usage_total={"m": Usage(10, 20, estimated=False)})
    result = compare(orch, base, {"composite": 0.5}, {"composite": 0.5})
    assert result["orch_estimated"] is True
    assert result["base_estimated"] is False


async def test_compare_via_fakeprovider_full_path():
    provider = FakeProvider(responses=[_code_response()])
    base = await run_baseline(SLUGIFY_GOAL, provider, "strong-model", _registry())
    orch = await run_orchestration(SLUGIFY_GOAL, _StubRuntime(GOOD_CODE))
    result = compare(
        orch,
        base,
        score_task(orch.final, REFERENCE_TEST),
        score_task(base.output, REFERENCE_TEST),
    )
    assert set(result) == {
        "orch_cost",
        "base_cost",
        "orch_ms",
        "base_ms",
        "orch_composite",
        "base_composite",
        "orch_estimated",
        "base_estimated",
        "winner",
    }
    assert result["base_estimated"] is False  # FakeProvider mengirim usage riil
    assert result["winner"] in {"orchestration", "baseline", "tie"}


# --- k=2 (rata-rata) --------------------------------------------------------


def test_mean_scores_averages():
    s1 = {"code": 1.0, "has_tests": 1.0, "has_readme": 0.0, "composite": 0.85}
    s2 = {"code": 0.0, "has_tests": 1.0, "has_readme": 0.0, "composite": 0.15}
    avg = mean_scores([s1, s2])
    assert avg["code"] == 0.5
    assert avg["has_tests"] == 1.0
    assert avg["composite"] == pytest.approx(0.5)


async def test_run_eval_runs_k_times_and_returns_compare_dict():
    provider = FakeProvider(responses=[_code_response(), _code_response()])
    runtimes: list[_StubRuntime] = []

    def make_runtime() -> _StubRuntime:
        rt = _StubRuntime(GOOD_CODE)
        runtimes.append(rt)
        return rt

    result = await run_eval(
        SLUGIFY_GOAL, REFERENCE_TEST, make_runtime, provider, "strong-model",
        _registry(), k=2,
    )
    assert len(runtimes) == 2  # tiap sisi berjalan EVAL_K=2 kali
    assert set(result) >= {"orch_composite", "base_composite", "winner"}
    # GOOD_CODE: code=1.0, tanpa test/README -> composite 0.7 (rata-rata k identik).
    assert result["orch_composite"] == pytest.approx(0.7)
    assert result["base_composite"] == pytest.approx(0.7)

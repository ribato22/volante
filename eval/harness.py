from __future__ import annotations

import ast
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from eval.stats import PairedDelta, paired_delta

from volante.agent import AgenticWorker
from volante.cost import CostMeter
from volante.providers.base import ProviderError
from volante.tools.run_python import RunPythonTool
from volante.tools.sandbox import read_model_file, sandbox_for
from volante.types import (
    CanonicalRequest,
    CapabilityUnavailableError,
    TextBlock,
    text,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from eval.tasks import EvalTask, RepoSpec

    from volante.providers.base import LLMProvider
    from volante.registry import Registry
    from volante.runtime import Runtime
    from volante.types import CanonicalResponse, RunResult, Usage

EVAL_TEMPERATURE: float = 0.0
EVAL_K: int = 2

# Batas byte untuk artefak yang ditulis MODEL di workspace (solution.py, test_*.py).
# `read_text()` tanpa plafon pada path yang dipilih model = alokasi yang dipilih
# model. 1 MB jauh di atas solusi mana pun yang masuk akal untuk tugas eval ini.
_MAX_ARTIFACT_BYTES: int = 1_000_000

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


# Pemisahan proses (forgery-defense robust) + kanal hasil ber-nonce. score_code
# meng-inject preamble ini SEBELUM body runner (SETELAH `from __future__`). Preamble:
#   (1) baca nonce dari stdin (dikonsumsi sebelum apa pun) -> set `_TAG`;
#   (2) spawn `_solution_server.py` di PROSES TERPISAH (satu-satunya yang import
#       solution) dan definisikan `call_solution(fn, *args)` (RPC JSON-baris).
# Runner memakai call_solution alih-alih `import solution`, jadi kode solusi tak
# pernah jalan di proses runner: ia tak bisa membaca nonce/expected dari memori
# maupun menyuntik baris ber-nonce ke stdout runner. isatty guard = aman manual.
_RESULT_PREAMBLE = (
    "import json as _aj, os as _aos, subprocess as _asp, sys as _asys, atexit as _aat\n"
    "_TAG = '' if _asys.stdin.isatty() else "
    "'AIORCH_RESULT:' + _asys.stdin.readline().strip() + ':'\n"
    # KRITIS (pemisahan FILESYSTEM): hapus source runner (yang memuat CASES+expected)
    # SETELAH runpy me-load-nya ke memori tapi SEBELUM spawn server. Jadi solusi di
    # server tak bisa `open('reference_runner.py')` untuk memanen jawaban. Fail-closed:
    # bila remove gagal, preamble raise -> runner crash -> tak-terukur (bukan forgery).
    "_aos.remove('reference_runner.py')\n"
    "_srv = _asp.Popen([_asys.executable, '_solution_server.py'], "
    "stdin=_asp.PIPE, stdout=_asp.PIPE, text=True)\n"
    "def _shutdown_srv():\n"
    "    try:\n"
    "        _srv.stdin.close()\n"
    "    except Exception:\n"
    "        pass\n"
    "    _srv.terminate()\n"
    "_aat.register(_shutdown_srv)\n"
    "def call_in(_mod, _fn, *_args):\n"
    "    _srv.stdin.write(_aj.dumps({'mod': _mod, 'fn': _fn, 'args': list(_args)}) + '\\n')\n"
    "    _srv.stdin.flush()\n"
    "    _line = _srv.stdout.readline()\n"
    "    if not _line:\n"
    "        raise RuntimeError('solution server exited')\n"
    "    _r = _aj.loads(_line)\n"
    "    if not _r.get('ok'):\n"
    "        raise RuntimeError(_r.get('error', 'solution error'))\n"
    "    return _r['result']\n"
    "def call_solution(_fn, *_args):\n"
    "    return call_in('solution', _fn, *_args)\n"
)


def _solution_server_src(cpu: int) -> str:
    """Sumber `_solution_server.py`: proses TERPISAH, satu-satunya yang meng-import
    solution. Baca `{"fn","args"}` per baris stdin -> `getattr(sol, fn)(*args)` ->
    tulis `{"ok",...}`. RLIMIT_CPU child-side (S2 parity). Tak pernah menerima nonce
    atau expected -> solusi tak bisa memalsukan LULUS (harus benar-benar menghitung).
    `except BaseException` di import & call: SystemExit solusi tak membunuh server.

    The repo lives in a SIBLING directory and is put on `sys.path` only after every name
    this server needs is already bound. Reproduced before this was written: a model file
    named `json.py` sitting where a trusted process imports it makes that process print
    a nonce-tagged line claiming 42/42 against a real result of 0/42. Binding first means
    a shadowing repo module can break the MODEL's own code and nothing else."""
    return "\n".join(
        [
            "import importlib, json, os, resource, sys",
            f"_c = {cpu}",
            "resource.setrlimit(resource.RLIMIT_CPU, (_c, _c))",
            # Kanal RPC = dup stdout asli (pipe ke runner); lalu fd 1 DAN fd 2 ->
            # devnull SEBELUM import solution, jadi output stdout/stderr solusi dibuang
            # (tak mengotori RPC; tak membanjiri buffer stderr parent). Robust thd
            # polusi stdio saat import.
            "_rpc = os.fdopen(os.dup(1), 'w')",
            "_dn = os.open(os.devnull, os.O_WRONLY)",
            "os.dup2(_dn, 1)",
            "os.dup2(_dn, 2)",
            # Same trick on fd 0: the RPC channel is a private dup, and fd 0 becomes
            # devnull BEFORE any repo code runs. A module whose import calls input()
            # would otherwise eat the next RPC request, or block forever holding the
            # whole score hostage — recorded as a timeout, which reads as the model
            # being slow rather than the harness handing it our request stream.
            "_rin = os.fdopen(os.dup(0), 'r')",
            "_dn0 = os.open(os.devnull, os.O_RDONLY)",
            "os.dup2(_dn0, 0)",
            # Every name above is bound BEFORE the repo can shadow it.
            "sys.path.insert(0, os.path.join(os.pardir, 'repo'))",
            "importlib.invalidate_caches()",
            "_mods = {}",
            "def _load(_name):",
            "    if _name not in _mods:",
            "        try:",
            "            _mods[_name] = importlib.import_module(_name)",
            "        except BaseException:",
            "            _mods[_name] = None",
            "    return _mods[_name]",
            "_load('solution')",
            "for _line in _rin:",
            "    try:",
            "        _req = json.loads(_line)",
            "        _tgt = _load(_req.get('mod') or 'solution')",
            "        _res = getattr(_tgt, _req['fn'])(*_req['args'])",
            "        _out = json.dumps({'ok': True, 'result': _res})",
            "    except BaseException as _e:",
            "        _out = json.dumps({'ok': False, 'error': str(_e)[:200]})",
            "    _rpc.write(_out + '\\n')",
            "    _rpc.flush()",
            "",
        ]
    )


def _inject_preamble(reference_test: str, nonce_tag_src: str) -> str:
    """Sisipkan preamble hasil SETELAH statement `from __future__` TERAKHIR (bila
    ada) — impor __future__ wajib mendahului statement lain, jadi preamble (statement
    biasa) tak boleh menggesernya. Pakai AST agar tahan docstring/komentar/baris
    kosong sebelum future-import (Python mengizinkannya) dan tak salah-cocok teks
    '__future__' di dalam string. Runner rusak (SyntaxError) -> prepend saja; error
    muncul saat dijalankan -> tak-terukur (disurface run_suite)."""
    try:
        tree = ast.parse(reference_test)
    except SyntaxError:
        return nonce_tag_src + reference_test
    last_future_end = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            last_future_end = max(last_future_end, node.end_lineno or 0)
    if last_future_end == 0:
        return nonce_tag_src + reference_test
    lines = reference_test.split("\n")
    head = "\n".join(lines[:last_future_end])  # s/d baris future terakhir (1-based)
    tail = "\n".join(lines[last_future_end:])
    return head + "\n" + nonce_tag_src + tail


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
# Dipisah dari _PY_FENCE: pembuka dan penutup dicari terpisah supaya penutupnya bisa
# DIPILIH (lihat extract_python), bukan sekadar yang pertama muncul.
_PY_FENCE_OPEN = re.compile(
    _TRIPLE + r"[^\S\r\n]*python[^\S\r\n]*\r?\n", re.IGNORECASE
)
# Penutup harus di awal baris: ``` di tengah baris adalah bagian dari teks.
_FENCE_CLOSE = re.compile(r"(?m)^[^\S\r\n]*" + _TRIPLE)
_README_HEADING = re.compile(r"(?m)^#{1,6}\s+\S")

# The path a multi-file answer declares, as the FIRST line inside the fence.
#
# Measured, and this is not a style choice: putting the path in the fence info string
# (```python path=app/store.py) destroys both extractors. `_PY_FENCE_OPEN` stops
# matching entirely, so `extract_python` returns the raw text WITH its backticks, and
# `src/volante/assembler.py` paired the first block's closing fence with the second
# block's opening fence and captured the PROSE between them — both files dropped, the
# commentary kept. A first-line comment survives every extractor already in this repo
# untouched, because it is ordinary Python.
_PATH_COMMENT = re.compile(r"^[^\S\r\n]*#[^\S\r\n]*path[^\S\r\n]*:[^\S\r\n]*(\S+)[^\S\r\n]*$")

_MAX_REPO_FILES = 24


@dataclass(frozen=True)
class LayoutReport:
    """What the extractor could and could not attribute — never a silent 0.0.

    A repo goal scored 0.0 because no block carried a path is a HARNESS artifact, not a
    model that failed. The two are indistinguishable from the score alone, which is the
    exact confusion `unmeasured_goals` was added to end for the single-file arms.
    """

    attributed: int
    unattributed_blocks: int
    refused_paths: tuple[str, ...]
    fmt: str  # "labelled" | "unlabelled" | "empty"

    @property
    def usable(self) -> bool:
        return self.attributed > 0


def _safe_repo_path(raw: str) -> str | None:
    """The declared path, or None when it must not be written.

    Refuses anything that could escape the repo directory or land on the grading
    machinery. Reproduced before this was written: a file the model names `json.py`,
    written where the TRUSTED runner can import it, makes that runner print a
    nonce-tagged line claiming 42/42 while the real result was 0/42 — total collapse of
    the forgery defence. The structural fix is the repo/ vs _grade/ split; this is the
    second lock, because one lock on a forgery defence is not enough.
    """
    path = raw.strip().strip("`'\"")
    if not path or path.startswith(("/", "\\", "~")) or ":" in path:
        return None
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if any(p in ("..", ".", "") for p in parts):
        return None
    if any(c in path for c in "\x00\n\r"):
        return None
    return "/".join(parts)


def extract_files(text_in: str) -> tuple[dict[str, str], LayoutReport]:
    """Every fenced block that declares `# path: <p>` on its first line.

    Reuses `extract_python`'s closing-fence choice per block: the close is the one whose
    body PARSES, not the first one seen, because a model that embeds a README containing
    ```bash inside a triple-quoted string otherwise truncates its own file mid-string.
    That bug scored a working answer 0.000 and is the reason `extract_python` looks the
    way it does; a second extractor that forgot it would reintroduce it.
    """
    files: dict[str, str] = {}
    refused: list[str] = []
    unattributed = 0
    pos = 0
    while (m := _PY_FENCE_OPEN.search(text_in, pos)) is not None:
        body = text_in[m.end() :]
        first: str | None = None
        chosen: str | None = None
        end = len(text_in)
        for close in _FENCE_CLOSE.finditer(body):
            candidate = body[: close.start()]
            if first is None:
                first, end = candidate, m.end() + close.end()
            try:
                ast.parse(candidate)
            except SyntaxError:
                continue
            chosen, end = candidate, m.end() + close.end()
            break
        block = chosen if chosen is not None else first
        if block is None:
            break
        pos = end
        head = block.split("\n", 1)[0]
        pm = _PATH_COMMENT.match(head)
        if pm is None:
            unattributed += 1
            continue
        safe = _safe_repo_path(pm.group(1))
        if safe is None:
            refused.append(pm.group(1).strip())
            continue
        if len(files) >= _MAX_REPO_FILES and safe not in files:
            refused.append(pm.group(1).strip())
            continue
        # Later block wins: a model that revises a file mid-answer means the revision.
        files[safe] = block
    fmt = "labelled" if files else ("unlabelled" if unattributed else "empty")
    return files, LayoutReport(len(files), unattributed, tuple(refused), fmt)


@dataclass
class BaselineResult:
    output: str
    usage_total: dict[str, Usage]
    cost_usd: float
    duration_ms: int


def _extract_text(resp: CanonicalResponse) -> str:
    return "".join(b.text for b in resp.content if isinstance(b, TextBlock))


def extract_python(text_in: str) -> str:
    """Isi blok ```python pertama; fallback teks mentah bila tak ada fence.

    Penutup fence dipilih yang menghasilkan kode YANG BISA DI-PARSE, bukan yang
    pertama ditemui. Sebabnya terukur pada keluaran orkestrasi nyata: model menulis
    solution.py lalu menyisipkan README yang diminta ke dalam string triple-quote,
    dan README itu memuat contoh ```bash. Pencocokan non-greedy berhenti DI SANA —
    di tengah string literal — sehingga kode terekstrak berakhir dengan string tak
    tertutup, gagal di-import, dan SELURUH case gagal. Run itu berskor 0,000 dengan
    `resolve` yang baik-baik saja di dalamnya.

    Yang dihukum adalah penulis SATU blok mandiri, yaitu bentuk keluaran sebuah
    sintesis, jadi arm orkestrasi terkena jauh lebih sering daripada baseline:
    artefak penilaian yang terbaca sebagai selisih kemampuan.

    Perilaku lama dipertahankan persis ketika penutup pertama sudah benar — kandidat
    diuji berurutan, jadi blok pertama yang valid tetap yang menang.
    """
    m = _PY_FENCE_OPEN.search(text_in)
    if m is None:
        return text_in
    body = text_in[m.end() :]
    first: str | None = None
    for close in _FENCE_CLOSE.finditer(body):
        candidate = body[: close.start()]
        if first is None:
            first = candidate
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        return candidate
    # Tak ada kandidat yang parse: kembalikan yang pertama (perilaku lama) agar
    # kode yang memang rusak tetap dinilai rusak, bukan disembunyikan.
    return first if first is not None else body


def _clean_env() -> dict[str, str]:
    """Env subprocess: HANYA PATH. Membuang semua *_API_KEY / *_KEY (dan lainnya)
    sehingga kode yang dinilai tak pernah bisa membaca/mengeksfiltrasi kredensial."""
    return {"PATH": os.environ.get("PATH", "")}


def score_code(model_output: str, reference_test: str) -> float:
    """Skor code 0..1 dari runner referensi (lihat _score_reference). 0.0 mencakup
    baik 'terukur tapi 0 lulus' maupun 'tak terukur' — pakai _score_reference bila
    perlu membedakannya (mis. deteksi runner rusak, H2)."""
    return _score_reference(model_output, reference_test)[0]


def _score_reference(
    model_output: str,
    reference_test: str,
    repo_files: Mapping[str, str] | None = None,
) -> tuple[float, bool]:
    """Ekstrak kode, jalankan runner referensi di subprocess terisolasi. Kembalikan
    (skor, measured): `measured=True` HANYA bila runner mengemit baris hasil
    ber-nonce tepercaya; `measured=False` = tak terukur (timeout/crash/runner rusak)
    dan skor dipaksa 0.0.

    Pemisahan proses (forgery-defense robust): runner (tepercaya) TAK meng-import
    solution — ia menggerakkan `_solution_server.py` (proses terpisah) via
    `call_solution` (RPC), memegang expected+nonce PRIVAT. Solusi hanya menerima input
    di proses lain -> tak bisa membaca nonce/expected dari memori maupun menyuntik
    baris ber-nonce ke stdout runner; untuk LULUS ia wajib benar-benar menghitung.
    Hanya baris `AIORCH_RESULT:<nonce>:{json}` (dari runner) dipercaya.

    Isolasi (S2): runner via `_SCORE_WRAPPER` (RLIMIT_CPU + `start_new_session=True`);
    server di-spawn runner tanpa setsid -> SEGRUP -> `killpg` (timeout MAUPUN tiap exit)
    membunuh runner+server+fork. Server punya RLIMIT_CPU sendiri. Batas tetap best-effort
    POSIX (setsid oleh solusi di server bisa lepas grup; tetap tak bisa forgery)."""
    code = extract_python(model_output)
    nonce = secrets.token_hex(16)
    tag = f"AIORCH_RESULT:{nonce}:"
    runner_src = _inject_preamble(reference_test, _RESULT_PREAMBLE)
    with tempfile.TemporaryDirectory() as tmp:
        # TWO directories, and the split IS the forgery defence. The runner holds the
        # nonce and runs with cwd=_grade, whose contents this harness alone writes, so
        # `sys.path[0]` never contains a file the model named. Everything the model
        # produced lives in repo/, reachable only by the server, and only after the
        # server has bound its own imports. Verified end to end: with the two merged, a
        # model file called `json.py` made the trusted runner print a nonce-tagged
        # 42/42 for a solution that really scored 0/42.
        grade = Path(tmp, "_grade")
        repo = Path(tmp, "repo")
        grade.mkdir()
        repo.mkdir()
        for rel, body in (repo_files or {"solution.py": code}).items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        Path(grade, "reference_runner.py").write_text(runner_src, encoding="utf-8")
        Path(grade, "_solution_server.py").write_text(
            _solution_server_src(SCORE_CPU_S), encoding="utf-8"
        )
        # Popen (bukan subprocess.run): agar timeout bisa killpg SELURUH grup.
        # subprocess.run hanya mem-proc.kill() anak langsung, lalu communicate reap
        # bisa MENGGANTUNG bila anak fork masih memegang pipe stdout.
        proc = subprocess.Popen(
            [sys.executable, "-c", _SCORE_WRAPPER, str(SCORE_CPU_S), "reference_runner.py"],
            cwd=str(grade),
            env=_clean_env(),
            stdin=subprocess.PIPE,  # kanal nonce (dikonsumsi preamble sebelum solusi)
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # grup proses sendiri -> killpg bunuh fork/cucu
        )
        try:
            try:
                out, _err = proc.communicate(input=f"{nonce}\n", timeout=SCORE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                _killpg(proc.pid)
                try:
                    proc.communicate(timeout=5)  # reap grup yang sudah di-SIGKILL
                except subprocess.TimeoutExpired:
                    pass
                return (0.0, False)
            # Percaya HANYA baris memuat tag (nonce cocok); ambil kemunculan tag
            # terakhir. Pakai `tag in line` (bukan startswith) agar output parsial
            # solusi tanpa newline yang menempel di depan baris runner tak merusak
            # parse — nonce tetap tak terpalsukan, jadi ini tetap aman.
            payload = None
            for line in out.splitlines():
                idx = line.rfind(tag)
                if idx != -1:
                    payload = line[idx + len(tag):]
            if payload is None:
                return (0.0, False)  # tak ada hasil tepercaya -> tak terukur
            try:
                data = json.loads(payload)
                total = int(data["total"])
                passed = int(data["passed"])
            except (ValueError, KeyError):
                return (0.0, False)  # baris ber-tag rusak -> tak terukur
        finally:
            # killpg TIAP jalur keluar: anak fork yang MELEPAS pipe (close fd 1/2)
            # lalu spin membuat runner selesai NORMAL (bukan timeout) -> tanpa ini
            # ia jadi orphan yang menyintasi skor. Grup sudah mati di jalur timeout
            # (killpg kedua ini no-op/ProcessLookupError, ditelan _killpg).
            _killpg(proc.pid)
    if total <= 0:
        return (0.0, True)  # terukur, tapi runner melaporkan total 0
    return (max(0.0, min(1.0, passed / total)), True)


def score_text(output: str, checks) -> float:
    """Pass fraction over a text goal's checks.

    A check that raises counts as FAILED rather than crashing the run: a rubric bug
    must not be able to abort a measurement campaign, and silently treating it as a
    pass would inflate every score. Negative checks (`must be absent`) are inverted
    here, so the arithmetic stays a plain fraction of satisfied requirements.
    """
    if not checks:
        return 0.0
    satisfied = 0
    for check in checks:
        try:
            hit = bool(check.passes(output))
        except Exception:
            hit = check.negative  # -> counts as unsatisfied either way
        if hit != check.negative:
            satisfied += 1
    return satisfied / len(checks)


def score_for(task, output: str) -> float:
    """Score any goal, dispatching on its task type.

    Code goals run the model's program against hidden cases; every other type runs
    a mechanical rubric over its prose. Both return 0..1, so a calibration file can
    mix them without the caller knowing which is which.
    """
    if task.task_type == "code":
        return score_code(output, task.reference_test)
    return score_text(output, task.checks)


def score_for_calibration(task, output: str) -> float | None:
    """Score a goal for a CALIBRATION file, where ``None`` writes JSON ``null``.

    `volante.calibrate` documents ``null`` as "this run produced nothing usable" and
    counts those runs separately from graded ones, deliberately: a crash is evidence
    about RELIABILITY, a low score is evidence about QUALITY, and grading a crash 0.0
    makes the reliability component a copy of the quality one. Nothing in this
    harness could ever emit it — ``score_for`` calls ``score_code``, which throws away
    the ``measured`` flag that ``_score_reference`` computes for exactly this
    distinction — so `reliability_score` was unreachable from our own pipeline.

    The 3-arm path (``compare_arms``) already carries ``measured`` through and uses it
    to spot a broken reference runner. This is the same idea on the calibration path,
    which is the one whose numbers reach the router.
    """
    if task.task_type == "code":
        score, measured = _score_reference(output, task.reference_test)
        return score if measured else None
    return score_text(output, task.checks)


def repo_files_for(output: str, repo: RepoSpec | None) -> dict[str, str] | None:
    """The tree to grade: the starting repo with the model's files laid over it.

    Returns None for a single-file goal, which keeps the legacy path byte-identical.

    The starting files are written FIRST and the model's on top, so a model that
    rewrites `pricing.py` replaces it and a model that never mentions it still gets the
    original — which is what makes "did you break something you were not asked to
    touch" a gradeable question at all.
    """
    if repo is None:
        return None
    files = dict(repo.starting_files)
    written, _report = extract_files(output)
    files.update(written)
    # Probes last, unconditionally. A model that writes a file with the probe's name
    # would otherwise replace the module that grades it — the same class of hole as
    # naming a file `json.py`, one layer up.
    files.update(dict(repo.probe_files))
    return files


def score_task(
    output: str, reference_test: str, repo: RepoSpec | None = None
) -> dict[str, float]:
    """Skor komposit berbobot: code .7 / has_tests .15 / has_readme .15.

    `reference_test` diteruskan apa adanya ke score_code (runner per-goal). Key
    non-numerik `measured` (bool) ikut dibawa untuk sinyal H2; mean_scores
    mengabaikannya saat merata-rata.

    A REPO goal scores on code alone. Its prompt states the existing project already
    has tests and a README, so every competent model emits both and the two flags
    become constants worth 0.30 — a floor under an answer that changed nothing, which
    is precisely the free-point rule this suite exists to respect. They are still
    reported, because a flag that stopped being scored should not also stop being
    visible."""
    repo_files = repo_files_for(output, repo)
    code, measured = _score_reference(output, reference_test, repo_files)
    has_tests = 1.0 if "def test_" in output else 0.0
    has_readme = (
        1.0
        if ("readme" in output.lower() or _README_HEADING.search(output))
        else 0.0
    )
    composite = (
        code if repo is not None else 0.7 * code + 0.15 * has_tests + 0.15 * has_readme
    )
    return {
        "code": code,
        "has_tests": has_tests,
        "has_readme": has_readme,
        "composite": composite,
        "measured": measured,
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

# A repo goal must NOT be told to write `solution.py`: the project already has its own
# filenames and two of this suite's criteria are about editing the right files. The
# working copy is seeded on disk, so the instruction is to edit it in place — and the
# read-back collects the whole tree instead of one hardcoded name.
_AGENTIC_REPO_SUFFIX = (
    "\n\nThe project files above are already in your current working directory. Edit "
    "them in place — keep the existing filenames. Also add a pytest test module "
    "(test_*.py). Use the run_python tool to run your tests and iterate until they "
    "pass. When done, briefly summarize."
)

# Read back at most this many source files from an agentic workspace. A tree is the
# model's to shape, but the number of files it may hand back is not unbounded.
_MAX_WORKSPACE_FILES = 24


def _collect_workspace_repo(ws: Path) -> dict[str, str]:
    """Every source file the workspace holds, keyed by path relative to it.

    The seeded files are here too, so a model that edited two of four still yields the
    whole working tree — which is what makes "you broke the file you did not touch"
    a question with an answer.

    Test modules are skipped: they are the model's scratch work, they are already
    scored by `_scan_workspace`, and a `test_*.py` that imports pytest would be one
    more way for a graded import to fail for a reason that is not the model's code.
    """
    found: dict[str, str] = {}
    for path in sorted(ws.rglob("*.py")):
        rel = path.relative_to(ws).as_posix()
        name = path.name.lower()
        if name.startswith("test_") or name.endswith("_test.py"):
            continue
        if len(found) >= _MAX_WORKSPACE_FILES:
            break
        body = read_model_file(path, max_bytes=_MAX_ARTIFACT_BYTES)
        if body is not None:
            found[rel] = body
    return found


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
    # Tool names the loop actually invoked. Empty means the model answered without
    # touching a tool — legitimate here, but worth seeing next to the score.
    tools_used: tuple[str, ...] = ()
    # The working tree read back from the workspace, for a repo goal. None for a
    # single-file goal, where `solution_code` is the whole answer.
    repo_files: dict[str, str] | None = None


def _scan_workspace(ws: Path) -> tuple[bool, bool]:
    has_tests = False
    has_readme = False
    # Recursive: a repo goal hands the model a tree, and a model that puts its tests in
    # `tests/` — the ordinary place for them — was being marked as having written none.
    for p in ws.rglob("*"):
        name = p.name.lower()
        # `is_file()` + `read_text()` FOLLOW symlinks, and this workspace is the
        # sandbox bind mount — the model can name any host file `test_x.py` and be
        # credited for it. read_model_file refuses anything that is not a regular
        # file the model itself wrote here.
        if name.startswith("test_") or name.endswith("_test.py"):
            body = read_model_file(p, max_bytes=_MAX_ARTIFACT_BYTES)
            if body is not None and "def test_" in body:
                has_tests = True
        if name.startswith("readme"):
            has_readme = read_model_file(p, max_bytes=1) is not None or has_readme
    return has_tests, has_readme


async def run_agentic_single(
    goal: str,
    provider: LLMProvider,
    model_id: str,
    registry: Registry,
    repo: RepoSpec | None = None,
) -> AgenticArmResult:
    """Arm agentic-single: 1 model + loop run_python, TANPA dekomposisi. Model diminta
    menulis solution.py + tests + README di workspace; skor diambil dari file itu.

    For a repo goal the workspace is SEEDED with the starting tree and the model edits
    it in place. Without that this arm was structurally unable to attempt such a goal:
    it was ordered to write `solution.py`, and only `solution.py` was ever read back,
    so any cross-arm number would have recorded a harness limit as a capability gap."""
    meter = CostMeter()
    # 16 was tried and measured worse than 8 (failures 35% -> 53%); the engine default
    # is the honest choice here, so the arm reports what a user would actually get.
    worker = AgenticWorker({model_id: provider}, meter)
    mi = registry.get(model_id)
    start = time.perf_counter()
    error: str | None = None
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        if repo is not None:
            for rel, body in repo.starting_files:
                target = ws / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
        tools = {"run_python": RunPythonTool(sandbox_for(ws))}
        suffix = _AGENTIC_SUFFIX if repo is None else _AGENTIC_REPO_SUFFIX
        req = CanonicalRequest(
            messages=[text("user", goal + suffix)],
            max_tokens=mi.max_output_tokens,
            temperature=EVAL_TEMPERATURE,
            run_id="agentic",
            task_id="agentic",
        )
        try:
            # Tools are OFFERED here, not required: this arm measures what one model
            # achieves with a tool loop available. A model that solves the goal in a
            # single turn without tools has done the arm's job in the cheapest possible
            # way; scoring that as a terminal 0.0 measured our contract, not the model.
            res = await worker.run(req, model_id, tools, required_tools=frozenset())
            final_text = res.final_text
            usage_total = res.usage_total
            tools_used = res.tools_used
        except (ProviderError, CapabilityUnavailableError) as exc:
            # Jangan telan diam-diam: catat sebab. Solusi di disk (bila model sempat
            # menulisnya sebelum gagal) tetap dinilai; kalau tak ada, error yang
            # disurface membedakan kegagalan arm dari solusi 0.0 sungguhan.
            final_text = ""
            usage_total = meter.totals()
            tools_used = ()
            error = f"{type(exc).__name__}: {exc}"
        repo_files: dict[str, str] | None = None
        if repo is not None:
            repo_files = _collect_workspace_repo(ws)
            # The final message is the fallback for a model that answered in chat
            # rather than on disk. Laid UNDER the workspace, so a file it actually
            # wrote always wins over a copy it pasted into its summary.
            spoken, _report = extract_files(final_text)
            repo_files = {**spoken, **repo_files}
            solution_code = ""
        else:
            solution_code = read_model_file(
                ws / "solution.py", max_bytes=_MAX_ARTIFACT_BYTES
            ) or extract_python(final_text)
        has_tests, has_readme = _scan_workspace(ws)
    duration_ms = int((time.perf_counter() - start) * 1000)
    return AgenticArmResult(
        solution_code=solution_code,
        repo_files=repo_files,
        has_tests=has_tests,
        has_readme=has_readme,
        usage_total=usage_total,
        cost_usd=meter.cost_usd(registry),
        duration_ms=duration_ms,
        error=error,
        tools_used=tuple(tools_used),
    )


def score_agentic(
    res: AgenticArmResult, reference_test: str, repo: RepoSpec | None = None
) -> dict[str, float]:
    """Skor komposit arm agentic dari workspace (analog score_task, file-aware).
    Membawa `measured` (bool) untuk sinyal H2, seperti score_task.

    A repo goal is graded from the working TREE the arm left behind, with the probes
    laid over it — the same rule `repo_files_for` applies to the other arms, so the
    three are scored by one standard and not three."""
    if repo is not None:
        files = {**dict(repo.starting_files), **(res.repo_files or {})}
        files.update(dict(repo.probe_files))
        code, measured = _score_reference("", reference_test, files)
    else:
        code, measured = _score_reference(res.solution_code, reference_test)
    ht = 1.0 if res.has_tests else 0.0
    hr = 1.0 if res.has_readme else 0.0
    return {
        "code": code,
        "has_tests": ht,
        "has_readme": hr,
        # Code alone for a repo goal, exactly as in `score_task`. Weighting the two
        # flags here would put a 0.30 floor under an agentic run that changed nothing,
        # and would do it for ONE arm — a scale that differs by arm cannot compare arms.
        "composite": code if repo is not None else 0.7 * code + 0.15 * ht + 0.15 * hr,
        "measured": measured,
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
    """Rata-rata per-kunci dari daftar dict score_task (untuk agregasi k-run).

    Hanya kunci numerik yang dirata-ratakan; kunci bool seperti `measured` (bool
    adalah subclass int) sengaja dilewati — agregasinya ditangani run_suite."""
    if not scores:
        return {"code": 0.0, "has_tests": 0.0, "has_readme": 0.0, "composite": 0.0}
    n = len(scores)
    keys = [
        k
        for k, v in scores[0].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
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



def _delta_dict(d: PairedDelta) -> dict[str, Any]:
    """A paired difference, serialised so an artifact carries its own uncertainty."""
    return {
        "n": d.n,
        "mean": d.mean,
        "ci_low": d.ci_low,
        "ci_high": d.ci_high,
        "detectable": d.detectable,
        "significant": d.significant,
    }


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
        b_runs: list[BaselineResult] = []
        o_runs: list[RunResult] = []
        a_runs: list[AgenticArmResult] = []
        for _ in range(k):
            b_run = await run_baseline(t.goal, baseline_provider, model_id, registry)
            o_run = await run_orchestration(t.goal, make_runtime())
            a_run = await run_agentic_single(
                t.goal, baseline_provider, model_id, registry, t.repo
            )
            b_runs.append(b_run)
            o_runs.append(o_run)
            a_runs.append(a_run)
            b_scores.append(score_task(b_run.output, t.reference_test, t.repo))
            o_scores.append(score_task(o_run.final or "", t.reference_test, t.repo))
            a_scores.append(score_agentic(a_run, t.reference_test, t.repo))
            if a_run.error is not None:
                a_errors.append(a_run.error)
        # The per-iteration composites, kept rather than averaged away: baseline,
        # orchestration and agentic run inside the SAME loop iteration above, so these
        # are matched triples and the difference can be analysed pair by pair. Averaging
        # first throws that away and turns run-to-run drift into apparent effect — which
        # is how +0.289 was published here and then failed to reproduce.
        b_series = [s["composite"] for s in b_scores]
        o_series = [s["composite"] for s in o_scores]
        a_series = [s["composite"] for s in a_scores]
        b_mean = mean_scores(b_scores)
        o_mean = mean_scores(o_scores)
        a_mean = mean_scores(a_scores)
        # measured per-arm: True bila ADA iterasi yang menghasilkan hasil ber-nonce
        # tepercaya. Semua arm unmeasured utk satu goal = sinyal kuat runner rusak (H2).
        b_measured = any(s.get("measured", True) for s in b_scores)
        o_measured = any(s.get("measured", True) for s in o_scores)
        a_measured = any(s.get("measured", True) for s in a_scores)
        deltas = {
            "orchestration_vs_baseline": _delta_dict(
                paired_delta(b_series, o_series)
            ),
            "agentic_vs_baseline": _delta_dict(paired_delta(b_series, a_series)),
        }
        series = {"baseline": b_series, "orchestration": o_series, "agentic": a_series}
        arms = {
            "baseline": {
                "composite": b_mean["composite"],
                # Cost is the actual total spent across all k repetitions; latency is
                # the mean observed duration so reports remain comparable across k.
                "cost": sum(run.cost_usd for run in b_runs),
                "ms": round(sum(run.duration_ms for run in b_runs) / len(b_runs)),
                "estimated": any(
                    usage.estimated
                    for run in b_runs
                    for usage in run.usage_total.values()
                ),
                "measured": b_measured,
            },
            "orchestration": {
                "composite": o_mean["composite"],
                "cost": sum(run.cost_usd for run in o_runs),
                "ms": round(sum(run.duration_ms for run in o_runs) / len(o_runs)),
                "estimated": any(
                    usage.estimated
                    for run in o_runs
                    for usage in run.usage_total.values()
                ),
                "measured": o_measured,
            },
            "agentic": {
                "composite": a_mean["composite"],
                "cost": sum(run.cost_usd for run in a_runs),
                "ms": round(sum(run.duration_ms for run in a_runs) / len(a_runs)),
                "estimated": any(
                    usage.estimated
                    for run in a_runs
                    for usage in run.usage_total.values()
                ),
                "measured": a_measured,
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
            # The paired analysis, and the per-iteration series it came from. A mean
            # without these cannot say whether it is a finding or noise, which is what
            # produced three corrections in this project.
            "deltas": deltas,
            "series": series,
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
    # Goal yang TAK SATU arm pun terukur (tak ada hasil ber-nonce tepercaya) = sinyal
    # kuat runner referensi-nya rusak (H2): skor 0.0-nya artefak harness, bukan solusi.
    unmeasured_goals = [
        g["id"]
        for g in per_goal
        if not any(g["arms"][n].get("measured", True) for n in names)
    ]
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
            "unmeasured_goals": unmeasured_goals,
            "verdict": verdict,
        },
    }

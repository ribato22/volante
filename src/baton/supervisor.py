from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from baton.cost import CostMeter
from baton.providers.base import LLMProvider, call_provider
from baton.types import (
    CanonicalRequest,
    CanonicalResponse,
    Task,
    TextBlock,
    text,
)

_PLAN_SYSTEM = (
    "You are a planning supervisor. Decompose the user's goal into a minimal "
    "directed acyclic graph (DAG) of tasks. Reply with ONLY a JSON array. Each "
    "element is an object with these exact keys: "
    '"id" (unique string), "description" (string), '
    '"type" (one of: research, code, write, analyze), '
    '"mode" (one of: one_shot, agentic), '
    '"difficulty" (one of: trivial, easy, medium, hard), '
    'and "depends_on" (array of task ids that must finish first). '
    "Do not include any prose or markdown outside the JSON array."
)

_DIFFICULTIES: set[str] = {"trivial", "easy", "medium", "hard"}

# Live-observed finding: a subscription-CLI planner (e.g. `claude -p`) can
# probabilistically ANSWER the goal (prose/markdown) instead of emitting the
# strict JSON task array, even with _PLAN_SYSTEM. A single-shot plan has no
# recovery, so plan() retries a bounded number of times with a corrective
# follow-up before giving up.
_MAX_PLAN_ATTEMPTS = 3


def _plan_correction(exc: Exception) -> str:
    # Feed the ACTUAL rejection reason back to the model: a parse error
    # ("planner did not return valid JSON: ...") and a validate error ("plan
    # is empty", "contains a dependency cycle", ...) need different fixes, so
    # a static "send JSON only" nudge gives zero signal when the reply was
    # already valid JSON that merely failed structural validation.
    return (
        f"Your previous reply was rejected: {exc}. Reply with ONLY a valid "
        "JSON array of task objects (keys: id, description, type, mode, "
        "difficulty, depends_on) — do not answer or explain the goal, output "
        "nothing but the JSON array."
    )


class Supervisor:
    def __init__(
        self,
        provider: LLMProvider,
        model_id: str,
        cost_meter: CostMeter,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._cost_meter = cost_meter
        self._used = False

    async def plan(
        self, goal: str, on_text: Callable[[str], None] | None = None
    ) -> list[Task]:
        if self._used:
            raise RuntimeError(
                "Supervisor.plan is non-re-entrant; use a fresh Supervisor per run"
            )
        self._used = True
        req = self._build_request(goal)
        first_exc: ValueError | None = None
        for attempt in range(1, _MAX_PLAN_ATTEMPTS + 1):
            # on_text -> streaming (progres planning live); else complete (nol
            # regresi). NOTE: on retry, a partial re-stream of the corrected
            # attempt is re-emitted to on_text — acceptable (bounded retries).
            resp = await call_provider(self._provider, req, on_text)
            # PATCH v2.1: tagih panggilan planning ke model_id SETELAH complete()
            # sukses dan SEBELUM validasi -> panggilan yang benar-benar dieksekusi
            # tetap terhitung meski plan-nya ternyata invalid. Setiap attempt
            # dari retry loop ditagih, bukan hanya attempt terakhir.
            self._cost_meter.add(self._model_id, resp.usage)
            raw = _extract_text(resp)
            try:
                data = _parse_plan_json(raw)
                tasks = _build_tasks(data)
                _validate(tasks)
                return tasks
            except ValueError as exc:
                # Re-raise the FIRST rejection, not the last: later attempts
                # can fail for an unrelated reason (e.g. the corrective
                # follow-up itself gets no better a reply), but the caller
                # should see the ORIGINAL cause.
                if first_exc is None:
                    first_exc = exc
                if attempt == _MAX_PLAN_ATTEMPTS:
                    # `from None`: suppress implicit chaining to this LATER
                    # attempt's exc (unrelated noise) -- the traceback should
                    # tell the story of the ORIGINAL failure, not the last one.
                    raise first_exc from None
                # Corrective follow-up: append the model's bad reply + a
                # correction that carries the ACTUAL rejection reason (parse
                # or validate), then retry within the same plan() call.
                req = CanonicalRequest(
                    messages=[
                        *req.messages,
                        text("assistant", raw),
                        text("user", _plan_correction(exc)),
                    ],
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    # Carry over defensively so a future planner request that
                    # sets these doesn't silently lose them on retry.
                    tools=req.tools,
                    run_id=req.run_id,
                    task_id=req.task_id,
                    attempt=req.attempt,
                )
        raise AssertionError("unreachable: loop always returns or raises")

    def _build_request(self, goal: str) -> CanonicalRequest:
        return CanonicalRequest(
            messages=[text("system", _PLAN_SYSTEM), text("user", goal)],
            max_tokens=2048,
            temperature=0.0,
        )


def _extract_text(resp: CanonicalResponse) -> str:
    return "".join(b.text for b in resp.content if isinstance(b, TextBlock))


def _strip_fence(s: str) -> str:
    s = s.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()[1:]  # drop opening ``` or ```json line
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]  # drop closing fence
    return "\n".join(lines).strip()


def _parse_plan_json(raw: str) -> list[Any]:
    cleaned = _strip_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"planner did not return valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("planner JSON must be an array of task objects")
    return data


def _build_tasks(data: list[Any]) -> list[Task]:
    tasks: list[Task] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"plan item #{i} is not a JSON object")
        # depends_on: key absen ATAU JSON null -> [] (bukan TypeError mentah).
        # Tipe non-array ditolak sebagai plan invalid (ValueError bersih), bukan
        # lolos ke iterasi yang melempar TypeError tak-tertangkap.
        raw_deps = item.get("depends_on")
        if raw_deps is None:
            raw_deps = []
        if not isinstance(raw_deps, list):
            raise ValueError(
                f"plan item #{i} 'depends_on' must be a JSON array, got {type(raw_deps).__name__}"
            )
        # difficulty: lenient — absent/unknown/non-string -> "medium" (isinstance guard
        # avoids TypeError when a JSON list/dict is passed as the value).
        raw_diff = item.get("difficulty")
        difficulty = (
            raw_diff if isinstance(raw_diff, str) and raw_diff in _DIFFICULTIES else "medium"
        )
        try:
            task = Task(
                id=str(item["id"]),
                description=str(item["description"]),
                type=str(item["type"]),
                mode=str(item["mode"]),
                depends_on=[str(d) for d in raw_deps],
                difficulty=difficulty,
            )
        except KeyError as exc:
            raise ValueError(f"plan item #{i} missing required key {exc}") from exc
        tasks.append(task)
    return tasks


def _validate(tasks: list[Task]) -> None:
    # Plan kosong = no-op yang, tanpa penjagaan ini, lolos DAG-check (Kahn:
    # resolved==0==len) dan bikin aexecute lapor "success" tanpa kerja apa pun.
    if not tasks:
        raise ValueError("plan is empty: planner returned no tasks")
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("plan contains duplicate task ids")
    id_set = set(ids)
    for t in tasks:
        for dep in t.depends_on:
            if dep not in id_set:
                raise ValueError(f"task {t.id!r} depends on unknown task {dep!r}")
    _assert_acyclic(tasks)


def _assert_acyclic(tasks: list[Task]) -> None:
    indegree = {t.id: len(t.depends_on) for t in tasks}
    dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            dependents[dep].append(t.id)
    ready = [tid for tid, deg in indegree.items() if deg == 0]
    resolved = 0
    while ready:
        tid = ready.pop()
        resolved += 1
        for child in dependents[tid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if resolved != len(tasks):
        raise ValueError("plan contains a dependency cycle")

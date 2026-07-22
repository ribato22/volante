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
    'and "depends_on" (array of task ids that must finish first). '
    "Do not include any prose or markdown outside the JSON array."
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
        # on_text -> streaming (progres planning live); else complete (nol regresi).
        resp = await call_provider(self._provider, req, on_text)
        # PATCH v2.1: tagih panggilan planning ke model_id SETELAH complete()
        # sukses dan SEBELUM validasi -> panggilan yang benar-benar dieksekusi
        # tetap terhitung meski plan-nya ternyata invalid.
        self._cost_meter.add(self._model_id, resp.usage)
        raw = _extract_text(resp)
        data = _parse_plan_json(raw)
        tasks = _build_tasks(data)
        _validate(tasks)
        return tasks

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
        try:
            task = Task(
                id=str(item["id"]),
                description=str(item["description"]),
                type=str(item["type"]),
                mode=str(item["mode"]),
                depends_on=[str(d) for d in raw_deps],
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

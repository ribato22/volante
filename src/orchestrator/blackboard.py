from __future__ import annotations

from typing import Any

from orchestrator.types import Entry, Task


class Blackboard:
    def __init__(self, goal: str, plan: list[Task]) -> None:
        self.goal = goal
        self.plan = plan
        self._log: list[Entry] = []

    def append(self, e: Entry) -> None:
        self._log.append(e)

    def entries(self) -> list[Entry]:
        return list(self._log)

    def current_artifacts(self) -> dict[str, Any]:
        artifacts: dict[str, Any] = {}
        for e in self._log:
            if e.kind == "artifact":
                artifacts[e.task_id] = e.payload
        return artifacts

    def facts(self) -> list[str]:
        return [e.payload for e in self._log if e.kind == "fact"]

    def status_of(self, task_id: str) -> str:
        status = "pending"
        for e in self._log:
            if e.kind == "status" and e.task_id == task_id:
                status = e.payload
        return status

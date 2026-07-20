from __future__ import annotations

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

from __future__ import annotations

from orchestrator.blackboard import Blackboard
from orchestrator.registry import Registry
from orchestrator.types import CanonicalRequest, Task, text


class Projector:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def project(self, task: Task, model_id: str, bb: Blackboard) -> CanonicalRequest:
        model = self.registry.get(model_id)

        system_content = (
            "You are a specialized worker in an AI orchestration engine. "
            "Execute the assigned task using only the provided context. "
            f"Overall goal: {bb.goal}"
        )
        task_line = f"Task: {task.description}"

        artifacts = bb.current_artifacts()
        dep_blocks = [
            f"[artifact:{dep}]\n{artifacts[dep]}"
            for dep in task.depends_on
            if dep in artifacts
        ]
        artifact_text = "\n\n".join(dep_blocks)
        user_content = (
            task_line if not artifact_text else f"{task_line}\n\n{artifact_text}"
        )

        return CanonicalRequest(
            messages=[
                text("system", system_content),
                text("user", user_content),
            ],
            max_tokens=model.max_output_tokens,
            task_id=task.id,
        )

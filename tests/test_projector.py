from __future__ import annotations

from orchestrator.blackboard import Blackboard
from orchestrator.projector import Projector
from orchestrator.registry import Registry
from orchestrator.types import Entry, ModelInfo, Task


def _model(**overrides) -> ModelInfo:
    base = dict(
        id="test/model",
        provider="openai_compat",
        strengths={"reasoning"},
        context_window=100_000,
        max_output_tokens=4_000,
        supports_tools=False,
        cost_per_1k_in=0.1,
        cost_per_1k_out=0.2,
    )
    base.update(overrides)
    return ModelInfo(**base)


def _artifact(task_id: str, payload: str) -> Entry:
    return Entry(
        run_id="r",
        task_id=task_id,
        attempt=0,
        kind="artifact",
        payload=payload,
        model_id="test/model",
        usage=None,
        timestamp=0.0,
    )


def test_project_includes_only_dependency_artifacts() -> None:
    registry = Registry([_model()])
    projector = Projector(registry)

    t1 = Task(id="t1", description="collect data", type="research", mode="one_shot")
    t2 = Task(id="t2", description="unrelated branch", type="analyze", mode="one_shot")
    t3 = Task(
        id="t3",
        description="write summary",
        type="write",
        mode="one_shot",
        depends_on=["t1"],
    )
    bb = Blackboard(goal="Produce a market report", plan=[t1, t2, t3])
    bb.append(_artifact("t1", "ARTIFACT_ONE_UNIQUE"))
    bb.append(_artifact("t2", "ARTIFACT_TWO_UNIQUE"))

    req = projector.project(t3, "test/model", bb)
    sys_text = req.messages[0].content[0].text
    usr_text = req.messages[1].content[0].text

    assert req.messages[0].role == "system"
    assert req.messages[1].role == "user"
    assert "Produce a market report" in sys_text
    assert "write summary" in usr_text
    assert "ARTIFACT_ONE_UNIQUE" in usr_text
    assert "ARTIFACT_TWO_UNIQUE" not in usr_text  # non-dependency TIDAK bocor
    assert req.task_id == "t3"
    assert req.run_id == ""      # diisi Runtime, bukan Projector
    assert req.attempt == 0      # diisi Runtime, bukan Projector
    assert req.max_tokens == 4_000

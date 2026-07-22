from __future__ import annotations

from baton.blackboard import Blackboard
from baton.projector import Projector
from baton.registry import Registry
from baton.types import Entry, ModelInfo, Task


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


def test_project_trims_per_artifact_and_keeps_every_dependency() -> None:
    model = _model(context_window=1_600, max_output_tokens=200)
    registry = Registry([model])
    projector = Projector(registry)
    budget = int((model.context_window - model.max_output_tokens) * 0.85)  # 1190

    t1 = Task(id="t1", description="gather A", type="research", mode="one_shot")
    t2 = Task(id="t2", description="gather B", type="research", mode="one_shot")
    t_noise = Task(id="tn", description="noise", type="analyze", mode="one_shot")
    t3 = Task(
        id="t3",
        description="merge both sources",
        type="write",
        mode="one_shot",
        depends_on=["t1", "t2"],
    )
    bb = Blackboard(goal="Merge sources", plan=[t1, t2, t_noise, t3])
    huge = "HEAD_ONE_" + ("A" * 8_000) + "_TAIL_ONE"
    small = "TWO_UNIQUE_SHORT_PAYLOAD"
    bb.append(_artifact("t1", huge))
    bb.append(_artifact("t2", small))
    bb.append(_artifact("tn", "NOISE_NON_DEPENDENCY_PAYLOAD"))

    req = projector.project(t3, "test/model", bb)
    sys_text = req.messages[0].content[0].text
    usr_text = req.messages[1].content[0].text

    # Kedua dependency terwakili lewat blok berlabelnya masing-masing.
    assert "[artifact:t1]" in usr_text
    assert "[artifact:t2]" in usr_text
    # t2 TIDAK hilang meski t1 raksasa (properti anti-starvation PATCH).
    assert small in usr_text
    # t1 dipangkas PER-ARTIFACT: kepala + ekor tersimpan, marker di tengah.
    assert "[dipangkas tengah artifact t1]" in usr_text
    assert "HEAD_ONE_" in usr_text
    assert "_TAIL_ONE" in usr_text
    assert huge not in usr_text          # benar-benar terpangkas
    # Marker per-artifact hanya untuk yang terpangkas (t2 utuh -> tanpa marker t2).
    assert "[dipangkas tengah artifact t2]" not in usr_text
    # Scoping tetap: artifact non-dependency tak pernah masuk.
    assert "NOISE_NON_DEPENDENCY_PAYLOAD" not in usr_text
    # Budget (margin 0.85) dihormati.
    assert (len(sys_text) + len(usr_text)) // 4 <= budget
    assert req.max_tokens == 200
    assert req.task_id == "t3"


def test_project_budget_uses_085_safety_margin() -> None:
    # context_window - max_output_tokens = 1000 tok. Budget penuh = 4000 char;
    # budget-margin 0.85 = 850 tok = 3400 char. Artifact 3600 char MUAT pada
    # budget penuh tapi MELEBIHI budget-margin -> hanya impl ber-margin 0.85
    # yang memangkasnya (marker WAJIB muncul). Ini yang menahan margin.
    model = _model(context_window=1_200, max_output_tokens=200)
    registry = Registry([model])
    projector = Projector(registry)
    margin_budget = int((model.context_window - model.max_output_tokens) * 0.85)  # 850

    t1 = Task(id="t1", description="gather", type="research", mode="one_shot")
    t2 = Task(
        id="t2",
        description="summarize",
        type="write",
        mode="one_shot",
        depends_on=["t1"],
    )
    bb = Blackboard(goal="Goal", plan=[t1, t2])
    bb.append(_artifact("t1", "Z" * 3_600))

    req = projector.project(t2, "test/model", bb)
    sys_text = req.messages[0].content[0].text
    usr_text = req.messages[1].content[0].text

    assert "[dipangkas tengah artifact t1]" in usr_text
    assert (len(sys_text) + len(usr_text)) // 4 <= margin_budget
    assert req.max_tokens == 200

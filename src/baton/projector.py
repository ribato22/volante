from __future__ import annotations

from baton.blackboard import Blackboard
from baton.registry import Registry
from baton.types import CanonicalRequest, Task, text

_BUDGET_MARGIN = 0.85
_CHARS_PER_TOKEN = 4


def _mid_trim(content: str, dep_id: str, char_share: int) -> str:
    """Pangkas satu artifact ke jatah karakternya (`char_share`).

    Simpan KEPALA + EKOR lalu sisipkan marker di tengah bila konten melebihi
    jatahnya. Panjang hasil selalu <= char_share sehingga budget total aman
    dan dependency ini tetap terwakili (tak pernah hilang total).
    """
    if len(content) <= char_share:
        return content
    marker = f"\n…[dipangkas tengah artifact {dep_id}]…\n"
    keep = char_share - len(marker)
    if keep <= 0:
        # Jatah lebih kecil dari marker: kembalikan penanda terpangkas agar blok
        # tetap hadir (dependency tak hilang) & budget tetap dihormati.
        return marker[:char_share]
    head_len = keep // 2
    tail_len = keep - head_len
    head = content[:head_len]
    tail = content[len(content) - tail_len :] if tail_len else ""
    return f"{head}{marker}{tail}"


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
        deps = [dep for dep in task.depends_on if dep in artifacts]

        # Budget input token dengan margin keamanan 0.85 (PATCH v2.1).
        budget = int(
            (model.context_window - model.max_output_tokens) * _BUDGET_MARGIN
        )
        char_budget = budget * _CHARS_PER_TOKEN

        if not deps:
            user_content = task_line
        else:
            # Tiap dependency dapat blok berlabel sendiri.
            labels = {dep: f"[artifact:{dep}]\n" for dep in deps}
            # Overhead tetap: system + framing "\n\n" + task_line + separator antar
            # blok + label tiap blok. Sisanya dibagi RATA sebagai jatah konten.
            fixed = (
                len(system_content)
                + 2  # "\n\n" system -> user
                + len(task_line)
                + 2  # "\n\n" task_line -> blok pertama
                + (len(deps) - 1) * 2  # "\n\n" antar blok
                + sum(len(labels[dep]) for dep in deps)
            )
            remaining = max(char_budget - fixed, 0)
            share = remaining // len(deps)  # ruang konten per-dependency (rata)
            blocks = [
                f"{labels[dep]}{_mid_trim(str(artifacts[dep]), dep, share)}"
                for dep in deps
            ]
            user_content = f"{task_line}\n\n" + "\n\n".join(blocks)

        return CanonicalRequest(
            messages=[
                text("system", system_content),
                text("user", user_content),
            ],
            max_tokens=model.max_output_tokens,
            task_id=task.id,
        )

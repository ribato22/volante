from __future__ import annotations

from volante.blackboard import Blackboard
from volante.registry import Registry
from volante.types import CanonicalRequest, Task, text

_BUDGET_MARGIN = 0.85
_CHARS_PER_TOKEN = 4


def model_input_token_budget(context_window: int, max_output_tokens: int) -> int:
    """Conservative input-token budget after output reserve and safety margin."""

    if context_window <= 1:
        raise ValueError("model context_window must be greater than one token")
    if max_output_tokens <= 0:
        raise ValueError("model max_output_tokens must be positive")
    # A malformed registry must not reserve more output than the entire context.
    # Keep at least half of a tiny context for input; normal model declarations
    # retain their configured output limit unchanged.
    output_reserve = min(max_output_tokens, max(1, context_window // 2))
    input_tokens = max(context_window - output_reserve, 1)
    return int(input_tokens * _BUDGET_MARGIN)


def model_input_char_budget(context_window: int, max_output_tokens: int) -> int:
    """Character form of :func:`model_input_token_budget` for prompt trimming."""

    return (
        model_input_token_budget(context_window, max_output_tokens)
        * _CHARS_PER_TOKEN
    )


def _mid_trim(content: str, dep_id: str, char_share: int) -> str:
    """Trim one artifact to its character share (`char_share`).

    Keep the HEAD + TAIL then insert a marker in the middle when the content
    exceeds its share. The result length is always <= char_share, so the overall
    budget stays safe and this dependency is still represented (it never drops
    out entirely).
    """
    if len(content) <= char_share:
        return content
    marker = f"\n…[artifact {dep_id} truncated in the middle]…\n"
    keep = char_share - len(marker)
    if keep <= 0:
        # share smaller than the marker: return a truncated marker so the block
        # is still present (the dependency is not lost) & the budget is respected.
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

        system_prefix = (
            "You are a specialized worker in an AI orchestration engine. "
            "Execute the assigned task using only the provided context. "
            "Respond in the same language as the overall goal. "
            "Overall goal: "
        )
        task_prefix = "Task: "

        artifacts = bb.current_artifacts()
        deps = [dep for dep in task.depends_on if dep in artifacts]

        # Input token budget with a 0.85 safety margin (PATCH v2.1).
        max_tokens = min(
            model.max_output_tokens, max(1, model.context_window // 2)
        )
        char_budget = model_input_char_budget(
            model.context_window, max_tokens
        )

        labels = {dep: f"[artifact:{dep}]\n" for dep in deps}
        # Fixed framing is small but still part of context. Fail before a provider
        # call if model metadata describes a context too small to hold the protocol.
        fixed = (
            len(system_prefix)
            + 2  # separator between system and user messages
            + len(task_prefix)
            + (2 if deps else 0)
            + (len(deps) - 1) * 2
            + sum(len(label) for label in labels.values())
        )
        if fixed >= char_budget:
            raise ValueError(
                f"model {model_id!r} context is too small for worker prompt framing"
            )
        remaining = char_budget - fixed
        # Bound both user-controlled strings before allocating the rest fairly to
        # dependency artifacts. Large goals/tasks therefore cannot make the first
        # agentic turn overflow before the transcript guard can help.
        goal_limit = remaining // (4 if deps else 2)
        bounded_goal = _mid_trim(bb.goal, "goal", goal_limit)
        remaining -= len(bounded_goal)
        task_limit = remaining // (3 if deps else 1)
        bounded_task = _mid_trim(task.description, task.id, task_limit)
        remaining -= len(bounded_task)
        system_content = f"{system_prefix}{bounded_goal}"
        task_line = f"{task_prefix}{bounded_task}"

        if not deps:
            user_content = task_line
        else:
            # Each dependency gets its own labelled block; no artifact can starve
            # another because each receives an equal share of remaining context.
            share = remaining // len(deps)  # content space per dependency (even)
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
            max_tokens=max_tokens,
            task_id=task.id,
            context_window=model.context_window,
        )

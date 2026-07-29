from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from volante.cost import CostMeter
from volante.projector import model_input_char_budget
from volante.providers.base import (
    LLMProvider,
    call_provider,
    ensure_complete_response,
)
from volante.types import (
    TASK_TOOL_NAMES,
    CanonicalRequest,
    CanonicalResponse,
    InvalidGoalError,
    Task,
    TextBlock,
    effective_required_tools,
    text,
    validate_goal,
    validate_task,
)

_PLAN_SYSTEM = (
    "You are a planning supervisor. Decompose the user's goal into a minimal "
    "directed acyclic graph (DAG) of tasks. Reply with ONLY a JSON array. Each "
    "element is an object with these exact keys: "
    '"id" (unique string), "description" (string), '
    '"type" (one of: research, code, write, analyze), '
    '"mode" (one of: one_shot, agentic), '
    '"difficulty" (one of: trivial, easy, medium, hard), '
    '"depends_on" (array of task ids that must finish first), '
    'and "required_tools" (array of exact tool names). '
    '"mode" defaults to one_shot: use one_shot for any task the assigned model can '
    "complete purely by generating text from what it already knows or from context "
    "already on the blackboard -- this covers ALL research, analysis, writing, and "
    "reasoning tasks (no tools needed). Use agentic ONLY when the task genuinely "
    "requires a tool loop: it must EXECUTE/run code, READ a local file, or FETCH a "
    "URL to do its job. An agentic task must declare every tool it needs in "
    "required_tools; a one_shot task must use an empty array. When in doubt, pick "
    "one_shot. "
    "Do not include any prose or markdown outside the JSON array."
)

# Live-observed finding: a subscription-CLI planner (e.g. `claude -p`) can
# probabilistically ANSWER the goal (prose/markdown) instead of emitting the
# strict JSON task array, even with _PLAN_SYSTEM. A single-shot plan has no
# recovery, so plan() retries a bounded number of times with a corrective
# follow-up before giving up.
_MAX_PLAN_ATTEMPTS = 3
_DEFAULT_CONTEXT_WINDOW = 32_768
_DEFAULT_MAX_OUTPUT_TOKENS = 2_048


def _plan_correction(exc: Exception) -> str:
    # Feed the ACTUAL rejection reason back to the model: a parse error
    # ("planner did not return valid JSON: ...") and a validate error ("plan
    # is empty", "contains a dependency cycle", ...) need different fixes, so
    # a static "send JSON only" nudge gives zero signal when the reply was
    # already valid JSON that merely failed structural validation.
    return (
        f"Your previous reply was rejected: {exc}. Reply with ONLY a valid "
        "JSON array of task objects (keys: id, description, type, mode, "
        "difficulty, depends_on, required_tools) — do not answer or explain the goal, output "
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
        self._before_call: Callable[[str], None] | None = None
        self._context_window = _DEFAULT_CONTEXT_WINDOW
        self._max_output_tokens = _DEFAULT_MAX_OUTPUT_TOKENS
        self._available_tools = {"run_python"}

    def set_call_gate(self, before_call: Callable[[str], None] | None) -> None:
        """Install a per-physical-call gate used by ``Runtime``.

        The hook is invoked for every planner attempt, including corrective
        retries. Direct ``Supervisor`` users need not configure one.
        """

        self._before_call = before_call

    def set_model_limits(
        self, *, context_window: int, max_output_tokens: int
    ) -> None:
        """Configure planner limits from the selected model's registry record."""

        if context_window <= 1:
            raise ValueError("supervisor context_window must be greater than one")
        if max_output_tokens <= 0 or max_output_tokens >= context_window:
            raise ValueError(
                "supervisor max_output_tokens must be positive and below context_window"
            )
        self._context_window = context_window
        self._max_output_tokens = max_output_tokens

    def set_available_tools(self, tool_names: set[str]) -> None:
        unknown = set(tool_names) - TASK_TOOL_NAMES
        if unknown:
            raise ValueError(f"unknown supervisor tools: {sorted(unknown)}")
        self._available_tools = set(tool_names)

    async def plan(
        self, goal: str, on_text: Callable[[str], None] | None = None
    ) -> list[Task]:
        if self._used:
            raise RuntimeError(
                "Supervisor.plan is non-re-entrant; use a fresh Supervisor per run"
            )
        self._used = True
        req = self._build_request(goal)
        base_messages = list(req.messages)
        first_exc: ValueError | None = None
        for attempt in range(1, _MAX_PLAN_ATTEMPTS + 1):
            # on_text -> streaming (live planning progress); else complete (zero
            # regression). NOTE: on retry, a partial re-stream of the corrected
            # attempt is re-emitted to on_text — acceptable (bounded retries).
            if self._before_call is not None:
                self._before_call(self._model_id)
            resp = await call_provider(self._provider, req, on_text)
            # PATCH v2.1: bill the planning call to model_id AFTER complete()
            # succeeds and BEFORE validation -> a call that really was executed
            # still counts even if the plan turns out to be invalid. Every attempt
            # of the retry loop is billed, not just the last one.
            self._cost_meter.add(
                self._model_id, resp.usage, cost_usd=resp.cost_usd
            )
            ensure_complete_response(resp, phase="planning")
            raw = _extract_text(resp)
            try:
                data = _parse_plan_json(raw)
                tasks = _build_tasks(data)
                _validate(tasks)
                for task in tasks:
                    unavailable = (
                        effective_required_tools(task) - self._available_tools
                    )
                    if unavailable:
                        raise ValueError(
                            f"task {task.id!r} requires tools unavailable in "
                            f"this run: {sorted(unavailable)}"
                        )
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
                correction = _plan_correction(exc)
                fixed_chars = sum(
                    len(block.text)
                    for message in base_messages
                    for block in message.content
                    if isinstance(block, TextBlock)
                ) + len(correction)
                char_budget = model_input_char_budget(
                    self._context_window, req.max_tokens
                )
                if fixed_chars >= char_budget:
                    raise first_exc from None
                available_reply = max(char_budget - fixed_chars, 0)
                bounded_raw = raw
                if len(bounded_raw) > available_reply:
                    marker = "\n…[previous planner reply truncated]…\n"
                    if available_reply <= len(marker):
                        bounded_raw = marker[:available_reply]
                    else:
                        keep = available_reply - len(marker)
                        head = keep // 2
                        tail = keep - head
                        bounded_raw = (
                            f"{raw[:head]}{marker}"
                            f"{raw[-tail:] if tail else ''}"
                        )
                req = CanonicalRequest(
                    messages=[
                        *base_messages,
                        text("assistant", bounded_raw),
                        text("user", correction),
                    ],
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    # Carry over defensively so a future planner request that
                    # sets these doesn't silently lose them on retry.
                    tools=req.tools,
                    run_id=req.run_id,
                    task_id=req.task_id,
                    attempt=req.attempt,
                    context_window=req.context_window,
                )
        raise AssertionError("unreachable: loop always returns or raises")

    def _build_request(self, goal: str) -> CanonicalRequest:
        validate_goal(goal)
        system_prompt = (
            f"{_PLAN_SYSTEM} Available tools for this run: "
            f"{', '.join(sorted(self._available_tools)) or '(none)'}. "
            "For every agentic task, required_tools must be a non-empty subset "
            "of that list. For one_shot tasks, required_tools must be []."
        )
        output_tokens = min(
            self._max_output_tokens,
            _DEFAULT_MAX_OUTPUT_TOKENS,
            max(1, self._context_window // 2),
        )
        char_budget = model_input_char_budget(
            self._context_window, output_tokens
        )
        framing = len(system_prompt) + 2
        available_goal = char_budget - framing
        if available_goal < 1 or len(goal) > available_goal:
            raise InvalidGoalError(
                f"goal does not fit planner model {self._model_id!r}: "
                f"{len(goal)} characters supplied, {max(available_goal, 0)} available"
            )
        return CanonicalRequest(
            messages=[text("system", system_prompt), text("user", goal)],
            max_tokens=output_tokens,
            temperature=0.0,
            context_window=self._context_window,
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
        # depends_on: key absent OR JSON null -> [] (not a raw TypeError).
        # A non-array type is rejected as an invalid plan (a clean ValueError), rather
        # than slipping through to an iteration that raises an uncaught TypeError.
        raw_deps = item.get("depends_on")
        if raw_deps is None:
            raw_deps = []
        if not isinstance(raw_deps, list):
            raise ValueError(
                f"plan item #{i} 'depends_on' must be a JSON array, got {type(raw_deps).__name__}"
            )
        raw_tools = item.get("required_tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError(
                f"plan item #{i} 'required_tools' must be a JSON array, "
                f"got {type(raw_tools).__name__}"
            )
        try:
            raw_id = item["id"]
            raw_description = item["description"]
            raw_type = item["type"]
            raw_mode = item["mode"]
            if not isinstance(raw_id, str):
                raise ValueError(f"plan item #{i} 'id' must be a string")
            if not isinstance(raw_description, str):
                raise ValueError(f"plan item #{i} 'description' must be a string")
            if not isinstance(raw_type, str):
                raise ValueError(f"plan item #{i} 'type' must be a string")
            if not isinstance(raw_mode, str):
                raise ValueError(f"plan item #{i} 'mode' must be a string")
            raw_diff = item.get("difficulty", "medium")
            if not isinstance(raw_diff, str):
                raise ValueError(f"plan item #{i} 'difficulty' must be a string")
            for dep in raw_deps:
                if not isinstance(dep, str):
                    raise ValueError(
                        f"plan item #{i} dependency ids must be strings"
                    )
            for tool_name in raw_tools:
                if not isinstance(tool_name, str):
                    raise ValueError(
                        f"plan item #{i} required tool names must be strings"
                    )
            task = Task(
                id=raw_id,
                description=raw_description,
                type=raw_type,
                mode=raw_mode,
                depends_on=list(raw_deps),
                difficulty=raw_diff,
                required_tools=list(raw_tools),
            )
        except KeyError as exc:
            raise ValueError(f"plan item #{i} missing required key {exc}") from exc
        tasks.append(task)
    return tasks


def validate_plan(tasks: list[Task]) -> None:
    """Validate an entire task DAG, including every task's trusted schema."""

    # An empty plan is a no-op that, without this guard, passes the DAG check (Kahn:
    # resolved==0==len) and makes aexecute report "success" without doing any work.
    if not tasks:
        raise ValueError("plan is empty: planner returned no tasks")
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("plan contains duplicate task ids")
    id_set = set(ids)
    for t in tasks:
        validate_task(t)
        for dep in t.depends_on:
            if dep not in id_set:
                raise ValueError(f"task {t.id!r} depends on unknown task {dep!r}")
    _assert_acyclic(tasks)


def _validate(tasks: list[Task]) -> None:
    """Backward-compatible private alias retained for existing integrations."""

    validate_plan(tasks)


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

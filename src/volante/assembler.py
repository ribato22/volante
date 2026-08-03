"""Put the workers' artifacts together mechanically, with no model call.

The alternative to `Synthesizer` for goals whose PRODUCT is an artifact rather than
an answer about one.

Every orchestration run funnels its workers' output through a single synthesis call,
and that call has to be bounded — a non-streaming generation asked for a model's full
64k or 128k output crosses the caller's deadline after every token has been generated
and billed. The bound is correct for a summary. It is the wrong shape for a module,
a test suite, or a document in parts, because it means the thing the decomposition
existed to build can never be larger than what one model response could have produced
on its own. That removes the only structural reason to decompose the work.

Assembly has no generation to bound. Five workers each writing near their own budget
produce five workers' worth of output, and the total is the sum of their budgets
rather than one of them.

What it does NOT do, deliberately: reconcile the parts. It will not resolve two
workers defining the same name, reorder imports, or notice that one part contradicts
another. The planner's DAG decides what each worker owns and in what order; this
concatenates the result. A goal whose parts genuinely have to be reconciled wants
`Synthesizer`, and choosing between them is the caller's decision, not a heuristic
applied to the artifacts.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from volante.blackboard import Blackboard

# ```python … ``` — the fence a model puts round code when asked for it. The TAG is
# captured as well as the body, and both are kept.
#
# Stripping the fences was a defect, not a simplification. On a goal asking for a
# module plus tests plus a README, the workers produce one artifact each; dropping
# the fences concatenated the README's prose straight into the middle of the Python
# and left nothing to mark where one file ended. Measured on `resolve`: 6 runs out of
# 6 scored 0.000, every one of them a syntax error inside the README text. Keeping
# the fences separates the files, which is the same shape `Synthesizer` is told to
# produce — the two strategies should not disagree about what an answer looks like.
#
# The prose a worker wraps round its answer ("Here is the function you asked for") is
# still dropped: it is commentary on the artifact, not part of it.
_FENCE = re.compile(
    r"```[^\S\r\n]*([a-zA-Z0-9_+-]*)[^\S\r\n]*\r?\n(.*?)```", re.DOTALL
)


class ArtifactAssembler:
    """A `Synthesizer`-shaped component that concatenates instead of generating."""

    async def synthesize(
        self,
        goal: str,
        bb: Blackboard,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        artifacts = list(bb.current_artifacts().items())
        if not artifacts:
            # The same failure Synthesizer reports rather than returning "": a run
            # that produced nothing has failed, and an empty final answer hides it.
            raise ValueError("cannot assemble a final answer: no artifacts produced")

        parts: list[str] = []
        for _task_id, payload in artifacts:
            text = str(payload)
            fenced = [
                f"```{tag}\n{body.strip()}\n```"
                for tag, body in _FENCE.findall(text)
                if body.strip()
            ]
            # No fence means the worker answered with the artifact bare. Keeping the
            # whole thing is the only safe reading; dropping it would lose the task.
            # It is fenced too, so a bare README cannot bleed into the next file.
            parts.extend(fenced if fenced else [f"```\n{text.strip()}\n```"])

        assembled = "\n\n".join(part for part in parts if part)
        if on_text is not None:
            # The CLI and Web UI stream this phase. There is nothing to stream token
            # by token, but a silent surface would read as a stalled run.
            on_text(assembled)
        return assembled

    # --- the rest of what Runtime probes for -------------------------------- #
    # Runtime configures its phase components with these. Both are no-ops here:
    # there is no model call to gate and no output budget to fit.

    def set_call_gate(self, before_call: Callable[[str], None] | None) -> None:
        return None

    def set_model_limits(self, *, context_window: int, max_output_tokens: int) -> None:
        return None

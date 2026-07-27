---
description: Orchestrate a goal with Volante (plan → route → run → synthesize)
argument-hint: [goal]
---

Use the `volante_run` tool from the `volante` MCP server to orchestrate the following goal
end-to-end — Volante plans a task DAG, routes each sub-task to the best predicted fit from
configured metadata/evidence after hard-capability filtering, runs it, and synthesizes a
final answer. Report the synthesized answer and the status/cost footer the tool returns.

Goal:

$ARGUMENTS

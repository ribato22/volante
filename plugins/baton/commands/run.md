---
description: Orchestrate a goal with Baton (plan → route → run → synthesize)
argument-hint: [goal]
---

Use the `baton_run` tool from the `baton` MCP server to orchestrate the following goal
end-to-end — Baton plans a task DAG, routes each sub-task to the best-quality capable
model, runs it, and synthesizes a final answer. Report the synthesized answer and the
status/cost footer the tool returns.

Goal:

$ARGUMENTS

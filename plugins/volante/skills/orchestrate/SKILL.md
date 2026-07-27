---
name: orchestrate
description: >-
  Delegate a LARGE, multi-part goal to Volante's cross-model orchestration engine via the
  volante_run MCP tool. Use ONLY when the request genuinely decomposes into several sub-tasks
  that benefit from routing across different models (e.g. "research X, draft Y, then produce
  Z"). Do NOT use it for simple, single-step, or quick tasks — do those directly.
---

Use this skill **only** for a large, multi-part goal that benefits from being decomposed into a
task DAG and routed across models. For anything simple, single-step, or quick, handle it directly
and do **not** invoke Volante.

When it genuinely applies:

1. Call the `volante_run` tool from the `volante` MCP server, passing the user's full goal as `goal`
   (optionally set `prefer` to `cash_protect_quota` to protect subscription quota instead of the
   default quality-first routing).
2. Volante plans a task DAG, routes each sub-task to the best predicted fit from configured
   metadata/evidence after hard-capability filtering, runs each one, and synthesizes a single
   final answer — it returns that answer plus a status/cost footer.
3. Present Volante's synthesized answer to the user, and mention the cost footer it returned.

If the `volante` MCP server or the `volante_run` tool isn't available, tell the user how to install the
Volante MCP server (see the plugin README) rather than attempting the multi-model orchestration by hand.

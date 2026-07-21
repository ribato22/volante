from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
@pytest.mark.asyncio
async def test_agentic_fixes_code_until_tests_pass(tmp_path) -> None:
    from orchestrator.agent import AgenticWorker
    from orchestrator.cost import CostMeter
    from orchestrator.providers.anthropic import AnthropicProvider
    from orchestrator.tools.run_python import RunPythonTool
    from orchestrator.tools.sandbox import Sandbox
    from orchestrator.types import CanonicalRequest, text

    model = "claude-sonnet-5"
    provider = AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"], model=model)
    tools = {"run_python": RunPythonTool(Sandbox(tmp_path))}
    worker = AgenticWorker({model: provider}, CostMeter(), max_iters=8)
    req = CanonicalRequest(
        messages=[
            text(
                "user",
                "Write add(a,b) that returns a+b in solution.py, then run a test that asserts "
                "add(2,3)==5 via run_python. Iterate until it passes, then say DONE.",
            )
        ],
        max_tokens=2048,
        task_id="int1",
    )
    res = await worker.run(req, model, tools)
    assert "DONE" in res.final_text.upper()
    assert res.usage_total[model].completion_tokens > 0

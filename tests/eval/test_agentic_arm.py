from __future__ import annotations

import pytest
from eval.harness import AgenticArmResult, run_agentic_single, score_agentic

from orchestrator.providers.base import ProviderError
from orchestrator.providers.fake import FakeProvider
from orchestrator.registry import Registry
from orchestrator.types import (
    CanonicalRequest,
    CanonicalResponse,
    ModelInfo,
    TextBlock,
    ToolUseBlock,
    Usage,
)


def _model(mid: str) -> ModelInfo:
    return ModelInfo(
        id=mid, provider="fake", strengths={"coding"}, context_window=100_000,
        max_output_tokens=4096, supports_tools=True,
        cost_per_1k_in=0.001, cost_per_1k_out=0.002,
    )


def _resp(content, stop, usage=(3, 2)) -> CanonicalResponse:
    return CanonicalResponse(
        content=content, usage=Usage(prompt_tokens=usage[0], completion_tokens=usage[1]),
        model="m1", stop_reason=stop, latency_ms=1,
    )


# Runner referensi minimal untuk score_agentic: impor slugify dari solution.
_REF = (
    "import json, sys\n"
    "def main():\n"
    "    total = 2\n"
    "    try:\n"
    "        from solution import slugify\n"
    "    except Exception:\n"
    "        print(_TAG + json.dumps({'passed': 0, 'total': total})); return\n"
    "    p = 0\n"
    "    for a, b in [('Hi There', 'hi-there'), ('A_B', 'a-b')]:\n"
    "        try:\n"
    "            if slugify(a) == b: p += 1\n"
    "        except Exception: pass\n"
    "    print(_TAG + json.dumps({'passed': p, 'total': total}))\n"
    "if __name__ == '__main__':\n"
    "    main(); sys.exit(0)\n"
)

_GOOD = (
    "import re\n"
    "def slugify(t):\n"
    "    t = t.lower().replace('_', '-').replace(' ', '-')\n"
    "    t = re.sub(r'[^a-z0-9-]', '', t)\n"
    "    t = re.sub(r'-+', '-', t).strip('-')\n"
    "    return t\n"
)
_BROKEN = "def slugify(t):\n    return t\n"


@pytest.mark.asyncio
async def test_run_agentic_single_captures_workspace() -> None:
    # Tool menulis solution.py + test_x.py + README.md ke workspace (cwd sandbox).
    code = (
        "open('solution.py','w').write(" + repr(_GOOD) + ")\n"
        "open('test_x.py','w').write('def test_ok():\\n    assert True\\n')\n"
        "open('README.md','w').write('# readme\\n')\n"
    )
    provider = FakeProvider(responses=[
        _resp([ToolUseBlock(id="u1", name="run_python", input={"code": code})], "tool_use"),
        _resp([TextBlock(text="done")], "end_turn"),
    ])
    res = await run_agentic_single("goal", provider, "m1", Registry([_model("m1")]))
    assert isinstance(res, AgenticArmResult)
    assert "def slugify" in res.solution_code
    assert res.has_tests is True
    assert res.has_readme is True
    assert res.duration_ms >= 0
    assert res.usage_total["m1"].completion_tokens == 4  # 2 turn x 2


class _FailProvider:
    name = "m1"

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        raise ProviderError("infra down", retryable=False)

    async def stream(self, req: CanonicalRequest, on_text) -> CanonicalResponse:
        raise ProviderError("infra down", retryable=False)


@pytest.mark.asyncio
async def test_run_agentic_single_surfaces_provider_failure() -> None:
    # Regresi H1: kegagalan terminal arm agentic TIDAK boleh diam-diam jadi 0.0
    # tak-terbedakan. res.error harus terisi supaya report bisa memisahkan
    # "arm gagal jalan" dari "solusi buruk sungguhan".
    res = await run_agentic_single("goal", _FailProvider(), "m1", Registry([_model("m1")]))
    assert isinstance(res, AgenticArmResult)
    assert res.error is not None
    assert "ProviderError" in res.error
    assert "infra down" in res.error
    assert res.solution_code == ""  # tak ada file ditulis sebelum gagal


@pytest.mark.asyncio
async def test_run_agentic_single_success_has_no_error() -> None:
    provider = FakeProvider(responses=[_resp([TextBlock(text="done")], "end_turn")])
    res = await run_agentic_single("goal", provider, "m1", Registry([_model("m1")]))
    assert res.error is None


def test_score_agentic_good_vs_broken() -> None:
    good = AgenticArmResult(_GOOD, True, True, {}, 0.0, 0)
    broken = AgenticArmResult(_BROKEN, True, True, {}, 0.0, 0)
    sg = score_agentic(good, _REF)
    sb = score_agentic(broken, _REF)
    assert sg["code"] == 1.0
    assert sg["composite"] == pytest.approx(0.7 * 1.0 + 0.15 + 0.15)
    assert sb["code"] < 1.0
    assert sb["composite"] < sg["composite"]

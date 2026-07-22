from __future__ import annotations

import demo

from baton.agent import TurnRecord
from baton.registry import Registry
from baton.types import ModelInfo, Usage


def _m(mid: str, tools: bool) -> ModelInfo:
    return ModelInfo(
        id=mid, provider="x", strengths={"coding"}, context_window=1000,
        max_output_tokens=100, supports_tools=tools,
        cost_per_1k_in=0.001, cost_per_1k_out=0.002,
    )


def test_detect_providers() -> None:
    assert demo.detect_providers({"ANTHROPIC_API_KEY": "k"}) == ["anthropic"]
    assert demo.detect_providers({"MOONSHOT_API_KEY": "k", "OLLAMA_BASE_URL": "u"}) == [
        "kimi",
        "ollama",
    ]
    assert demo.detect_providers({"OPENAI_COMPAT_BASE_URL": "u"}) == ["openai-compat"]
    # slot generik bernomor juga terdeteksi
    assert demo.detect_providers({"OPENAI_COMPAT_2_BASE_URL": "u"}) == ["openai-compat"]
    # urutan prioritas: anthropic > openai-compat > kimi > ollama
    assert demo.detect_providers(
        {"ANTHROPIC_API_KEY": "k", "OPENAI_COMPAT_BASE_URL": "u", "OLLAMA_BASE_URL": "u"}
    ) == ["anthropic", "openai-compat", "ollama"]
    assert demo.detect_providers({}) == []


def test_pick_agentic_prefers_tool_capable() -> None:
    reg = Registry([_m("a", False), _m("b", True)])
    assert demo.pick_agentic_model(reg, {"a": 1, "b": 2}) == "b"


def test_pick_agentic_falls_back_to_configured() -> None:
    reg = Registry([_m("a", False)])
    assert demo.pick_agentic_model(reg, {"a": 1}) == "a"


def test_pick_agentic_none_without_configured_provider() -> None:
    reg = Registry([_m("a", True)])
    assert demo.pick_agentic_model(reg, {}) is None


def test_fmt_turns_one_line_per_turn() -> None:
    turns = [
        TurnRecord(0, "tool_use", "run_python({'code': '...'})\nsecond line", Usage(1, 1), "m"),
        TurnRecord(1, "final", "DONE", None, "m"),
    ]
    out = demo._fmt_turns(turns)
    assert out.count("\n") == 1  # dua turn = satu newline pemisah
    assert "[0] tool_use" in out
    assert "[1] final" in out
    assert "second line" in out  # newline internal payload dilipat jadi spasi

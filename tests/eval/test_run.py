from __future__ import annotations

import eval.run as run
import pytest
from eval.run import format_report

from baton.registry import Registry
from baton.router import Router
from baton.types import Task

_ARM_NAMES = ("baseline", "orchestration", "agentic")


def _scores(comp: float) -> dict:
    return {"code": comp, "has_tests": 0.0, "has_readme": 0.0, "composite": comp}


def _arm(comp: float, cost: float, ms: int = 1, est: bool = False) -> dict:
    return {"composite": comp, "cost": cost, "ms": ms, "estimated": est}


def _per_goal(
    gid: str,
    winner: str = "orchestration",
    *,
    b: float = 0.3,
    o: float = 0.7,
    a: float = 0.5,
    b_cost: float = 0.002,
    o_cost: float = 0.01,
    a_cost: float = 0.005,
    est: bool = False,
) -> dict:
    return {
        "id": gid,
        "winner": winner,
        "arms": {
            "baseline": _arm(b, b_cost, est=est),
            "orchestration": _arm(o, o_cost),
            "agentic": _arm(a, a_cost),
        },
        "scores": {
            "baseline": _scores(b),
            "orchestration": _scores(o),
            "agentic": _scores(a),
        },
    }


def _result(
    per_goal: list[dict], *, verdict: str = "orchestration", any_estimated: bool = False
) -> dict:
    wins = {n: sum(1 for g in per_goal if g["winner"] == n) for n in _ARM_NAMES}
    ties = sum(1 for g in per_goal if g["winner"] == "tie")
    cost_total = {n: sum(g["arms"][n]["cost"] for g in per_goal) for n in _ARM_NAMES}
    return {
        "per_goal": per_goal,
        "aggregate": {
            "wins": wins,
            "ties": ties,
            "cost_total": cost_total,
            "any_estimated": any_estimated,
            "verdict": verdict,
        },
    }


def test_format_report_lists_every_goal_id():
    per = [_per_goal("slugify"), _per_goal("roman", winner="baseline")]
    report = format_report(_result(per, verdict="tie"))
    assert "slugify" in report
    assert "roman" in report


def test_format_report_names_all_three_arms():
    report = format_report(_result([_per_goal("slugify")]))
    for name in _ARM_NAMES:
        assert name in report


def test_format_report_returns_str():
    report = format_report(_result([_per_goal("slugify")]))
    assert isinstance(report, str)
    assert report.strip() != ""


def test_format_report_has_verdict_line_with_value():
    report = format_report(_result([_per_goal("slugify")], verdict="agentic"))
    verdict_lines = [ln for ln in report.splitlines() if "VERDICT:" in ln]
    assert len(verdict_lines) == 1
    assert "AGENTIC" in verdict_lines[0].upper()


def test_format_report_mentions_estimated_when_flagged():
    per = [_per_goal("slugify", est=True)]
    report = format_report(_result(per, any_estimated=True))
    assert "estimated" in report.lower()


def test_format_report_omits_estimated_when_all_measured():
    report = format_report(_result([_per_goal("slugify")], any_estimated=False))
    assert "estimated" not in report.lower()


def test_format_report_warns_when_agentic_arm_errored():
    # H1 surfacing: aggregate.agentic_errors > 0 harus memunculkan peringatan agar
    # skor 0.0 arm agentic tak dibaca sebagai hasil kapabilitas.
    result = _result([_per_goal("slugify")])
    result["aggregate"]["agentic_errors"] = 2
    report = format_report(result)
    assert "agentic arm failed" in report.lower()
    assert "2 run" in report


def test_format_report_no_agentic_warning_when_clean():
    report = format_report(_result([_per_goal("slugify")]))
    assert "agentic arm failed" not in report.lower()


def test_format_report_warns_when_goal_unmeasured():
    # H2 surfacing: aggregate.unmeasured_goals non-kosong -> peringatan runner rusak.
    result = _result([_per_goal("slugify")])
    result["aggregate"]["unmeasured_goals"] = ["slugify"]
    report = format_report(result)
    assert "no trusted result" in report.lower()
    assert "slugify" in report


def test_format_report_no_unmeasured_warning_when_all_measured():
    report = format_report(_result([_per_goal("slugify")]))
    assert "no trusted result" not in report.lower()


# --- slot provider OpenAI-compatible generik (OPENAI_COMPAT_*) ---------------


def test_openai_compat_from_env_none_when_unset():
    assert run._openai_compat_from_env({}) is None


def test_openai_compat_from_env_standard_defaults():
    # Default standar: context 128k, output 8k, tool-capable, biaya 0, id diturunkan.
    info, base_url, api_key, wire = run._openai_compat_from_env(
        {
            "OPENAI_COMPAT_BASE_URL": "https://x/v1",
            "OPENAI_COMPAT_MODEL": "gemini-2.5-flash",
        }
    )
    assert base_url == "https://x/v1"
    assert wire == "gemini-2.5-flash"
    assert api_key == "none"  # placeholder default (endpoint lokal sering tak butuh key)
    assert info.id == "openai-compat/gemini-2.5-flash"
    assert info.provider == "openai_compat"
    assert info.context_window == 128_000
    assert info.max_output_tokens == 8_192
    assert info.supports_tools is True
    assert info.cost_per_1k_in == 0.0
    assert info.cost_per_1k_out == 0.0
    assert info.strengths == {"coding", "reasoning"}


def test_openai_compat_from_env_overrides():
    info, _, api_key, _ = run._openai_compat_from_env(
        {
            "OPENAI_COMPAT_BASE_URL": "https://x/v1",
            "OPENAI_COMPAT_MODEL": "m",
            "OPENAI_COMPAT_KEY": "sk-123",
            "OPENAI_COMPAT_NAME": "google/gemini-flash",
            "OPENAI_COMPAT_CONTEXT": "1000000",
            "OPENAI_COMPAT_MAX_OUTPUT": "65536",
            "OPENAI_COMPAT_TOOLS": "false",
            "OPENAI_COMPAT_COST_IN": "0.0001",
            "OPENAI_COMPAT_COST_OUT": "0.0004",
        }
    )
    assert api_key == "sk-123"
    assert info.id == "google/gemini-flash"
    assert info.context_window == 1_000_000
    assert info.max_output_tokens == 65_536
    assert info.supports_tools is False
    assert info.cost_per_1k_in == 0.0001
    assert info.cost_per_1k_out == 0.0004


def test_openai_compat_from_env_requires_model():
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_MODEL"):
        run._openai_compat_from_env({"OPENAI_COMPAT_BASE_URL": "https://x/v1"})


def test_openai_compat_model_routable_for_all_task_types():
    # strengths catch-all {coding, reasoning} + tool-capable -> router bisa
    # mengarahkan SEMUA jenis task (dan task agentic) ke model tunggal ini.
    info, *_ = run._openai_compat_from_env(
        {"OPENAI_COMPAT_BASE_URL": "https://x/v1", "OPENAI_COMPAT_MODEL": "m"}
    )
    router = Router(Registry([info]))
    for ttype in ("code", "research", "write", "analyze"):
        task = Task(id="t", description="d", type=ttype, mode="one_shot")
        assert router.route(task) == info.id
    agentic = Task(id="a", description="d", type="code", mode="agentic")
    assert router.route(agentic) == info.id


def test_build_providers_wires_openai_compat_slot(monkeypatch):
    # Slot generik ter-wire: provider + ModelInfo di registry + jadi baseline bila
    # tak ada Anthropic. (Konstruksi OpenAICompatProvider offline -> nol jaringan.)
    for k in ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://x/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("OPENAI_COMPAT_NAME", "google/gemini-flash")
    registry, providers, baseline = run.build_providers_from_env()
    assert baseline == "google/gemini-flash"
    assert "google/gemini-flash" in providers
    assert registry.get("google/gemini-flash").context_window == 128_000


# --- beberapa slot OpenAI-compatible (OPENAI_COMPAT_* + _2_*/_3_*) ------------


def test_all_openai_compat_none_when_unset():
    assert run._all_openai_compat_from_env({}) == []


def test_all_openai_compat_collects_slot1_and_numbered():
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://a/v1",
        "OPENAI_COMPAT_MODEL": "gemini-flash-latest",
        "OPENAI_COMPAT_NAME": "google/gemini-flash",
        "OPENAI_COMPAT_2_BASE_URL": "https://api.groq.com/openai/v1",
        "OPENAI_COMPAT_2_MODEL": "llama-3.3-70b-versatile",
        "OPENAI_COMPAT_2_NAME": "groq/llama-3.3",
        "OPENAI_COMPAT_2_COST_OUT": "0.0006",
    }
    slots = run._all_openai_compat_from_env(env)
    assert [s[0].id for s in slots] == ["google/gemini-flash", "groq/llama-3.3"]
    info2, base2, _key2, wire2 = slots[1]
    assert base2 == "https://api.groq.com/openai/v1"
    assert wire2 == "llama-3.3-70b-versatile"
    assert info2.cost_per_1k_out == 0.0006  # harga per-slot benar (bukan numpang slot lain)


def test_all_openai_compat_stops_at_gap():
    # slot 1 + slot 3 tanpa slot 2 -> hanya slot 1 (penomoran kontigu mulai 2).
    env = {
        "OPENAI_COMPAT_BASE_URL": "https://a/v1",
        "OPENAI_COMPAT_MODEL": "m1",
        "OPENAI_COMPAT_3_BASE_URL": "https://c/v1",
        "OPENAI_COMPAT_3_MODEL": "m3",
    }
    assert [s[0].id for s in run._all_openai_compat_from_env(env)] == ["openai-compat/m1"]


def test_all_openai_compat_numbered_slot_requires_model():
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_2_MODEL"):
        run._all_openai_compat_from_env(
            {
                "OPENAI_COMPAT_BASE_URL": "https://a/v1",
                "OPENAI_COMPAT_MODEL": "m1",
                "OPENAI_COMPAT_2_BASE_URL": "https://b/v1",  # tanpa _2_MODEL
            }
        )


def _clear_provider_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for n in ("", "_2", "_3", "_4"):
        for suf in ("_BASE_URL", "_MODEL", "_KEY", "_NAME", "_COST_OUT"):
            monkeypatch.delenv(f"OPENAI_COMPAT{n}{suf}", raising=False)


def test_build_providers_wires_multiple_compat_slots(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://a/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "gemini-flash-latest")
    monkeypatch.setenv("OPENAI_COMPAT_NAME", "google/gemini-flash")
    monkeypatch.setenv("OPENAI_COMPAT_2_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("OPENAI_COMPAT_2_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("OPENAI_COMPAT_2_NAME", "groq/llama-3.3")
    registry, providers, baseline = run.build_providers_from_env()
    assert baseline == "google/gemini-flash"  # slot 1 dulu -> baseline
    assert {"google/gemini-flash", "groq/llama-3.3"} <= set(providers)
    assert registry.get("groq/llama-3.3").provider == "openai_compat"


def test_build_providers_duplicate_compat_id_raises(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://a/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    monkeypatch.setenv("OPENAI_COMPAT_NAME", "dup/x")
    monkeypatch.setenv("OPENAI_COMPAT_2_BASE_URL", "https://b/v1")
    monkeypatch.setenv("OPENAI_COMPAT_2_MODEL", "m2")
    monkeypatch.setenv("OPENAI_COMPAT_2_NAME", "dup/x")  # id sama -> error jelas
    with pytest.raises(RuntimeError, match="duplicate model_id"):
        run.build_providers_from_env()

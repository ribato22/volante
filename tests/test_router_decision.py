from __future__ import annotations

import json

import pytest

from volante.registry import ModelQualityProfile, Registry
from volante.router import (
    InvalidRoutingRequest,
    NoEligibleModelError,
    Router,
)
from volante.types import ModelInfo, Task


def _model(
    model_id: str,
    provider: str,
    *,
    tier: int,
    strengths: set[str] | None = None,
    tools: bool = True,
    context: int = 128_000,
    output: int = 4_096,
    cost: float = 0.01,
    billing: str = "card",
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider=provider,
        strengths=strengths or {"coding", "reasoning"},
        context_window=context,
        max_output_tokens=output,
        supports_tools=tools,
        cost_per_1k_in=cost,
        cost_per_1k_out=cost,
        tier=tier,
        billing=billing,
    )


def _task(
    *,
    task_type: str = "code",
    mode: str = "one_shot",
    difficulty: str = "hard",
) -> Task:
    return Task(
        id="build-api",
        description="Implement and verify a robust API endpoint.",
        type=task_type,
        mode=mode,
        difficulty=difficulty,
    )


def _cross_provider_inventory() -> list[ModelInfo]:
    # More than one candidate per API/compatible provider guards against accidentally
    # reducing the user's inventory to one representative model per provider.
    return [
        _model("anthropic/opus", "anthropic", tier=4, cost=0.08),
        _model("anthropic/sonnet", "anthropic", tier=3, cost=0.02),
        _model("compat/deepseek", "openai_compat", tier=3, cost=0.002),
        _model("ollama/qwen-coder", "openai_compat", tier=2, cost=0.0),
        _model(
            "claude-code/opus",
            "claude_code",
            tier=4,
            tools=False,
            billing="plan_included",
            cost=0.08,
        ),
        _model(
            "codex/default",
            "codex",
            tier=3,
            billing="plan_included",
            cost=0.03,
        ),
    ]


def test_decision_accounts_for_entire_cross_provider_inventory() -> None:
    models = _cross_provider_inventory()
    profiles = {
        # A representative user evaluation can make a task-specialized tier-3 model
        # outrank a coarse tier-4 prior. Volante does not hard-code this vendor claim.
        "codex/default": ModelQualityProfile(
            task_scores={"code": 0.99},
            overall_score=0.91,
            reliability_score=0.96,
            source="team-code-eval-v3",
        )
    }
    decision = Router(
        Registry(models, quality_profiles=profiles), prefer="quality"
    ).route_decision(_task())

    assert decision.selected_model_id == "codex/default"
    assert len(decision.candidates) == len(models)
    assert {candidate.model_id for candidate in decision.candidates} == {
        model.id for model in models
    }
    assert {
        candidate.provider for candidate in decision.candidates
    } == {"anthropic", "openai_compat", "claude_code", "codex"}
    assert sum(c.provider == "anthropic" for c in decision.candidates) == 2
    assert sum(c.provider == "openai_compat" for c in decision.candidates) == 2
    assert decision.ranked_model_ids[0] == decision.selected_model_id


def test_decision_explains_score_evidence_and_empirical_caveat() -> None:
    registry = Registry(
        [_model("api/frontier", "api", tier=4)],
        quality_profiles={
            "api/frontier": ModelQualityProfile(
                task_scores={"code": 0.95},
                source="maintainer-benchmark",
                confidence=0.8,
            )
        },
    )
    decision = Router(registry).decide(_task())
    candidate = decision.candidates[0]

    assert candidate.rank == 1
    assert candidate.profile_source == "maintainer-benchmark"
    assert candidate.score is not None
    assert {component.name for component in candidate.components} == {
        "task_fit",
        "overall_quality",
        "difficulty_readiness",
        "context_headroom",
        "output_capacity",
        "reliability",
    }
    assert "Predicted quality score" in decision.explanation
    assert "not an empirical 'best model' claim" in " ".join(decision.caveats)
    # The structured result is ready for a CLI/UI/MCP JSON surface.
    json.dumps(decision.to_dict())


def test_agentic_hard_constraints_and_truncating_fallbacks_remain_visible() -> None:
    registry = Registry(
        [
            _model("no-tools", "claude_code", tier=4, tools=False),
            _model(
                "tiny-context",
                "local",
                tier=3,
                context=256,
                output=128,
            ),
            _model("eligible", "openai_compat", tier=3, tools=True),
        ]
    )
    decision = Router(registry).route_decision(_task(mode="agentic"))
    by_id = {candidate.model_id: candidate for candidate in decision.candidates}

    assert decision.selected_model_id == "eligible"
    assert by_id["no-tools"].eligible is False
    assert "requires tool support" in by_id["no-tools"].rejection_reasons[0]
    assert by_id["tiny-context"].eligible is True
    assert by_id["tiny-context"].requires_truncation is True
    assert by_id["tiny-context"].rank == 2
    assert "truncate" in (by_id["tiny-context"].objective_note or "")
    assert by_id["eligible"].eligible is True
    assert by_id["eligible"].requires_truncation is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "translate"),
        ("mode", "magic"),
        ("difficulty", "extreme"),
    ],
)
def test_unknown_task_contract_values_fail_fast(field: str, value: str) -> None:
    task = _task()
    setattr(task, field, value)
    router = Router(Registry([_model("m", "p", tier=3)]))

    with pytest.raises(InvalidRoutingRequest, match=f"invalid {field}"):
        router.route(task)


def test_unknown_objective_fails_fast() -> None:
    with pytest.raises(InvalidRoutingRequest, match="unknown routing objective"):
        Router(Registry([]), prefer="fastest")


def test_no_eligible_model_reports_each_configured_rejection() -> None:
    registry = Registry(
        [
            _model("writer", "api", tier=4, strengths={"reasoning"}),
            _model("coder-no-tools", "local", tier=2, tools=False),
        ]
    )

    with pytest.raises(NoEligibleModelError) as raised:
        Router(registry).route(_task(mode="agentic"))

    assert set(raised.value.rejected) == {"writer", "coder-no-tools"}
    assert "missing required strengths" in raised.value.rejected["writer"][0]
    assert "requires tool support" in raised.value.rejected["coder-no-tools"][0]


def test_local_and_cheap_objectives_have_deterministic_fallback_order() -> None:
    models = [
        _model("api/frontier", "anthropic", tier=4, cost=0.08),
        _model("ollama/qwen", "openai_compat", tier=2, cost=0.0),
        _model(
            "codex/default",
            "codex",
            tier=3,
            billing="plan_included",
            cost=0.03,
        ),
    ]

    local = Router(Registry(models), prefer="local").route_decision(_task())
    cheap = Router(Registry(models), prefer="cheap").route_decision(_task())

    assert local.ranked_model_ids == (
        "ollama/qwen",
        "api/frontier",
        "codex/default",
    )
    assert cheap.ranked_model_ids[0] == "ollama/qwen"


def test_runtime_context_estimate_prefers_full_fit_before_truncating_fallback() -> None:
    small = _model(
        "small/frontier",
        "api",
        tier=4,
        context=4_096,
        output=512,
    )
    large = _model("large/solid", "api", tier=3, context=64_000)
    decision = Router(Registry([small, large])).route_decision(
        _task(),
        estimated_input_tokens=8_000,
    )

    assert decision.selected_model_id == "large/solid"
    assert decision.estimated_input_tokens == 8_000
    fallback = next(
        candidate
        for candidate in decision.candidates
        if candidate.model_id == "small/frontier"
    )
    assert fallback.eligible is True
    assert fallback.requires_truncation is True
    assert fallback.rank == 2
    assert "truncate" in (fallback.objective_note or "")


def test_context_fit_boundary_matches_projector_margin_and_output_reserve() -> None:
    model = _model(
        "boundary/model",
        "api",
        tier=4,
        context=1_000,
        output=700,
    )
    router = Router(Registry([model]))

    at_budget = router.route_decision(
        _task(),
        estimated_input_tokens=425,
    )
    over_budget = router.route_decision(
        _task(),
        estimated_input_tokens=426,
    )

    assert at_budget.candidates[0].requires_truncation is False
    assert over_budget.candidates[0].requires_truncation is True


def test_all_contexts_too_small_selects_most_headroom_with_caveat() -> None:
    tiny_frontier = _model(
        "tiny/frontier",
        "api",
        tier=4,
        context=4_096,
        output=512,
    )
    larger_solid = _model(
        "larger/solid",
        "api",
        tier=3,
        context=16_384,
        output=1_024,
    )

    decision = Router(Registry([tiny_frontier, larger_solid])).route_decision(
        _task(),
        estimated_input_tokens=32_000,
    )

    assert decision.selected_model_id == "larger/solid"
    assert all(candidate.requires_truncation for candidate in decision.candidates)
    assert "Every hard-capable model requires projection truncation" in " ".join(
        decision.caveats
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_runtime_context_estimate_fails_fast(value: object) -> None:
    registry = Registry([_model("m", "api", tier=3)])
    with pytest.raises(InvalidRoutingRequest, match="estimated_input_tokens"):
        Router(registry).route_decision(
            _task(),
            estimated_input_tokens=value,  # type: ignore[arg-type]
        )


def _component(decision, model_id: str, name: str):
    candidate = next(c for c in decision.candidates if c.model_id == model_id)
    return next(c for c in candidate.components if c.name == name)


def test_evidence_free_components_get_zero_weight_not_a_flat_constant() -> None:
    # With no quality profile and only baseline strengths, task_fit (45) and reliability
    # (4) are the SAME value for every eligible model — 49% of the score carrying no
    # information while reading as evidence. They must be zeroed and their weight
    # redistributed, and the trace must say so.
    registry = Registry(
        [
            _model("a/flagship", "a", tier=4),
            _model("b/flagship", "b", tier=4, context=1_000_000),
        ]
    )
    decision = Router(registry, prefer="quality").route_decision(_task())

    assert _component(decision, "a/flagship", "task_fit").weight == 0.0
    assert _component(decision, "a/flagship", "reliability").weight == 0.0
    assert "no evidence" in _component(decision, "a/flagship", "task_fit").evidence
    # The surviving components still span the full 100-point scale.
    weights = sum(
        c.weight
        for c in next(
            cand for cand in decision.candidates if cand.model_id == "a/flagship"
        ).components
    )
    assert weights == pytest.approx(100.0)
    assert any("No configured evidence" in caveat for caveat in decision.caveats)


def test_declared_strength_tags_make_task_fit_discriminate() -> None:
    # The lever that turns task_fit back on: one model declaring an optional strength tag
    # means the component now says something specific, so it keeps its full weight for
    # EVERY candidate and the better-tagged model wins on equal tier.
    registry = Registry(
        [
            _model("plain/flagship", "a", tier=4),
            _model(
                "tagged/flagship",
                "b",
                tier=4,
                strengths={"coding", "reasoning", "software_engineering", "debugging"},
            ),
        ]
    )
    decision = Router(registry, prefer="quality").route_decision(_task())

    # task_fit is no longer zeroed for ANYONE (one model can speak to it), and both
    # candidates carry the same weight so their scores stay comparable.
    plain_weight = _component(decision, "plain/flagship", "task_fit").weight
    assert plain_weight > 0.0
    assert _component(decision, "tagged/flagship", "task_fit").weight == plain_weight
    assert (
        _component(decision, "tagged/flagship", "task_fit").value
        > _component(decision, "plain/flagship", "task_fit").value
    )
    assert decision.selected_model_id == "tagged/flagship"


def test_profile_reliability_keeps_the_reliability_component_weighted() -> None:
    registry = Registry(
        [_model("a/flagship", "a", tier=4), _model("b/flagship", "b", tier=4)],
        quality_profiles={
            "a/flagship": ModelQualityProfile(
                source="internal eval 2026-07",
                reliability_score=0.95,
                confidence=0.8,
            )
        },
    )
    decision = Router(registry, prefer="quality").route_decision(_task())

    weighted = _component(decision, "a/flagship", "reliability").weight
    assert weighted > 0.0  # evidence exists, so the component is not zeroed
    assert _component(decision, "b/flagship", "reliability").weight == weighted
    assert decision.selected_model_id == "a/flagship"


def test_redistribution_does_not_change_the_ranking() -> None:
    # A component identical across candidates shifts every score equally, so zeroing it
    # must not reorder anything — it only stops the score from overstating evidence.
    models = [
        _model("a/flagship", "a", tier=4),
        _model("b/mid", "b", tier=3),
        _model("c/small", "c", tier=2, context=32_000, output=2_048),
    ]
    decision = Router(Registry(models), prefer="quality").route_decision(_task())

    assert decision.ranked_model_ids == ("a/flagship", "b/mid", "c/small")


def test_identical_evidence_across_all_candidates_still_carries_no_weight() -> None:
    # Evidence that every candidate shares cannot rank anything either. Testing the
    # VALUES (not merely "is a profile configured") catches this case too.
    tags = {"coding", "reasoning", "software_engineering", "debugging"}
    registry = Registry(
        [
            _model("a/flagship", "a", tier=4, strengths=tags),
            _model("b/flagship", "b", tier=4, strengths=tags),
        ]
    )
    decision = Router(registry, prefer="quality").route_decision(_task())

    # The tags DID lift the value above the 0.70 baseline...
    assert _component(decision, "a/flagship", "task_fit").value > 0.70
    # ...but every candidate got the same lift, so it still ranks nothing.
    assert _component(decision, "a/flagship", "task_fit").weight == 0.0


def test_a_single_candidate_keeps_its_absolute_score() -> None:
    # With one candidate nothing ranks anything by definition; zeroing every prior would
    # collapse the reported score for no benefit.
    registry = Registry([_model("solo/model", "a", tier=3)])
    decision = Router(registry, prefer="quality").route_decision(_task())
    candidate = decision.candidates[0]

    assert sum(c.weight for c in candidate.components) == pytest.approx(100.0)
    assert _component(decision, "solo/model", "task_fit").weight == 45.0


def test_caveat_distinguishes_unmeasured_from_coincident_evidence() -> None:
    # Telling a user who already calibrated to "run calibrate" is a false claim; the two
    # reasons a component gets dropped must be worded differently.
    plain = Router(
        Registry([_model("a", "p", tier=4), _model("b", "p", tier=3)]), prefer="quality"
    ).route_decision(_task())
    assert any("No configured evidence" in c for c in plain.caveats)

    same = ModelQualityProfile(
        task_scores={"code": 0.9}, reliability_score=0.9, source="team", confidence=0.8
    )
    calibrated = Router(
        Registry(
            [_model("a", "p", tier=4), _model("b", "p", tier=4)],
            quality_profiles={"a": same, "b": same},
        ),
        prefer="quality",
    ).route_decision(_task())
    assert not any("No configured evidence" in c for c in calibrated.caveats)
    assert any("identical for every eligible model" in c for c in calibrated.caveats)


def test_caveat_names_a_command_that_actually_exists() -> None:
    # "volante calibrate" is not a subcommand — it parses as a GOAL and would start a real,
    # billed orchestration run.
    decision = Router(
        Registry([_model("a", "p", tier=4), _model("b", "p", tier=3)]), prefer="quality"
    ).route_decision(_task())
    hint = next(c for c in decision.caveats if "`volante" in c)
    assert "`volante --calibrate" in hint
    assert "`volante calibrate`" not in " ".join(decision.caveats)


def test_single_candidate_discloses_its_evidence_free_priors() -> None:
    decision = Router(
        Registry([_model("solo", "p", tier=3)]), prefer="quality"
    ).route_decision(_task())
    assert any("without any evidence behind them" in c for c in decision.caveats)


def test_component_arithmetic_and_score_bounds_hold_after_redistribution() -> None:
    registry = Registry(
        [
            _model("a", "p", tier=4, context=1_000_000, output=8_192),
            _model("b", "p", tier=3, context=128_000),
            _model("c", "p", tier=1, context=8_000, output=1_024),
        ]
    )
    decision = Router(registry, prefer="quality").route_decision(_task())

    for candidate in decision.candidates:
        if not candidate.eligible:
            continue
        for component in candidate.components:
            assert component.contribution == pytest.approx(
                component.value * component.weight, abs=1e-3
            )
        assert sum(c.weight for c in candidate.components) == pytest.approx(100.0)
        assert candidate.score is not None
        assert 0.0 <= candidate.score <= 100.0


def test_redistribution_preserves_the_raw_ranking_exactly() -> None:
    # The property that makes redistribution safe: it scales every candidate by the same
    # factor. Rounding each weight BEFORE multiplying would break it, so compare the
    # produced ranking against one computed from the unweighted components.
    from volante.router import _quality_components

    models = [
        _model("a/cheap", "p", tier=2, context=130_453, output=1_024, cost=0.01),
        _model("b/dear", "p", tier=2, context=95_857, output=1_024, cost=0.02),
        _model("c/mid", "p", tier=3, context=131_072, output=2_048, cost=0.015),
    ]
    registry = Registry(models)
    task = _task()
    decision = Router(registry, prefer="quality").route_decision(task)

    raw_totals = {
        model.id: sum(
            component.contribution
            for component in _quality_components(
                model, task, None, estimated_input_tokens=decision.estimated_input_tokens
            )
        )
        for model in models
    }
    expected = tuple(
        sorted(raw_totals, key=lambda mid: (-raw_totals[mid], mid))
    )
    assert decision.ranked_model_ids == expected

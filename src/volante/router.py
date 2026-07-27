from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from volante.projector import model_input_token_budget
from volante.registry import ModelQualityProfile, Registry
from volante.types import ModelInfo, Task, effective_required_tools, validate_task

logger = logging.getLogger(__name__)

# Task type is a planner/user contract, not a hint. Unknown values must not silently
# become "reasoning": doing so makes route explanations and capability guarantees false.
_TYPE_STRENGTHS: dict[str, frozenset[str]] = {
    "code": frozenset({"coding"}),
    "research": frozenset({"reasoning"}),
    "write": frozenset({"reasoning"}),
    "analyze": frozenset({"reasoning"}),
}

# Optional, more specific strength tags. They improve task fit when users supply richer
# model metadata while preserving compatibility with today's coding/reasoning profiles.
_RELATED_STRENGTHS: dict[str, frozenset[str]] = {
    "code": frozenset(
        {"coding", "software_engineering", "debugging", "instruction_following"}
    ),
    "research": frozenset(
        {"reasoning", "research", "grounding", "long_context"}
    ),
    "write": frozenset(
        {"reasoning", "writing", "creativity", "instruction_following"}
    ),
    "analyze": frozenset({"reasoning", "analysis", "math", "long_context"}),
}

_SUBSCRIPTION_BILLING = frozenset({"plan_included", "plan_credit"})
_OBJECTIVES = frozenset({"quality", "cash_protect_quota", "local", "cheap"})
_DESIRED_TIER: dict[str, int] = {
    "trivial": 1,
    "easy": 2,
    "medium": 3,
    "hard": 4,
}
_CONTEXT_TARGET: dict[str, int] = {
    "trivial": 4_096,
    "easy": 8_192,
    "medium": 32_768,
    "hard": 131_072,
}
_OUTPUT_TARGET: dict[str, int] = {
    "trivial": 512,
    "easy": 1_024,
    "medium": 2_048,
    "hard": 4_096,
}


class InvalidRoutingRequest(ValueError):
    """The task or routing objective is outside Volante's declared contract."""


class NoEligibleModelError(ValueError):
    """No configured model satisfies the task's hard capability constraints."""

    def __init__(
        self,
        task: Task,
        rejected: dict[str, tuple[str, ...]],
    ) -> None:
        self.task_id = task.id
        self.rejected = rejected
        detail = "; ".join(
            f"{model_id}: {', '.join(reasons)}"
            for model_id, reasons in sorted(rejected.items())
        )
        super().__init__(
            f"no configured model is eligible for task {task.id!r}"
            + (f" ({detail})" if detail else "")
        )


@dataclass(frozen=True)
class ScoreComponent:
    """One normalized, inspectable contribution to predicted task quality."""

    name: str
    value: float
    weight: float
    contribution: float
    evidence: str


@dataclass(frozen=True)
class RoutingCandidate:
    """Eligibility, quality score, and rank for one configured model."""

    model_id: str
    provider: str
    eligible: bool
    score: float | None
    components: tuple[ScoreComponent, ...]
    rejection_reasons: tuple[str, ...] = ()
    objective_note: str | None = None
    rank: int | None = None
    is_local: bool = False
    profile_source: str | None = None
    requires_truncation: bool = False


@dataclass(frozen=True)
class RoutingDecision:
    """Structured and serializable explanation of a routing choice."""

    task_id: str
    objective: str
    selected_model_id: str
    ranked_model_ids: tuple[str, ...]
    required_strengths: tuple[str, ...]
    needs_tools: bool
    required_tools: tuple[str, ...]
    estimated_input_tokens: int
    candidates: tuple[RoutingCandidate, ...]
    explanation: str
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for CLI/UI/MCP integrations."""

        return asdict(self)


def _cash(model: ModelInfo) -> float:
    # Subscription calls draw quota rather than incremental card cash.
    return (
        0.0
        if model.billing in _SUBSCRIPTION_BILLING
        else model.cost_per_1k_out
    )


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _estimated_input_tokens(task: Task) -> int:
    # Router sees the task description, not the eventual dependency artifacts. This is
    # therefore only a hard lower bound; Projector/worker context budgeting still owns
    # the complete prompt at execution time.
    return max(256, (len(task.description) + 3) // 4 + 128)


def _profile_blend(
    configured: float | None,
    fallback: float,
    profile: ModelQualityProfile | None,
) -> float:
    if configured is None or profile is None:
        return fallback
    return (
        profile.confidence * float(configured)
        + (1.0 - profile.confidence) * fallback
    )


def _is_local(model: ModelInfo, profile: ModelQualityProfile | None) -> bool:
    if profile is not None and profile.is_local is not None:
        return profile.is_local
    provider = model.provider.lower()
    model_id = model.id.lower()
    return provider in {"local", "ollama", "lmstudio"} or model_id.startswith(
        ("local/", "ollama/", "lmstudio/")
    )


def _eligibility_reasons(
    model: ModelInfo,
    *,
    required_strengths: frozenset[str],
    needs_tools: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    missing = required_strengths - model.strengths
    if missing:
        reasons.append(f"missing required strengths {sorted(missing)}")
    if needs_tools and not model.supports_tools:
        reasons.append("task mode 'agentic' requires tool support")

    return tuple(reasons)


def _metadata_task_fit(model: ModelInfo, task_type: str) -> float:
    required = _TYPE_STRENGTHS[task_type]
    related_optional = _RELATED_STRENGTHS[task_type] - required
    if not related_optional:
        return 1.0
    optional_coverage = len(model.strengths & related_optional) / len(related_optional)
    # A model passing the hard strength requirement has a meaningful baseline. Richer
    # capability tags refine, rather than replace, that guarantee.
    return 0.70 + 0.30 * optional_coverage


def _quality_components(
    model: ModelInfo,
    task: Task,
    profile: ModelQualityProfile | None,
    *,
    estimated_input_tokens: int,
) -> tuple[ScoreComponent, ...]:
    metadata_task_fit = _metadata_task_fit(model, task.type)
    configured_task_fit = (
        profile.task_scores.get(task.type) if profile is not None else None
    )
    task_fit = _profile_blend(configured_task_fit, metadata_task_fit, profile)
    task_evidence = (
        f"profile {profile.source!r}, confidence={profile.confidence:.2f}"
        if configured_task_fit is not None and profile is not None
        else "declared model strengths"
    )

    tier_prior = _clamp_unit(model.tier / 4.0)
    configured_overall = profile.overall_score if profile is not None else None
    overall = _profile_blend(configured_overall, tier_prior, profile)
    overall_evidence = (
        f"profile {profile.source!r}, confidence={profile.confidence:.2f}"
        if configured_overall is not None and profile is not None
        else f"coarse declared tier {model.tier}"
    )

    desired_tier = _DESIRED_TIER[task.difficulty]
    difficulty_fit = _clamp_unit(model.tier / desired_tier)

    usable_context = model_input_token_budget(
        model.context_window,
        model.max_output_tokens,
    )
    context_target = max(
        estimated_input_tokens,
        _CONTEXT_TARGET[task.difficulty],
    )
    context_fit = _clamp_unit(usable_context / context_target)
    output_fit = _clamp_unit(
        model.max_output_tokens / _OUTPUT_TARGET[task.difficulty]
    )

    configured_reliability = (
        profile.reliability_score if profile is not None else None
    )
    reliability = _profile_blend(configured_reliability, 0.75, profile)
    reliability_evidence = (
        f"profile {profile.source!r}, confidence={profile.confidence:.2f}"
        if configured_reliability is not None and profile is not None
        else "neutral prior; no reliability evidence configured"
    )

    raw = (
        (
            "task_fit",
            task_fit,
            45.0,
            task_evidence,
        ),
        (
            "overall_quality",
            overall,
            28.0,
            overall_evidence,
        ),
        (
            "difficulty_readiness",
            difficulty_fit,
            10.0,
            f"tier {model.tier} versus desired tier {desired_tier}",
        ),
        (
            "context_headroom",
            context_fit,
            8.0,
            f"{usable_context} usable tokens versus "
            f"{context_target} target",
        ),
        (
            "output_capacity",
            output_fit,
            5.0,
            f"{model.max_output_tokens} tokens versus "
            f"{_OUTPUT_TARGET[task.difficulty]} target",
        ),
        (
            "reliability",
            reliability,
            4.0,
            reliability_evidence,
        ),
    )
    return tuple(
        ScoreComponent(
            name=name,
            value=round(value, 4),
            weight=weight,
            contribution=round(value * weight, 4),
            evidence=evidence,
        )
        for name, value, weight, evidence in raw
    )


def _score(components: tuple[ScoreComponent, ...]) -> float:
    return round(sum(component.contribution for component in components), 4)


# Only the two evidence-backed priors are eligible to be dropped. The capability
# components (tier, context, output) are always meaningful statements about a model even
# when they happen to agree, and zeroing them would change what the score means.
_DROPPABLE_COMPONENTS = frozenset({"task_fit", "reliability"})


def _varying_component_names(
    scored: list[tuple[ScoreComponent, ...]],
) -> tuple[ScoreComponent, ...]:
    """Components whose values actually differ across candidates.

    Used to describe a ranking truthfully: naming a fixed list of "declared tier,
    context, and output capacity" would be wrong whenever one of those also tied, or
    when a profile-backed component did the deciding.
    """
    if len(scored) < 2:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for components in scored:
        for component in components:
            if component.name in seen:
                continue
            seen.add(component.name)
            names.append(component.name)
    varying: list[ScoreComponent] = []
    for name in names:
        values = {
            component.value
            for components in scored
            for component in components
            if component.name == name
        }
        if len(values) > 1:
            varying.append(
                ScoreComponent(
                    name=name, value=0.0, weight=0.0, contribution=0.0, evidence=""
                )
            )
    return tuple(varying)


def _uninformative_components(
    scored: list[tuple[ScoreComponent, ...]],
) -> frozenset[str]:
    """Names of prior components that are identical for every eligible candidate.

    Identical means the component cannot rank anything: it adds the same constant to
    every score. That is true both when no evidence is configured at all (every model
    falls back to the same prior) and when every model happens to carry the same
    evidence, so testing the VALUES catches both instead of only the first.

    With a single candidate nothing ranks anything by definition; keep the full weights
    so the reported score stays on its absolute scale.
    """
    if len(scored) < 2:
        return frozenset()
    uninformative: set[str] = set()
    for name in _DROPPABLE_COMPONENTS:
        values = {
            component.value
            for components in scored
            for component in components
            if component.name == name
        }
        if len(values) == 1:
            uninformative.add(name)
    return frozenset(uninformative)


def _reweight_without(
    components: tuple[ScoreComponent, ...], uninformative: frozenset[str]
) -> tuple[ScoreComponent, ...]:
    """Redistribute the weight of evidence-free components onto the informative ones.

    A component that is identical for every eligible candidate cannot rank anything, yet
    at full weight it still dominates the reported score — a 45-point "task fit" that is
    really a constant reads as evidence when it is not. Zeroing it and spreading its
    weight proportionally keeps the score on the same 100-point scale, leaves the ranking
    untouched (a constant shifts every candidate equally), and makes the trace honest.

    The decision to drop is made ONCE per routing call, so every candidate is scored on
    the same weights and the numbers stay comparable.
    """
    if not uninformative:
        return components
    kept = [c for c in components if c.name not in uninformative]
    freed = sum(c.weight for c in components if c.name in uninformative)
    kept_weight = sum(c.weight for c in kept)
    if not kept or kept_weight <= 0:  # nothing informative left; keep the original scale
        return components
    scale = (kept_weight + freed) / kept_weight
    rewritten: list[ScoreComponent] = []
    for component in components:
        if component.name in uninformative:
            rewritten.append(
                ScoreComponent(
                    name=component.name,
                    value=component.value,
                    weight=0.0,
                    contribution=0.0,
                    evidence=(
                        f"{component.evidence} — identical for every eligible model, so "
                        "it carries no evidence here; its weight was redistributed"
                    ),
                )
            )
            continue
        # Derive the contribution from the UNROUNDED scaled weight. Rounding the weight
        # first would scale each component by a slightly different factor, which breaks
        # the property that makes this safe: every candidate must be scaled identically,
        # or redistribution could reorder the ranking it is supposed to leave alone.
        scaled_weight = component.weight * scale
        rewritten.append(
            ScoreComponent(
                name=component.name,
                value=component.value,
                weight=round(scaled_weight, 4),
                contribution=round(component.value * scaled_weight, 4),
                evidence=component.evidence,
            )
        )
    return tuple(rewritten)


class Router:
    """Select the best predicted model fit from the user's configured inventory.

    Hard capabilities are checked first. Eligible models are then ranked under an
    explicit objective. The default ``quality`` objective never lowers a model's score
    because it costs cash or quota; price is only a deterministic final tie-break.
    """

    def __init__(self, registry: Registry, *, prefer: str = "quality") -> None:
        if prefer not in _OBJECTIVES:
            raise InvalidRoutingRequest(
                f"unknown routing objective {prefer!r}; "
                f"expected one of {sorted(_OBJECTIVES)}"
            )
        self._registry = registry
        self._prefer = prefer

    def _validate_task(self, task: Task) -> None:
        try:
            validate_task(task)
        except ValueError as exc:
            raise InvalidRoutingRequest(str(exc)) from exc

    def _objective_ranking(
        self,
        task: Task,
        eligible: list[tuple[ModelInfo, float, bool]],
    ) -> tuple[list[ModelInfo], dict[str, str]]:
        by_id = {model.id: (model, score, local) for model, score, local in eligible}
        notes: dict[str, str] = {}

        def quality_key(model: ModelInfo) -> tuple[float, int, float, str]:
            _, score, _ = by_id[model.id]
            # Cost cannot overtake predicted quality under this objective.
            return (-score, -model.tier, _cash(model), model.id)

        models = [model for model, _, _ in eligible]
        if self._prefer == "quality":
            return sorted(models, key=quality_key), notes

        if self._prefer == "local":
            local_models = [model for model, _, local in eligible if local]
            remote_models = [model for model, _, local in eligible if not local]
            if not local_models:
                logger.info(
                    "no local model configured for task %r; falling back to quality",
                    task.id,
                )
                for model in remote_models:
                    notes[model.id] = (
                        "no local model was identified; quality fallback applied"
                    )
                return sorted(remote_models, key=quality_key), notes
            for model in remote_models:
                notes[model.id] = "ranked after eligible local models by objective"
            return (
                sorted(local_models, key=quality_key)
                + sorted(remote_models, key=quality_key),
                notes,
            )

        if self._prefer == "cheap":
            return (
                sorted(
                    models,
                    key=lambda model: (
                        # Unlike ``cash_protect_quota``, cheap compares the configured
                        # economic value for every billing mode. This prevents a
                        # subscription call from tying a genuinely free local model
                        # merely because neither produces an incremental card charge.
                        model.cost_per_1k_out,
                        model.cost_per_1k_in,
                        -by_id[model.id][1],
                        -model.tier,
                        model.id,
                    ),
                ),
                notes,
            )

        # cash_protect_quota retains the established semantics: use an adequate direct
        # model for non-hard work where possible, reserving subscription quota. Quality
        # remains a tie-break after cash and right-sizing.
        desired_tier = _DESIRED_TIER[task.difficulty]
        adequate = [model for model in models if model.tier >= desired_tier]
        pool = adequate or models
        if not adequate:
            for model in models:
                notes[model.id] = (
                    f"no model reached desired tier {desired_tier}; "
                    "best-effort cash ranking applied"
                )

        if task.difficulty != "hard":
            direct = [
                model
                for model in pool
                if model.billing not in _SUBSCRIPTION_BILLING
            ]
            if direct:
                excluded = {model.id for model in pool} - {model.id for model in direct}
                for model_id in excluded:
                    notes[model_id] = (
                        "eligible but reserved to protect subscription quota"
                    )
                pool = direct
            elif pool:
                logger.info(
                    "using quota: no adequate direct candidate for task %r "
                    "(difficulty=%s)",
                    task.id,
                    task.difficulty,
                )

        ranked = sorted(
            pool,
            key=lambda model: (
                _cash(model),
                model.tier,
                -by_id[model.id][1],
                model.id,
            ),
        )
        # Selection policy applies to the preferred pool, but rerouting must retain
        # every eligible candidate. Otherwise a quota/cap failure on the winner can
        # make Runtime fail even though a lower-tier or subscription fallback exists.
        ranked_ids = {model.id for model in ranked}
        fallback_models = [
            model for model in models if model.id not in ranked_ids
        ]
        if task.difficulty != "hard":
            fallback = sorted(
                fallback_models,
                key=lambda model: (
                    model.billing in _SUBSCRIPTION_BILLING,
                    _cash(model),
                    abs(model.tier - desired_tier),
                    -by_id[model.id][1],
                    model.id,
                ),
            )
        else:
            fallback = sorted(
                fallback_models,
                key=lambda model: (
                    _cash(model),
                    abs(model.tier - desired_tier),
                    -by_id[model.id][1],
                    model.id,
                ),
            )
        for model in fallback:
            notes.setdefault(
                model.id,
                "eligible fallback ranked after the objective-preferred pool",
            )
        return ranked + fallback, notes

    def route_decision(
        self,
        task: Task,
        *,
        estimated_input_tokens: int | None = None,
    ) -> RoutingDecision:
        """Return the selected model plus the evidence for every configured model."""

        self._validate_task(task)
        required = _TYPE_STRENGTHS[task.type]
        needs_tools = task.mode == "agentic"
        required_tools = tuple(sorted(effective_required_tools(task)))
        if estimated_input_tokens is None:
            estimated_input = _estimated_input_tokens(task)
        elif (
            isinstance(estimated_input_tokens, bool)
            or not isinstance(estimated_input_tokens, int)
            or estimated_input_tokens < 1
        ):
            raise InvalidRoutingRequest(
                "estimated_input_tokens must be a positive integer"
            )
        else:
            estimated_input = estimated_input_tokens

        raw_candidates: list[
            tuple[
                ModelInfo,
                bool,
                float | None,
                tuple[ScoreComponent, ...],
                tuple[str, ...],
                bool,
                bool,
                ModelQualityProfile | None,
            ]
        ] = []
        rejected: dict[str, tuple[str, ...]] = {}
        full_fit: list[tuple[ModelInfo, float, bool]] = []
        truncating: list[tuple[ModelInfo, float, bool]] = []
        pending: list[
            tuple[
                int,
                ModelInfo,
                tuple[ScoreComponent, ...],
                bool,
                bool,
                ModelQualityProfile | None,
            ]
        ] = []

        # Iterate over every model actually present in the user's Registry. A decision
        # therefore accounts for the complete cross-provider inventory, including
        # candidates rejected by hard constraints.
        for model in self._registry.all():
            profile = self._registry.quality_profile(model.id)
            rejection_reasons = _eligibility_reasons(
                model,
                required_strengths=required,
                needs_tools=needs_tools,
            )
            local = _is_local(model, profile)
            usable_input = model_input_token_budget(
                model.context_window,
                model.max_output_tokens,
            )
            requires_truncation = usable_input < estimated_input
            if rejection_reasons:
                rejected[model.id] = rejection_reasons
                raw_candidates.append(
                    (
                        model,
                        False,
                        None,
                        (),
                        rejection_reasons,
                        local,
                        requires_truncation,
                        profile,
                    )
                )
                continue

            components = _quality_components(
                model,
                task,
                profile,
                estimated_input_tokens=estimated_input,
            )
            # Score after the loop: whether a component carries evidence is a property of
            # the whole candidate set, not of one model, and every candidate must be
            # scored on the same weights to stay comparable.
            pending.append(
                (len(raw_candidates), model, components, local, requires_truncation, profile)
            )
            raw_candidates.append(
                (
                    model,
                    True,
                    None,
                    components,
                    (),
                    local,
                    requires_truncation,
                    profile,
                )
            )

        # Decide the weighting ONCE, from the whole candidate set, so every candidate is
        # scored on the same weights and the numbers stay comparable.
        uninformative = _uninformative_components(
            [components for _, _, components, _, _, _ in pending]
        )
        # Which dropped priors were actually MEASURED? A component can be identical
        # across candidates because nobody measured it, or because everybody measured the
        # same thing — telling a calibrated user to go calibrate would be false.
        measured: set[str] = set()
        for _, _, _, _, _, entry_profile in pending:
            if entry_profile is None:
                continue
            if entry_profile.task_scores.get(task.type) is not None:
                measured.add("task_fit")
            if entry_profile.reliability_score is not None:
                measured.add("reliability")
        for index, model, components, local, requires_truncation, _ in pending:
            weighted = _reweight_without(components, uninformative)
            total = _score(weighted)
            target = truncating if requires_truncation else full_fit
            target.append((model, total, local))
            entry = list(raw_candidates[index])
            entry[2] = total
            entry[3] = weighted
            raw_candidates[index] = tuple(entry)  # type: ignore[assignment]

        eligible_for_ranking = full_fit + truncating
        if not eligible_for_ranking:
            raise NoEligibleModelError(task, rejected)

        if full_fit:
            ranked, objective_notes = self._objective_ranking(task, full_fit)
            if truncating:
                truncating_ranked, truncating_notes = self._objective_ranking(
                    task,
                    truncating,
                )
                ranked.extend(truncating_ranked)
                objective_notes.update(truncating_notes)
                for model, _, _ in truncating:
                    objective_notes[model.id] = (
                        "eligible fallback, but projection must truncate input "
                        f"estimated at {estimated_input} tokens"
                    )
        else:
            # Every hard-capable model needs projection truncation. Preserve the
            # most task context first; configured quality remains the tie-break.
            ranked, objective_notes = self._objective_ranking(task, truncating)
            objective_rank = {
                model.id: index for index, model in enumerate(ranked)
            }
            ranked.sort(
                key=lambda model: (
                    -model_input_token_budget(
                        model.context_window,
                        model.max_output_tokens,
                    ),
                    objective_rank[model.id],
                )
            )
            for model, _, _ in truncating:
                objective_notes[model.id] = (
                    "best-effort candidate: every hard-capable model requires "
                    f"truncating the estimated {estimated_input}-token input"
                )
        ranked_ids = tuple(model.id for model in ranked)
        ranks = {model_id: index + 1 for index, model_id in enumerate(ranked_ids)}

        candidates = tuple(
            RoutingCandidate(
                model_id=model.id,
                provider=model.provider,
                eligible=eligible,
                score=score,
                components=components,
                rejection_reasons=rejection_reasons,
                objective_note=objective_notes.get(model.id),
                rank=ranks.get(model.id),
                is_local=local,
                profile_source=profile.source if profile is not None else None,
                requires_truncation=requires_truncation,
            )
            for (
                model,
                eligible,
                score,
                components,
                rejection_reasons,
                local,
                requires_truncation,
                profile,
            ) in raw_candidates
        )

        winner = next(
            candidate
            for candidate in candidates
            if candidate.model_id == ranked_ids[0]
        )
        top_components = sorted(
            winner.components,
            key=lambda component: component.contribution,
            reverse=True,
        )[:3]
        component_text = ", ".join(
            f"{component.name}={component.value:.2f}"
            for component in top_components
        )
        eligible_count = len(eligible_for_ranking)
        explanation = (
            f"Selected {winner.model_id!r} from {eligible_count} eligible model(s) "
            f"out of {len(raw_candidates)} configured for task {task.id!r} "
            f"under objective {self._prefer!r}. Predicted quality score "
            f"{winner.score:.2f}; leading signals: {component_text}."
        )
        caveats_list = [
            "The winner is a deterministic prediction from configured capabilities, "
            "quality profiles, and metadata; it is not an empirical 'best model' claim.",
            "Scores become evidence-based only when callers supply calibrated "
            "ModelQualityProfile results from representative evaluations.",
        ]
        varied = sorted(
            {
                component.name
                for _, _, components, _, _, _ in pending
                for component in components
                if component.name not in uninformative
            }
            & {
                component.name
                for component in _varying_component_names(
                    [components for _, _, components, _, _, _ in pending]
                )
            }
        )
        unmeasured = sorted(uninformative - measured)
        coincident = sorted(uninformative & measured)
        if unmeasured:
            caveats_list.append(
                "No configured evidence for "
                + ", ".join(unmeasured)
                + "; those components were given zero weight instead of a flat constant"
                + (f", so this ranking rests on {', '.join(varied)}" if varied else "")
                + ". Run `volante --calibrate measurements.json` to supply real evidence."
            )
        if coincident:
            caveats_list.append(
                "Configured evidence for "
                + ", ".join(coincident)
                + " is identical for every eligible model here, so it cannot rank them; "
                "its weight was redistributed."
            )
        if len(pending) == 1 and not uninformative:
            # The single-candidate branch keeps the absolute scale, so the flat priors
            # stay in the score. Say so rather than letting them read as evidence.
            flat = sorted(
                component.name
                for component in pending[0][2]
                if component.name in _DROPPABLE_COMPONENTS
                and component.name not in measured
            )
            if flat:
                caveats_list.append(
                    "Only one model was eligible, so "
                    + ", ".join(flat)
                    + " contributed their neutral priors to the reported score without "
                    "any evidence behind them."
                )
        if truncating:
            if full_fit:
                caveats_list.append(
                    "Some eligible fallbacks require projection truncation and are "
                    "ranked after candidates that fit the estimated input."
                )
            else:
                caveats_list.append(
                    "Every hard-capable model requires projection truncation; Volante "
                    "selected the candidate with the most usable context as a "
                    "best-effort route."
                )
        return RoutingDecision(
            task_id=task.id,
            objective=self._prefer,
            selected_model_id=ranked_ids[0],
            ranked_model_ids=ranked_ids,
            required_strengths=tuple(sorted(required)),
            needs_tools=needs_tools,
            required_tools=required_tools,
            estimated_input_tokens=estimated_input,
            candidates=candidates,
            explanation=explanation,
            caveats=tuple(caveats_list),
        )

    # Short alias for integrations that prefer decision-oriented terminology.
    decide = route_decision

    def route_ranked(
        self,
        task: Task,
        *,
        estimated_input_tokens: int | None = None,
    ) -> list[str]:
        """Return model ids in deterministic reroute order (legacy API)."""

        return list(
            self.route_decision(
                task, estimated_input_tokens=estimated_input_tokens
            ).ranked_model_ids
        )

    def route(
        self,
        task: Task,
        *,
        estimated_input_tokens: int | None = None,
    ) -> str:
        """Return the selected model id (legacy API)."""

        return self.route_decision(
            task, estimated_input_tokens=estimated_input_tokens
        ).selected_model_id

    def explain(self, task: Task) -> str:
        """Return a concise human-readable selection explanation."""

        return self.route_decision(task).explanation

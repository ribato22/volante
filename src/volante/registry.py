# src/volante/registry.py
from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from volante.types import TASK_TYPES, ModelInfo

_BILLING_MODES = frozenset({"card", "plan_credit", "plan_included"})

logger = logging.getLogger(__name__)



def _validate_model(model: ModelInfo) -> None:
    """Reject invalid inventory records before they can influence routing."""
    if not isinstance(model.id, str) or not model.id.strip():
        raise ValueError("model id must be a non-empty string")
    if not isinstance(model.provider, str) or not model.provider.strip():
        raise ValueError(f"model {model.id!r} provider must be a non-empty string")
    if (
        not isinstance(model.strengths, set)
        or not model.strengths
        or any(not isinstance(item, str) or not item.strip() for item in model.strengths)
    ):
        raise ValueError(f"model {model.id!r} strengths must be a non-empty set of strings")
    for name, value in (
        ("context_window", model.context_window),
        ("max_output_tokens", model.max_output_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"model {model.id!r} {name} must be a positive integer")
    if model.max_output_tokens >= model.context_window:
        raise ValueError(
            f"model {model.id!r} max_output_tokens must be smaller than context_window"
        )
    if isinstance(model.tier, bool) or not isinstance(model.tier, int) or not 1 <= model.tier <= 4:
        raise ValueError(f"model {model.id!r} tier must be an integer from 1 to 4")
    if model.billing not in _BILLING_MODES:
        raise ValueError(
            f"model {model.id!r} has invalid billing {model.billing!r}; "
            f"expected one of {sorted(_BILLING_MODES)}"
        )
    if not isinstance(model.supports_tools, bool):
        raise ValueError(f"model {model.id!r} supports_tools must be boolean")
    for name, cost in (
        ("cost_per_1k_in", model.cost_per_1k_in),
        ("cost_per_1k_out", model.cost_per_1k_out),
    ):
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(float(cost))
            or cost < 0
        ):
            raise ValueError(f"model {model.id!r} {name} must be a finite non-negative number")


def _validate_unit_score(name: str, value: float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value!r}")


@dataclass(frozen=True)
class ModelQualityProfile:
    """Optional calibrated evidence used by the router.

    ``ModelInfo.tier`` remains the coarse, always-available quality prior.
    Profiles let callers replace that coarse prior with benchmark- or
    user-derived scores without baking vendor claims into Volante. All scores use
    a provider-independent 0..1 scale. ``source`` should identify the evidence,
    for example ``"user-eval-2026-07"``.
    """

    task_scores: Mapping[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    reliability_score: float | None = None
    is_local: bool | None = None
    source: str = "user-configured"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_scores", dict(self.task_scores))
        unknown = set(self.task_scores) - TASK_TYPES
        if unknown:
            raise ValueError(
                f"unknown task quality profile keys: {sorted(unknown)}; "
                f"expected a subset of {sorted(TASK_TYPES)}"
            )
        for task_type, score in self.task_scores.items():
            _validate_unit_score(f"task_scores[{task_type!r}]", score)
        _validate_unit_score("overall_score", self.overall_score)
        _validate_unit_score("reliability_score", self.reliability_score)
        _validate_unit_score("confidence", self.confidence)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("profile source must be a non-empty string")


class Registry:
    def __init__(
        self,
        models: list[ModelInfo],
        *,
        quality_profiles: Mapping[str, ModelQualityProfile] | None = None,
    ) -> None:
        self._models: list[ModelInfo] = list(models)
        for model in self._models:
            _validate_model(model)
        ids = [m.id for m in self._models]
        duplicates = sorted(
            model_id for model_id, count in Counter(ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"duplicate model ids: {duplicates}")
        self._by_id: dict[str, ModelInfo] = {m.id: m for m in self._models}
        self._quality_profiles = dict(quality_profiles or {})
        unknown_profiles = set(self._quality_profiles) - set(self._by_id)
        # A profile naming models you do not have is not automatically an error. A
        # calibration run, or a profile shared between machines, describes the
        # inventory it MEASURED — and rejecting the whole file for mentioning a model
        # this process happens not to configure makes such a file unusable anywhere but
        # its origin. Entries that match nothing cannot affect routing, so they are
        # dropped and named.
        #
        # But a file where NOTHING matches is a different thing: it describes some
        # other inventory, or every id in it is wrong. Failing fast there keeps the
        # typo protection this check was added for.
        if unknown_profiles:
            if not (set(self._quality_profiles) & set(self._by_id)):
                raise ValueError(
                    "quality profiles reference unknown models: "
                    f"{sorted(unknown_profiles)}"
                )
            logger.warning(
                "ignoring %d quality profile(s) for models not configured here: %s",
                len(unknown_profiles),
                ", ".join(sorted(unknown_profiles)),
            )
            for stale in unknown_profiles:
                del self._quality_profiles[stale]

    def all(self) -> list[ModelInfo]:
        return list(self._models)

    def get(self, model_id: str) -> ModelInfo:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise ValueError(f"unknown model_id: {model_id!r}") from exc

    def quality_profile(self, model_id: str) -> ModelQualityProfile | None:
        """Return calibrated quality evidence for ``model_id``, if configured."""

        # Match ``get``'s fail-fast contract instead of silently accepting a typo.
        self.get(model_id)
        return self._quality_profiles.get(model_id)

    def matching(self, strengths: set[str], needs_tools: bool = False) -> list[ModelInfo]:
        result: list[ModelInfo] = []
        for m in self._models:
            if not strengths.issubset(m.strengths):
                continue
            if needs_tools and not m.supports_tools:
                continue
            result.append(m)
        return result


def default_models() -> list[ModelInfo]:
    """Seed inventory.

    Two kinds of number live in a ModelInfo and they are NOT interchangeable.
    `context_window`, `max_output_tokens` and the two prices are FACTS a vendor
    publishes: every one carries the URL it was read from and the date it was read,
    because a stale one is silent and expensive. `tier`, `strengths` and `billing`
    are OUR policy — argue with them, don't cite them.

    That distinction is not decorative. Every field below was wrong on 2026-07-28:
    the Anthropic row had been carried forward from the Opus 4.1 generation, so it
    priced every run 3x too high and budgeted context against a fifth of the real
    window; the Kimi row pointed at a series the vendor had already retired.
    """
    return [
        ModelInfo(
            id="anthropic/claude-opus-4-8",
            provider="anthropic",
            strengths={"coding", "reasoning"},
            # Verified 2026-07-28 against platform.claude.com:
            #   /docs/en/build-with-claude/context-windows — 1M context, and a
            #     single request on a 1M model may generate up to 128k output.
            #   /docs/en/about-claude/pricing — $5 / MTok in, $25 / MTok out.
            #     MTok is a MILLION tokens; this field is per THOUSAND, so the
            #     divisor is 1000: 5/1000 = 0.005, 25/1000 = 0.025. Getting that
            #     conversion wrong is what produced the previous 3x error.
            # A single scalar cannot express fast mode, batch (-50%), cache reads
            # (0.1x) or partner pricing on Bedrock/Vertex; this is the list rate.
            # GET /v1/models/{id} serves max_input_tokens and max_tokens live and
            # is the better source for those two. It does NOT serve prices.
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_tools=True,
            cost_per_1k_in=0.005,
            cost_per_1k_out=0.025,
            tier=4,
            billing="card",
        ),
        ModelInfo(
            id="kimi/kimi-k3",
            provider="openai_compat",
            # Catch-all {coding, reasoning}: the router can send EVERY task type
            # here when this is the only configured provider.
            strengths={"coding", "reasoning"},
            # Verified 2026-07-28 against platform.kimi.ai:
            #   /docs/models — the kimi-k2 SERIES was discontinued 2026-05-25 and
            #     all five k2 wire ids sit in the Deprecated table. This entry used
            #     to name that dead series, so the default wire id would have
            #     failed against the live API — worse than a wrong number.
            #   /docs/pricing/chat-k3 — context 1,048,576; input $0.30/1M on a
            #     cache hit and $3.00/1M on a miss; output $15.00/1M.
            #   /docs/api/chat — max_completion_tokens defaults to 131,072.
            # Moonshot prices input at two rates and this schema holds one, so the
            # CACHE-MISS rate is stored: it is the safe direction for an estimator,
            # since assuming hits would understate cold traffic tenfold.
            # Currency caveat, recorded rather than hidden: the pricing page uses a
            # bare "$" and never writes "USD". Very likely USD, but unsourced.
            context_window=1_048_576,
            max_output_tokens=131_072,
            supports_tools=True,
            cost_per_1k_in=0.003,
            cost_per_1k_out=0.015,
            tier=3,
            billing="card",
        ),
        ModelInfo(
            id="ollama/llama3.2",
            provider="openai_compat",
            # Catch-all so an Ollama-only (free) setup can still run a full
            # orchestration. supports_tools=False -> agentic tasks are not routed
            # here, because llama3.2 does not reliably obey tool-calling.
            strengths={"coding", "reasoning"},
            # These two are a DEPLOYMENT FLOOR we chose, not a vendor fact, and the
            # difference matters. Llama 3.2 the model declares 131,072 tokens, but
            # this registry records what a running Ollama actually grants: since
            # v0.15.5 that is VRAM-tiered (4k under 24 GiB, 32k to 48 GiB, 256k
            # above — github.com/ollama/ollama/blob/main/docs/context-length.mdx,
            # read 2026-07-28), and the OpenAI-compatible shim exposes no num_ctx
            # knob, so a client CANNOT raise it. Over-claiming fails silently:
            # Ollama drops the oldest messages to fit and never raises. 4096 is the
            # bottom tier — what an out-of-the-box install gives on most machines.
            # Raise it with OLLAMA_CONTEXT only to match the server's own
            # OLLAMA_CONTEXT_LENGTH. max_output is ours too (Ollama's num_predict
            # defaults to unlimited); it is sized against a real 4096 window,
            # because input and output share one budget.
            context_window=4_096,
            max_output_tokens=1_024,
            supports_tools=False,
            # Genuinely free: inference runs on the user's own hardware, so there
            # is no vendor rate to convert.
            cost_per_1k_in=0.0,
            cost_per_1k_out=0.0,
            tier=1,
            billing="card",
        ),
    ]


def default_registry() -> Registry:
    return Registry(default_models())

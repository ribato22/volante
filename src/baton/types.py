from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    type: str = "tool_result"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class CanonicalMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: list[ContentBlock]


def text(role: str, s: str) -> CanonicalMessage:
    return CanonicalMessage(role=role, content=[TextBlock(text=s)])


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class CanonicalRequest:
    messages: list[CanonicalMessage]
    max_tokens: int
    temperature: float = 0.7
    tools: list[ToolSpec] | None = None
    run_id: str = ""
    task_id: str = ""
    attempt: int = 0


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False  # PATCH v2.1: True bila token diestimasi (SDK tak kirim usage)


@dataclass
class CanonicalResponse:
    content: list[ContentBlock]
    usage: Usage
    model: str
    stop_reason: str
    latency_ms: int
    cost_usd: float | None = None  # biaya call otoritatif (total_cost_usd); None jika lain


@dataclass
class ModelInfo:
    id: str  # e.g. "anthropic/claude-opus-4-8"
    provider: str  # "anthropic" | "openai_compat"
    strengths: set[str]  # {"coding","reasoning","long_context","cheap_fast"}
    context_window: int
    max_output_tokens: int
    supports_tools: bool
    cost_per_1k_in: float
    cost_per_1k_out: float
    tier: int = 2  # 1 small … 4 frontier
    billing: str = "card"  # MUST BE LAST: "card" | "plan_credit" | "plan_included"


@dataclass
class Task:
    id: str
    description: str
    type: str  # "research" | "code" | "write" | "analyze"
    mode: str  # "one_shot" | "agentic"
    depends_on: list[str] = field(default_factory=list)
    difficulty: str = "medium"  # trivial|easy|medium|hard


@dataclass
class Entry:
    run_id: str
    task_id: str
    attempt: int
    kind: str  # "artifact" | "fact" | "status"
    payload: Any
    model_id: str | None
    usage: Usage | None
    timestamp: float


@dataclass
class RunResult:
    status: str  # "success" | "failed"
    final: Any | None
    partial_artifacts: dict[str, Any]
    failed_task: str | None
    # PATCH v2.1: close-out akunting (diisi Runtime di KEDUA jalur success & failed)
    usage_total: dict[str, Usage] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    billed_usd: float = 0.0  # cash keluar (card); baca INI utk budget/cap
    credit_usd: float = 0.0  # nilai konsumsi plan_included/plan_credit (bukan cash)

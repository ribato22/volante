from __future__ import annotations

import os

import pytest

from baton.providers.anthropic import AnthropicProvider
from baton.types import CanonicalRequest, text


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY; hits the live Anthropic API",
)
async def test_live_complete_returns_real_usage() -> None:
    provider = AnthropicProvider(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model="claude-haiku-4-5",
    )
    req = CanonicalRequest(
        messages=[text("user", "Reply with exactly the word: pong")],
        max_tokens=16,
    )
    resp = await provider.complete(req)
    assert resp.usage.estimated is False
    assert resp.usage.prompt_tokens > 0
    assert resp.usage.completion_tokens > 0
    assert resp.latency_ms >= 0

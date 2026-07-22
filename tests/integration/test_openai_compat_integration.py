# tests/integration/test_openai_compat_integration.py
from __future__ import annotations

import os

import pytest

from baton.providers.openai_compat import OpenAICompatProvider
from baton.types import CanonicalRequest, TextBlock, text

pytestmark = pytest.mark.integration


async def test_ollama_roundtrip():
    """Butuh Ollama lokal dengan model ter-pull. Skip default."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")

    provider = OpenAICompatProvider(
        base_url=base_url, api_key="ollama", model=model, timeout=30.0
    )
    req = CanonicalRequest(
        messages=[text("user", "Reply with the single word: pong")],
        max_tokens=16,
        temperature=0.0,
    )
    resp = await provider.complete(req)

    assert resp.content
    assert isinstance(resp.content[0], TextBlock)
    assert resp.content[0].text.strip() != ""
    # usage nyata >= 0; bila server tak kirim usage, adapter menandai estimated.
    assert resp.usage.prompt_tokens >= 1
    assert resp.usage.completion_tokens >= 1
    assert resp.latency_ms >= 0

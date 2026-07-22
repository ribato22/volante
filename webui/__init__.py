"""Baton Web UI — a small FastAPI + SSE app that streams an orchestration run live.

Run it:  uv sync --extra ui  &&  uv run python -m webui
Uses real providers if configured (see .env.example), else a FakeProvider demo.
"""

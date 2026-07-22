from __future__ import annotations

from typing import Protocol

from baton.types import ToolSpec


class Tool(Protocol):
    name: str
    spec: ToolSpec

    async def run(self, args: dict) -> str: ...


ToolRegistry = dict[str, Tool]

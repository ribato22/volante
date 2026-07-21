from __future__ import annotations

from pathlib import Path

from orchestrator.types import ToolSpec


class ReadFileTool:
    """Baca file HOST-MEDIATED hanya di bawah `root` (no traversal / simlink keluar).
    Bukan host-FS arbitrer; batas kepercayaan = `root`."""

    name = "read_file"

    def __init__(self, root, max_bytes: int = 100_000) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.spec = ToolSpec(
            name="read_file",
            description="Read a text file under the allowed root directory (host-mediated).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

    async def run(self, args: dict) -> str:
        rel = args.get("path")
        if not isinstance(rel, str):
            return "error: 'path' (string) argument is required"
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            return f"error: path escapes allowed root: {rel!r}"
        if not target.is_file():
            return f"error: not a file: {rel!r}"
        try:
            data = target.read_bytes()[: self.max_bytes]
        except OSError as exc:
            return f"error: read failed: {exc}"
        return data.decode(errors="replace")

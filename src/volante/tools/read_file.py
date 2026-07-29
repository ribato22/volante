from __future__ import annotations

from pathlib import Path

from volante.types import ToolSpec


class ReadFileTool:
    """HOST-MEDIATED file read, only under `root` (no traversal / symlink escape).
    Not arbitrary host FS access; the trust boundary is `root`."""

    name = "read_file"

    def __init__(self, root, max_bytes: int = 100_000) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
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
            # Do not use read_bytes() here: it buffers the whole file before slicing,
            # so max_bytes would only cap output rather than memory consumption.
            with target.open("rb") as handle:
                data = handle.read(self.max_bytes)
        except OSError as exc:
            return f"error: read failed: {exc}"
        return data.decode(errors="replace")

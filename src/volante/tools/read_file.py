from __future__ import annotations

import os
import stat
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
        # The resolve() check above bounds TRAVERSAL; it is not a file-type check, and
        # the gap between checking and opening is a window. Same three refusals as
        # sandbox.read_model_file, because the model can race a host-side open:
        # * O_NOFOLLOW — a symlink planted at `target` is not read, in place or
        #   through a parent name.
        # * O_NONBLOCK + a regular-file check — a FIFO would otherwise BLOCK the
        #   opener until something writes to it, with no timeout anywhere near this
        #   call (TOCTOU: is_file() passed, then the path was swapped for a pipe).
        # * max_bytes — an unbounded read on a path the model chooses is an
        #   allocation the model chooses (read_bytes() would buffer the whole file
        #   before slicing, so max_bytes would only cap output, not memory).
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(os.fspath(target), flags)
        except OSError as exc:
            return f"error: cannot open {rel!r}: {exc}"
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return f"error: not a regular file: {rel!r}"
            data = os.read(fd, self.max_bytes)
        except OSError as exc:
            return f"error: read failed: {exc}"
        finally:
            os.close(fd)
        return data.decode(errors="replace")

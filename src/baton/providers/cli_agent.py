# src/baton/providers/cli_agent.py
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from baton.providers.base import OnText, ProviderError
from baton.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage

# claude/codex --output-format stream-json emit one JSON object per line; a single
# line (e.g. a tool_result) routinely exceeds asyncio's default 64KB StreamReader
# limit, which would otherwise raise ValueError("chunk is longer than limit") and
# orphan the child. 8 MB is a generous ceiling for that.
_STREAM_LIMIT = 8 * 1024 * 1024


@dataclass
class CliRunResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False  # True bila proses dibunuh karena melewati timeout


# Injected async runner (mock di test → tak pernah spawn claude/codex sungguhan).
# async def runner(argv, *, stdin, env, timeout, on_line=None) -> CliRunResult
CliRunner = Callable[..., Awaitable[CliRunResult]]


def _killpg(proc: asyncio.subprocess.Process) -> None:
    # Bunuh seluruh grup proses (mirror tools/sandbox._killpg) → cucu ikut mati.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def subprocess_cli_runner(
    argv: list[str],
    *,
    stdin: str,
    env: dict[str, str],
    timeout: float,
    on_line: Callable[[str], object] | None = None,
) -> CliRunResult:
    """Real runner: spawn a CLI agent in a FRESH temp cwd + its OWN session, feed the
    prompt via stdin. killpg(SIGKILL) on BOTH TimeoutError AND CancelledError, then
    await proc.wait() (mirror src/baton/tools/sandbox.py)."""
    workdir = tempfile.mkdtemp(prefix="baton-cli-")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
        env=env,
        start_new_session=True,  # grup proses sendiri → killpg bunuh cucu juga
        limit=_STREAM_LIMIT,  # allow large stream-json lines without erroring
    )
    try:
        if on_line is None:
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(stdin.encode()), timeout=timeout
                )
            except TimeoutError:
                _killpg(proc)
                await proc.wait()
                return CliRunResult("", "", -9, timed_out=True)
            except asyncio.CancelledError:
                _killpg(proc)
                await proc.wait()
                raise
            return CliRunResult(
                out.decode(errors="replace"),
                err.decode(errors="replace"),
                proc.returncode if proc.returncode is not None else -1,
            )
        return await _stream_lines(proc, stdin, timeout, on_line)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _stream_lines(
    proc: asyncio.subprocess.Process,
    stdin: str,
    timeout: float,
    on_line: Callable[[str], object],
) -> CliRunResult:
    """Feed stdin, then relay stdout lines to on_line (for --output-format stream-json).
    Truthy on_line -> cooperative early-stop: killpg the producer and return the
    accumulated partial. killpg on BOTH TimeoutError AND CancelledError as well.
    Outer finally is a belt-and-suspenders net: ANY other exception from the pump
    (e.g. on_line raising, or a line that still overruns _STREAM_LIMIT) must not
    leave the child orphaned -- reap it before the exception propagates."""
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(stdin.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    parts: list[str] = []
    stopped = False

    async def _pump() -> None:
        nonlocal stopped
        async for raw in proc.stdout:  # type: ignore[union-attr]
            parts.append(raw.decode(errors="replace"))
            if on_line(parts[-1]):  # truthy -> cooperative early-stop
                stopped = True
                return

    try:
        try:
            await asyncio.wait_for(_pump(), timeout=timeout)
        except TimeoutError:
            _killpg(proc)
            await proc.wait()
            return CliRunResult("".join(parts), "", -9, timed_out=True)
        except asyncio.CancelledError:
            _killpg(proc)
            await proc.wait()
            raise
        if stopped:
            _killpg(proc)  # early-stop: kill the remaining producer
            await proc.wait()
            return CliRunResult(
                "".join(parts), "", proc.returncode if proc.returncode is not None else -9
            )
        err = b""
        if proc.stderr is not None:
            err = await proc.stderr.read()
        await proc.wait()
        return CliRunResult(
            "".join(parts),
            err.decode(errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )
    finally:
        if proc.returncode is None:  # any other exception -> still reap, never orphan
            _killpg(proc)
            await proc.wait()


@runtime_checkable
class CliAgentAdapter(Protocol):
    """Claude Code vs Codex differences behind one interface."""

    name: str  # "claude_code" | "codex"

    def argv(
        self,
        req: CanonicalRequest,
        *,
        model: str,
        max_output: int,
        system_prompt_mode: str,
        stream: bool,
    ) -> list[str]: ...

    def child_env(self, base: dict[str, str], *, depth: int) -> dict[str, str]: ...

    def stdin(self, req: CanonicalRequest) -> str: ...

    def parse(self, result: CliRunResult, req: CanonicalRequest) -> CanonicalResponse: ...

    def parse_delta(self, line: str) -> str | None: ...

    def classify_error(self, result: CliRunResult) -> ProviderError: ...

    def is_error(self, result: CliRunResult) -> bool: ...

    def stream_result_line(self, lines: list[str]) -> str | None: ...


class CliAgentProvider:
    """LLMProvider over a subscription CLI agent (claude -p / codex exec) via an INJECTED
    async subprocess runner. Ignores req.temperature & req.max_tokens (the CLI manages
    sampling/length itself — §8.3); fills CanonicalResponse.cost_usd from the CLI JSON."""

    def __init__(
        self,
        adapter: CliAgentAdapter,
        model: str,
        *,
        runner: CliRunner,
        tier: int = 4,
        timeout: float = 120.0,
        max_output: int = 4096,  # conservative; CLI ignores req.max_tokens
        system_prompt_mode: str = "replace",  # "replace" (default) | "append"; see bootstrap
        concurrency: int = 1,  # per-provider cap, nested in fan-out sem
        depth_env: str = "BATON_CLI_AGENT_DEPTH",
        max_depth: int = 1,  # anti-recursion (Baton may run INSIDE Claude Code)
    ) -> None:
        self.name = adapter.name
        self.adapter = adapter
        self.model = model
        self.tier = tier
        self.timeout = timeout
        self.max_output = max_output
        self.system_prompt_mode = system_prompt_mode
        self.depth_env = depth_env
        self.max_depth = max_depth
        self._runner = runner
        self._sem = asyncio.Semaphore(concurrency)

    def _child_env(self) -> dict[str, str]:
        depth = int(os.environ.get(self.depth_env, "0"))
        if depth >= self.max_depth:
            raise ProviderError(
                f"{self.name}: refusing to recurse "
                f"(depth {depth} >= max_depth {self.max_depth}); "
                "Baton may be running inside a CLI agent",
                retryable=False,
            )
        return self.adapter.child_env(dict(os.environ), depth=depth + 1)

    async def complete(self, req: CanonicalRequest) -> CanonicalResponse:
        env = self._child_env()
        argv = self.adapter.argv(
            req,
            model=self.model,
            max_output=self.max_output,
            system_prompt_mode=self.system_prompt_mode,
            stream=False,
        )
        prompt = self.adapter.stdin(req)
        async with self._sem:
            result = await self._runner(argv, stdin=prompt, env=env, timeout=self.timeout)
        if result.timed_out or result.returncode != 0 or self.adapter.is_error(result):
            raise self.adapter.classify_error(result)
        return self.adapter.parse(result, req)

    async def stream(self, req: CanonicalRequest, on_text: OnText) -> CanonicalResponse:
        env = self._child_env()
        argv = self.adapter.argv(
            req,
            model=self.model,
            max_output=self.max_output,
            system_prompt_mode=self.system_prompt_mode,
            stream=True,
        )
        prompt = self.adapter.stdin(req)
        parts: list[str] = []
        lines: list[str] = []
        stopped = False

        def _on_line(line: str) -> bool:
            nonlocal stopped
            lines.append(line)
            delta = self.adapter.parse_delta(line)
            if delta is None:
                return False
            parts.append(delta)
            if on_text(delta):  # truthy -> cooperative early-stop (kills process group)
                stopped = True
                return True
            return False

        async with self._sem:
            result = await self._runner(
                argv, stdin=prompt, env=env, timeout=self.timeout, on_line=_on_line
            )
        if not stopped and (
            result.timed_out or result.returncode != 0 or self.adapter.is_error(result)
        ):
            raise self.adapter.classify_error(result)
        if not stopped:
            # §5.3: cost_usd is the primary credit source -- surface REAL usage/cost
            # from the terminal `type:"result"` line instead of a blind Usage(0, 0).
            result_line = self.adapter.stream_result_line(lines)
            if result_line is not None:
                terminal = CliRunResult(result_line, "", 0)
                # Mirror the complete-path guard on the TERMINAL line: on a real
                # spawn, `result.stdout` above is concatenated JSONL (multi-line) and
                # never parses as one JSON object, so `is_error(result)` can't see a
                # mid-stream `is_error: true` -- the single-object terminal line can.
                if self.adapter.is_error(terminal):
                    raise self.adapter.classify_error(terminal)
                return self.adapter.parse(terminal, req)
        return CanonicalResponse(
            content=[TextBlock(text="".join(parts))],
            usage=Usage(prompt_tokens=0, completion_tokens=0, estimated=True),
            model=self.name,
            stop_reason="end_turn",
            latency_ms=0,
        )

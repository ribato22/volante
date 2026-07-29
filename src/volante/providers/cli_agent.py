# src/volante/providers/cli_agent.py
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from volante.providers.base import OnText, ProviderError
from volante.types import CanonicalRequest, CanonicalResponse, TextBlock, Usage

# claude/codex --output-format stream-json emit one JSON object per line; a single
# line (e.g. a tool_result) routinely exceeds asyncio's default 64KB StreamReader
# limit, which would otherwise raise ValueError("chunk is longer than limit") and
# orphan the child. 8 MB is a generous ceiling for that.
_STREAM_LIMIT = 8 * 1024 * 1024
_CAPTURE_LIMIT = 16 * 1024 * 1024
_TRUNCATION_MARKER = b"\n...[CLI output truncated; tail retained]...\n"

# Subscription CLIs inherit only environment needed to find the executable,
# locate their OAuth state, and establish normal TLS. Provider/API credentials
# and unrelated project secrets are deliberately excluded. Users behind a
# corporate proxy can explicitly opt individual variables in through
# VOLANTE_CLI_AGENT_PASSTHROUGH=HTTPS_PROXY,HTTP_PROXY,NO_PROXY.
_SAFE_CHILD_ENV = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "NO_COLOR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
})


@dataclass
class CliRunResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False  # True when the process was killed for exceeding the timeout


# Injected async runner (mocked in tests → never spawns a real claude/codex).
# async def runner(argv, *, stdin, env, timeout, on_line=None) -> CliRunResult
CliRunner = Callable[..., Awaitable[CliRunResult]]


class _BoundedCapture:
    """Retain a bounded head and tail while continuing to drain the pipe."""

    def __init__(self, limit: int = _CAPTURE_LIMIT) -> None:
        self._limit = limit
        self._head_limit = limit * 3 // 4
        self._tail_limit = limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self._total = 0

    def add(self, data: bytes) -> None:
        self._total += len(data)
        head_space = self._head_limit - len(self._head)
        if head_space > 0:
            self._head.extend(data[:head_space])
            data = data[head_space:]
        if data:
            self._tail.extend(data)
            if len(self._tail) > self._tail_limit:
                del self._tail[: len(self._tail) - self._tail_limit]

    def text(self) -> str:
        separator = _TRUNCATION_MARKER if self._total > self._limit else b""
        return bytes(self._head + separator + self._tail).decode(errors="replace")


async def _drain(
    reader: asyncio.StreamReader | None, capture: _BoundedCapture
) -> None:
    if reader is None:
        return
    while chunk := await reader.read(64 * 1024):
        capture.add(chunk)


async def _feed_stdin(proc: asyncio.subprocess.Process, stdin: str) -> None:
    """Write the prompt and close stdin, as a task the deadline can cover.

    Feeding INLINE (write; await drain) before the timeout region looked harmless
    because a CLI normally reads its prompt at once. It is not: `drain()` blocks
    once the prompt exceeds the ~64 KB pipe buffer and the child has not started
    reading, and canonical prompts are routinely far larger than that. Sitting
    outside the deadline meant the configured timeout had not started yet, and
    sitting outside the killpg handlers meant an outer cancellation left the child
    running and unreaped. Running the feed CONCURRENTLY with the stdout/stderr
    drains also removes the reverse deadlock, where the child fills its stdout pipe
    while nobody is draining it because we are still filling its stdin pipe.

    A child that exits or closes stdin early is an ordinary outcome — its
    returncode is what decides the call — so a broken pipe here must not fail it.
    """
    assert proc.stdin is not None
    try:
        proc.stdin.write(stdin.encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        # Also on cancellation: a child blocked reading stdin needs the EOF.
        proc.stdin.close()


async def _communicate_bounded(
    proc: asyncio.subprocess.Process,
    stdin: str,
    timeout: float,
) -> CliRunResult:
    stdout = _BoundedCapture()
    stderr = _BoundedCapture()
    tasks = [
        asyncio.create_task(_feed_stdin(proc, stdin)),
        asyncio.create_task(_drain(proc.stdout, stdout)),
        asyncio.create_task(_drain(proc.stderr, stderr)),
        asyncio.create_task(proc.wait()),
    ]
    # `combined` is named rather than inlined so the cleanup below can retrieve
    # ITS CancelledError too. Draining only the member tasks leaves the gather
    # wrapper holding an unretrieved exception, which asyncio prints to stderr when
    # it is collected — on a path Runtime takes for every outer timeout.
    combined = asyncio.gather(*tasks)
    try:
        await asyncio.wait_for(combined, timeout=timeout)
    except TimeoutError:
        _killpg(proc)
        await proc.wait()
        await asyncio.gather(combined, *tasks, return_exceptions=True)
        return CliRunResult(
            stdout.text(), stderr.text(), -9, timed_out=True
        )
    except asyncio.CancelledError:
        _killpg(proc)
        await proc.wait()
        await asyncio.gather(combined, *tasks, return_exceptions=True)
        raise
    return CliRunResult(
        stdout.text(),
        stderr.text(),
        proc.returncode if proc.returncode is not None else -1,
    )


def _killpg(proc: asyncio.subprocess.Process) -> None:
    # Kill the whole process group (mirrors tools/sandbox._killpg) → grandchildren die too.
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
    await proc.wait() (mirror src/volante/tools/sandbox.py)."""
    workdir = tempfile.mkdtemp(prefix="volante-cli-")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=env,
            start_new_session=True,  # own process group → killpg kills grandchildren too
            limit=_STREAM_LIMIT,  # allow large stream-json lines without erroring
        )
        if on_line is None:
            return await _communicate_bounded(proc, stdin, timeout)
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
    accumulated partial. The deadline covers the complete child lifecycle: stdout
    pumping, process exit, and stderr drain. killpg on BOTH TimeoutError AND
    CancelledError as well.
    Outer finally is a belt-and-suspenders net: ANY other exception from the pump
    (e.g. on_line raising, or a line that still overruns _STREAM_LIMIT) must not
    leave the child orphaned -- reap it before the exception propagates."""
    assert proc.stdin is not None and proc.stdout is not None
    stdout = _BoundedCapture()
    stderr = _BoundedCapture()
    stopped = False
    stderr_task = asyncio.create_task(_drain(proc.stderr, stderr))

    async def _pump() -> None:
        nonlocal stopped
        async for raw in proc.stdout:  # type: ignore[union-attr]
            stdout.add(raw)
            if on_line(raw.decode(errors="replace")):  # truthy -> early-stop
                stopped = True
                return

    async def _run_to_completion() -> None:
        # Inside the deadline, and concurrent with the pump: see _feed_stdin.
        feed = asyncio.create_task(_feed_stdin(proc, stdin))
        try:
            await _pump()
            if stopped:
                _killpg(proc)  # early-stop: kill the remaining producer
        finally:
            feed.cancel()
            await asyncio.gather(feed, return_exceptions=True)
        await proc.wait()
        await stderr_task

    try:
        try:
            await asyncio.wait_for(_run_to_completion(), timeout=timeout)
        except TimeoutError:
            _killpg(proc)
            await proc.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            return CliRunResult(
                stdout.text(), stderr.text(), -9, timed_out=True
            )
        except asyncio.CancelledError:
            _killpg(proc)
            await proc.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        if stopped:
            return CliRunResult(
                stdout.text(),
                stderr.text(),
                proc.returncode if proc.returncode is not None else -9,
            )
        return CliRunResult(
            stdout.text(),
            stderr.text(),
            proc.returncode if proc.returncode is not None else -1,
        )
    finally:
        if proc.returncode is None:  # any other exception -> still reap, never orphan
            _killpg(proc)
            await proc.wait()
        if not stderr_task.done():
            await asyncio.gather(stderr_task, return_exceptions=True)


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
        depth_env: str = "VOLANTE_CLI_AGENT_DEPTH",
        max_depth: int = 1,  # anti-recursion (Volante may run INSIDE Claude Code)
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
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self._concurrency = concurrency
        # asyncio synchronization primitives bind to the event loop when they
        # contend. A provider can legitimately be reused by multiple sequential
        # Runtime.execute() calls, each of which creates a new loop.
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._concurrency)
            self._semaphores[loop] = semaphore
        return semaphore

    def _child_env(self) -> dict[str, str]:
        depth = int(os.environ.get(self.depth_env, "0"))
        if depth >= self.max_depth:
            raise ProviderError(
                f"{self.name}: refusing to recurse "
                f"(depth {depth} >= max_depth {self.max_depth}); "
                "Volante may be running inside a CLI agent",
                retryable=False,
            )
        names = set(_SAFE_CHILD_ENV)
        names.update(
            name.strip()
            for name in os.environ.get("VOLANTE_CLI_AGENT_PASSTHROUGH", "").split(",")
            if name.strip()
        )
        base = {
            key: value
            for key, value in os.environ.items()
            if key in names or key.startswith("LC_")
        }
        return self.adapter.child_env(base, depth=depth + 1)

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
        async with self._semaphore():
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

        async with self._semaphore():
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

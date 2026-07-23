# tests/providers/test_claude_code.py
from __future__ import annotations

from baton.providers.claude_code import ClaudeCodeAdapter
from baton.types import CanonicalRequest, text


def _req(sys_prompt: str | None, user: str) -> CanonicalRequest:
    msgs = []
    if sys_prompt is not None:
        msgs.append(text("system", sys_prompt))
    msgs.append(text("user", user))
    return CanonicalRequest(messages=msgs, max_tokens=64)


def test_name_is_claude_code() -> None:
    assert ClaudeCodeAdapter().name == "claude_code"


def test_argv_json_append_is_canonical_no_bare() -> None:
    a = ClaudeCodeAdapter()
    argv = a.argv(
        _req("SYS", "hello"),
        model="opus",
        max_output=4096,
        system_prompt_mode="append",
        stream=False,
    )
    assert argv == [
        "claude", "-p",
        "--input-format", "text",
        "--output-format", "json",
        "--model", "opus",
        "--tools", "",
        "--strict-mcp-config",
        "--append-system-prompt", "SYS",
    ]
    # OAuth langganan HARUS hidup + tak boleh skip permission (§8.1).
    assert "--bare" not in argv
    assert "--dangerously-skip-permissions" not in argv


def test_argv_stream_switches_output_format() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req("SYS", "hi"), model="opus", max_output=4096,
        system_prompt_mode="append", stream=True,
    )
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_argv_replace_mode_uses_system_prompt_flag() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req("SYS", "hi"), model="opus", max_output=4096,
        system_prompt_mode="replace", stream=False,
    )
    assert "--system-prompt" in argv
    assert "--append-system-prompt" not in argv


def test_argv_omits_system_flag_when_no_system_message() -> None:
    argv = ClaudeCodeAdapter().argv(
        _req(None, "hi"), model="opus", max_output=4096,
        system_prompt_mode="append", stream=False,
    )
    assert "--append-system-prompt" not in argv
    assert "--system-prompt" not in argv
    assert argv[-1] == "--strict-mcp-config"  # trailing flag when no sys prompt


def test_stdin_is_user_text_only() -> None:
    # --input-format text: prompt user lewat stdin; sistem lewat argv (Task 1).
    a = ClaudeCodeAdapter()
    assert a.stdin(_req("SYS", "hello world")) == "hello world"
    assert a.stdin(_req(None, "just user")) == "just user"


def test_child_env_bumps_depth_and_preserves_oauth() -> None:
    from baton.providers.claude_code import DEPTH_ENV

    a = ClaudeCodeAdapter()
    base = {"PATH": "/bin", "ANTHROPIC_API_KEY": "sk-should-survive", DEPTH_ENV: "0"}
    env = a.child_env(base, depth=0)
    assert env[DEPTH_ENV] == "1"                        # anak +1 (§8.2)
    assert env["PATH"] == "/bin"                        # base diteruskan
    # OAuth langganan dipertahankan: key TIDAK disuntik/dihapus di sini (kontras Codex).
    assert env["ANTHROPIC_API_KEY"] == "sk-should-survive"
    assert base[DEPTH_ENV] == "0"                       # tak mutasi input

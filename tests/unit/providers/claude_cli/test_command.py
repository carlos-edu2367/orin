from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

import pytest

from agentos.providers.claude_cli.command import (
    ISOLATION_FLAGS,
    MCP_TOOL_TIMEOUT_ENV,
    VALIDATED_CLI_RANGE,
    build_claude_command,
    build_isolated_claude_env,
    check_claude_cli_status,
    claude_cli_version,
    clear_version_cache,
    is_version_validated,
    redact_argv,
    resolve_claude_binary,
)
from agentos.providers.claude_cli.errors import ProviderTransportError


def test_command_uses_restricted_isolation_and_never_bare_or_safe_mode() -> None:
    argv = build_claude_command(
        binary="claude.exe",
        system_prompt_file="C:/t/s.txt",
        model="claude-opus-5-5",
    )
    assert argv[:2] == ["claude.exe", "-p"]
    assert "--restricted" in argv and "--strict-mcp-config" in argv
    assert ISOLATION_FLAGS == ("--restricted", "--strict-mcp-config")
    assert argv[argv.index("--tools") + 1] == ""
    assert "--bare" not in argv and "--safe-mode" not in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--disable-slash-commands" in argv
    assert "--no-chrome" in argv
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert "--include-partial-messages" in argv


def test_command_native_adds_mcp_config_and_allowed_tools() -> None:
    argv = build_claude_command(
        binary="c",
        system_prompt_file="s",
        mcp_config_file="C:/t/m.json",
        max_turns=40,
    )
    assert argv[argv.index("--mcp-config") + 1] == "C:/t/m.json"
    assert argv[argv.index("--allowedTools") + 1] == "mcp__orin__*"
    assert argv[argv.index("--max-turns") + 1] == "40"


def test_command_forbids_session_persistence_flags() -> None:
    uid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", session_id=uid, persist_session=True)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", resume_session_id=uid)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", session_id=uid, resume_session_id=uid)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", session_id="not-a-uuid", persist_session=True)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", persist_session=True)


def test_command_autocompact_validation() -> None:
    argv_auto = build_claude_command(binary="c", system_prompt_file="s", autocompact="auto")
    assert argv_auto[argv_auto.index("--autocompact") + 1] == "auto"

    argv_int = build_claude_command(binary="c", system_prompt_file="s", autocompact=500_000)
    assert argv_int[argv_int.index("--autocompact") + 1] == "500000"

    for bad_compact in ("off", "50000", 99_999, 1_000_001, "-100000", True):
        with pytest.raises(ValueError):
            build_claude_command(binary="c", system_prompt_file="s", autocompact=bad_compact)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["-x", "a b", "x\x00", "x" * 200, "claude; rm -rf /", "--settings=evil.json"])
def test_command_rejects_unsafe_model(bad: str) -> None:
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", model=bad)


@pytest.mark.parametrize("bad_effort", ["--bare", "ultra", "high\n--bare"])
def test_command_rejects_unsafe_effort(bad_effort: str) -> None:
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", effort=bad_effort)


@pytest.mark.parametrize("bad_file", ["", "  ", "-rf", "--bare", "path\x00.txt", "path\n.txt"])
def test_command_rejects_unsafe_paths(bad_file: str) -> None:
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file=bad_file)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", mcp_config_file=bad_file)


def test_env_strips_api_keys_and_sets_output_and_mcp_timeouts() -> None:
    env = build_isolated_claude_env(
        {
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_AUTH_TOKEN": "t",
            "ANTHROPIC_BASE_URL": "https://evil.example",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "x",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "PATH": "p",
        },
        max_output_tokens=8000,
        mcp_tool_timeout_ms=600000,
    )
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" not in env
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["PATH"] == "p"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    assert env[MCP_TOOL_TIMEOUT_ENV] == "600000"
    assert env["MCP_TIMEOUT"] == "30000"


def test_env_omits_optional_timeouts_when_not_given() -> None:
    env = build_isolated_claude_env({"PATH": "p"})
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
    assert MCP_TOOL_TIMEOUT_ENV not in env
    assert "MCP_TIMEOUT" not in env


def test_redact_argv_hides_mcp_config_and_system_prompt_paths() -> None:
    out = redact_argv(
        [
            "c",
            "-p",
            "--mcp-config",
            "C:/t/m.json",
            "--system-prompt-file",
            "C:/t/s.txt",
            "--model",
            "m",
        ]
    )
    assert out == [
        "c",
        "-p",
        "--mcp-config",
        "<redacted>",
        "--system-prompt-file",
        "<redacted>",
        "--model",
        "m",
    ]


def test_claude_cli_version_parses_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_version_cache()
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = "2.1.309 (Claude Code)\n"
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: Any) -> _Completed:
        calls.append(cmd)
        return _Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    assert claude_cli_version("C:/fake/claude.exe") == (2, 1, 309)
    assert claude_cli_version("C:/fake/claude.exe") == (2, 1, 309)
    assert len(calls) == 1
    assert is_version_validated((2, 1, 309)) is True
    assert is_version_validated((2, 1, 310)) is True
    assert is_version_validated((2, 2, 0)) is False
    assert is_version_validated((2, 0, 9)) is False
    assert is_version_validated(None) is False
    assert VALIDATED_CLI_RANGE == (">=2.1.309", "<2.2")


def test_resolve_claude_binary_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    bin_dir = appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "claude.exe"
    exe.write_bytes(b"MZ")

    assert resolve_claude_binary({"APPDATA": str(appdata)}) == str(exe)

    class _Completed:
        returncode = 0
        stdout = '{"loggedIn": false, "authMethod": "none"}'
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Completed())
    with pytest.raises(ProviderTransportError):
        check_claude_cli_status(binary=str(exe))

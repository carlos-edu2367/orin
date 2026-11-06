from __future__ import annotations

from pathlib import Path
import pytest

from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_adapter import CliCapabilities, CliTurnRequest
from tests.unit.providers.claude_cli.test_adapter_conformance import (
    TestClaudeCliAdapterConformance,
)
from tests.unit.providers.cli_adapter_conformance import FakeToolServerHandle


def test_claude_argv_unchanged_by_contract_v2(tmp_path: Path) -> None:
    adapter = ClaudeCliAdapter()
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("system prompt", encoding="utf-8")
    wiring = adapter.wire_tool_server(FakeToolServerHandle(), tmp_path)
    assert len(wiring.files) == 1
    mcp_json = wiring.files[0]
    assert wiring.argv == ("--mcp-config", str(mcp_json))

    req = CliTurnRequest(
        binary="/usr/bin/claude",
        system_prompt_file=sys_file,
        model="claude-opus-5-5",
        effort="high",
        fallback_model="claude-sonnet-5-5",
        max_turns=12,
        tool_server_wiring=wiring,
        tool_server_name="orin",
        autocompact="auto",
        capabilities=adapter.capabilities,
    )
    assert req.prompt_via_stdin_once is False
    with pytest.raises(TypeError):
        CliTurnRequest(
            binary="/usr/bin/claude",
            system_prompt_file=sys_file,
            model="claude-opus-5-5",
            prompt_via_stdin_once=True,  # type: ignore[call-arg]
        )

    one_shot_req = CliTurnRequest(
        binary="/usr/bin/codex",
        system_prompt_file=sys_file,
        model="gpt-5",
        capabilities=CliCapabilities(persistent_stdin=False),
    )
    assert one_shot_req.prompt_via_stdin_once is True

    expected_snapshot = [
        "/usr/bin/claude",
        "-p",
        "--restricted",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-chrome",
        "--tools",
        "",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--max-turns",
        "12",
        "--system-prompt-file",
        str(sys_file),
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--mcp-config",
        str(mcp_json),
        "--allowedTools",
        "mcp__orin__*",
        "--autocompact",
        "auto",
        "--model",
        "claude-opus-5-5",
        "--effort",
        "high",
        "--fallback-model",
        "claude-sonnet-5-5",
    ]
    assert adapter.build_command(req) == expected_snapshot

    logged_c = adapter.loggable_command(
        [
            "codex",
            "-c",
            "features.shell_tool=false",
            "-c",
            "mcp_servers.orin.bearer_token_env_var=SECRET_TOKEN",
            "-c",
            "api_key=sk-secret-key",
            "-c",
            "headers.Authorization=Bearer xyz",
        ]
    )
    assert "features.shell_tool=false" in logged_c
    assert "SECRET_TOKEN" not in " ".join(logged_c)
    assert "sk-secret-key" not in " ".join(logged_c)
    assert "Bearer xyz" not in " ".join(logged_c)


def test_redact_argv_and_log_cli_run_cover_all_secret_flags(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging
    from agentos.providers.claude_cli.provider import log_cli_run

    adapter = ClaudeCliAdapter()
    logged = adapter.loggable_command(
        [
            "codex",
            "--config",
            "mcp_servers.orin.bearer_token=my-secret-value",
            "-c",
            "client_secret=super-secret-123",
            "-c",
            "proxy.password=hunter2",
            "-c",
            "custom_header=Bearer eyJhbGci",
            "--config=client_secret=inline-secret",
            "--mcp-config=/tmp/secret-mcp.json",
            "--system-prompt-file=/tmp/secret-prompt.txt",
        ]
    )
    joined = " ".join(logged)
    assert "my-secret-value" not in joined
    assert "super-secret-123" not in joined
    assert "hunter2" not in joined
    assert "eyJhbGci" not in joined
    assert "inline-secret" not in joined
    assert "/tmp/secret-mcp.json" not in joined
    assert "/tmp/secret-prompt.txt" not in joined

    with caplog.at_level(logging.INFO, logger="agentos.providers.claude_cli"):
        log_cli_run(
            argv=["codex", "--custom-secret-flag", "TOP_SECRET_CUSTOM_ARG"],
            requested_model="gpt-5.4",
            init=None,
            result=None,
            exit_code=0,
            duration_s=0.1,
            stderr_tail="",
            mode="native",
            loggable_command=lambda a: ["codex", "--custom-secret-flag", "<adapter-redacted>"],
        )
    assert "TOP_SECRET_CUSTOM_ARG" not in caplog.text
    assert "<adapter-redacted>" in caplog.text


__all__ = [
    "TestClaudeCliAdapterConformance",
    "test_claude_argv_unchanged_by_contract_v2",
    "test_redact_argv_and_log_cli_run_cover_all_secret_flags",
]

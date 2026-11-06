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


__all__ = [
    "TestClaudeCliAdapterConformance",
    "test_claude_argv_unchanged_by_contract_v2",
]

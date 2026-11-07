"""Phase 4 of specialised agents: a Codex model delegates only through Orin's Agent tool, and that call may last."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.providers.cli_adapter import CliTurnRequest, ToolServerWiring
from agentos.providers.codex_cli.adapter import CodexCliAdapter
from agentos.providers.codex_cli.command import build_codex_command, wire_codex_tool_server


class _Handle:
    url = "http://127.0.0.1:54321/mcp"
    token = "token-123"


def _request(tmp_path: Path, **overrides) -> CliTurnRequest:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x", encoding="utf-8")
    values = {
        "binary": "C:/codex/codex.exe", "system_prompt_file": prompt, "model": "gpt-5",
        "tool_server_wiring": wire_codex_tool_server(_Handle(), server_name="orin"),
    }
    return CliTurnRequest(**{**values, **overrides})


def _c_values(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, token in enumerate(argv) if token == "-c"]


def test_codex_native_multi_agent_is_off(tmp_path: Path) -> None:
    assert "features.multi_agent=false" in _c_values(build_codex_command(_request(tmp_path)))


def test_a_wiring_cannot_turn_codex_multi_agent_back_on(tmp_path: Path) -> None:
    wiring = ToolServerWiring(argv=("-c", "features.multi_agent=true"))
    with pytest.raises(ValueError, match="locked"):
        build_codex_command(_request(tmp_path, tool_server_wiring=wiring))


def test_codex_items_of_its_own_delegation_are_refused() -> None:
    assert "collab_tool_call" in CodexCliAdapter.capabilities.forbidden_item_kinds


def test_orins_tool_server_is_pre_approved_because_orin_asks_for_permission_itself(tmp_path: Path) -> None:
    assert 'mcp_servers.orin.default_tools_approval_mode="approve"' in _c_values(build_codex_command(_request(tmp_path)))


def test_the_tool_server_timeout_follows_the_turns_remaining_time(tmp_path: Path) -> None:
    values = _c_values(build_codex_command(_request(tmp_path, tool_timeout_ms=1_800_500)))
    assert "mcp_servers.orin.tool_timeout_sec=1801" in values  # rounded up, never shorter than the turn allows
    assert values.index("mcp_servers.orin.tool_timeout_sec=1801") > values.index("mcp_servers.orin.required=true")


@pytest.mark.parametrize("timeout", [None, 0, -5])
def test_no_timeout_is_set_without_a_positive_deadline(tmp_path: Path, timeout) -> None:
    assert not any("tool_timeout_sec" in value for value in _c_values(build_codex_command(_request(tmp_path, tool_timeout_ms=timeout))))


def test_no_timeout_is_set_without_a_tool_server(tmp_path: Path) -> None:
    argv = build_codex_command(_request(tmp_path, tool_server_wiring=None, tool_timeout_ms=60_000))
    assert not any("tool_timeout_sec" in value for value in _c_values(argv))


def test_a_refused_tool_says_which_toolkit_unlocks_it() -> None:
    from agentos.agentic.phases import Phase, toolkit_hint

    hint = toolkit_hint("run_subagent", Phase.EXECUTE)
    assert "write_contract" in hint and "'delegation'" in hint
    assert toolkit_hint("run_subagent", Phase.VERIFY) == ""  # verification never unlocks it
    assert toolkit_hint("not_a_tool", Phase.EXECUTE) == ""

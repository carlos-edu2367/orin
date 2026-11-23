"""Every native CLI turn (Claude, Codex, Antigravity) is told that only Orin's native tools run."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.native_turn import TEXT_TOOL_CALL_CORRECTION, builtin_tool_reminder
from agentos.agentic.tool_text_rules import ONLY_ORIN_TOOLS_RULE
from agentos.providers.claude_cli.native_loop import isolated_cli_note, with_isolated_cli_note


@pytest.mark.parametrize("adapter", [None, "claude", "codex", "antigravity"])
def test_every_native_cli_gets_the_fixed_rule_once(adapter: str | None, tmp_path: Path) -> None:
    for kwargs in ({}, {"permission_mode": "bypassPermissions", "workspace_root": tmp_path}, {"read_only": True}):
        note = isolated_cli_note("orin", adapter_name=adapter, **kwargs)
        assert note.count(ONLY_ORIN_TOOLS_RULE) == 1


def test_the_prompt_stays_byte_stable_between_turns(tmp_path: Path) -> None:
    first = with_isolated_cli_note("You are Orin.", "orin", adapter_name="codex", workspace_root=tmp_path)
    again = with_isolated_cli_note("You are Orin.", "orin", adapter_name="codex", workspace_root=tmp_path)
    assert first == again
    assert with_isolated_cli_note(first, "orin", adapter_name="codex", workspace_root=tmp_path) == first


def test_the_rule_names_what_runs_and_what_never_does() -> None:
    for must in ("Orin MCP server", "native tool mechanism", "<invoke>", "<function_calls>", "[Tool Use]", "never executed"):
        assert must in ONLY_ORIN_TOOLS_RULE, must
    assert "Never write a tool result" in ONLY_ORIN_TOOLS_RULE


def test_the_rule_agrees_with_the_builtin_tool_reminder() -> None:
    reminder = builtin_tool_reminder("Task", ["Agent"])
    assert "Use only the tools of the `orin` MCP server" in reminder
    # Same message, no opposite instruction: the rule never invites the CLI's own tools.
    assert "CLI itself does not run" in ONLY_ORIN_TOOLS_RULE


def test_the_correction_repeats_the_rule_in_portuguese() -> None:
    assert "Só rodam as ferramentas do Orin" in TEXT_TOOL_CALL_CORRECTION
    assert "mecanismo nativo de ferramentas" in TEXT_TOOL_CALL_CORRECTION
    assert "nunca deve escrever resultados de ferramenta" in TEXT_TOOL_CALL_CORRECTION

"""W6 Task 10: how `orin code` flags become a turn's CLI options and the conversation's permission mode."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.cli_code.args import CliUsageError, parse_code_arguments
from agentos.cli_code.headless import refuse_unsupported
from agentos.cli_code.turn_options import build_turn_options, permission_mode_for


def _options(*argv: str):
    return parse_code_arguments(["-p", "x", *argv])[0]


def test_flags_build_the_cli_settings_layer(tmp_path: Path) -> None:
    (tmp_path / "extra").mkdir()
    options = _options(
        "--settings", json.dumps({"permissions": {"allow": ["Read"]}, "env": {"A": "1"}}),
        "--allowedTools", "Bash(git status:*),Read", "--disallowedTools", "WebFetch", "--add-dir", "extra",
        "--max-turns", "4", "--max-budget-usd", "0.25", "--fallback-model", "ollama/x", "--effort", "high",
        "--autocompact", "off", "--system-prompt-snapshot", "off", "--setting-sources", "user",
    )
    built = build_turn_options(options, tmp_path, host_answers_prompts=False)
    layer = built.settings
    assert layer["permissions"]["allow"] == ["Read", "Bash(git status:*)"]
    assert layer["permissions"]["deny"] == ["WebFetch"]
    assert layer["permissions"]["additionalDirectories"] == [str((tmp_path / "extra").resolve())]
    assert (layer["maxTurns"], layer["maxBudgetUsd"], layer["fallbackModel"], layer["effortLevel"]) == (4, 0.25, "ollama/x", "high")
    assert layer["autoCompact"] is False and layer["systemPromptSnapshot"] is False and layer["env"] == {"A": "1"}
    assert built.setting_sources == ["user"] and built.permission_prompts == "none" and built.headless is True


def test_a_settings_file_and_prompt_files(tmp_path: Path) -> None:
    (tmp_path / "s.json").write_text(json.dumps({"maxTurns": 9}), encoding="utf-8")
    (tmp_path / "sys.md").write_text("System!", encoding="utf-8")
    (tmp_path / "more.md").write_text("More.", encoding="utf-8")
    built = build_turn_options(_options("--settings", "s.json", "--system-prompt-file", "sys.md", "--append-system-prompt", "A",
                                        "--append-system-prompt-file", "more.md"), tmp_path, host_answers_prompts=True)
    assert built.settings == {"maxTurns": 9} and built.system_prompt == "System!" and built.append_system_prompt == "A\n\nMore."
    assert built.permission_prompts == "host"


@pytest.mark.parametrize(("argv", "effort"), [
    (["--max-thinking-tokens", "5000"], "medium"), (["--max-thinking-tokens", "99999"], "max"),
    (["--thinking", "disabled", "--max-thinking-tokens", "5000"], None), ([], None),
])
def test_thinking_budgets_become_an_effort(tmp_path: Path, argv: list[str], effort: str | None) -> None:
    assert build_turn_options(_options(*argv), tmp_path, host_answers_prompts=False).settings.get("effortLevel") == effort


@pytest.mark.parametrize("value", ["{not json", "[1, 2]", "missing.json"])
def test_bad_settings_are_usage_errors(tmp_path: Path, value: str) -> None:
    with pytest.raises(CliUsageError):
        build_turn_options(_options("--settings", value), tmp_path, host_answers_prompts=False)


def test_prompts_none_wins_over_a_host(tmp_path: Path) -> None:
    built = build_turn_options(_options("--permission-prompts", "none"), tmp_path, host_answers_prompts=True)
    assert built.permission_prompts == "none"


@pytest.mark.parametrize(("argv", "mode"), [
    ([], "ask_permissions"), (["--permission-mode", "acceptEdits"], "accept_edits"), (["--permission-mode", "plan"], "plan_mode"),
    (["--dangerously-skip-permissions"], "full_autonomy"), (["--permission-mode", "bypassPermissions"], "full_autonomy"),
    (["--permission-mode", "dontAsk"], "dont_ask"), (["--permission-mode", "auto"], "auto"),
])
def test_permission_modes(argv: list[str], mode: str) -> None:
    assert permission_mode_for(_options(*argv)) == mode


@pytest.mark.parametrize("argv", [["--fork-session"], ["--session-mirror"], ["--agent", "reviewer"], ["--bg"], ["--permission-prompt-tool", "mcp__p__ask"], ["--thinking-display", "summarized"]])
def test_flags_without_support_yet_are_refused(argv: list[str]) -> None:
    with pytest.raises(CliUsageError):
        refuse_unsupported(_options(*argv))

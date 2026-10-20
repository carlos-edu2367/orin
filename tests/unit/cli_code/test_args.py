"""W6 Task 1: the `orin code` command line (spec §9.2)."""
from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

from agentos.cli_code.args import CliUsageError, parse_code_arguments, split_tool_list
from agentos.cli_code.main import main, version_line

ROOT = Path(__file__).resolve().parents[3]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_the_command_line_an_sdk_host_builds_is_accepted() -> None:
    # What claude-agent-sdk's subprocess transport passes (no -p: stream-json input means headless).
    argv = [
        "--output-format", "stream-json", "--verbose", "--system-prompt", "", "--tools", "Read,Edit",
        "--allowedTools", "Read,Bash(git status:*)", "--max-turns", "3", "--max-budget-usd", "0.5",
        "--disallowedTools", "WebFetch", "--model", "ollama/qwen", "--fallback-model", "x",
        "--permission-prompt-tool", "stdio", "--permission-mode", "acceptEdits", "--resume=abc",
        "--session-id=0b3f6c2e-7d7b-4f39-9c9e-1f1f1f1f1f1f", "--settings", "{\"a\": 1}", "--add-dir", "/tmp/x",
        "--mcp-config", "{\"mcpServers\": {}}", "--include-partial-messages", "--include-hook-events",
        "--strict-mcp-config", "--fork-session", "--setting-sources=user,project", "--plugin-dir", "p1",
        "--thinking", "adaptive", "--max-thinking-tokens", "1000", "--effort", "high",
        "--json-schema", "{\"type\": \"object\"}", "--input-format", "stream-json",
    ]
    options, show_version = parse_code_arguments(argv)
    assert show_version is False
    assert options.headless and options.output_format == "stream-json" and options.input_format == "stream-json"
    assert options.tools == ["Read", "Edit"]
    assert options.allowed_tools == ["Read", "Bash(git status:*)"]
    assert options.disallowed_tools == ["WebFetch"]
    assert options.max_turns == 3 and options.max_budget_usd == 0.5
    assert options.resume == "abc" and options.session_id.startswith("0b3f6c2e")
    assert options.setting_sources == ["user", "project"]
    assert options.permission_prompt_tool == "stdio" and options.permission_mode == "acceptEdits"
    assert options.add_dirs == ["/tmp/x"] and options.mcp_configs == ["{\"mcpServers\": {}}"]
    assert options.system_prompt == ""


def test_print_mode_takes_the_prompt() -> None:
    options, _ = parse_code_arguments(["-p", "explain this repo", "--output-format", "json"])
    assert options.print_mode and options.prompt == "explain this repo" and options.output_format == "json"


def test_an_empty_tools_value_means_no_tools() -> None:
    options, _ = parse_code_arguments(["-p", "x", "--tools", ""])
    assert options.tools == []


def test_tool_lists_split_on_commas_outside_parentheses() -> None:
    assert split_tool_list(["Read, Edit", "Bash(npm run test:*), Bash(git log:*)", "Read"]) == [
        "Read", "Edit", "Bash(npm run test:*)", "Bash(git log:*)",
    ]


@pytest.mark.parametrize("flag", ["--chrome", "--no-chrome", "--teleport", "--ide", "--tmux", "--betas=x", "--remote-control-session"])
def test_claude_code_only_flags_are_refused(flag: str) -> None:
    with pytest.raises(CliUsageError, match="Orin does not provide"):
        parse_code_arguments(["-p", "x", flag])


@pytest.mark.parametrize("argv", [
    ["-p", "x", "--unknown-flag"],
    ["-p", "x", "--max-turns", "0"],
    ["-p", "x", "--max-budget-usd", "0"],
    ["-p", "x", "-c", "-r", "abc"],
    ["-p", "x", "--input-format", "stream-json"],
    ["-p", "x", "--include-partial-messages"],
    ["--output-format", "json", "x"],
    ["-p", "x", "--setting-sources", "user,cloud"],
    ["-p", "x", "--dangerously-skip-permissions", "--permission-mode", "plan"],
    ["-p", "x", "--output-format", "yaml"],
])
def test_invalid_command_lines_exit_1(argv: list[str]) -> None:
    code, out, err = _run(argv)
    assert code == 1 and out == ""
    assert err.startswith("Error: ")


@pytest.mark.parametrize("name", ["auth", "setup-token", "gateway", "install", "update", "ultrareview"])
def test_claude_code_only_subcommands_are_refused(name: str) -> None:
    code, _, err = _run([name])
    assert code == 1 and "Orin does not have" in err


def test_version_prints_orins_version() -> None:
    code, out, _ = _run(["-v"])
    assert code == 0 and out == version_line() + "\n"
    assert out.split()[0].count(".") == 2  # an SDK host reads the leading x.y.z


def test_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    assert "--output-format" in capsys.readouterr().out


def test_orin_code_is_dispatched_by_the_launcher(capsys: pytest.CaptureFixture[str]) -> None:
    from agentos.launcher.cli import main as launcher_main

    assert launcher_main(["code", "-v"]) == 0
    assert capsys.readouterr().out == version_line() + "\n"


def test_a_copy_of_the_binary_named_orin_code_runs_orin_code(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    # A frozen build ships one orin.exe; a copy named orin-code(.exe) is what an SDK host's cli_path points at.
    from agentos.launcher import cli as launcher

    monkeypatch.setattr(launcher.sys, "argv", ["C:/Orin/orin-code.exe", "-v"])
    assert launcher.main() == 0
    assert capsys.readouterr().out == version_line() + "\n"


def test_the_orin_code_console_script_is_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"]["orin-code"] == "agentos.cli_code.main:main"


@pytest.mark.parametrize("verb", ["internal-service", "start"])
def test_a_frozen_orin_code_copy_still_runs_the_launcher_verbs(monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
    # The supervisor re-executes sys.executable with `internal-service`, and `orin code` starts the runtime with `start`.
    from agentos.launcher import cli as launcher

    ran: list[str] = []
    monkeypatch.setattr(launcher.sys, "argv", ["C:/Orin/orin-code.exe", verb, *(["backend"] if verb == "internal-service" else [])])
    monkeypatch.setattr(launcher, "run_service", lambda name: ran.append(name) or 0)
    monkeypatch.setattr(launcher, "command_start", lambda *args, **kwargs: ran.append("start") or 0)
    assert launcher.main() == 0
    assert ran == (["backend"] if verb == "internal-service" else ["start"])

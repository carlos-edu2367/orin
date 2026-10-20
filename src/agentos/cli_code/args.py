"""The ``orin code`` command line: Claude Code's headless flags, Orin's extras, and what Orin refuses.

Parsing only. A flag that exists in Claude Code but has no meaning in Orin (spec §9.2, "excluded") is refused with a
clear message instead of being ignored, so a script never believes it got a behaviour it did not get.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Sequence

EXIT_OK = 0
EXIT_ERROR = 1

# Claude Code flags with no Orin meaning (spec §9.2): refused, never silently ignored.
EXCLUDED_FLAGS: frozenset[str] = frozenset({
    "--chrome", "--no-chrome", "--cloud", "--teleport", "--remote-control", "--environment", "--ide", "--tmux",
    "--file", "--brief", "--betas", "--from-pr", "--ax-screen-reader",
})
EXCLUDED_FLAG_PREFIXES: tuple[str, ...] = ("--remote-control",)
# Subcommands of `orin code` (spec §9.2); each lives in its own module.
SUBCOMMANDS: frozenset[str] = frozenset({
    "mcp", "plugin", "agents", "logs", "stop", "rm", "attach", "doctor", "auto-mode", "purge", "review", "import",
})
# Claude Code subcommands Orin does not have (accounts, its own installer): refused.
EXCLUDED_SUBCOMMANDS: frozenset[str] = frozenset({"auth", "setup-token", "gateway", "install", "update", "ultrareview"})

PERMISSION_MODES: tuple[str, ...] = ("default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto")
ORIN_PERMISSION_MODES: tuple[str, ...] = ("accept_edits", "plan_mode", "ask_permissions", "full_autonomy", "dont_ask")
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


class CliUsageError(Exception):
    """A command line Orin cannot run; the message is shown as is and the exit code is 1."""


def split_tool_list(values: Sequence[str] | None) -> list[str]:
    """``["Read,Bash(git status:*)", "Edit"]`` -> each rule once, split on commas outside parentheses."""
    parts: list[str] = []
    for value in values or ():
        depth, current = 0, []
        for ch in str(value):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(ch)
        parts.append("".join(current))
    unique: list[str] = []
    for item in (part.strip() for part in parts):
        if item and item not in unique:
            unique.append(item)
    return unique


@dataclass(slots=True)
class CodeOptions:
    """Every value of one ``orin code`` invocation, already checked."""

    prompt: str | None = None
    print_mode: bool = False
    output_format: str = "text"
    input_format: str = "text"
    include_partial_messages: bool = False
    include_hook_events: bool = False
    replay_user_messages: bool = False
    verbose: bool = False
    continue_session: bool = False
    resume: str | None = None
    fork_session: bool = False
    session_id: str | None = None
    name: str | None = None
    no_session_persistence: bool = False
    worktree: str | None = None
    background: bool = False
    model: str | None = None
    fallback_model: str | None = None
    effort: str | None = None
    provider: str | None = None
    permission_mode: str | None = None
    dangerously_skip_permissions: bool = False
    allow_dangerously_skip_permissions: bool = False
    permission_prompts: str = "host"
    permission_prompt_tool: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    add_dirs: list[str] = field(default_factory=list)
    trust: bool = False
    mcp_configs: list[str] = field(default_factory=list)
    strict_mcp_config: bool = False
    settings: str | None = None
    setting_sources: list[str] | None = None
    agents: str | None = None
    agent: str | None = None
    plugin_dirs: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    system_prompt_file: str | None = None
    append_system_prompt: str | None = None
    append_system_prompt_file: str | None = None
    system_prompt_snapshot: str | None = None
    exclude_dynamic_system_prompt_sections: bool = False
    json_schema: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    task_budget: int | None = None
    autocompact: str | None = None
    prompt_suggestions: bool = False
    init: bool = False
    maintenance: bool = False
    bare: bool = False
    safe_mode: bool = False
    restricted: bool = False
    debug: str | None = None
    debug_file: str | None = None
    desktop: bool = False
    thinking: str | None = None
    max_thinking_tokens: int | None = None
    thinking_display: str | None = None
    resume_session_at: str | None = None
    resume_drops_turn: str | None = None
    session_mirror: bool = False

    @property
    def headless(self) -> bool:
        """No interface: ``-p``, or an SDK host speaking ``--input-format stream-json`` (it never passes ``-p``)."""
        return self.print_mode or self.input_format == "stream-json"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse exits 2; the contract (spec §9.2) is 1 for every error
        raise CliUsageError(message)


def build_code_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="orin code", description="Orin Code from a terminal or an SDK host.", allow_abbrev=False)
    add = parser.add_argument
    add("prompt", nargs="?", default=None, help="the prompt; with -p and no prompt, stdin is read")
    add("-p", "--print", dest="print_mode", action="store_true", help="answer once and exit, without the interface")
    add("-v", "--version", action="store_true", help="print the version and exit")
    # Format
    add("--output-format", choices=("text", "json", "stream-json"), default="text")
    add("--input-format", choices=("text", "stream-json"), default="text")
    add("--include-partial-messages", action="store_true")
    add("--include-hook-events", action="store_true")
    add("--replay-user-messages", action="store_true")
    add("--verbose", action="store_true")
    # Session
    add("-c", "--continue", dest="continue_session", action="store_true")
    add("-r", "--resume", nargs="?", const="", default=None)
    add("--fork-session", action="store_true")
    add("--session-id")
    add("-n", "--name")
    add("--no-session-persistence", action="store_true")
    add("-w", "--worktree", nargs="?", const="", default=None)
    add("--bg", dest="background", action="store_true")
    # Model
    add("--model")
    add("--fallback-model")
    add("--effort", choices=EFFORT_LEVELS)
    add("--provider")
    # Permissions
    add("--permission-mode", choices=(*PERMISSION_MODES, *ORIN_PERMISSION_MODES))
    add("--dangerously-skip-permissions", action="store_true")
    add("--allow-dangerously-skip-permissions", action="store_true")
    add("--permission-prompts", choices=("host", "none"), default="host")
    add("--permission-prompt-tool")
    add("--allowedTools", "--allowed-tools", dest="allowed_tools", action="append", default=[])
    add("--disallowedTools", "--disallowed-tools", dest="disallowed_tools", action="append", default=[])
    add("--tools", action="append", default=None)
    add("--add-dir", dest="add_dirs", action="append", default=[])
    add("--trust", action="store_true")
    # Configuration
    add("--mcp-config", dest="mcp_configs", action="append", default=[])
    add("--strict-mcp-config", action="store_true")
    add("--settings")
    add("--setting-sources")
    add("--agents")
    add("--agent")
    add("--plugin-dir", dest="plugin_dirs", action="append", default=[])
    # Prompt
    add("--system-prompt")
    add("--system-prompt-file")
    add("--append-system-prompt")
    add("--append-system-prompt-file")
    add("--system-prompt-snapshot", choices=("on", "off"))
    add("--exclude-dynamic-system-prompt-sections", action="store_true")
    # Execution
    add("--json-schema")
    add("--max-turns", type=int)
    add("--max-budget-usd", type=float)
    add("--task-budget", type=int)
    add("--autocompact")
    add("--prompt-suggestions", action="store_true")
    add("--init", action="store_true")
    add("--maintenance", action="store_true")
    # Modes
    add("--bare", action="store_true")
    add("--safe-mode", action="store_true")
    add("--restricted", action="store_true")
    # Debug and interface
    add("-d", "--debug", nargs="?", const="*", default=None)
    add("--debug-file")
    add("--desktop", action="store_true")
    # What SDK hosts pass besides the flags above
    add("--thinking", choices=("adaptive", "enabled", "disabled"))
    add("--max-thinking-tokens", type=int)
    add("--thinking-display")
    add("--resume-session-at")
    add("--resume-drops-turn")
    add("--session-mirror", action="store_true")
    return parser


def _refuse_excluded(argv: Sequence[str]) -> None:
    for token in argv:
        if token == "--":
            return
        name = token.split("=", 1)[0]
        if name in EXCLUDED_FLAGS or name.startswith(EXCLUDED_FLAG_PREFIXES):
            raise CliUsageError(f"{name} is a Claude Code option Orin does not provide")


def parse_code_arguments(argv: Sequence[str]) -> tuple[CodeOptions, bool]:
    """``(options, show_version)`` for the main command; raises ``CliUsageError`` for anything Orin cannot run."""
    _refuse_excluded(argv)
    values = vars(build_code_parser().parse_args(list(argv)))
    show_version = bool(values.pop("version"))
    values["allowed_tools"] = split_tool_list(values.get("allowed_tools"))
    values["disallowed_tools"] = split_tool_list(values.get("disallowed_tools"))
    if values.get("tools") is not None:
        values["tools"] = split_tool_list(values["tools"])
        _check_tool_names(values["tools"])
    sources = values.pop("setting_sources")
    values["setting_sources"] = None if sources is None else split_tool_list([sources])
    options = CodeOptions(**values)
    _check_combinations(options)
    return options, show_version


def _check_tool_names(names: list[str]) -> None:
    """Every name is ``default`` (the SDK's preset), an MCP tool, or a tool Orin publishes; anything else is refused (W6-R5)."""
    from agentos.agentic.tools.public_names import PUBLIC_NAMES, internal_name

    for name in names:
        if name == "default" or name.startswith("mcp__") or internal_name(name) in PUBLIC_NAMES:
            continue
        raise CliUsageError(f"--tools names a tool Orin does not have: {name!r}")


def _check_combinations(options: CodeOptions) -> None:
    if options.dangerously_skip_permissions and options.permission_mode not in (None, "bypassPermissions", "full_autonomy"):
        raise CliUsageError("--dangerously-skip-permissions cannot be combined with another --permission-mode")
    if options.max_turns is not None and options.max_turns < 1:
        raise CliUsageError("--max-turns must be at least 1")
    if options.max_budget_usd is not None and not options.max_budget_usd > 0:
        raise CliUsageError("--max-budget-usd must be greater than 0")
    if options.continue_session and options.resume is not None:
        raise CliUsageError("--continue and --resume cannot be used together")
    if options.input_format == "stream-json" and options.output_format != "stream-json":
        raise CliUsageError("--input-format stream-json requires --output-format stream-json")
    if options.include_partial_messages and options.output_format != "stream-json":
        raise CliUsageError("--include-partial-messages requires --output-format stream-json")
    if options.output_format != "text" and not options.headless:
        raise CliUsageError("--output-format applies only with -p (--print)")
    if options.setting_sources is not None:
        unknown = [item for item in options.setting_sources if item not in ("user", "project", "local")]
        if unknown:
            raise CliUsageError(f"--setting-sources accepts user, project and local, not {', '.join(unknown)}")


__all__ = [
    "EFFORT_LEVELS", "EXCLUDED_FLAGS", "EXCLUDED_SUBCOMMANDS", "EXIT_ERROR", "EXIT_OK", "PERMISSION_MODES",
    "SUBCOMMANDS", "CliUsageError", "CodeOptions", "build_code_parser", "parse_code_arguments", "split_tool_list",
]

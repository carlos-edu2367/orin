# W6 — Headless CLI and SDK protocol — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `orin code` — Claude Code's headless surface on top of the local Orin: `-p` with text, json and stream-json, Claude Code's control protocol on stdin/stdout, its subcommands, so that the official `claude-agent-sdk` drives `orin code` without modification (spec §2, W6 acceptance).

**Architecture:** A thin client (`src/agentos/cli_code/`, decision D6): it never runs the agent loop. It finds the local runtime through the launcher's run directory (starting it in the background when it is not running), authenticates with a per-run token the launcher writes there with owner-only access, creates or resumes a Code conversation through the API, follows the conversation's SSE stream and translates it to Claude Code's messages; host control requests (`initialize`, `interrupt`, `set_permission_mode`, …) become API calls, and the worker's waits (permission requests, SDK hook callbacks) become `control_request`s to the host. Only the local profile: a shared Orin (SERVER) is refused.

**Tech Stack:** Python 3.13, argparse, httpx (already a dependency), FastAPI (API side), the existing launcher; `claude-agent-sdk==0.2.159` as a dev dependency for the conformance tests (Batch F).

**Spec:** `docs/superpowers/specs/2026-10-18-orin-code-claude-code-full-parity-design.md` — §9 (W6: §9.1 architecture, §9.2 surface, §9.3 stream-json, §9.4 conformance), §2 (acceptance), §13–§17. Inherited items: reviews `docs/superpowers/reviews/2026-10-18-w1-review.md` (W1-R10), `…-w3-review.md` (W3-R23, W3-R24, W3-R25, W3-R31), `…-w4-review.md` (W4-R26), `2026-10-19-w5-review.md` (W5-D2, W5-D3, W5-D4), ledgers W0 (Setup hook), W2 (SDK hook callbacks), W5 (deviations 4 and 9). Lote 0 (`docs/superpowers/plans/2026-10-19-w6-headless-sdk-ledger.md`, section "Lote 0") is already on `main` and part of this wave's review range.

## Global Constraints

- **Clean-room.** Never copy Anthropic prompts, tool descriptions, `sdk-tools.d.ts` or SDK source into the repository. Message and field names of the public protocol are facts and may be used; every description is Orin's own wording.
- **Branch `main`.** Never create or switch branches. **Never `git push`** (the coordinator pushes at release).
- **Commit trailer:** every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` — exactly that line, whatever model runs the task.
- **Never delete a file** and never use `git stash`, `git reset` or `git checkout` on files you did not change. If a step seems to need it, stop and report.
- **TDD:** write the test, run it and see it fail for the reason the step gives, implement, see it pass, commit. Never delete, skip or weaken an existing test; when a deliberate behaviour change forces an expectation to change, change only that expectation and record it in the ledger.
- **Python 3.13.** Windows is the primary platform; POSIX branches run in CI (and in the coordinator's Docker run). Never pass a test file together with its own directory to pytest.
- **Fail closed.** `orin code` refuses a shared Orin (SERVER) twice: from its own environment (`ORIN_MODE=server`) and from what the instance reports (`GET /v1/instance`). Anything the CLI sends that changes permissions is accepted by the API only from a principal authenticated with this run's token.
- **`src/agentos/agentic/agent_tools.py` is at 1699 lines** and `tests/unit/agentic/tools/test_split.py` requires fewer than 1700: this wave adds **no line** to it. New toolset behaviour goes in its own module; new toolset state is set by the session as an attribute.
- **Isolation:** no test writes to the developer's real `~/.orin`, `~/.claude`, `~/.claude.json` or Orin run directory. `tests/conftest.py` isolates the `ORIN_*` settings variables for in-process tests; the live-runtime fixture (Task 5) gives the runtime and every `orin code` process their own `ORIN_HOME`, settings folders, `ORIN_CLAUDE_CONFIG`, `HOME` and `USERPROFILE`.
- **Suites** at the end of every batch: `uv run pytest -q tests/unit` (timeout 600000 ms) and `uv run pytest -q tests/integration`. When a task touches `frontend/`: `npm --prefix frontend test`, `npm --prefix frontend run lint`, `npm --prefix frontend run build`.
- **Scripts used while planning** live outside the repository in `C:\Users\carlos\AppData\Local\Temp\claude\c--Users-carlos-Documents-projetos-orin\a0c842bf-8090-4127-b277-31874af29638\scratchpad\w6\` (`$W6`). Never copy them into the repository.

## Intentional deviations from the master spec (decided while planning; the ledger repeats them)

1. **The executable an SDK host points `cli_path` at is `orin-code`**: a console script (`orin-code = agentos.cli_code.main:main`) that is `orin code`. An SDK puts its flags right after `cli_path`, so `orin code` (two words) cannot be a `cli_path`; on Windows the SDK also refuses `.cmd`/`.bat` shims, and the console script is a native `.exe`. A frozen `orin.exe` copied or linked as `orin-code.exe` behaves the same (`launcher.cli.main` dispatches on the program name, except for the launcher's own `internal-service` and `start` verbs). Shipping that copy in the installers is not part of W6.
2. **`orin code -v` prints Orin's version** (`0.9.x (Orin Code)`). An SDK host compares it with Claude Code's minimum (2.0.0), logs a warning, and continues.
3. **The API token is a file, `ORIN_RUN_DIR/api-token`, owner-only** (POSIX `0600`; Windows: inherited entries removed and full control granted to the current account's SID with `%SystemRoot%\System32\icacls.exe`). A fresh token every launch, removed on shutdown and on a failed start. When it cannot be restricted, no token is issued and the interface still starts; `orin code` then says to restart Orin. The browser keeps using the loopback check alone: the token only tells a CLI call apart (it is what lets a call carry CLI-layer settings, Batch B).
4. **`ORIN_WEB_DIST`** names the interface build explicitly. The live-runtime tests point it at a stub build, so they run the real launcher on CI's backend job, which has no frontend build.
5. **`GET /v1/instance`** (new): version, mode, capabilities and the caller's credential kind — what `orin code` checks before it drives an Orin.

(Later batches add deviations 6+ where they arise; each is restated in its task and in the ledger.)

## Batches

| Batch | Tasks | Theme |
|---|---|---|
| A | 1–5 | Foundation: the `orin code` command line, the per-run API token, `GET /v1/instance`, the local runtime client, a live-runtime test fixture on a fake Ollama |
| B | 6– | Headless turns: sessions (`-c`, `-r`, `--session-id`, `--fork-session`, `-n`), model and provider selection, the event translator (assistant, user, stream_event, system, result), `-p` with text/json/stream-json, stream-json input, CLI-layer settings and permission flags, `--max-turns`, `--max-budget-usd`, `--json-schema` with retries |
| C | | Control protocol: `initialize` (hooks, agents, SDK MCP servers), `interrupt`, `set_permission_mode`, `set_model`, `set_max_thinking_tokens`, `rewind_files`, `mcp_status`; `can_use_tool` for permission requests and `--permission-prompts none` (W3-R24); SDK hook callbacks (W2 ledger); `mcp_message` |
| D | | Subcommands: `mcp` (incl. `serve`, `reset-project-choices`), `plugin`, `agents`, `auto-mode`, `doctor`, `logs`/`stop`/`rm`/`attach` with `--bg`, `purge`, `import`; `orin code` without `-p` opens the interface |
| E | | Inherited items: W1-R10, W3-R23, Setup hook, skill `model`/`effort`, agent fields, W5-D2, W5-D3, W5-D4, W4-R26 |
| F | | Conformance: `claude-agent-sdk` drives `orin code` (query, streaming, can_use_tool, SDK hooks, interrupt, resume); W6 close |

Batches B to F are specified in this file after the previous batch has run and been verified; their numbering continues from the last task of the batch before.

---

## Batch A — Foundation

`orin` today only manages the runtime (`launcher/cli.py`). This batch adds the `orin code` command line with Claude Code's flag surface (refusing Claude Code-only flags and subcommands, exit code 1 for every error), the per-run API token and the loopback security that accepts it, `GET /v1/instance`, the client that finds or starts the local runtime and refuses a shared one, and a fixture that runs the real launcher, API and worker on a fake Ollama — the base every later batch tests against. Headless runs, sessions and the protocol arrive in Batch B; until then `orin code -p` answers "not available in this build yet" with exit code 1.

### File structure (Batch A)

- Create `src/agentos/cli_code/__init__.py`, `args.py` (parser and `CodeOptions`), `errors.py`, `main.py` (entry point), `runtime.py` (`OrinClient`, `find_or_start_runtime`, `connect_local`).
- Create `src/agentos/installation/api_token.py` (issue, read, revoke; owner-only).
- Modify `src/agentos/launcher/cli.py` (dispatch `orin code` and an `orin-code` program name), `src/agentos/launcher/supervisor.py` (issue and revoke the token), `src/agentos/api/security.py` (`LoopbackSecurityService` accepts the token), `src/agentos/api/gateway.py` (`GET /v1/instance`), `src/agentos/installation/profile.py` (`ORIN_WEB_DIST`), `pyproject.toml` (`orin-code` script).
- Create tests: `tests/unit/cli_code/__init__.py` (empty), `test_args.py`, `test_api_token.py`, `test_instance_route.py`, `test_runtime_client.py`, `test_web_dist_override.py`; `tests/fixtures/fake_ollama.py`; `tests/integration/cli_code/__init__.py` (empty), `live_runtime.py`, `conftest.py`, `test_live_runtime.py`.
- Modify test expectation: `tests/integration/api/test_profile_isolation.py` (classify `GET /v1/instance`).

### Task 1: The `orin code` command line

**Files:**
- Create: `src/agentos/cli_code/__init__.py`, `src/agentos/cli_code/args.py`, `src/agentos/cli_code/errors.py`, `src/agentos/cli_code/main.py`
- Modify: `src/agentos/launcher/cli.py`, `pyproject.toml`
- Test: `tests/unit/cli_code/__init__.py` (empty), `tests/unit/cli_code/test_args.py`

**Interfaces:**
- Produces: `parse_code_arguments(argv) -> tuple[CodeOptions, bool]` (options, show_version); `CodeOptions` (every flag; `.headless` is `print_mode or input_format == "stream-json"`); `split_tool_list(values) -> list[str]`; `CliUsageError`; `CliRuntimeError` (in `errors.py`); constants `SUBCOMMANDS`, `EXCLUDED_SUBCOMMANDS`, `EXCLUDED_FLAGS`, `EXIT_OK = 0`, `EXIT_ERROR = 1`; `main(argv, *, stdin, stdout, stderr) -> int` with the replaceable stages `run_headless(options, *, stdin, stdout, stderr)`, `open_interface(options, *, stdout, stderr)`, `run_subcommand(name, argv, *, stdout, stderr)` (all three raise `CliRuntimeError("... not available in this build yet")` in this batch); `version_line() -> str`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/cli_code/__init__.py` (empty) and `tests/unit/cli_code/test_args.py`:

```python
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/unit/cli_code/test_args.py`
Expected: collection error, `ModuleNotFoundError: No module named 'agentos.cli_code'`.

- [ ] **Step 3: Implement.** Create the four modules:

`src/agentos/cli_code/__init__.py`:

```python
"""``orin code``: Orin Code from a terminal or an SDK host.

A thin client of the local Orin API (spec §9, decision D6): it never runs the agent loop itself. It makes sure the
local runtime is up, creates or resumes a conversation through the API, follows the conversation's event stream and
speaks Claude Code's headless protocol (text, json and stream-json, with control requests) on stdin and stdout.
"""

from .args import CliUsageError, CodeOptions, parse_code_arguments

__all__ = ["CliUsageError", "CodeOptions", "parse_code_arguments"]
```

`src/agentos/cli_code/args.py`:

```python
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
    sources = values.pop("setting_sources")
    values["setting_sources"] = None if sources is None else split_tool_list([sources])
    options = CodeOptions(**values)
    _check_combinations(options)
    return options, show_version


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
```

`src/agentos/cli_code/errors.py`:

```python
"""Errors of ``orin code``: both end the run with exit code 1 and the message on stderr."""

from __future__ import annotations

from .args import CliUsageError


class CliRuntimeError(Exception):
    """The command line was fine but the run could not happen (no runtime, refused by the API, ...)."""


__all__ = ["CliRuntimeError", "CliUsageError"]
```

`src/agentos/cli_code/main.py`:

```python
"""Entry point of ``orin code`` (also the ``orin-code`` console script an SDK host points ``cli_path`` at)."""

from __future__ import annotations

import sys
from typing import Sequence, TextIO

from agentos.version import __version__

from .args import EXCLUDED_SUBCOMMANDS, EXIT_ERROR, EXIT_OK, SUBCOMMANDS, CliUsageError, CodeOptions, parse_code_arguments
from .errors import CliRuntimeError


def version_line() -> str:
    # An SDK host reads the leading x.y.z; Orin's own version is the honest answer (it warns below 2.0.0 and goes on).
    return f"{__version__} (Orin Code)"


def run_headless(options: CodeOptions, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError("headless runs are not available in this build yet")


def open_interface(options: CodeOptions, *, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError("opening a Code session in the interface is not available in this build yet")


def run_subcommand(name: str, argv: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError(f"`orin code {name}` is not available in this build yet")


def main(argv: Sequence[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        if args and args[0] in EXCLUDED_SUBCOMMANDS:
            raise CliUsageError(f"`orin code {args[0]}` is a Claude Code command Orin does not have")
        if args and args[0] in SUBCOMMANDS:
            return run_subcommand(args[0], args[1:], stdout=stdout, stderr=stderr)
        options, show_version = parse_code_arguments(args)
        if show_version:
            stdout.write(version_line() + "\n")
            return EXIT_OK
        if options.headless:
            return run_headless(options, stdin=stdin, stdout=stdout, stderr=stderr)
        return open_interface(options, stdout=stdout, stderr=stderr)
    except (CliUsageError, CliRuntimeError) as error:
        stderr.write(f"Error: {error}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
```

In `src/agentos/launcher/cli.py`, register the verb for the help listing — after the line `commands.add_parser("start", parents=[shared], help="start the Orin runtime (the default)")` add:

```python
    commands.add_parser("code", add_help=False, help="Orin Code from a terminal or an SDK host (`orin code --help`)")
```

and replace the first three lines of `main`

```python
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv if argv is not None else sys.argv[1:])
```

with

```python
def _invoked_as_orin_code() -> bool:
    """A frozen build ships one binary; a copy named ``orin-code`` is ``orin code`` (what an SDK's ``cli_path`` runs)."""
    return Path(sys.argv[0]).stem.lower() == "orin-code"


def main(argv: list[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    # The runtime re-executes sys.executable with `internal-service` (and `orin code` may start it with `start`): a
    # frozen copy named orin-code must still run those as the launcher.
    as_code = argv is None and _invoked_as_orin_code() and raw[:1] not in (["internal-service"], ["start"])
    if raw[:1] == ["code"] or as_code:
        from agentos.cli_code.main import main as code_main

        return code_main(raw[1:] if raw[:1] == ["code"] else raw)
    parser = build_parser()
    arguments = parser.parse_args(raw)
```

(`Path` and `sys` are already imported there.) In `pyproject.toml`, after the line `orin = "agentos.launcher.cli:main"` add:

```toml
# What an SDK host points `cli_path` at: the same as `orin code`.
orin-code = "agentos.cli_code.main:main"
```

Then run `uv sync --all-groups` so the `orin-code` script is installed in `.venv` (`uv.lock` must not change; if it does, stop and report).

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/unit/cli_code/test_args.py tests/unit/launcher`
Expected: all pass (the launcher tests unchanged).
Also run `.venv/Scripts/orin-code.exe -v` (Windows) and expect `{version} (Orin Code)`.

- [ ] **Step 5: Commit**

```
feat(w6): the orin code command line

Claude Code's headless flags and subcommand names, Claude Code-only ones
refused, exit code 1 for every error; `orin code` and the orin-code script.
```

### Task 2: The per-run API token

**Files:**
- Create: `src/agentos/installation/api_token.py`
- Modify: `src/agentos/launcher/supervisor.py`, `src/agentos/api/security.py`
- Test: `tests/unit/cli_code/test_api_token.py`

**Interfaces:**
- Produces: `issue_api_token(run_dir) -> str`, `read_api_token(run_dir) -> str | None`, `revoke_api_token(run_dir) -> None`, `restrict_to_owner(path)`, `token_path(run_dir)`, `ApiTokenError`; `LoopbackSecurityService(*, maximum_requests=120, api_token=None)` where `api_token` is a callable returning the current token (default: read from `orin_paths().run`); a bearer equal to it authenticates as `AuthenticatedPrincipal("local-user", "loopback-cli", {"api"}, credential_kind="cli_token")`; any other bearer, and any session cookie, is refused.

- [ ] **Step 1: Write the failing test** — `tests/unit/cli_code/test_api_token.py`:

```python
"""W6 Task 2: the per-run API token `orin code` authenticates with (spec §9.1)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path, PureWindowsPath

import pytest
from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticationError, LoopbackSecurityService
from agentos.installation.api_token import issue_api_token, read_api_token, revoke_api_token, token_path


def test_a_token_is_issued_read_and_revoked(tmp_path: Path) -> None:
    first = issue_api_token(tmp_path)
    assert len(first) >= 40 and read_api_token(tmp_path) == first
    second = issue_api_token(tmp_path)
    assert second != first and read_api_token(tmp_path) == second
    assert [item.name for item in tmp_path.iterdir()] == ["api-token"]  # no temporary file left behind
    revoke_api_token(tmp_path)
    assert read_api_token(tmp_path) is None
    revoke_api_token(tmp_path)  # idempotent


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_token_file_is_owner_only_on_posix(tmp_path: Path) -> None:
    issue_api_token(tmp_path)
    assert stat.S_IMODE(token_path(tmp_path).stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_the_token_file_is_owner_only_on_windows(tmp_path: Path) -> None:
    issue_api_token(tmp_path)
    listing = subprocess.run(["icacls", str(token_path(tmp_path))], capture_output=True, text=True, check=True).stdout
    entries = [line.strip() for line in listing.splitlines()[:-1] if ":(" in line]
    assert entries, listing
    assert all("(I)" not in entry for entry in entries), listing  # nothing inherited from the folder
    assert len(entries) == 1 and os.environ["USERNAME"].lower() in entries[0].lower(), listing


def test_a_failed_restriction_leaves_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token

    def refuse(path: Path) -> None:
        raise api_token.ApiTokenError("no")

    monkeypatch.setattr(api_token, "restrict_to_owner", refuse)
    with pytest.raises(api_token.ApiTokenError):
        issue_api_token(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_loopback_accepts_only_the_current_token() -> None:
    current = {"token": "run-token-1"}
    security = LoopbackSecurityService(api_token=lambda: current["token"])
    assert security.authenticate(bearer_token=None, session_id=None).credential_kind == "loopback"
    cli = security.authenticate(bearer_token="run-token-1", session_id=None)
    assert cli.credential_kind == "cli_token" and cli.user_id == "local-user"
    for wrong in ("run-token-2", "run-token-1 ", "ﬀ"):
        with pytest.raises(AuthenticationError):
            security.authenticate(bearer_token=wrong, session_id=None)
    current["token"] = None  # the runtime issued no token: every bearer is refused
    with pytest.raises(AuthenticationError):
        security.authenticate(bearer_token="run-token-1", session_id=None)
    with pytest.raises(AuthenticationError):
        security.authenticate(bearer_token=None, session_id="sid")


def test_the_gateway_still_requires_a_loopback_peer_for_the_token() -> None:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "t")))
    remote = TestClient(app, client=("192.0.2.9", 40000)).get("/v1/conversations", headers={"Authorization": "Bearer t"})
    assert remote.status_code == 401


def _supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import io

    from agentos.installation.paths import OrinPaths
    from agentos.installation.profile import RuntimeProfile
    from agentos.launcher import supervisor as module
    from agentos.launcher.environment import RuntimeEnvironment
    from agentos.launcher.ports import PortChoice
    from agentos.launcher.ui import Console

    paths = OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()
    monkeypatch.setattr(module, "load_environment", lambda *_: RuntimeEnvironment({"DATABASE_URL": "sqlite://"}, (), None))
    monkeypatch.setattr(module, "select_port", lambda *_, **__: PortChoice(48123, 48123, False))
    profile = RuntimeProfile("installed", tmp_path / "install", "9.9.9", None)
    return module.Supervisor(paths=paths, profile=profile, console=Console(io.StringIO(), colour=False)), paths


def test_the_supervisor_issues_a_fresh_token_each_run_and_removes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, paths = _supervisor(tmp_path, monkeypatch)
    supervisor._prepare()
    first = read_api_token(paths.run)
    assert first
    supervisor.shutdown()
    assert read_api_token(paths.run) is None
    supervisor._prepare()
    second = read_api_token(paths.run)
    assert second and second != first
    supervisor._rollback()  # a failed startup leaves no token behind either
    assert read_api_token(paths.run) is None


def test_a_token_that_cannot_be_restricted_does_not_stop_the_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token
    from agentos.launcher import supervisor as module

    supervisor, paths = _supervisor(tmp_path, monkeypatch)

    def refuse(run: Path) -> str:
        raise api_token.ApiTokenError("no ACL")

    monkeypatch.setattr(module, "issue_api_token", refuse)
    supervisor._prepare()  # no exception: the interface does not need the token
    assert read_api_token(paths.run) is None


def test_windows_tools_run_from_system32_and_grant_by_sid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A planted icacls.exe in the current folder or on PATH must never run; a non-ASCII account name must not matter.
    from agentos.installation import api_token

    calls: list[list[str]] = []

    class Done:
        def __init__(self, stdout: str) -> None:
            self.returncode, self.stdout, self.stderr = 0, stdout, ""

    def fake_run(command: list[str], **_: object) -> Done:
        calls.append(command)
        return Done('"desktop\\joão","S-1-5-21-1-2-3-1001"\r\n' if command[0].lower().endswith("whoami.exe") else "ok")

    monkeypatch.setenv("SystemRoot", "C:\Windows")
    monkeypatch.setattr(api_token.subprocess, "run", fake_run)
    api_token._windows_owner_only(tmp_path / "f")
    assert PureWindowsPath(calls[0][0]) == PureWindowsPath("C:/Windows/System32/whoami.exe")
    assert PureWindowsPath(calls[1][0]) == PureWindowsPath("C:/Windows/System32/icacls.exe")
    assert calls[1][-1] == "*S-1-5-21-1-2-3-1001:(F)" and "/inheritance:r" in calls[1]


def test_an_unknown_windows_account_issues_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token

    class Failed:
        returncode, stdout, stderr = 1, "", "denied"

    monkeypatch.setattr(api_token.subprocess, "run", lambda *_, **__: Failed())
    with pytest.raises(api_token.ApiTokenError):
        api_token._windows_owner_only(tmp_path / "f")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/unit/cli_code/test_api_token.py`
Expected: collection error, `ModuleNotFoundError: No module named 'agentos.installation.api_token'`.

- [ ] **Step 3: Implement.** Create `src/agentos/installation/api_token.py`:

```python
"""The per-run API token ``orin code`` authenticates with (spec §9.1).

The launcher issues a fresh random token every time it starts the runtime, in ``ORIN_RUN_DIR/api-token``, readable
only by the person who started it, and removes it on shutdown. The local API accepts it as a bearer credential next to
the loopback check; the browser keeps using loopback alone. The token is what separates a call made by ``orin code``
(which may carry CLI-layer settings) from any other local request.
"""

from __future__ import annotations

import csv
import logging
import os
import secrets
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILE_NAME = "api-token"
_TOKEN_BYTES = 32
_MAX_TOKEN_CHARS = 256


class ApiTokenError(RuntimeError):
    """The token could not be written with owner-only access, so none was issued."""


def token_path(run_dir: Path) -> Path:
    return Path(run_dir) / TOKEN_FILE_NAME


def _system_tool(name: str) -> str:
    # Never resolved through PATH or the current folder: a planted icacls.exe must not run instead of Windows' own.
    return str(Path(os.environ.get("SystemRoot") or "C:/Windows") / "System32" / name)


def _windows_user_sid() -> str:
    """The current account's SID, which icacls takes as ``*SID`` whatever characters the account name has."""
    result = subprocess.run(
        [_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    rows = list(csv.reader((result.stdout or "").splitlines()))
    sid = rows[0][-1].strip() if result.returncode == 0 and rows and rows[0] else ""
    if not sid.startswith("S-1-"):
        raise ApiTokenError("the current Windows account could not be identified")
    return sid


def _windows_owner_only(path: Path) -> None:
    """Drop inherited entries and grant only the current account."""
    result = subprocess.run(
        [_system_tool("icacls.exe"), str(path), "/inheritance:r", "/grant:r", f"*{_windows_user_sid()}:(F)"],
        capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode != 0:
        raise ApiTokenError(f"icacls could not restrict {path.name} (exit {result.returncode})")


def restrict_to_owner(path: Path) -> None:
    """Owner-only access, or ``ApiTokenError``."""
    if os.name == "nt":
        _windows_owner_only(path)
    else:
        os.chmod(path, 0o600)


def issue_api_token(run_dir: Path) -> str:
    """Write a new token (replacing any earlier one) and return it; nothing is left behind on failure."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    temporary = run_dir / f".{TOKEN_FILE_NAME}.{secrets.token_hex(4)}.tmp"
    try:
        # Created owner-only on POSIX from the first byte; Windows is restricted before the token is written.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        restrict_to_owner(temporary)
        temporary.write_text(token, encoding="ascii")
        os.replace(temporary, token_path(run_dir))
    except Exception as error:
        try:
            temporary.unlink()
        except OSError:
            pass
        if isinstance(error, ApiTokenError):
            raise
        raise ApiTokenError(f"the API token could not be written: {error}") from error
    return token


def read_api_token(run_dir: Path) -> str | None:
    """The current run's token, or None when no runtime issued one."""
    try:
        text = token_path(run_dir).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if 0 < len(text) <= _MAX_TOKEN_CHARS else None


def revoke_api_token(run_dir: Path) -> None:
    try:
        token_path(run_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("the API token could not be removed", exc_info=True)


__all__ = ["ApiTokenError", "TOKEN_FILE_NAME", "issue_api_token", "read_api_token", "restrict_to_owner", "revoke_api_token", "token_path"]
```

In `src/agentos/launcher/supervisor.py`: after `from agentos.installation import OrinPaths, RuntimeProfile` add

```python
from agentos.installation.api_token import ApiTokenError, issue_api_token, revoke_api_token
```

In `_prepare`, right after `clear_stop_request(self.paths)` (before `if self.options.desktop:`) add

```python
        try:
            # A fresh token for every run: `orin code` reads it from the run directory (W6, spec §9.1).
            issue_api_token(self.paths.run)
        except ApiTokenError as error:
            # The interface does not need it; only `orin code` stops working, and says why.
            revoke_api_token(self.paths.run)
            self.log.warning("no API token for orin code this run: %s", error)
```

Make `revoke_api_token(self.paths.run)` the first line of `_rollback` (before `if not self.children:`), and in `shutdown` add it right after `clear_stop_request(self.paths)`.

In `src/agentos/api/security.py`: add `from typing import Callable` after `from time import monotonic`, and replace the body of `LoopbackSecurityService` from `def __init__` to the end of `authenticate` with:

```python
    def __init__(self, *, maximum_requests: int = 120, api_token: Callable[[], str | None] | None = None) -> None:
        super().__init__(maximum_requests=maximum_requests)
        self._principal = AuthenticatedPrincipal(
            user_id="local-user",
            credential_ref="loopback-local",
            scopes=frozenset({"api"}),
            credential_kind="loopback",
        )
        # ``orin code`` (W6): the same person, told apart by the per-run token the launcher wrote for them.
        self._cli_principal = AuthenticatedPrincipal(
            user_id="local-user",
            credential_ref="loopback-cli",
            scopes=frozenset({"api"}),
            credential_kind="cli_token",
        )
        self._api_token = api_token if api_token is not None else _current_run_token

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        # Mixed credentials are an operational error in local mode.  Accepting
        # them would hide a mistaken host/IdP configuration and weaken the
        # eventual VPS migration path.
        if session_id:
            raise AuthenticationError("loopback mode does not accept credentials")
        if bearer_token:
            # The only bearer a local Orin accepts is this run's token; anything else is refused, never ignored.
            expected = self._api_token()
            if not expected or not compare_digest(expected.encode("utf-8"), bearer_token.encode("utf-8")):
                raise AuthenticationError("loopback mode does not accept credentials")
            return self._cli_principal
        return self._principal


def _current_run_token() -> str | None:
    from agentos.installation.api_token import read_api_token
    from agentos.installation.paths import orin_paths

    return read_api_token(orin_paths().run)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/unit/cli_code/test_api_token.py tests/unit/api/test_api_asgi.py tests/unit/launcher`
Expected: all pass (on Windows the POSIX permission test skips; on POSIX the ACL test skips).

- [ ] **Step 5: Commit**

```
feat(w6): a per-run API token for orin code

The launcher writes a fresh owner-only token to the run directory and removes
it on shutdown; the loopback API accepts exactly that bearer as the CLI.
```

### Task 3: `GET /v1/instance`

**Files:**
- Modify: `src/agentos/api/gateway.py`, `tests/integration/api/test_profile_isolation.py` (expectation: classify the route)
- Test: `tests/unit/cli_code/test_instance_route.py`

**Interfaces:**
- Produces: `GET /v1/instance` → `{"version": str, "mode": "local" | "server", "capabilities": {name: bool}, "credential": str}` (`credential` is the caller's `credential_kind`: `"cli_token"`, `"loopback"`, `"session"`…).

- [ ] **Step 1: Write the failing test** — `tests/unit/cli_code/test_instance_route.py`:

```python
"""W6 Task 3: GET /v1/instance tells `orin code` which Orin it is talking to."""
from __future__ import annotations

from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.version import __version__


def _client(**services) -> TestClient:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), **services))
    return TestClient(app, client=("127.0.0.1", 40000))


def test_a_local_orin_describes_itself_and_the_credential(monkeypatch) -> None:
    monkeypatch.delenv("ORIN_MODE", raising=False)
    body = _client().get("/v1/instance", headers={"Authorization": "Bearer tok"}).json()
    assert body == {
        "version": __version__, "mode": "local",
        "capabilities": InstanceCapabilities.for_mode(RuntimeMode.LOCAL).as_dict(), "credential": "cli_token",
    }
    assert _client().get("/v1/instance").json()["credential"] == "loopback"


def test_a_shared_orin_says_so(monkeypatch) -> None:
    monkeypatch.setenv("ORIN_MODE", "server")
    body = _client(capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER)).get("/v1/instance").json()
    assert body["mode"] == "server" and body["capabilities"]["host_folders"] is False


def test_a_wrong_token_is_refused() -> None:
    assert _client().get("/v1/instance", headers={"Authorization": "Bearer nope"}).status_code == 401
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/unit/cli_code/test_instance_route.py`
Expected: 3 failures (404 for `/v1/instance`; the JSON body has no `version`).

- [ ] **Step 3: Implement.** In `src/agentos/api/gateway.py`, right before `@app.get("/v1/installation/status")`, add:

```python
    @app.get("/v1/instance")
    async def get_instance(request: Request) -> JSONResponse:
        """What `orin code` checks before it drives this Orin (W6): its version, mode and capabilities."""
        from agentos.configuration.mode import current_mode
        from agentos.version import __version__

        principal = principal_for(request)
        services.security.authorize(principal, action="instance.read", resource_id=None, purpose="instance.read")
        return JSONResponse({
            "version": __version__,
            "mode": current_mode().value,
            "capabilities": services.capabilities.as_dict(),
            "credential": principal.credential_kind,
        })
```

In `tests/integration/api/test_profile_isolation.py`, in the `INSTANCE` set, change the first line to

```python
    ("GET", "/v1/plugins/library"), ("GET", "/v1/mcp/catalog"), ("GET", "/v1/installation/status"), ("GET", "/v1/instance"),
```

(an instance-wide route: it names nobody's resource). Record this expectation change in the ledger.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/unit/cli_code/test_instance_route.py tests/integration/api/test_profile_isolation.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```
feat(w6): GET /v1/instance tells orin code which Orin it reached
```

### Task 4: The local runtime client

**Files:**
- Create: `src/agentos/cli_code/runtime.py`
- Test: `tests/unit/cli_code/test_runtime_client.py`

**Interfaces:**
- Consumes: `read_api_token` (Task 2), `GET /v1/instance` (Task 3), `CliRuntimeError` (Task 1), `agentos.launcher.state.running_instance`.
- Produces: `OrinClient(base_url, token, *, http=None)` with `request(method, path, *, body=None, params=None)`, `get(path, **params)`, `post(path, body=None)`, `patch(path, body)`, `put(path, body)`, `delete(path)`, `events(conversation_id, *, after="0", stop=None) -> Iterator[dict]` (yields the API's activity events and `{"event_type": "heartbeat", "cursor": ...}`; reconnects after the server ends the stream), `close()`; every mutating call sends `Idempotency-Key: cli-<hex>`; an HTTP error status raises `CliRuntimeError("<METHOD> <path> was refused by the local Orin (<status>: <code>)")`. `find_or_start_runtime(paths=None, *, start=True, timeout=180.0, spawn=..., clock=..., sleep=...) -> InstanceState`; `connect_local(paths=None, *, start=True, **runtime_options) -> OrinClient`; `_launcher_command() -> list[str]`.

- [ ] **Step 1: Write the failing test** — `tests/unit/cli_code/test_runtime_client.py`:

```python
"""W6 Task 4: `orin code` finds (or starts) the local Orin and talks to it with the run token."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agentos.cli_code import runtime
from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.runtime import OrinClient, connect_local, find_or_start_runtime
from agentos.installation.api_token import issue_api_token
from agentos.installation.paths import OrinPaths
from agentos.launcher.state import InstanceState


@pytest.fixture
def paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


STATE = InstanceState(pid=1, port=48123, url="http://127.0.0.1:48123", version="9", profile="development", started_at="now")


def _client(handler) -> OrinClient:
    return OrinClient("http://127.0.0.1:48123", "secret-token", http=httpx.Client(base_url="http://127.0.0.1:48123", transport=httpx.MockTransport(handler)))


def test_requests_carry_the_token_and_mutations_an_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    assert client.get("/v1/instance") == {"ok": True}
    client.post("/v1/conversations", {"message": "hi"})
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "idempotency-key" not in seen[0].headers
    assert seen[1].headers["idempotency-key"].startswith("cli-")
    assert "secret-token" not in repr(client)


def test_an_api_refusal_is_a_runtime_error_without_the_body() -> None:
    client = _client(lambda request: httpx.Response(409, json={"error": {"code": "workspace_risk_acknowledgement_required", "detail": "x"}}))
    with pytest.raises(CliRuntimeError, match="409: workspace_risk_acknowledgement_required"):
        client.post("/v1/conversations", {})


def test_events_are_read_from_the_sse_stream_and_heartbeats_keep_the_cursor() -> None:
    body = (
        'id: 1\nevent: turn.started\ndata: {"event_type": "turn.started", "cursor": "c1"}\n\n'
        'event: heartbeat\ndata: {"cursor": "c1"}\n\n'
        'id: 2\nevent: assistant.delta\ndata: {"event_type": "assistant.delta", "payload": {"content": "hé"}, "cursor": "c2"}\n\n'
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("after")))
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})

    client = _client(handler)
    received: list[dict] = []
    for event in client.events("chat_x", stop=lambda: len(received) >= 3):
        received.append(event)
    assert [item["event_type"] for item in received] == ["turn.started", "heartbeat", "assistant.delta"]
    assert received[2]["payload"]["content"] == "hé"
    assert calls == ["0"]


def test_a_running_healthy_instance_is_used(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: STATE)
    monkeypatch.setattr(runtime, "_healthy", lambda _: True)
    assert find_or_start_runtime(paths, spawn=lambda _: pytest.fail("must not start a second Orin")) is STATE


def test_a_stopped_orin_is_started_and_waited_for(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter([None, None, STATE])
    monkeypatch.setattr(runtime, "running_instance", lambda _: next(states))
    monkeypatch.setattr(runtime, "_healthy", lambda _: True)
    spawned: list[OrinPaths] = []
    assert find_or_start_runtime(paths, spawn=spawned.append, sleep=lambda _: None) is STATE
    assert spawned == [paths]


def test_without_start_a_stopped_orin_is_an_error(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: None)
    with pytest.raises(CliRuntimeError, match="not running"):
        find_or_start_runtime(paths, start=False)


def test_an_orin_that_never_comes_up_times_out(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: None)
    ticks = iter(range(0, 1000, 10))
    with pytest.raises(CliRuntimeError, match="did not start within 30 s"):
        find_or_start_runtime(paths, timeout=30, spawn=lambda _: None, clock=lambda: next(ticks), sleep=lambda _: None)


def _patch_instance(monkeypatch: pytest.MonkeyPatch, info: dict) -> None:
    monkeypatch.setattr(runtime, "find_or_start_runtime", lambda *_, **__: STATE)
    monkeypatch.setattr(OrinClient, "get", lambda self, path, **params: info)


LOCAL = {"mode": "local", "capabilities": {"host_folders": True}, "credential": "cli_token", "version": "9"}


def test_connect_returns_a_client_for_a_local_orin_that_accepts_the_token(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    issue_api_token(paths.run)
    _patch_instance(monkeypatch, LOCAL)
    client = connect_local(paths)
    assert client.base_url == STATE.url
    client.close()


@pytest.mark.parametrize(("info", "message"), [
    ({**LOCAL, "mode": "server"}, "local Orin only"),
    ({**LOCAL, "capabilities": {"host_folders": False}}, "local Orin only"),
    ({**LOCAL, "credential": "loopback"}, "did not accept"),
    ([], "local Orin only"),
])
def test_connect_refuses_what_is_not_this_persons_local_orin(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch, info, message: str) -> None:
    issue_api_token(paths.run)
    _patch_instance(monkeypatch, info)
    with pytest.raises(CliRuntimeError, match=message):
        connect_local(paths)


def test_connect_without_a_token_says_how_to_fix_it(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_instance(monkeypatch, LOCAL)
    with pytest.raises(CliRuntimeError, match="orin restart"):
        connect_local(paths)


def test_a_server_environment_is_refused_before_anything_runs(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.setattr(runtime, "find_or_start_runtime", lambda *_, **__: pytest.fail("nothing may start"))
    with pytest.raises(CliRuntimeError, match="ORIN_MODE=server"):
        connect_local(paths)


def test_the_runtime_is_started_with_the_orin_binary_beside_a_frozen_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.cli_code import runtime

    copy = tmp_path / "orin-code.exe"
    real = tmp_path / "orin.exe"
    copy.write_bytes(b"")
    real.write_bytes(b"")

    class Profile:
        def launcher_command(self) -> tuple[str, ...]:
            return (str(copy),)

    monkeypatch.setattr(runtime, "runtime_profile", lambda: Profile())
    assert runtime._launcher_command() == [str(real)]
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/unit/cli_code/test_runtime_client.py`
Expected: collection error, `ModuleNotFoundError: No module named 'agentos.cli_code.runtime'`.

- [ ] **Step 3: Implement** — `src/agentos/cli_code/runtime.py`:

```python
"""The local Orin ``orin code`` drives: found (or started) through the launcher's run directory, reached with the token.

Only the local profile (spec §9.1): the instance must say it is local and has host folders, and must accept this run's
token. A shared Orin (SERVER) is refused, from the CLI's own environment and from what the instance reports.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from agentos.configuration.mode import RuntimeMode, current_mode
from agentos.installation import OrinPaths, orin_paths, runtime_profile
from agentos.installation.api_token import read_api_token
from agentos.launcher.state import InstanceState, running_instance

from .errors import CliRuntimeError

START_TIMEOUT_SECONDS = 180.0
_POLL_SECONDS = 0.5
_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class OrinClient:
    """The local API with this run's bearer token; every mutating call carries a fresh idempotency key."""

    def __init__(self, base_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = http or httpx.Client(base_url=self.base_url, timeout=_REQUEST_TIMEOUT, trust_env=False)

    def __repr__(self) -> str:  # never shows the token
        return f"OrinClient({self.base_url!r})"

    def _headers(self, mutating: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if mutating:
            headers["Idempotency-Key"] = f"cli-{uuid.uuid4().hex}"
        return headers

    def request(self, method: str, path: str, *, body: Any = None, params: dict[str, str] | None = None) -> Any:
        mutating = method.upper() not in {"GET", "HEAD"}
        try:
            response = self._http.request(method, path, json=body, params=params, headers=self._headers(mutating))
        except httpx.HTTPError as error:
            raise CliRuntimeError(f"the local Orin did not answer ({type(error).__name__})") from error
        if response.status_code >= 400:
            raise CliRuntimeError(_api_error(method, path, response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise CliRuntimeError(f"{method} {path}: the local Orin answered with something that is not JSON") from error

    def get(self, path: str, **params: str) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body=body if body is not None else {})

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def events(self, conversation_id: str, *, after: str = "0", stop: Callable[[], bool] | None = None) -> Iterator[dict[str, Any]]:
        """The conversation's activity as it happens (the API's SSE stream); reconnects where it left off."""
        cursor = after
        while not (stop and stop()):
            try:
                with self._http.stream(
                    "GET", f"/v1/conversations/{conversation_id}/events", params={"after": cursor},
                    headers=self._headers(False), timeout=httpx.Timeout(None, connect=5.0),
                ) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise CliRuntimeError(_api_error("GET", "events", response))
                    kind = ""
                    for line in response.iter_lines():
                        if stop and stop():
                            return
                        if line.startswith("event: "):
                            kind = line[7:].strip()
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            if kind == "heartbeat":
                                cursor = str(data.get("cursor") or cursor)
                                yield {"event_type": "heartbeat", "cursor": cursor}
                                continue
                            if kind == "resync":
                                raise CliRuntimeError("the conversation's event stream lost its place")
                            if isinstance(data, dict):
                                cursor = str(data.get("cursor") or cursor)
                                yield data
            except httpx.HTTPError as error:
                if stop and stop():
                    return
                raise CliRuntimeError(f"the conversation's event stream broke ({type(error).__name__})") from error

    def close(self) -> None:
        self._http.close()


def _api_error(method: str, path: str, response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        detail = str(error.get("code") or error.get("message") or "")
    except (ValueError, AttributeError):
        detail = ""
    return f"{method} {path} was refused by the local Orin ({response.status_code}{': ' + detail if detail else ''})"


def _launcher_command() -> list[str]:
    command = list(runtime_profile().launcher_command())
    executable = Path(command[0])
    if executable.stem.lower() == "orin-code":  # a frozen copy: start the runtime with the real orin binary beside it
        sibling = executable.with_name("orin" + executable.suffix)
        if sibling.is_file():
            command[0] = str(sibling)
    return command


def _spawn_runtime(paths: OrinPaths) -> None:
    """``orin start --no-browser`` in the background, detached from this terminal."""
    command = [*_launcher_command(), "start", "--no-browser"]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        base = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        try:  # outlive a job the calling terminal or SDK host may kill on exit
            subprocess.Popen(command, creationflags=base | breakaway, **kwargs)
            return
        except OSError:
            pass
        subprocess.Popen(command, creationflags=base, **kwargs)
    else:
        subprocess.Popen(command, start_new_session=True, **kwargs)


def _healthy(state: InstanceState) -> bool:
    try:
        return httpx.get(f"{state.url}/healthz", timeout=2.0, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def find_or_start_runtime(
    paths: OrinPaths | None = None,
    *,
    start: bool = True,
    timeout: float = START_TIMEOUT_SECONDS,
    spawn: Callable[[OrinPaths], None] = _spawn_runtime,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> InstanceState:
    paths = paths or orin_paths()
    state = running_instance(paths)
    if state is not None and _healthy(state):
        return state
    if state is None:
        if not start:
            raise CliRuntimeError("Orin is not running (start it with `orin start`)")
        spawn(paths)
    deadline = clock() + timeout
    while clock() < deadline:
        state = running_instance(paths)
        if state is not None and _healthy(state):
            return state
        sleep(_POLL_SECONDS)
    raise CliRuntimeError(f"Orin did not start within {int(timeout)} s; see {paths.service_log('launcher')}")


def connect_local(paths: OrinPaths | None = None, *, start: bool = True, **runtime_options: Any) -> OrinClient:
    """A client of this person's local Orin, started when it is not running; SERVER is refused."""
    if current_mode() is RuntimeMode.SERVER:
        raise CliRuntimeError("orin code works with a local Orin only; this environment is a shared Orin (ORIN_MODE=server)")
    paths = paths or orin_paths()
    state = find_or_start_runtime(paths, start=start, **runtime_options)
    token = read_api_token(paths.run)
    if not token:
        raise CliRuntimeError("the running Orin issued no token for orin code; restart it with `orin restart`")
    client = OrinClient(state.url, token)
    try:
        info = client.get("/v1/instance")
    except CliRuntimeError:
        client.close()
        raise
    capabilities = info.get("capabilities") if isinstance(info, dict) else None
    if not isinstance(info, dict) or info.get("mode") != "local" or not (isinstance(capabilities, dict) and capabilities.get("host_folders")):
        client.close()
        raise CliRuntimeError("orin code works with a local Orin only; this one is a shared Orin")
    if info.get("credential") != "cli_token":
        client.close()
        raise CliRuntimeError("the local Orin did not accept this run's token")
    return client


__all__ = ["OrinClient", "connect_local", "find_or_start_runtime"]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/unit/cli_code`
Expected: all pass.

- [ ] **Step 5: Commit**

```
feat(w6): orin code finds or starts the local Orin and refuses a shared one
```

### Task 5: A live runtime on a fake Ollama (test fixture) and `ORIN_WEB_DIST`

**Files:**
- Modify: `src/agentos/installation/profile.py`
- Create: `tests/fixtures/fake_ollama.py`, `tests/integration/cli_code/__init__.py` (empty), `tests/integration/cli_code/live_runtime.py`, `tests/integration/cli_code/conftest.py`
- Test: `tests/unit/cli_code/test_web_dist_override.py`, `tests/integration/cli_code/test_live_runtime.py`

**Interfaces:**
- Produces: `RuntimeProfile.web_dist` honours `ORIN_WEB_DIST`; `tests.fixtures.fake_ollama.FakeOllama` (`start()`, `stop()`, `base_url`, `requests`) and `MODEL = "fake-coder:latest"`, its markers `[[write:<path>]]`, `[[slow]]`, `[[think]]`, `[[say:<text>]]`; `LiveRuntime` (`start()`, `stop()`, `paths`, `url`, `env`, `model`, `api()`, `workspace(name)`, `fake`) and the session fixture `orin_runtime` in `tests/integration/cli_code/conftest.py`. Later batches run `orin-code` as a subprocess with `env=orin_runtime.env`.

- [ ] **Step 1: Write the failing unit test** — `tests/unit/cli_code/test_web_dist_override.py`:

```python
"""W6 Task 5: ORIN_WEB_DIST names the interface build explicitly (packaging, and the runtime tests' stub build)."""
from __future__ import annotations

from pathlib import Path

from agentos.installation.profile import RuntimeProfile


def test_orin_web_dist_is_used_as_is(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repo"
    (repository / "frontend" / "dist").mkdir(parents=True)
    (repository / "frontend" / "dist" / "index.html").write_text("x", encoding="utf-8")
    profile = RuntimeProfile("development", repository, "9.9.9", repository)
    assert profile.web_dist == repository / "frontend" / "dist"
    monkeypatch.setenv("ORIN_WEB_DIST", str(tmp_path / "stub"))
    assert profile.web_dist == tmp_path / "stub"  # even when it has no build: load_environment then says so


def test_a_blank_override_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORIN_WEB_DIST", "  ")
    profile = RuntimeProfile("installed", tmp_path, "9.9.9", None)
    assert profile.web_dist != Path("  ")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/unit/cli_code/test_web_dist_override.py`
Expected: `test_orin_web_dist_is_used_as_is` fails (the override is ignored).

- [ ] **Step 3: Implement.** In `src/agentos/installation/profile.py`, at the top of the `web_dist` property body (after its docstring, before `candidates = (`), add:

```python
        override = os.environ.get("ORIN_WEB_DIST", "").strip()
        if override:
            # An explicit build (packaging, and the runtime tests' stub build): used as is, never searched past.
            return Path(override)
```

(`os` and `Path` are already imported.)

- [ ] **Step 4: Run it to see it pass**

Run: `uv run pytest -q tests/unit/cli_code/test_web_dist_override.py`
Expected: 2 passed.

- [ ] **Step 5: Add the fixture and the live tests.** Create `tests/fixtures/fake_ollama.py`:

```python
"""A scripted Ollama server for offline end-to-end runs of the real Orin runtime (W6).

It speaks the part of Ollama's native API Orin uses: ``/api/tags`` and ``/api/show`` for the catalog, ``/api/chat``
(NDJSON) for turns. What it answers is chosen by a marker in the person's latest prompt:

- no marker: two text chunks, ``Hello from fake.``;
- ``[[write:<path>]]``: a ``Write`` call creating ``<path>`` with ``hi`` and, once its result is back, ``Done.``;
- ``[[slow]]``: forty words, one every 0.25 s, so a test can interrupt the turn while it streams;
- ``[[think]]``: a thinking chunk, then text;
- ``[[say:<text>]]``: exactly ``<text>`` (used for structured output).
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "fake-coder:latest"
_MARKER = re.compile(r"\[\[(write|slow|think|say)(?::([^\]]*))?\]\]")


def _done(prompt: int = 12, completion: int = 3) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
            "prompt_eval_count": prompt, "eval_count": completion}


def _text(*chunks: str) -> list[dict[str, Any]]:
    return [{"message": {"role": "assistant", "content": chunk}, "done": False} for chunk in chunks]


def _latest_prompt(messages: list[dict[str, Any]]) -> tuple[str, bool]:
    """The person's latest prompt (reminders skipped) and whether a tool result came after it."""
    index = None
    for position in range(len(messages) - 1, -1, -1):
        message = messages[position]
        content = str(message.get("content") or "")
        if message.get("role") == "user" and not content.lstrip().startswith("<system-reminder>"):
            index = position
            break
    if index is None:
        return "", False
    after = messages[index + 1:]
    return str(messages[index].get("content") or ""), any(item.get("role") == "tool" for item in after)


def script(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """``(chunks, seconds between chunks)`` for one ``/api/chat`` request."""
    prompt, answered = _latest_prompt(messages)
    found = _MARKER.search(prompt)
    kind, value = (found.group(1), found.group(2) or "") if found else ("", "")
    if kind == "write":
        if answered:
            return [*_text("Done."), _done(20, 2)], 0.0
        call = {"function": {"name": "Write", "arguments": {"file_path": value or "hello.txt", "content": "hi\n"}}}
        return [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "done": False}, _done(10, 5)], 0.0
    if kind == "slow":
        return [*_text(*(f"word{number} " for number in range(40))), _done(10, 40)], 0.25
    if kind == "think":
        return [{"message": {"role": "assistant", "content": "", "thinking": "Considering the request."}, "done": False},
                *_text("Thought it through."), _done(10, 6)], 0.0
    if kind == "say":
        return [*_text(value), _done(10, 4)], 0.0
    return [*_text("Hello ", "from fake."), _done()], 0.0


class _Handler(BaseHTTPRequestHandler):
    server: "FakeOllama"

    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/api/tags"):
            return self._json({"models": [{"name": MODEL, "model": MODEL}]})
        if self.path.startswith("/api/version"):
            return self._json({"version": "0.0-fake"})
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/api/show"):
            return self._json({"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 32768}})
        if not self.path.startswith("/api/chat"):
            return self._json({"error": "not found"}, 404)
        with self.server.lock:
            self.server.requests.append(body)
        chunks, pause = script(list(body.get("messages") or []))
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write((json.dumps({"model": MODEL, **chunk}) + "\n").encode("utf-8"))
                self.wfile.flush()
                if pause:
                    time.sleep(pause)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the turn was interrupted: the runtime closed the stream


class FakeOllama(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "FakeOllama":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


__all__ = ["MODEL", "FakeOllama", "script"]
```

`tests/integration/cli_code/__init__.py` (empty), `tests/integration/cli_code/live_runtime.py`:

```python
"""A real, isolated Orin runtime for `orin code` tests: the launcher, its API and its worker, on a fake Ollama.

Everything the runtime and the CLI write lives under one temporary folder: ORIN_HOME (config, data, logs, cache,
run), the settings layers (ORIN_USER_DIR and friends), ~/.claude.json, and HOME/USERPROFILE. Nothing touches the
developer's own Orin or Claude Code files.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from agentos.installation.api_token import read_api_token
from agentos.installation.paths import OrinPaths
from agentos.launcher.state import running_instance
from tests.fixtures.fake_ollama import MODEL, FakeOllama

START_TIMEOUT = 180.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LiveRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "orin-home"
        self.paths = OrinPaths(self.home / "config", self.home / "data", self.home / "logs", self.home / "cache", self.home / "run")
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.fake = FakeOllama()
        self.env = self._environment()
        self._launcher: subprocess.Popen[bytes] | None = None

    def _environment(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith(("ORIN_", "AGENTOS_"))}
        settings = self.root / "settings"
        for name in ("ORIN_USER_DIR", "ORIN_CLAUDE_HOME", "ORIN_MANAGED_DIR", "ORIN_CLAUDE_MANAGED_DIR"):
            (settings / name).mkdir(parents=True, exist_ok=True)
            env[name] = str(settings / name)
        env["ORIN_CLAUDE_CONFIG"] = str(settings / "claude.json")
        env["ORIN_HOME"] = str(self.home)
        env.update({
            "ORIN_CONFIG_DIR": str(self.paths.config), "ORIN_DATA_DIR": str(self.paths.data),
            "ORIN_LOGS_DIR": str(self.paths.logs), "ORIN_CACHE_DIR": str(self.paths.cache), "ORIN_RUN_DIR": str(self.paths.run),
        })
        user_home = self.root / "home"
        user_home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = env["USERPROFILE"] = str(user_home)
        env["PYTHONIOENCODING"] = "utf-8"
        # A stub web build: the launcher checks that an interface is served, the tests never open it (and CI's
        # backend job has no frontend build).
        web = self.root / "web"
        (web / "assets").mkdir(parents=True, exist_ok=True)
        (web / "index.html").write_text('<!doctype html><html><body><div id="root"></div></body></html>', encoding="utf-8")
        env["ORIN_WEB_DIST"] = str(web)
        return env

    def start(self) -> "LiveRuntime":
        self.fake.start()
        self.paths.ensure()
        log = open(self.root / "launcher.out", "wb")  # noqa: SIM115 - closed with the process
        self._launcher = subprocess.Popen(
            [sys.executable, "-m", "agentos.launcher", "start", "--no-browser", "--port", str(self.port)],
            env=self.env, cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._launcher.poll() is not None:
                raise RuntimeError(f"the launcher exited: {(self.root / 'launcher.out').read_text(errors='replace')[-2000:]}")
            # What `orin code` itself waits for: the launcher's record, the token and a healthy API.
            if running_instance(self.paths) is not None and read_api_token(self.paths.run) and self._healthy():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Orin did not start: {(self.root / 'launcher.out').read_text(errors='replace')[-2000:]}")
        self._configure_provider()
        return self

    def _healthy(self) -> bool:
        try:
            return httpx.get(f"{self.url}/healthz", timeout=2.0, trust_env=False).status_code == 200
        except httpx.HTTPError:
            return False

    def api(self) -> httpx.Client:
        token = read_api_token(self.paths.run) or ""
        return httpx.Client(base_url=self.url, timeout=30.0, trust_env=False, headers={"Authorization": f"Bearer {token}"})

    def _configure_provider(self) -> None:
        with self.api() as client:
            client.put("/v1/providers/ollama", json={"enabled": True, "base_url": self.fake.base_url, "purpose": "provider.configure"},
                       headers={"Idempotency-Key": "fixture-provider"}).raise_for_status()
            client.post("/v1/providers/ollama/models:refresh", headers={"Idempotency-Key": "fixture-refresh"}).raise_for_status()

    @property
    def model(self) -> str:
        return MODEL

    def workspace(self, name: str) -> Path:
        folder = self.root / "workspaces" / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def stop(self) -> None:
        try:
            subprocess.run([sys.executable, "-m", "agentos.launcher", "stop"], env=self.env, cwd=str(self.root),
                           capture_output=True, timeout=90, check=False)
        finally:
            if self._launcher is not None:
                try:
                    self._launcher.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self._launcher.kill()
            self.fake.stop()


__all__ = ["LiveRuntime"]
```

`tests/integration/cli_code/conftest.py`:

```python
from __future__ import annotations

from collections.abc import Iterator

import pytest

from .live_runtime import LiveRuntime


@pytest.fixture(scope="session")
def orin_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveRuntime]:
    runtime = LiveRuntime(tmp_path_factory.mktemp("orin-code-live")).start()
    try:
        yield runtime
    finally:
        runtime.stop()
```

`tests/integration/cli_code/test_live_runtime.py`:

```python
"""W6 Task 5: `orin code` reaches a real local runtime with this run's token (and nothing else gets in with one)."""
from __future__ import annotations

import httpx

from agentos.cli_code.runtime import connect_local
from agentos.installation.api_token import read_api_token

from .live_runtime import LiveRuntime


def test_the_cli_connects_to_the_running_orin_with_the_run_token(orin_runtime: LiveRuntime) -> None:
    client = connect_local(orin_runtime.paths, start=False)
    try:
        info = client.get("/v1/instance")
    finally:
        client.close()
    assert info["mode"] == "local" and info["credential"] == "cli_token"


def test_a_wrong_bearer_is_refused_and_no_bearer_is_still_the_loopback_person(orin_runtime: LiveRuntime) -> None:
    wrong = httpx.get(f"{orin_runtime.url}/v1/instance", headers={"Authorization": "Bearer not-the-token"}, trust_env=False, timeout=10)
    assert wrong.status_code == 401
    plain = httpx.get(f"{orin_runtime.url}/v1/instance", trust_env=False, timeout=10)
    assert plain.status_code == 200 and plain.json()["credential"] == "loopback"


def test_the_fake_provider_answers_a_code_turn(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("smoke")
    with orin_runtime.api() as api:
        created = api.post("/v1/conversations", headers={"Idempotency-Key": "smoke-1"}, json={
            "message": "hello", "selection": {"provider": "ollama", "model_id": orin_runtime.model},
            "workspace_path": str(workspace), "code_mode": "code", "permission_mode": "accept_edits",
        })
        assert created.status_code == 201, created.text
        conversation = created.json()["conversation_id"]
        seen: list[str] = []
        with api.stream("GET", f"/v1/conversations/{conversation}/events", timeout=httpx.Timeout(60.0, connect=5.0)) as stream:
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    seen.append(line[7:])
                    if seen[-1] in {"turn.completed", "turn.failed"}:
                        break
    assert seen[-1] == "turn.completed"
    assert "assistant.delta" in seen
    assert read_api_token(orin_runtime.paths.run)
```

- [ ] **Step 6: Run the live tests**

Run: `uv run pytest -q tests/integration/cli_code` (timeout 600000 ms)
Expected: 3 passed in about 20–40 s (the fixture starts the real launcher, API and worker once). Afterwards no `agentos.launcher` process may be left running (Windows: `Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'agentos.launcher' }` prints nothing).

- [ ] **Step 7: Commit**

```
test(w6): a live Orin runtime on a fake Ollama for orin code tests

ORIN_WEB_DIST names the interface build, so the launcher runs unchanged on a
stub build in CI's backend job.
```

### Task 5b: Batch A close

- [ ] **Step 1:** Run `uv run pytest -q tests/unit` (timeout 600000 ms) and `uv run pytest -q tests/integration` (timeout 600000 ms). Both must be green; report the counts.
- [ ] **Step 2:** Confirm `src/agentos/agentic/agent_tools.py` still has 1699 lines (`wc -l`).
- [ ] **Step 3:** Append a section `## Batch A — Foundation (Tasks 1–5)` to `docs/superpowers/plans/2026-10-19-w6-headless-sdk-ledger.md` with: the commit list (`git log --oneline <plan commit>..HEAD`), the tests added (files and counts), the suite results, every deviation from this plan (what and why), the expectation change in `test_profile_isolation.py`, and anything left incomplete. Commit it:

```
docs(w6): Batch A ledger
```

---

## Batch B — Headless turns

Batch A gave `orin code` a command line, a token and a runtime. This batch makes it answer: a turn carries the CLI's
options to the worker (Task 6), the session applies them (Task 7), the CLI keeps Claude Code-style sessions and picks
the model (Task 8), translates Orin's activity into Claude Code's messages (Task 9), runs `-p` and stream-json turns
(Task 10) and validates `--json-schema` answers (Task 11).

### Deviations decided while planning Batch B (the ledger repeats them)

6. **The CLI's options travel with the turn** (`conversation_turns.cli_options`, migration `0052`), accepted only from the CLI token. `--trust` is applied by the API before the turn is queued.
7. **A CLI run's permission mode is Claude Code's `default` (ask) unless a flag sets one**, and it is sent on every run, so a resumed conversation never keeps a more permissive mode by accident.
8. **Tool inputs and results ride the tool events of a CLI turn only**, capped at 64 KB each (interface turns are unchanged).
9. **A headless turn skips Code mode's completion gate** (the "verify before you answer" second pass): an SDK host gets Claude Code's single answer.
10. **`--system-prompt ""` keeps Orin's prompt** (SDK hosts send it by default); a non-empty one replaces the base Code prompt only — the project instructions and the turn context (mode, environment, git) stay.
11. **`--tools` and bare `--disallowedTools` entries hide tools from the model and deny their calls**; MCP tools are not affected by `--tools`.
12. **A prompt longer than 16,000 characters, or with image or document blocks, is refused** (the API's message limit; attachments through the CLI are not supported yet).
13. **A turn that ends waiting for the person (plan approval, AskUserQuestion) ends the headless run as success**; the person continues it in the interface or with `-c`.
14. **A subagent's own events are not forwarded**: the host sees its `Agent` call and result.
15. **`duration_api_ms` is the wall time; usage and cost are the conversation overview's difference over the turn; cache token counts are 0.**
16. **`--max-thinking-tokens` and `--thinking` map to Orin's effort** (the smallest level whose budget holds the number; `disabled` means no effort); `--thinking-display` is refused.
17. **`--no-session-persistence` only keeps the run out of the CLI's session index**; the conversation stays in Orin.
18. **Model aliases (`sonnet`, `opus`) are not mapped**; a model must be named as its provider lists it.
19. **Until Batch C no permission prompt reaches the host** (every CLI turn is non-interactive) and control requests other than `initialize` are answered with an error.
20. **`jsonschema` becomes a runtime dependency; `--json-schema` retries at most 3 times.**
21. **`claude-agent-sdk==0.2.159` is a dev dependency from Batch B on**, built from its sdist (no bundled binary).

### File structure (Batch B)

- Create: `src/agentos/code_mode/cli_options.py`, migration `0052_w6_cli_turn_options.py`, `src/agentos/agentic/cli_turn.py`, `src/agentos/cli_code/{sessions,selection,protocol,turn_options,headless,structured}.py`.
- Modify: `persistence/postgres/schema.py`, `conversations/chat.py`, `api/gateway.py`, `agentic/runtime.py`, `agentic/session.py`, `code_mode/permissions.py`, `cli_code/main.py`, `pyproject.toml`, `uv.lock`.
- Tests: `tests/unit/cli_code/test_{turn_options,cli_turn_session,sessions_and_selection,protocol,turn_options_builder,structured}.py`, `tests/integration/cli_code/test_headless.py`; expectation change in `tests/unit/persistence/test_w3_schema.py`.

### Task 6: A turn carries `orin code`'s options

What a CLI flag changes about a turn has to reach the worker with the turn. `CliTurnOptions` (`agentos/code_mode/cli_options.py`) is the validated shape; it is stored as JSON in a new nullable column `conversation_turns.cli_options` (migration `0052`, and the chat store adds the column itself on a database that skipped migrations, like `turn_kind`). `POST /v1/conversations` and `POST /v1/conversations/{id}/messages` take an optional `cli` object **only from a principal authenticated with this run's CLI token** (403 otherwise); `cli.trust_workspace` makes the API trust the folder **before** the turn is queued. The project conversation route is unchanged (the CLI does not create project conversations).

**Files:**
- Create: `src/agentos/code_mode/cli_options.py`, `src/agentos/persistence/postgres/migrations/versions/0052_w6_cli_turn_options.py`
- Modify: `src/agentos/persistence/postgres/schema.py`, `src/agentos/conversations/chat.py`, `src/agentos/api/gateway.py`, `tests/unit/persistence/test_w3_schema.py`
- Test: `tests/unit/cli_code/test_turn_options.py`

**Interfaces:**
- Produces: `CliTurnOptions` (pydantic, `extra=forbid`): `settings: dict` (≤ 64 KB of JSON), `setting_sources: list['user'|'project'|'local'] | None`, `system_prompt`, `append_system_prompt` (≤ 65536 chars), `tools: list[str] | None`, `permission_prompts: 'host'|'none'`, `transcript: bool = True`, `headless: bool = True`, `trust_workspace: bool = False`, `.dumps() -> str`; `load_cli_options(raw) -> CliTurnOptions | None` (raises `CliOptionsError` for a stored value that no longer validates); `PostgresChatStore.create(..., cli_options: str | None = None)`; `ChatApplication.create/send(..., cli_options=None)`; the claimed turn dict has `cli_options` (JSON text or None).

- [ ] **Step 1: Write the failing tests.**

`tests/unit/cli_code/test_turn_options.py`:

```python
"""W6 Task 6: the options `orin code` attaches to a turn — validated, accepted only with the CLI token, stored."""
from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService
from agentos.code_mode.cli_options import CliOptionsError, CliTurnOptions, load_cli_options
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def test_options_validate_and_round_trip() -> None:
    options = CliTurnOptions(settings={"permissions": {"deny": ["Bash"]}, "maxTurns": 3}, tools=["Read", " Edit "],
                             setting_sources=["user"], append_system_prompt="Be brief.", permission_prompts="none")
    loaded = load_cli_options(options.dumps())
    assert loaded == CliTurnOptions.model_validate(json.loads(options.dumps()))
    assert loaded.tools == ["Read", "Edit"] and loaded.permission_prompts == "none" and loaded.headless is True
    assert load_cli_options(None) is None and load_cli_options("") is None


@pytest.mark.parametrize("bad", [
    {"unknown": 1},
    {"settings": {"x": "y" * 70000}},
    {"settings": {"x": math.nan}},
    {"tools": ["Read", "  "]},
    {"setting_sources": ["cloud"]},
    {"permission_prompts": "sometimes"},
])
def test_invalid_options_are_refused(bad: dict) -> None:
    with pytest.raises(ValueError):
        CliTurnOptions.model_validate(bad)


def test_a_stored_value_that_no_longer_validates_stops_the_turn() -> None:
    with pytest.raises(CliOptionsError):
        load_cli_options('{"tools": 5}')
    with pytest.raises(CliOptionsError):
        load_cli_options("not json")


class _Application:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def allocate_conversation_id(self) -> str:
        return "chat_test"

    def create(self, context, **kwargs):
        self.calls.append(("create", kwargs))
        return {"conversation_id": "chat_test", "title": "t", "turn_id": "turn_1", "message_id": "m", "state": "queued"}

    def send(self, user_id, conversation_id, message, idempotency_key, **kwargs):
        self.calls.append(("send", kwargs))
        return {"conversation_id": conversation_id, "turn_id": "turn_2", "state": "queued"}


def _client(application: _Application) -> TestClient:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=application))
    return TestClient(app, client=("127.0.0.1", 40000))


CREATE = {"message": "hi", "selection": {"provider": "ollama", "model_id": "m"}, "code_mode": "code",
          "cli": {"settings": {"maxTurns": 2}, "permission_prompts": "none"}}


def test_the_cli_token_may_attach_options_on_create_and_send() -> None:
    application = _Application()
    client = _client(application)
    headers = {"Authorization": "Bearer tok", "Idempotency-Key": "k1"}
    assert client.post("/v1/conversations", json=CREATE, headers=headers).status_code == 201
    sent = client.post("/v1/conversations/chat_test/messages", json={"message": "more", "cli": {"tools": ["Read"]}},
                       headers={**headers, "Idempotency-Key": "k2"})
    assert sent.status_code == 201
    created_options = json.loads(application.calls[0][1]["cli_options"])
    assert created_options["settings"] == {"maxTurns": 2} and created_options["permission_prompts"] == "none"
    assert json.loads(application.calls[1][1]["cli_options"])["tools"] == ["Read"]


def test_without_the_token_cli_options_are_refused() -> None:
    application = _Application()
    response = _client(application).post("/v1/conversations", json=CREATE, headers={"Idempotency-Key": "k1"})
    assert response.status_code == 403
    assert application.calls == []


def test_a_turn_without_options_stores_none() -> None:
    application = _Application()
    body = {key: value for key, value in CREATE.items() if key != "cli"}
    assert _client(application).post("/v1/conversations", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "k1"}).status_code == 201
    assert "cli_options" not in application.calls[0][1]


def test_the_store_keeps_the_options_with_the_turn() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    stored = CliTurnOptions(tools=["Read"]).dumps()
    receipt = store.create(user_id="u", message="hi", provider="ollama", model_id="m", idempotency_key="k", code_mode="code", cli_options=stored)
    turn = store.claim(receipt.turn_id)
    assert turn is not None and load_cli_options(turn["cli_options"]).tools == ["Read"]
    plain = store.create(user_id="u", message="again", provider="ollama", model_id="m", idempotency_key="k2", conversation_id=receipt.conversation_id)
    store.finish(turn)
    assert store.claim(plain.turn_id)["cli_options"] is None


def test_a_database_that_skipped_the_migration_gets_the_column() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE conversation_turns DROP COLUMN cli_options"))
    PostgresChatStore(engine)
    assert "cli_options" in {column["name"] for column in inspect(engine).get_columns("conversation_turns")}


class _Roots:
    def __init__(self) -> None:
        self.roots: dict[tuple[str, str], str] = {}

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        self.roots[(workspace_id, user_id)] = root_path

    def clear_root(self, workspace_id: str, user_id: str) -> bool:
        return self.roots.pop((workspace_id, user_id), None) is not None

    def root_for(self, workspace_id: str, user_id: str):
        return self.roots.get((workspace_id, user_id))


def test_trust_is_recorded_before_the_turn_is_queued(tmp_path) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    folder = tmp_path / "project"
    folder.mkdir()
    seen: list[bool] = []

    class _Recording(_Application):
        def create(self, context, **kwargs):
            seen.append(WorkspaceTrustStore().is_trusted("local-user", folder.resolve()))
            return super().create(context, **kwargs)

    application = _Recording()
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=application,
                                 local_workspaces=_Roots()))
    client = TestClient(app, client=("127.0.0.1", 40000))
    body = {**CREATE, "workspace_path": str(folder), "cli": {"trust_workspace": True}}
    response = client.post("/v1/conversations", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "t1"})
    assert response.status_code == 201, response.text
    assert seen == [True]


def test_without_the_token_nothing_is_trusted(tmp_path) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    folder = tmp_path / "project"
    folder.mkdir()
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Application(),
                                 local_workspaces=_Roots()))
    body = {**CREATE, "workspace_path": str(folder), "cli": {"trust_workspace": True}}
    response = TestClient(app, client=("127.0.0.1", 40000)).post("/v1/conversations", json=body, headers={"Idempotency-Key": "t2"})
    assert response.status_code == 403
    assert WorkspaceTrustStore().is_trusted("local-user", folder.resolve()) is False
```

- [ ] **Step 2: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_turn_options.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.code_mode.cli_options'`.

- [ ] **Step 3: Implement.**

Create `src/agentos/code_mode/cli_options.py`:

```python
"""What ``orin code`` attaches to a turn (W6, spec §9): its CLI settings layer and headless switches.

The API accepts these only from a caller authenticated with this run's CLI token, stores them with the turn
(``conversation_turns.cli_options``) and the worker's session applies them. Everything here can only narrow what the
person's own settings allow, except what a CLI flag legitimately adds as the "cli" settings layer (which sits above
the person's and the repository's layers and below managed policy, as in Claude Code).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CLI_TEXT = 65536
MAX_CLI_SETTINGS_BYTES = 65536
MAX_CLI_TOOL_NAME = 128


class CliOptionsError(ValueError):
    """A turn's stored CLI options do not validate: the turn must not run without them."""


class CliTurnOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The "cli" settings layer: --settings, --allowedTools/--disallowedTools, --add-dir, --max-turns, ... merged.
    settings: dict[str, Any] = Field(default_factory=dict)
    # --setting-sources: which file layers load (managed and cli always do).
    setting_sources: list[Literal["user", "project", "local"]] | None = Field(default=None, max_length=3)
    # --system-prompt replaces Orin's base Code prompt; --append-system-prompt adds to the turn's context.
    system_prompt: str | None = Field(default=None, max_length=MAX_CLI_TEXT)
    append_system_prompt: str | None = Field(default=None, max_length=MAX_CLI_TEXT)
    # --tools: the only tools offered (Claude Code names); None means Orin's normal set.
    tools: list[str] | None = Field(default=None, max_length=256)
    # --permission-prompts none: nobody can answer a prompt, so whatever would ask is refused (W1-R9 / W3-R24).
    permission_prompts: Literal["host", "none"] = "host"
    # The tool inputs and results the protocol needs travel on the turn's tool events.
    transcript: bool = True
    # A headless run answers the host as Claude Code would: no "Code mode wants a verification" second pass.
    headless: bool = True
    # --trust: the API trusts the workspace folder before it queues the turn (the session itself ignores this).
    trust_workspace: bool = False

    @field_validator("settings")
    @classmethod
    def _settings_are_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("cli settings must be plain JSON") from error
        if len(encoded.encode("utf-8")) > MAX_CLI_SETTINGS_BYTES:
            raise ValueError("cli settings are too large")
        return value

    @field_validator("tools")
    @classmethod
    def _tool_names(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [str(item).strip() for item in value]
        if any(not item or len(item) > MAX_CLI_TOOL_NAME for item in cleaned):
            raise ValueError("tool names must be non-blank and short")
        return cleaned

    def dumps(self) -> str:
        return self.model_dump_json()


def load_cli_options(raw: object) -> CliTurnOptions | None:
    """A turn's stored options; None when the CLI did not start the turn. A value that no longer validates raises."""
    if raw is None or raw == "" or raw == b"":
        return None
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        return CliTurnOptions.model_validate(data)
    except Exception as error:  # noqa: BLE001 - any shape problem is the same refusal
        raise CliOptionsError("the turn's CLI options are invalid") from error


__all__ = ["CliOptionsError", "CliTurnOptions", "MAX_CLI_SETTINGS_BYTES", "MAX_CLI_TEXT", "load_cli_options"]
```

Create `src/agentos/persistence/postgres/migrations/versions/0052_w6_cli_turn_options.py`:

```python
"""W6: the options `orin code` attaches to a turn

Revision ID: 0052_w6_cli_turn_options
Revises: 0051_w5_plugin_settings
"""
from alembic import op
import sqlalchemy as sa


revision = "0052_w6_cli_turn_options"
down_revision = "0051_w5_plugin_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The chat store adds the column itself on a database that skipped migrations: add it only when it is missing.
    bind = op.get_bind()
    if "cli_options" not in {column["name"] for column in sa.inspect(bind).get_columns("conversation_turns")}:
        with op.batch_alter_table("conversation_turns") as batch:
            batch.add_column(sa.Column("cli_options", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch:
        batch.drop_column("cli_options")
```

In `src/agentos/persistence/postgres/schema.py` make these 1 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
    Column("turn_kind", String(16), nullable=True),
    Column("started_at", DateTime(timezone=True)), Column("finished_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True), nullable=False),
```

with

```python
    Column("turn_kind", String(16), nullable=True),
    # W6: the options `orin code` attached to the turn (agentos.code_mode.cli_options), as JSON; NULL otherwise.
    Column("cli_options", Text(), nullable=True),
    Column("started_at", DateTime(timezone=True)), Column("finished_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True), nullable=False),
```


In `src/agentos/conversations/chat.py` make these 4 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
        ("conversation_turns", "turn_kind", "VARCHAR(16)"),
        ("conversation_dispatches", "lease_renewed_at", timestamp),
```

with

```python
        ("conversation_turns", "turn_kind", "VARCHAR(16)"),
        ("conversation_turns", "cli_options", "TEXT"),
        ("conversation_dispatches", "lease_renewed_at", timestamp),
```

2. Replace

```python

    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str, conversation_id: str | None = None, project_id: str | None = None, workspace_id: str | None = None, attachments: Sequence[Mapping[str, object]] = (), new_conversation_id: str | None = None, scheduled_by_schedule_id: str | None = None, code_mode: str = "chat", permission_mode: str | None = None, turn_kind: str | None = None) -> ChatReceipt:
        message = message.strip()
```

with

```python

    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str, conversation_id: str | None = None, project_id: str | None = None, workspace_id: str | None = None, attachments: Sequence[Mapping[str, object]] = (), new_conversation_id: str | None = None, scheduled_by_schedule_id: str | None = None, code_mode: str = "chat", permission_mode: str | None = None, turn_kind: str | None = None, cli_options: str | None = None) -> ChatReceipt:
        message = message.strip()
```

3. Replace

```python
                } for item in attachments])
            c.execute(insert(conversation_turns).values(turn_id=turn_id, conversation_id=conversation_id, user_id=user_id, execution_id=execution_id, user_message_id=user_message_id, assistant_message_id=assistant_message_id, provider=provider, model_id=model_id, state="queued", idempotency_key=idempotency_key, scheduled_by_schedule_id=scheduled_by_schedule_id, code_mode="code" if code_active else None, turn_kind=turn_kind, created_at=now, updated_at=now))
            if code_active:
```

with

```python
                } for item in attachments])
            c.execute(insert(conversation_turns).values(turn_id=turn_id, conversation_id=conversation_id, user_id=user_id, execution_id=execution_id, user_message_id=user_message_id, assistant_message_id=assistant_message_id, provider=provider, model_id=model_id, state="queued", idempotency_key=idempotency_key, scheduled_by_schedule_id=scheduled_by_schedule_id, code_mode="code" if code_active else None, turn_kind=turn_kind, cli_options=cli_options, created_at=now, updated_at=now))
            if code_active:
```

4. Replace

```python
        return _id("chat")
    def create(self, context, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, project_id: str | None = None, attachments=(), new_conversation_id: str | None = None, code_mode: str = "chat", permission_mode: str | None = None):
        return self.store.create(user_id=context.user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, project_id=project_id, workspace_id=workspace_id, attachments=attachments, new_conversation_id=new_conversation_id, code_mode=code_mode, permission_mode=permission_mode)
    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str, attachments=(), provider: str = "", model_id: str = "", code_mode: str = "chat", permission_mode: str | None = None, turn_kind: str | None = None):
        waiting = self.store.waiting_execution_ids(conversation_id, user_id)
        receipt = self.store.create(user_id=user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, conversation_id=conversation_id, attachments=attachments, code_mode=code_mode, permission_mode=permission_mode, turn_kind=turn_kind)
        # A normal follow-up is the durable answer to an ask_user effect. The
```

with

```python
        return _id("chat")
    def create(self, context, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, project_id: str | None = None, attachments=(), new_conversation_id: str | None = None, code_mode: str = "chat", permission_mode: str | None = None, cli_options: str | None = None):
        return self.store.create(user_id=context.user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, project_id=project_id, workspace_id=workspace_id, attachments=attachments, new_conversation_id=new_conversation_id, code_mode=code_mode, permission_mode=permission_mode, cli_options=cli_options)
    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str, attachments=(), provider: str = "", model_id: str = "", code_mode: str = "chat", permission_mode: str | None = None, turn_kind: str | None = None, cli_options: str | None = None):
        waiting = self.store.waiting_execution_ids(conversation_id, user_id)
        receipt = self.store.create(user_id=user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, conversation_id=conversation_id, attachments=attachments, code_mode=code_mode, permission_mode=permission_mode, turn_kind=turn_kind, cli_options=cli_options)
        # A normal follow-up is the durable answer to an ask_user effect. The
```


In `src/agentos/api/gateway.py` make these 9 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
from agentos.accounts.errors import AccountError
from agentos.configuration.capabilities import CapabilityUnavailable, InstanceCapabilities
```

with

```python
from agentos.accounts.errors import AccountError
from agentos.code_mode.cli_options import CliTurnOptions
from agentos.configuration.capabilities import CapabilityUnavailable, InstanceCapabilities
```

2. Replace

```python
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
```

with

```python
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _trust_workspace(user_id: str, root: Path) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    WorkspaceTrustStore().trust(user_id, root)


def _cli_options_for(principal: AuthenticatedPrincipal, options: CliTurnOptions | None) -> str | None:
    """The turn's CLI options as stored JSON; only `orin code`, holding this run's token, may set them (W6)."""
    if options is None:
        return None
    if principal.credential_kind != "cli_token":
        raise AuthorizationError("cli options need the orin code token")
    return options.dumps()
```

3. Replace

```python
    attachments: list[str] = Field(default_factory=list, max_length=MAX_FILES_PER_MESSAGE)
    code_mode: str = Field(default="chat", pattern="^(auto|code|chat)$")
    permission_mode: str | None = Field(default=None, pattern="^(accept_edits|plan_mode|ask_permissions|full_autonomy|auto|dont_ask|default|acceptEdits|plan|bypassPermissions|dontAsk|manual)$")


class UpdateConversationPermissionModeRequest(_RequestModel):
```

with

```python
    attachments: list[str] = Field(default_factory=list, max_length=MAX_FILES_PER_MESSAGE)
    code_mode: str = Field(default="chat", pattern="^(auto|code|chat)$")
    permission_mode: str | None = Field(default=None, pattern="^(accept_edits|plan_mode|ask_permissions|full_autonomy|auto|dont_ask|default|acceptEdits|plan|bypassPermissions|dontAsk|manual)$")
    # W6: what `orin code` attaches to the turn; accepted only with this run's CLI token.
    cli: CliTurnOptions | None = None


class UpdateConversationPermissionModeRequest(_RequestModel):
```

4. Replace

```python
    selection: ConversationSelectionRequest | None = None
    code_mode: str = Field(default="chat", pattern="^(auto|code|chat)$")
    permission_mode: str | None = Field(default=None, pattern="^(accept_edits|plan_mode|ask_permissions|full_autonomy|auto|dont_ask|default|acceptEdits|plan|bypassPermissions|dontAsk|manual)$")


class SkillDependenciesRequest(_RequestModel):
```

with

```python
    selection: ConversationSelectionRequest | None = None
    code_mode: str = Field(default="chat", pattern="^(auto|code|chat)$")
    permission_mode: str | None = Field(default=None, pattern="^(accept_edits|plan_mode|ask_permissions|full_autonomy|auto|dont_ask|default|acceptEdits|plan|bypassPermissions|dontAsk|manual)$")
    # W6: what `orin code` attaches to the turn; accepted only with this run's CLI token.
    cli: CliTurnOptions | None = None


class SkillDependenciesRequest(_RequestModel):
```

5. Replace

```python
        principal = principal_for(request, mutable=True)
        provider = _provider_name(payload.selection.provider)
```

with

```python
        principal = principal_for(request, mutable=True)
        cli_options = _cli_options_for(principal, payload.cli)
        provider = _provider_name(payload.selection.provider)
```

6. Replace

```python
            _require_port(services.local_workspaces).set_root(workspace_root_id, principal.user_id, bind_path_for(principal, inspection))
        attachments: list[dict[str, object]] = []
```

with

```python
            _require_port(services.local_workspaces).set_root(workspace_root_id, principal.user_id, bind_path_for(principal, inspection))
            if payload.cli is not None and payload.cli.trust_workspace:
                # `orin code --trust` (W6): before the turn is queued, so the worker already sees a trusted folder.
                _trust_workspace(principal.user_id, Path(bind_path_for(principal, inspection)))
        attachments: list[dict[str, object]] = []
```

7. Replace

```python
            if payload.permission_mode is not None:
                create_kwargs["permission_mode"] = normalize_permission_mode(payload.permission_mode).value
            result = application.create(  # type: ignore[union-attr]
                ProviderCatalogContext(principal.user_id, "conversation.create"),
```

with

```python
            if payload.permission_mode is not None:
                create_kwargs["permission_mode"] = normalize_permission_mode(payload.permission_mode).value
            if cli_options is not None:
                create_kwargs["cli_options"] = cli_options
            result = application.create(  # type: ignore[union-attr]
                ProviderCatalogContext(principal.user_id, "conversation.create"),
```

8. Replace

```python
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.send")
        require_content(payload.message, payload.attachments)
```

with

```python
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.send")
        cli_options = _cli_options_for(principal, payload.cli)
        if payload.cli is not None and payload.cli.trust_workspace:
            # `orin code --trust` (W6): before the turn is queued, so the worker already sees a trusted folder.
            _trust_workspace(principal.user_id, Path(getattr(conversation_workspace(conversation_id, principal), "root")))
        require_content(payload.message, payload.attachments)
```

9. Replace

```python
                send_kwargs["permission_mode"] = normalize_permission_mode(payload.permission_mode).value
            result = require_port(services.conversation_application).send(
```

with

```python
                send_kwargs["permission_mode"] = normalize_permission_mode(payload.permission_mode).value
            if cli_options is not None:
                send_kwargs["cli_options"] = cli_options
            result = require_port(services.conversation_application).send(
```


In `tests/unit/persistence/test_w3_schema.py` make these 2 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0051_w5_plugin_settings"
    inspector = inspect(engine)
```

with

```python
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0052_w6_cli_turn_options"
    inspector = inspect(engine)
```

2. Replace

```python
    assert "ux_conversation_input_queue_idempotency" in {index["name"] for index in inspector.get_indexes("conversation_input_queue")}
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0051_w5_plugin_settings"
```

with

```python
    assert "ux_conversation_input_queue_idempotency" in {index["name"] for index in inspector.get_indexes("conversation_input_queue")}
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0052_w6_cli_turn_options"
```


- [ ] **Step 4: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code/test_turn_options.py tests/unit/persistence tests/unit/api tests/unit/conversations`
Expected: all pass.

Expectation change to record in the ledger: `tests/unit/persistence/test_w3_schema.py` now expects the head revision `0052_w6_cli_turn_options`.

- [ ] **Step 5: Commit.**

```
feat(w6): a turn carries the options orin code gives it

Stored with the turn (migration 0052), accepted only from the CLI token;
--trust records the folder's trust before the turn is queued.
```

### Task 7: The worker's session applies the options

The session reads the options once (a corrupt value stops the turn) and applies them: the `cli` settings layer with `--setting-sources` (for the turn's settings **and** the permission broker, which gains `setting_sources`); `--permission-prompts none` makes the turn non-interactive, so whatever would ask is refused (W1-R9, closing W3-R24); `--system-prompt` replaces the base Code prompt (an empty one keeps it, since SDK hosts send `--system-prompt ""` by default) and `--append-system-prompt` joins the turn context; `--tools` keeps only the named core tools and a bare `--disallowedTools` entry withholds its tool (both hidden from the model and denied by the cli layer); a headless turn skips Code mode's completion gate (the "verify before you answer" second pass). For the protocol, the tool events of a CLI turn carry the tool's input (`tool_input`, on `tool.started`) and its result text (`tool_result`, on `tool.finished`), each capped at 64 KB; interface turns are unchanged. The runtime passes `tool_input` and `tool_content` to the lifecycle callbacks and gains three class attributes (`offered_tools`, `withheld_tools`, `completion_gate`) that the session sets.

**Files:**
- Create: `src/agentos/agentic/cli_turn.py`
- Modify: `src/agentos/agentic/runtime.py`, `src/agentos/agentic/session.py`, `src/agentos/code_mode/permissions.py`
- Test: `tests/unit/cli_code/test_cli_turn_session.py`

**Interfaces:**
- Consumes: `CliTurnOptions`, `load_cli_options` (Task 6).
- Produces: `agentic.cli_turn`: `cli_options_for(turn)`, `tool_selection(options, core) -> (offered | None, withheld)`, `cli_settings_layer(options, core) -> dict | None`, `capped_text`, `capped_input`, `TRANSCRIPT_LIMIT = 65536`; `TurnSession.cli_options`; `AgenticTurnRuntime.offered_tools / withheld_tools / completion_gate`; `ToolPermissionBroker(..., setting_sources=None)`; activity payloads `tool_input` (tool.started) and `tool_result` (tool.finished).

- [ ] **Step 1: Write the failing tests.**

`tests/unit/cli_code/test_cli_turn_session.py`:

```python
"""W6 Task 7: the worker's session applies an `orin code` turn's options."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.cli_turn import capped_input, capped_text, cli_settings_layer, tool_selection
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession
from agentos.code_mode.cli_options import CliOptionsError, CliTurnOptions
from agentos.code_mode.models import CodePermissionMode
from agentos.settings.paths import orin_user_dir


class _Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.recorded: list[tuple[object, str, dict]] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record(self, turn, event_type, summary, payload=None, **kwargs):
        self.recorded.append((event_type, summary, dict(payload or {})))

    def main_agent_id(self, turn):
        return "agent:c:main"


class _Agents:
    def list(self):
        return []


def _session(tmp_path: Path, options: CliTurnOptions | None = None, **turn) -> TurnSession:
    store = _Store()
    extra = {"cli_options": options.dumps()} if options is not None else {}
    return TurnSession(
        turn={**store.turn, "code_mode": "code", **extra, **turn}, store=store, agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )


def test_tool_selection_and_the_cli_layer() -> None:
    options = CliTurnOptions(tools=["Read", "Grep"], settings={"permissions": {"deny": ["Bash", "Edit(src/**)"]}})
    offered, withheld = tool_selection(options, CODE_MODE_CORE_TOOLS)
    assert offered == frozenset({"read_file", "search_files"})
    assert "run_command" in withheld and "write_file" in withheld and "read_file" not in withheld
    layer = cli_settings_layer(options, CODE_MODE_CORE_TOOLS)
    assert "Write" in layer["permissions"]["deny"] and "Edit(src/**)" in layer["permissions"]["deny"]
    assert options.settings["permissions"]["deny"] == ["Bash", "Edit(src/**)"]  # the stored options are not mutated
    assert tool_selection(None, CODE_MODE_CORE_TOOLS) == (None, frozenset())
    assert cli_settings_layer(CliTurnOptions(), CODE_MODE_CORE_TOOLS) is None


def test_transcript_values_are_capped() -> None:
    assert capped_text("x" * 10, limit=100) == "x" * 10
    assert len(capped_text("x" * 1000, limit=100)) == 100 and capped_text("x" * 1000, limit=100).endswith("[truncated by Orin]")
    assert capped_input({"path": "a"}) == {"path": "a"}
    big = capped_input({"content": "y" * 200}, limit=100)
    assert set(big) == {"_truncated"} and len(big["_truncated"]) == 100
    assert capped_input("not a mapping") == {}


def test_no_prompts_makes_the_turn_non_interactive(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(permission_prompts="none"), interactive_permissions=True)
    assert session.turn["interactive_permissions"] is False
    runtime = session.build_runtime()
    assert runtime.toolset._interactive_permissions is False
    assert runtime.toolset._non_interactive_ask_allowed is False  # W1-R9: what would ask is refused
    assert runtime.toolset._permission_broker is session._active_permission_broker  # the CLI layer still decides


def test_the_cli_layer_reaches_the_loop_limits_and_the_permission_engine(tmp_path: Path) -> None:
    options = CliTurnOptions(settings={"maxTurns": 2, "permissions": {"deny": ["Bash"]}})
    runtime = _session(tmp_path, options).build_runtime()
    assert runtime.limits.max_turns == 2
    engine = runtime.toolset._permission_broker.engine()
    assert engine.decide("run_command", {"command": "ls"}, CodePermissionMode.FULL_AUTONOMY).behavior == "deny"


def test_tools_are_offered_and_withheld(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(tools=["Read", "Grep"], settings={"permissions": {"deny": ["Glob"]}}))
    runtime = session.build_runtime()
    names = {str((schema.get("function") or {}).get("name") or schema.get("name")) for schema in runtime._tool_schemas(session.turn)}
    assert names == {"read_file", "search_files"}
    write = runtime.toolset._permission_broker.engine().decide("write_file", {"path": "a.txt", "content": "x"}, CodePermissionMode.FULL_AUTONOMY)
    assert write.behavior == "deny"


def test_a_bare_disallowed_tool_is_not_offered(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(settings={"permissions": {"deny": ["Bash"]}}))
    runtime = session.build_runtime()
    names = {str((schema.get("function") or {}).get("name") or schema.get("name")) for schema in runtime._tool_schemas(session.turn)}
    assert "read_file" in names and "run_command" not in names  # internal names: the translator renames them after


def test_the_system_prompt_flags(tmp_path: Path) -> None:
    replaced = _session(tmp_path / "a", CliTurnOptions(system_prompt="You are a terse reviewer.", append_system_prompt="Answer in Portuguese.")).build_runtime()
    assert replaced.system_prompt.startswith("You are a terse reviewer.")
    assert "# Using tools" not in replaced.system_prompt
    assert "# Instructions from the command line\nAnswer in Portuguese." in replaced.volatile_prompt
    kept = _session(tmp_path / "b", CliTurnOptions(system_prompt="")).build_runtime()
    assert "# Using tools" in kept.system_prompt  # an SDK host's empty --system-prompt keeps Orin's prompt


def test_a_headless_turn_skips_the_verification_second_pass(tmp_path: Path) -> None:
    assert _session(tmp_path / "a", CliTurnOptions()).build_runtime().completion_gate is False
    assert _session(tmp_path / "b").build_runtime().completion_gate is True


def test_setting_sources_limit_the_file_layers(tmp_path: Path) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"maxTurns": 7}), encoding="utf-8")
    assert _session(tmp_path / "a", CliTurnOptions()).build_runtime().limits.max_turns == 7
    runtime = _session(tmp_path / "b", CliTurnOptions(setting_sources=["project"])).build_runtime()
    assert runtime.limits.max_turns is None
    assert runtime.toolset._permission_broker.setting_sources == ("project",)


def test_tool_events_carry_the_input_and_result_only_for_a_cli_turn(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions())
    session.emit_lifecycle(session.turn, "tool_started", tool_name="read_file", invocation_id="c1", tool_input={"path": "a.txt"})
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="read_file", invocation_id="c1", status="succeeded",
                           summary="ok", tool_payload={}, tool_content="line 1\n")
    payloads = {str(event): payload for event, _, payload in session.store.recorded}
    started = next(payload for event, payload in payloads.items() if event.endswith("TOOL_STARTED") or event == "tool.started")
    finished = next(payload for event, payload in payloads.items() if event.endswith("TOOL_FINISHED") or event == "tool.finished")
    assert started["tool_input"] == {"path": "a.txt"} and finished["tool_result"] == "line 1\n"
    plain = _session(tmp_path / "ui")
    plain.emit_lifecycle(plain.turn, "tool_started", tool_name="read_file", invocation_id="c1", tool_input={"path": "a.txt"})
    assert all("tool_input" not in payload for _, _, payload in plain.store.recorded)


def test_corrupt_stored_options_stop_the_turn(tmp_path: Path) -> None:
    with pytest.raises(CliOptionsError):
        _session(tmp_path, cli_options='{"tools": 3}')
```

- [ ] **Step 2: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_cli_turn_session.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.agentic.cli_turn'`.

- [ ] **Step 3: Implement.**

Create `src/agentos/agentic/cli_turn.py`:

```python
"""How a turn started by ``orin code`` differs from one started in the interface (W6).

The session reads the turn's stored CLI options (``agentos.code_mode.cli_options``) once and asks this module what
they mean: the "cli" settings layer, which tools are offered, and how much of a tool's input and result travels on
the turn's events so the CLI can show them as Claude Code's ``tool_use`` and ``tool_result`` blocks.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any

from agentos.code_mode.cli_options import CliTurnOptions, load_cli_options

from .tools.public_names import internal_name, public_name

# The tool input and result each tool event may carry for the CLI (the model saw at most MAX_TOOL_RESULT_CHARS).
TRANSCRIPT_LIMIT = 65536
_TRUNCATED = "\n[truncated by Orin]"


def cli_options_for(turn: Mapping[str, Any]) -> CliTurnOptions | None:
    """The turn's CLI options, or None for an interface turn; raises ``CliOptionsError`` for a corrupt value."""
    return load_cli_options(turn.get("cli_options"))


def _bare_tool_names(rules: object) -> list[str]:
    """``["Bash", "Edit(src/**)", "WebFetch"]`` -> the rules that name a whole tool (no specifier)."""
    if not isinstance(rules, list):
        return []
    return [str(rule).strip() for rule in rules if isinstance(rule, str) and rule.strip() and "(" not in rule]


def tool_selection(options: CliTurnOptions | None, core: Collection[str]) -> tuple[frozenset[str] | None, frozenset[str]]:
    """``(offered, withheld)`` internal tool names: ``--tools`` keeps only the named tools; a bare ``--disallowedTools``
    entry removes its tool from what the model sees (its deny rule refuses the call either way)."""
    if options is None:
        return None, frozenset()
    withheld: set[str] = set()
    permissions = options.settings.get("permissions") if isinstance(options.settings, Mapping) else None
    if isinstance(permissions, Mapping):
        withheld.update(internal_name(name) for name in _bare_tool_names(permissions.get("deny")))
    offered: frozenset[str] | None = None
    if options.tools is not None:
        offered = frozenset(internal_name(name) for name in options.tools)
        withheld.update(name for name in core if name not in offered)
    return offered, frozenset(withheld)


def cli_settings_layer(options: CliTurnOptions | None, core: Collection[str]) -> dict[str, Any] | None:
    """The "cli" settings layer for the turn; a tool left out by ``--tools`` is also denied, so a call to it is refused."""
    if options is None:
        return None
    layer = json.loads(json.dumps(options.settings))
    _, withheld = tool_selection(options, core)
    if options.tools is not None and withheld:
        permissions = layer.setdefault("permissions", {})
        if not isinstance(permissions, dict):
            permissions = layer["permissions"] = {}
        deny = permissions.setdefault("deny", [])
        if not isinstance(deny, list):
            deny = permissions["deny"] = []
        for name in sorted(withheld):
            rule = public_name(name)
            if rule not in deny:
                deny.append(rule)
    return layer or None


def capped_text(value: object, limit: int = TRANSCRIPT_LIMIT) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text if len(text) <= limit else text[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED


def capped_input(value: object, limit: int = TRANSCRIPT_LIMIT) -> dict[str, Any]:
    """A tool's arguments for the CLI, or ``{"_truncated": <json prefix>}`` when they are too large to repeat."""
    if not isinstance(value, Mapping):
        return {}
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(encoded) <= limit:
        return json.loads(encoded)
    return {"_truncated": encoded[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED}


__all__ = ["TRANSCRIPT_LIMIT", "capped_input", "capped_text", "cli_options_for", "cli_settings_layer", "tool_selection"]
```

In `src/agentos/agentic/runtime.py` make these 5 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
class AgenticTurnRuntime:
    def __init__(
```

with

```python
class AgenticTurnRuntime:
    # W6, set by the session for an `orin code` turn: the core tools offered (None: all) and those withheld (--tools,
    # --disallowedTools), and whether Code mode's "verify before you answer" second pass runs (off for a headless run).
    offered_tools: frozenset[str] | None = None
    withheld_tools: frozenset[str] = frozenset()
    completion_gate: bool = True

    def __init__(
```

2. Replace

```python
                gate_ok, gate_reason = (True, None)
                if callable(completion_gate) and not turn.get("is_subagent") and (final_iteration or code_gate_rejections < 1):
                    try:
```

with

```python
                gate_ok, gate_reason = (True, None)
                if callable(completion_gate) and self.completion_gate and not turn.get("is_subagent") and (final_iteration or code_gate_rejections < 1):
                    try:
```

3. Replace

```python
                allowed = set(CODE_MODE_CORE_TOOLS)
                active_mcp = getattr(self.toolset, "active_mcp_tool_names", ())
```

with

```python
                allowed = set(CODE_MODE_CORE_TOOLS)
                if self.offered_tools is not None:
                    allowed &= self.offered_tools
                allowed -= self.withheld_tools
                active_mcp = getattr(self.toolset, "active_mcp_tool_names", ())
```

4. Replace

```python
                    started_meta["command"] = cmd_val.strip()[:240]
            self._life(turn, "tool_started", tool_name=name, invocation_id=call_id, **started_meta)
            prepared.append((call_id, name, parsed_args, parse_err))
```

with

```python
                    started_meta["command"] = cmd_val.strip()[:240]
            self._life(
                turn, "tool_started", tool_name=name, invocation_id=call_id,
                tool_input=dict(parsed_args) if isinstance(parsed_args, Mapping) else {}, **started_meta,
            )
            prepared.append((call_id, name, parsed_args, parse_err))
```

5. Replace

```python
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
                tool_arguments=dict(arguments),
            )
```

with

```python
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
                tool_arguments=dict(arguments), tool_content=outcome.content,
            )
```


In `src/agentos/agentic/session.py` make these 9 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
        self.main_agent_id = store.main_agent_id(turn) if hasattr(store, "main_agent_id") else str(turn.get("agent_id", "agent:main"))
```

with

```python
        self.main_agent_id = store.main_agent_id(turn) if hasattr(store, "main_agent_id") else str(turn.get("agent_id", "agent:main"))
        from .cli_turn import cli_options_for

        # W6: an `orin code` turn's options; a corrupt stored value stops the turn here (CliOptionsError).
        self.cli_options = cli_options_for(turn)
        if self.cli_options is not None and self.cli_options.permission_prompts == "none":
            # Nobody can answer a prompt: whatever would ask is refused (W1-R9; closes W3-R24).
            self.turn["interactive_permissions"] = False
```

2. Replace

```python
                    started_payload[k] = val.strip()[:240]
            self._record(
```

with

```python
                    started_payload[k] = val.strip()[:240]
            if self.cli_options is not None and self.cli_options.transcript:
                from .cli_turn import capped_input

                started_payload["tool_input"] = capped_input(payload.get("tool_input"))  # W6: the CLI's tool_use block
            self._record(
```

3. Replace

```python
            summary = str(payload.get("summary") or f"{name} {status}")
            self._record(
```

with

```python
            summary = str(payload.get("summary") or f"{name} {status}")
            if self.cli_options is not None and self.cli_options.transcript:
                from .cli_turn import capped_text

                extra["tool_result"] = capped_text(payload.get("tool_content"))  # W6: the CLI's tool_result block
            self._record(
```

4. Replace

```python
            return ""
        stable = render_code_system_prompt(options)
        memory = self._memory.render() if getattr(self, "_memory", None) is not None else ""
```

with

```python
            return ""
        cli = getattr(self, "cli_options", None)
        # W6: `--system-prompt` replaces Orin's base Code prompt (an empty one keeps it: SDK hosts send "" by default).
        stable = cli.system_prompt if cli is not None and (cli.system_prompt or "").strip() else render_code_system_prompt(options)
        memory = self._memory.render() if getattr(self, "_memory", None) is not None else ""
```

5. Replace

```python
        )
        return stable, dynamic
```

with

```python
        )
        cli = getattr(self, "cli_options", None)
        if cli is not None and (cli.append_system_prompt or "").strip():
            dynamic += "\n\n# Instructions from the command line\n" + cli.append_system_prompt.strip()
        return stable, dynamic
```

6. Replace

```python
        trusted_workspace = self._workspace_trusted()
        try:
            self.effective_settings = load_effective_settings(self.workspace.root, trusted=trusted_workspace)
        except Exception:  # noqa: BLE001 - settings never stop a turn from starting
```

with

```python
        trusted_workspace = self._workspace_trusted()
        from .cli_turn import cli_settings_layer

        cli_layer = cli_settings_layer(self.cli_options, CODE_MODE_CORE_TOOLS)
        cli_sources = self.cli_options.setting_sources if self.cli_options is not None else None
        try:
            self.effective_settings = load_effective_settings(
                self.workspace.root, cli=cli_layer, sources=cli_sources, trusted=trusted_workspace,
            )
        except Exception:  # noqa: BLE001 - settings never stop a turn from starting
```

7. Replace

```python
                # Managed layers survive even when the whole load fails: they are organisation policy.
                self.effective_settings = load_effective_settings(None, sources=())
            except Exception:  # noqa: BLE001
```

with

```python
                # Managed layers survive even when the whole load fails: they are organisation policy.
                self.effective_settings = load_effective_settings(None, cli=cli_layer, sources=())
            except Exception:  # noqa: BLE001
```

8. Replace

```python
            "cancelled": lambda: bool(self.cancelled(self.turn)),
        }
```

with

```python
            "cancelled": lambda: bool(self.cancelled(self.turn)),
            "cli_settings": cli_layer,
            "setting_sources": cli_sources,
        }
```

9. Replace

```python
        )
        if resumed is not None:
```

with

```python
        )
        if self.cli_options is not None:
            from .cli_turn import tool_selection

            runtime.offered_tools, runtime.withheld_tools = tool_selection(self.cli_options, CODE_MODE_CORE_TOOLS)
            runtime.completion_gate = not self.cli_options.headless
        if resumed is not None:
```


In `src/agentos/code_mode/permissions.py` make these 5 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python
import time
from typing import Any, Callable, Literal, Mapping
```

with

```python
import time
from collections.abc import Collection
from typing import Any, Callable, Literal, Mapping
```

2. Replace

```python
        cli_settings: Mapping[str, Any] | None = None,
        event_emitter: Callable[[str, str, dict[str, Any]], None] | None = None,
```

with

```python
        cli_settings: Mapping[str, Any] | None = None,
        setting_sources: Collection[str] | None = None,
        event_emitter: Callable[[str, str, dict[str, Any]], None] | None = None,
```

3. Replace

```python
        self.cli_settings = dict(cli_settings) if cli_settings is not None else None
        self.event_emitter = event_emitter
```

with

```python
        self.cli_settings = dict(cli_settings) if cli_settings is not None else None
        # W6 `--setting-sources`: which file layers the engine reads (None: all of them).
        self.setting_sources = tuple(setting_sources) if setting_sources is not None else None
        self.event_emitter = event_emitter
```

4. Replace

```python
        # Fresh settings on every call, so a new grant applies to the next call.
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, trusted=self.trusted)
        extra = tuple(getattr(self._workspace, "additional_roots", ()) or ())
```

with

```python
        # Fresh settings on every call, so a new grant applies to the next call.
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, sources=self.setting_sources, trusted=self.trusted)
        extra = tuple(getattr(self._workspace, "additional_roots", ()) or ())
```

5. Replace

```python
    def load_rules(self) -> dict[str, list[str]]:
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, trusted=self.trusted)
        return {kind: [rule for rule, _source in settings.permission_rules(kind)] for kind in ("allow", "ask", "deny")}
```

with

```python
    def load_rules(self) -> dict[str, list[str]]:
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, sources=self.setting_sources, trusted=self.trusted)
        return {kind: [rule for rule, _source in settings.permission_rules(kind)] for kind in ("allow", "ask", "deny")}
```


- [ ] **Step 4: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code/test_cli_turn_session.py tests/unit/agentic tests/unit/code_mode tests/unit/permissions`
Expected: all pass.

`src/agentos/agentic/agent_tools.py` must stay at 1699 lines: this task does not touch it.

- [ ] **Step 5: Commit.**

```
feat(w6): an orin code turn runs with its CLI layer, tools and prompts

The cli settings layer and --setting-sources reach the turn and the permission
engine; --permission-prompts none refuses what would ask; tool inputs and
results ride the tool events of a CLI turn; headless turns skip the second pass.
```

### Task 8: Sessions and model selection

Claude Code names sessions with UUIDs: `SessionIndex` (`<ORIN_DATA_DIR>/cli-sessions.json`, owner-only, written atomically) maps each session id to its conversation, the folder it ran in and its name, for `-c` (latest in the folder), `-r <id|name|conversation id>` and `--session-id`. `ModelCatalog` asks the API once per provider whether it is enabled and which models it offers; `--model` may be `provider/model`, `provider:model` or a model id exactly one enabled provider offers, `--provider` names the provider; without `--model` a resumed session keeps its conversation's model and a new one uses the person's `model` setting. Nothing is guessed: unknown or ambiguous models are errors that say what is available.

**Files:**
- Create: `src/agentos/cli_code/sessions.py`, `src/agentos/cli_code/selection.py`
- Test: `tests/unit/cli_code/test_sessions_and_selection.py`

**Interfaces:**
- Produces: `SessionIndex(data_dir)` with `load()`, `find(ref)`, `latest_for(folder)`, `record(*, conversation_id, folder, session_id=None, name=None) -> CliSession`; `folder_key`, `is_uuid`; `Selection(provider, model_id)` (`.as_request()`), `ModelCatalog(api)`, `resolve_model(catalog, value, provider=None)`, `choose_selection(catalog, *, model, provider, remembered, configured)`, `KNOWN_PROVIDERS`.

- [ ] **Step 1: Write the failing tests.**

`tests/unit/cli_code/test_sessions_and_selection.py`:

```python
"""W6 Task 8: `orin code` sessions (UUIDs for conversations) and model selection."""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest

from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.selection import ModelCatalog, Selection, choose_selection, resolve_model
from agentos.cli_code.sessions import SessionIndex, folder_key, is_uuid


def test_sessions_are_recorded_found_and_touched(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "data")
    project = tmp_path / "project"
    project.mkdir()
    first = index.record(conversation_id="chat_1", folder=project, name="review")
    assert is_uuid(first.session_id) and first.folder == folder_key(project)
    chosen = str(uuid.uuid4())
    second = index.record(conversation_id="chat_2", folder=project, session_id=chosen.upper())
    assert second.session_id == chosen  # Claude Code ids are lower-case UUIDs
    assert index.find(chosen) == second and index.find("chat_1") == first and index.find("review") == first
    assert index.latest_for(project).conversation_id == "chat_2"
    touched = index.record(conversation_id="chat_1", folder=project, session_id=first.session_id)
    assert touched.name == "review" and touched.updated_at >= first.updated_at
    assert index.latest_for(project).conversation_id == "chat_1"
    assert index.find("nothing") is None and index.latest_for(tmp_path) is None


def test_a_session_id_cannot_move_to_another_conversation(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    taken = index.record(conversation_id="chat_1", folder=tmp_path)
    with pytest.raises(ValueError):
        index.record(conversation_id="chat_2", folder=tmp_path, session_id=taken.session_id)


def test_a_broken_index_reads_as_empty(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    index.path.write_text("{not json", encoding="utf-8")
    assert index.load() == []
    index.record(conversation_id="chat_1", folder=tmp_path)
    assert len(index.load()) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_index_is_owner_only(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    index.record(conversation_id="chat_1", folder=tmp_path)
    assert stat.S_IMODE(index.path.stat().st_mode) == 0o600


def test_is_uuid() -> None:
    assert is_uuid(str(uuid.uuid4()))
    assert is_uuid("0B3F6C2E-7D7B-4F39-9C9E-1F1F1F1F1F1F")
    assert not is_uuid("chat_1") and not is_uuid("") and not is_uuid("0b3f6c2e7d7b4f399c9e1f1f1f1f1f1f")


class _Api:
    def __init__(self, catalogs: dict[str, list[str]]) -> None:
        self.catalogs = catalogs
        self.calls: list[str] = []

    def get(self, path: str, **params: str) -> object:
        self.calls.append(path)
        provider = path.split("/")[3]
        if provider not in self.catalogs:
            raise CliRuntimeError("404")
        if path.endswith("/models"):
            return {"items": [{"model_id": item} for item in self.catalogs[provider]]}
        return {"provider": provider, "enabled": True}


CATALOGS = {"ollama": ["qwen3:8b", "llama3"], "openrouter": ["anthropic/claude-sonnet-4.5", "llama3"]}


@pytest.mark.parametrize(("value", "expected"), [
    ("ollama/qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("ollama:qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("anthropic/claude-sonnet-4.5", Selection("openrouter", "anthropic/claude-sonnet-4.5")),
    ("openrouter/anthropic/claude-sonnet-4.5", Selection("openrouter", "anthropic/claude-sonnet-4.5")),
])
def test_models_resolve_exactly(value: str, expected: Selection) -> None:
    assert resolve_model(ModelCatalog(_Api(CATALOGS)), value) == expected


def test_ambiguous_unknown_and_disabled_models_are_errors() -> None:
    catalog = ModelCatalog(_Api(CATALOGS))
    with pytest.raises(CliRuntimeError, match="choose one with --provider"):
        resolve_model(catalog, "llama3")
    assert resolve_model(catalog, "llama3", "ollama") == Selection("ollama", "llama3")
    with pytest.raises(CliRuntimeError, match="enabled providers: openrouter, ollama"):
        resolve_model(catalog, "gpt-9")
    with pytest.raises(CliRuntimeError, match="not set up"):
        resolve_model(catalog, "gpt-4o", "openai")
    with pytest.raises(CliRuntimeError, match="unknown provider"):
        resolve_model(catalog, "x", "nowhere")


def test_the_catalog_is_asked_once_per_provider() -> None:
    api = _Api(CATALOGS)
    catalog = ModelCatalog(api)
    resolve_model(catalog, "qwen3:8b")
    resolve_model(catalog, "qwen3:8b")
    assert api.calls.count("/v1/providers/ollama/models") == 1


def test_choosing_without_a_model_flag() -> None:
    catalog = ModelCatalog(_Api(CATALOGS))
    remembered = Selection("ollama", "llama3")
    assert choose_selection(catalog, model=None, provider=None, remembered=remembered, configured="qwen3:8b") == remembered
    assert choose_selection(catalog, model=None, provider=None, remembered=None, configured="qwen3:8b") == Selection("ollama", "qwen3:8b")
    assert choose_selection(catalog, model="ollama/llama3", provider=None, remembered=None, configured=None) == Selection("ollama", "llama3")
    with pytest.raises(CliRuntimeError, match="choose a model"):
        choose_selection(catalog, model=None, provider=None, remembered=None, configured=None)
```

- [ ] **Step 2: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_sessions_and_selection.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.cli_code.sessions'`.

- [ ] **Step 3: Implement.**

Create `src/agentos/cli_code/sessions.py`:

```python
"""``orin code`` sessions: Claude Code's session ids (UUIDs) for Orin conversations.

A session is one Orin Code conversation started from a terminal or an SDK host. Claude Code names sessions with
UUIDs and resumes them by id or name (``-r``), or the latest one in the folder (``-c``); the index here maps each id
to its conversation, the folder it ran in and its name. It lives in Orin's data folder, owner-only, and is written
atomically.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

INDEX_FILE_NAME = "cli-sessions.json"
MAX_SESSIONS = 2000
_lock = threading.Lock()


def folder_key(path: Path | str) -> str:
    """How two spellings of the same folder compare (resolved, case-folded where the OS folds case)."""
    return os.path.normcase(str(Path(path).resolve()))


def is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class CliSession:
    session_id: str
    conversation_id: str
    folder: str
    name: str | None
    created_at: str
    updated_at: str


class SessionIndex:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / INDEX_FILE_NAME

    def load(self) -> list[CliSession]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        sessions: list[CliSession] = []
        for item in raw.get("sessions", []) if isinstance(raw, dict) else []:
            try:
                sessions.append(CliSession(**{key: item.get(key) for key in CliSession.__slots__}))
            except (TypeError, AttributeError):
                continue
        return [item for item in sessions if isinstance(item.session_id, str) and isinstance(item.conversation_id, str)]

    def _write(self, sessions: list[CliSession]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        data = json.dumps({"sessions": [asdict(item) for item in sessions[-MAX_SESSIONS:]]}, indent=1)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(temporary, self.path)

    def find(self, reference: str) -> CliSession | None:
        """By session id, by conversation id, or by name (the most recent session with that name)."""
        reference = (reference or "").strip()
        if not reference:
            return None
        sessions = self.load()
        for item in sessions:
            if item.session_id == reference.lower() or item.conversation_id == reference:
                return item
        named = [item for item in sessions if item.name == reference]
        return max(named, key=lambda item: item.updated_at) if named else None

    def latest_for(self, folder: Path | str) -> CliSession | None:
        key = folder_key(folder)
        here = [item for item in self.load() if item.folder == key]
        return max(here, key=lambda item: item.updated_at) if here else None

    def record(self, *, conversation_id: str, folder: Path | str, session_id: str | None = None, name: str | None = None) -> CliSession:
        """Add a session or touch an existing one (same id): its name changes only when a new one is given."""
        now = datetime.now(UTC).isoformat()
        with _lock:
            sessions = self.load()
            chosen = (session_id or str(uuid.uuid4())).lower()
            existing = next((item for item in sessions if item.session_id == chosen), None)
            if existing is not None and existing.conversation_id != conversation_id:
                raise ValueError("that session id already belongs to another conversation")
            if existing is not None:
                updated = replace(existing, updated_at=now, name=name if name else existing.name)
                sessions = [updated if item.session_id == chosen else item for item in sessions]
            else:
                updated = CliSession(chosen, conversation_id, folder_key(folder), name or None, now, now)
                sessions.append(updated)
            self._write(sessions)
            return updated


__all__ = ["CliSession", "INDEX_FILE_NAME", "SessionIndex", "folder_key", "is_uuid"]
```

Create `src/agentos/cli_code/selection.py`:

```python
"""Which provider and model an ``orin code`` run uses.

``--model`` may be ``provider/model``, ``provider:model`` or a bare model id that exactly one enabled provider
offers; ``--provider`` names the provider explicitly. Without ``--model`` a resumed session keeps its conversation's
model, and a new one uses the ``model`` key of the person's own settings (the same key as Claude Code's), read the
same way. Nothing is guessed: an unknown or ambiguous model is an error that names what is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import CliRuntimeError

KNOWN_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openrouter", "opencode", "ollama", "omniroute")


class _Api(Protocol):
    def get(self, path: str, **params: str) -> object: ...


@dataclass(frozen=True, slots=True)
class Selection:
    provider: str
    model_id: str

    def as_request(self) -> dict[str, str]:
        return {"provider": self.provider, "model_id": self.model_id}


class ModelCatalog:
    """Enabled providers and their models, asked of the API at most once each per run."""

    def __init__(self, api: _Api) -> None:
        self._api = api
        self._enabled: dict[str, bool] = {}
        self._models: dict[str, frozenset[str]] = {}

    def enabled(self, provider: str) -> bool:
        if provider not in self._enabled:
            try:
                info = self._api.get(f"/v1/providers/{provider}")
            except CliRuntimeError:
                info = None
            self._enabled[provider] = bool(isinstance(info, dict) and info.get("enabled"))
        return self._enabled[provider]

    def models(self, provider: str) -> frozenset[str]:
        if provider not in self._models:
            try:
                body = self._api.get(f"/v1/providers/{provider}/models")
            except CliRuntimeError:
                body = None
            items = body.get("items") if isinstance(body, dict) else None
            self._models[provider] = frozenset(
                str(item.get("model_id")) for item in items or () if isinstance(item, dict) and item.get("model_id")
            )
        return self._models[provider]

    def offers(self, provider: str, model_id: str) -> bool:
        return self.enabled(provider) and model_id in self.models(provider)

    def enabled_providers(self) -> list[str]:
        return [name for name in KNOWN_PROVIDERS if self.enabled(name)]


def _explicit(catalog: ModelCatalog, value: str) -> Selection | None:
    for separator in ("/", ":"):
        head, found, tail = value.partition(separator)
        if found and head.lower() in KNOWN_PROVIDERS and tail and catalog.offers(head.lower(), tail):
            return Selection(head.lower(), tail)
    return None


def resolve_model(catalog: ModelCatalog, value: str, provider: str | None = None) -> Selection:
    value = value.strip()
    if not value:
        raise CliRuntimeError("--model is empty")
    if provider:
        name = provider.strip().lower()
        if name not in KNOWN_PROVIDERS:
            raise CliRuntimeError(f"unknown provider {provider!r}; Orin knows {', '.join(KNOWN_PROVIDERS)}")
        if not catalog.enabled(name):
            raise CliRuntimeError(f"the provider {name} is not set up in Orin (Settings > Providers)")
        if catalog.offers(name, value):
            return Selection(name, value)
        explicit = _explicit(catalog, value)
        if explicit is not None and explicit.provider == name:
            return explicit
        raise CliRuntimeError(f"{name} does not offer the model {value!r}")
    explicit = _explicit(catalog, value)
    if explicit is not None:
        return explicit
    matches = [name for name in catalog.enabled_providers() if value in catalog.models(name)]
    if len(matches) == 1:
        return Selection(matches[0], value)
    if len(matches) > 1:
        raise CliRuntimeError(f"the model {value!r} is offered by {', '.join(matches)}; choose one with --provider")
    enabled = catalog.enabled_providers()
    where = f"enabled providers: {', '.join(enabled)}" if enabled else "no provider is set up in Orin (Settings > Providers)"
    raise CliRuntimeError(f"no enabled provider offers the model {value!r} ({where})")


def choose_selection(
    catalog: ModelCatalog,
    *,
    model: str | None,
    provider: str | None,
    remembered: Selection | None,
    configured: str | None,
) -> Selection:
    """``--model`` (with ``--provider``), else the resumed conversation's model, else the ``model`` setting."""
    if model:
        return resolve_model(catalog, model, provider)
    if remembered is not None and (provider is None or provider.strip().lower() == remembered.provider):
        return remembered
    if configured:
        return resolve_model(catalog, configured, provider)
    raise CliRuntimeError("choose a model with --model provider/model (or set \"model\" in ~/.orin/settings.json)")


__all__ = ["KNOWN_PROVIDERS", "ModelCatalog", "Selection", "choose_selection", "resolve_model"]
```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code/test_sessions_and_selection.py`
Expected: all pass.

- [ ] **Step 5: Commit.**

```
feat(w6): orin code sessions and model selection
```

### Task 9: The protocol translator (and the SDK as a dev dependency)

`Translator` turns one turn's activity events into Claude Code's messages: one `assistant` message per content block (text, thinking, tool_use) sharing the model call's message id; `user` messages with `tool_result` blocks; with `--include-partial-messages`, `stream_event`s in Anthropic's streaming format (`message_start`, `content_block_*`, `message_delta`, `message_stop`) whatever the provider; `system` messages for init, compact boundaries and (with `--include-hook-events`) hook runs; and the `result` with Claude Code's subtypes (`success`, `error_max_turns`, `error_max_budget_usd`, `error_during_execution`). Tool names and inputs use Claude Code's public names (`Write`, `file_path`); each call gets a fresh `toolu_` id (Ollama numbers calls per response). Only the main agent's events become messages. The tests check every message with the official SDK's own parser, so `claude-agent-sdk==0.2.159` becomes a dev dependency now (it was planned for Batch F): built from its sdist (`[tool.uv] no-binary-package`), so no bundled Claude Code binary is downloaded (the platform wheels carry a ~100 MB one) and no test can run it.

**Files:**
- Create: `src/agentos/cli_code/protocol.py`
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/unit/cli_code/test_protocol.py`

**Interfaces:**
- Produces: `Translator(*, session_id, model, partial=False, hook_events=False)` with `init_message(*, cwd, tools, permission_mode, mcp_servers, slash_commands, version, output_style='default', agents=None)`, `feed(event) -> list[dict]`, `result(*, state, error_code, usage, duration_ms, duration_api_ms, structured_output=None, errors=None) -> list[dict]` (the closing assistant messages, then the result), `result_text`, `model_calls`, `denials`, `is_main_agent(event)`; `TurnUsage(input_tokens, output_tokens, cost_usd)`; `RESULT_SUBTYPES`.

- [ ] **Step 1: Dependency.** In `pyproject.toml` make these 2 replacement(s) (each old snippet occurs exactly once):

1. Replace

```toml
dev = [
    "pyinstaller>=6.11,<7",
```

with

```toml
dev = [
    # W6: the official SDK drives orin code in the conformance tests (built from its sdist: no bundled claude binary).
    "claude-agent-sdk==0.2.159",
    "pyinstaller>=6.11,<7",
```

2. Replace

```toml
    "pytest>=8.3,<9",
]

[tool.setuptools.packages.find]
```

with

```toml
    "pytest>=8.3,<9",
]

[tool.uv]
no-binary-package = ["claude-agent-sdk"]

[tool.setuptools.packages.find]
```

Then run `uv lock` and `uv sync --all-groups` (both must succeed; `git diff --stat uv.lock` shows only added packages and the `agentos` entry, nothing removed or downgraded — if anything else changes, stop and report).

- [ ] **Step 2: Write the failing tests.**

`tests/unit/cli_code/test_protocol.py`:

```python
"""W6 Task 9: Orin's activity events become Claude Code's headless messages, as the official SDK parses them."""
from __future__ import annotations

import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, UserMessage
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import StreamEvent, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

from agentos.cli_code.protocol import Translator, TurnUsage

MAIN = "agent:chat_x:main"


def _event(kind: str, agent: str = MAIN, **payload) -> dict:
    return {"event_type": kind, "agent_id": agent, "parent_agent_id": None, "payload": payload}


def _run(events: list[dict], *, partial: bool = False, state: str = "completed", error_code: str | None = None) -> list[dict]:
    translator = Translator(session_id="s-1", model="fake-coder:latest", partial=partial)
    out: list[dict] = []
    for event in events:
        out.extend(translator.feed(event))
    out.extend(translator.result(state=state, error_code=error_code, usage=TurnUsage(50, 13, 0.0), duration_ms=1200, duration_api_ms=900))
    for message in out:
        json.dumps(message)  # every message is plain JSON
    return out


def _parsed(messages: list[dict]) -> list[object]:
    return [parse_message(message) for message in messages]


TEXT_TURN = [_event("turn.started"), _event("assistant.delta", content="Hello "), _event("assistant.delta", content="from fake."), _event("turn.completed")]


def test_a_text_turn_is_one_assistant_message_and_a_result() -> None:
    parsed = _parsed(_run(TEXT_TURN))
    assert [type(item).__name__ for item in parsed] == ["AssistantMessage", "ResultMessage"]
    assistant, result = parsed
    assert isinstance(assistant, AssistantMessage) and assistant.model == "fake-coder:latest"
    assert assistant.content == [TextBlock(text="Hello from fake.")]
    assert isinstance(result, ResultMessage) and result.subtype == "success" and result.is_error is False
    assert result.result == "Hello from fake." and result.session_id == "s-1" and result.num_turns == 1
    assert result.usage["input_tokens"] == 50 and result.usage["output_tokens"] == 13
    assert result.model_usage["fake-coder:latest"]["outputTokens"] == 13


def test_partial_messages_follow_anthropics_stream_events() -> None:
    messages = _run(TEXT_TURN, partial=True)
    parsed = _parsed(messages)
    events = [item.event["type"] for item in parsed if isinstance(item, StreamEvent)]
    assert events == ["message_start", "content_block_start", "content_block_delta", "content_block_delta",
                      "content_block_stop", "message_delta", "message_stop"]
    deltas = [item.event["delta"]["text"] for item in parsed if isinstance(item, StreamEvent) and item.event["type"] == "content_block_delta"]
    assert deltas == ["Hello ", "from fake."]
    start = next(item for item in parsed if isinstance(item, StreamEvent))
    assistant = next(item for item in parsed if isinstance(item, AssistantMessage))
    assert start.event["message"]["id"] == assistant.message_id


def test_a_tool_round_is_tool_use_then_tool_result_then_a_new_model_call() -> None:
    events = [
        _event("assistant.delta", content="I will write it."),
        _event("tool.started", tool_name="write_file", invocation_id="tool-call:1", tool_input={"path": "a.txt", "content": "hi\n"}),
        _event("tool.finished", tool_name="write_file", invocation_id="tool-call:1", status="succeeded", tool_result="Wrote 3 bytes to a.txt."),
        _event("assistant.delta", content="Done."),
    ]
    parsed = _parsed(_run(events))
    kinds = [type(item).__name__ for item in parsed]
    assert kinds == ["AssistantMessage", "AssistantMessage", "UserMessage", "AssistantMessage", "ResultMessage"]
    text, tool_use, tool_result, done, result = parsed
    use = tool_use.content[0]
    assert isinstance(use, ToolUseBlock) and use.name == "Write" and use.input == {"file_path": "a.txt", "content": "hi\n"}
    assert text.message_id == tool_use.message_id != done.message_id
    block = tool_result.content[0]
    assert isinstance(block, ToolResultBlock) and block.tool_use_id == use.id and block.is_error is False
    assert block.content == "Wrote 3 bytes to a.txt."
    assert result.result == "Done." and result.num_turns == 2


def test_the_same_invocation_id_in_a_later_model_call_gets_a_new_tool_use_id() -> None:
    # Ollama numbers its calls per response ("tool-call:1" every time): the host still sees distinct ids.
    round_trip = [
        _event("tool.started", tool_name="read_file", invocation_id="tool-call:1", tool_input={"path": "a"}),
        _event("tool.finished", tool_name="read_file", invocation_id="tool-call:1", status="succeeded", tool_result="a"),
    ]
    parsed = _parsed(_run(round_trip + round_trip))
    ids = [item.content[0].id for item in parsed if isinstance(item, AssistantMessage)]
    results = [item.content[0].tool_use_id for item in parsed if isinstance(item, UserMessage)]
    assert len(set(ids)) == 2 and results == ids


def test_partial_tool_use_streams_its_input_as_json() -> None:
    events = [_event("tool.started", tool_name="run_command", invocation_id="c1", tool_input={"command": "ls"})]
    parsed = _parsed(_run(events, partial=True))
    stream = [item.event for item in parsed if isinstance(item, StreamEvent)]
    start = next(event for event in stream if event["type"] == "content_block_start")
    delta = next(event for event in stream if event["type"] == "content_block_delta")
    assert start["content_block"]["type"] == "tool_use" and start["content_block"]["name"] == "Bash"
    assert json.loads(delta["delta"]["partial_json"]) == {"command": "ls"}


def test_thinking_becomes_a_thinking_block() -> None:
    parsed = _parsed(_run([_event("agent.thought_delta", delta="Considering."), _event("assistant.delta", content="Answer.")]))
    assert isinstance(parsed[0].content[0], ThinkingBlock) and parsed[0].content[0].thinking == "Considering."
    assert parsed[1].content == [TextBlock(text="Answer.")] and parsed[0].message_id == parsed[1].message_id


def test_a_subagents_events_stay_inside_its_agent_call() -> None:
    sub = "agent:chat_x:sub-1"
    parsed = _parsed(_run([_event("assistant.delta", agent=sub, content="inner"), _event("assistant.delta", content="outer")]))
    texts = [block.text for item in parsed if isinstance(item, AssistantMessage) for block in item.content]
    assert texts == ["outer"]


@pytest.mark.parametrize(("error_code", "subtype"), [
    ("MAX_TURNS", "error_max_turns"), ("MAX_BUDGET_USD", "error_max_budget_usd"), ("PROVIDER_ERROR", "error_during_execution"),
])
def test_failed_turns_map_to_claude_codes_result_subtypes(error_code: str, subtype: str) -> None:
    result = _parsed(_run([_event("assistant.delta", content="partial")], state="failed", error_code=error_code))[-1]
    assert result.subtype == subtype and result.is_error is True and result.errors == [error_code]


def test_a_compaction_is_a_compact_boundary() -> None:
    translator = Translator(session_id="s", model="m")
    message = translator.feed(_event("context.compacted", trigger="manual", used_tokens=9000))[0]
    parsed = parse_message(message)
    assert isinstance(parsed, SystemMessage) and parsed.subtype == "compact_boundary"
    assert message["compact_metadata"] == {"trigger": "manual", "pre_tokens": 9000}


def test_a_denied_tool_is_a_permission_denial() -> None:
    events = [
        _event("tool.started", tool_name="write_file", invocation_id="c1", tool_input={"path": "a"}),
        _event("tool.finished", tool_name="write_file", invocation_id="c1", status="failed", error_code="CODE_PERMISSION_DENIED", tool_result="denied"),
    ]
    result = _parsed(_run(events))[-1]
    assert result.permission_denials and result.permission_denials[0]["tool_name"] == "Write"


def test_the_init_message() -> None:
    translator = Translator(session_id="s", model="ollama/m")
    message = translator.init_message(cwd="C:/w", tools=["Read", "Bash"], permission_mode="acceptEdits", mcp_servers=[],
                                      slash_commands=["compact"], version="0.9.3")
    parsed = parse_message(message)
    assert isinstance(parsed, SystemMessage) and parsed.subtype == "init"
    assert parsed.data["session_id"] == "s" and parsed.data["tools"] == ["Read", "Bash"] and parsed.data["model"] == "ollama/m"


def test_a_result_with_no_call_the_host_saw_is_ignored() -> None:
    # Orin's own bookkeeping (it settles the todo list at the end of a turn) is not a tool the model called.
    parsed = _parsed(_run([_event("tool.finished", tool_name="todo_write", invocation_id="auto_todo_settle_t", status="succeeded")]))
    assert [type(item).__name__ for item in parsed] == ["ResultMessage"]
```

- [ ] **Step 3: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_protocol.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.cli_code.protocol'` (after the dev dependency is installed).

- [ ] **Step 4: Implement.**

Create `src/agentos/cli_code/protocol.py`:

```python
"""Orin's activity events as Claude Code's headless messages (spec §9.3).

``Translator`` turns the conversation's activity events for one turn into the messages an SDK host reads:
``assistant`` (one per content block, sharing the model call's message id), ``user`` (tool results),
``stream_event`` (Anthropic's streaming events, whatever the provider), ``system`` (init, compact boundaries, hooks)
and the closing ``result``. Only the main agent's events become messages; a subagent's work reaches the host as the
result of its ``Agent`` tool call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentos.agentic.tools.public_names import arguments_out, public_name

RESULT_SUBTYPES: dict[str, str] = {"MAX_TURNS": "error_max_turns", "MAX_BUDGET_USD": "error_max_budget_usd"}
_MAIN_AGENT_SUFFIX = ":main"


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _new_tool_use_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def as_usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 0}, "service_tier": "standard",
        }


@dataclass
class _Block:
    kind: str  # "text" | "thinking"
    index: int
    parts: list[str] = field(default_factory=list)


class Translator:
    """Stateful, one per run: feed it a turn's events in order, then ``result(...)``."""

    def __init__(self, *, session_id: str, model: str, partial: bool = False, hook_events: bool = False,
                 new_uuid: Callable[[], str] = lambda: str(uuid.uuid4())) -> None:
        self.session_id = session_id
        self.model = model
        self.partial = partial
        self.hook_events = hook_events
        self._uuid = new_uuid
        self._message_id: str | None = None
        self._block: _Block | None = None
        self._next_index = 0
        self._open_tools: dict[str, list[tuple[str, str]]] = {}  # invocation id -> [(tool_use id, public name)]
        self._text_since_tools: list[str] = []
        self.model_calls = 0
        self.denials: list[dict[str, Any]] = []

    # -- envelopes ------------------------------------------------------------------------------------------------

    def _base(self, kind: str) -> dict[str, Any]:
        return {"type": kind, "session_id": self.session_id, "uuid": self._uuid()}

    def init_message(self, *, cwd: str, tools: list[str], permission_mode: str, mcp_servers: list[dict[str, str]],
                     slash_commands: list[str], version: str, output_style: str = "default",
                     agents: list[str] | None = None) -> dict[str, Any]:
        return {
            **self._base("system"), "subtype": "init", "cwd": cwd, "tools": tools, "mcp_servers": mcp_servers,
            "model": self.model, "permissionMode": permission_mode, "slash_commands": slash_commands,
            "apiKeySource": "none", "claude_code_version": version, "output_style": output_style,
            "agents": agents or [], "skills": [], "plugins": [],
        }

    def _stream(self, event: dict[str, Any]) -> dict[str, Any]:
        return {**self._base("stream_event"), "event": event, "parent_tool_use_id": None}

    def _assistant(self, block: dict[str, Any], stop_reason: str | None = None) -> dict[str, Any]:
        return {
            **self._base("assistant"),
            "message": {
                "id": self._message_id, "type": "message", "role": "assistant", "model": self.model,
                "content": [block], "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            "parent_tool_use_id": None,
        }

    # -- message and block boundaries -----------------------------------------------------------------------------

    def _start_message(self, out: list[dict[str, Any]]) -> None:
        if self._message_id is not None:
            return
        self._message_id = _new_message_id()
        self._next_index = 0
        self.model_calls += 1
        if self.partial:
            out.append(self._stream({"type": "message_start", "message": {
                "id": self._message_id, "type": "message", "role": "assistant", "model": self.model, "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0},
            }}))

    def _close_block(self, out: list[dict[str, Any]]) -> None:
        block = self._block
        if block is None:
            return
        self._block = None
        text = "".join(block.parts)
        if self.partial:
            out.append(self._stream({"type": "content_block_stop", "index": block.index}))
        if block.kind == "text":
            out.append(self._assistant({"type": "text", "text": text}))
        else:
            out.append(self._assistant({"type": "thinking", "thinking": text, "signature": ""}))

    def _end_message(self, out: list[dict[str, Any]], stop_reason: str) -> None:
        self._close_block(out)
        if self._message_id is None:
            return
        if self.partial:
            out.append(self._stream({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                     "usage": {"output_tokens": 0}}))
            out.append(self._stream({"type": "message_stop"}))
        self._message_id = None

    def _delta(self, out: list[dict[str, Any]], kind: str, text: str) -> None:
        if not text:
            return
        if self._open_tools:  # text after tool calls belongs to the next model call
            self._end_message(out, "tool_use")
        self._start_message(out)
        if self._block is not None and self._block.kind != kind:
            self._close_block(out)
        if self._block is None:
            self._block = _Block(kind, self._next_index)
            self._next_index += 1
            if self.partial:
                start = {"type": "text", "text": ""} if kind == "text" else {"type": "thinking", "thinking": "", "signature": ""}
                out.append(self._stream({"type": "content_block_start", "index": self._block.index, "content_block": start}))
        self._block.parts.append(text)
        if kind == "text":
            self._text_since_tools.append(text)
        if self.partial:
            delta = {"type": "text_delta", "text": text} if kind == "text" else {"type": "thinking_delta", "thinking": text}
            out.append(self._stream({"type": "content_block_delta", "index": self._block.index, "delta": delta}))

    # -- events ---------------------------------------------------------------------------------------------------

    @staticmethod
    def is_main_agent(event: Mapping[str, Any]) -> bool:
        agent = str(event.get("agent_id") or "")
        return not event.get("parent_agent_id") and (not agent or agent.endswith(_MAIN_AGENT_SUFFIX))

    def feed(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        kind = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if kind == "context.compacted":
            out.append({**self._base("system"), "subtype": "compact_boundary", "compact_metadata": {
                "trigger": "manual" if payload.get("trigger") == "manual" else "auto",
                "pre_tokens": int(payload.get("pre_tokens") or payload.get("used_tokens") or 0),
            }})
            return out
        if kind == "plugin.hook_executed" and self.hook_events:
            out.append({**self._base("system"), "subtype": "hook_response",
                        "hook_name": str(payload.get("hook_name") or payload.get("name") or ""),
                        "hook_event": str(payload.get("hook_event") or payload.get("event") or ""),
                        "output": str(payload.get("output") or ""), "exit_code": payload.get("exit_code")})
            return out
        if not self.is_main_agent(event):
            return out
        if kind == "assistant.delta":
            self._delta(out, "text", str(payload.get("content") or ""))
        elif kind == "agent.thought_delta":
            self._delta(out, "thinking", str(payload.get("delta") or payload.get("content") or ""))
        elif kind == "tool.started":
            self._tool_use(out, payload)
        elif kind == "tool.finished":
            self._tool_result(out, payload)
        return out

    def _tool_use(self, out: list[dict[str, Any]], payload: Mapping[str, Any]) -> None:
        internal = str(payload.get("tool_name") or "tool")
        invocation = str(payload.get("invocation_id") or "")
        self._start_message(out)
        self._close_block(out)
        tool_use_id = _new_tool_use_id()
        name = public_name(internal)
        raw_input = payload.get("tool_input")
        tool_input = arguments_out(internal, dict(raw_input)) if isinstance(raw_input, Mapping) else {}
        block = {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
        if self.partial:
            index = self._next_index
            self._next_index += 1
            out.append(self._stream({"type": "content_block_start", "index": index,
                                     "content_block": {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}}))
            out.append(self._stream({"type": "content_block_delta", "index": index,
                                     "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input, ensure_ascii=False)}}))
            out.append(self._stream({"type": "content_block_stop", "index": index}))
        out.append(self._assistant(block))
        self._open_tools.setdefault(invocation, []).append((tool_use_id, name))
        self._text_since_tools = []

    def _tool_result(self, out: list[dict[str, Any]], payload: Mapping[str, Any]) -> None:
        invocation = str(payload.get("invocation_id") or "")
        pending = self._open_tools.get(invocation)
        if not pending:
            return  # a result with no call the host saw (Orin's own bookkeeping, e.g. todo settling)
        tool_use_id, name = pending.pop(0)
        if not pending:
            del self._open_tools[invocation]
        status = str(payload.get("status") or "succeeded")
        is_error = status != "succeeded"
        content = str(payload.get("tool_result") if payload.get("tool_result") is not None else payload.get("summary") or "")
        if self._message_id is not None and not self._open_tools:
            self._end_message(out, "tool_use")
        out.append({
            **self._base("user"),
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error},
            ]},
            "parent_tool_use_id": None,
            "tool_use_result": content,
        })
        if is_error and str(payload.get("error_code") or "") == "CODE_PERMISSION_DENIED":
            self.denials.append({"tool_name": name, "tool_use_id": tool_use_id, "tool_input": {}})

    # -- end of the turn ------------------------------------------------------------------------------------------

    @property
    def result_text(self) -> str:
        return "".join(self._text_since_tools).strip()

    def result(self, *, state: str, error_code: str | None, usage: TurnUsage, duration_ms: int, duration_api_ms: int,
               structured_output: Any = None, errors: list[str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        self._end_message(out, "end_turn")
        if state == "completed":
            subtype = "success"
        else:
            subtype = RESULT_SUBTYPES.get(str(error_code or ""), "error_during_execution")
        message: dict[str, Any] = {
            **self._base("result"), "subtype": subtype, "is_error": subtype != "success",
            "duration_ms": int(duration_ms), "duration_api_ms": int(duration_api_ms), "num_turns": max(1, self.model_calls),
            "result": self.result_text, "stop_reason": "end_turn" if subtype == "success" else None,
            "total_cost_usd": float(usage.cost_usd), "usage": usage.as_usage(),
            "modelUsage": {self.model: {
                "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": float(usage.cost_usd),
            }},
            "permission_denials": list(self.denials),
        }
        if structured_output is not None:
            message["structured_output"] = structured_output
        if subtype != "success":
            message["errors"] = list(errors or ([str(error_code)] if error_code else ["the turn did not complete"]))
        out.append(message)
        self.model_calls = 0
        self.denials = []
        self._text_since_tools = []
        self._open_tools = {}
        return out


__all__ = ["RESULT_SUBTYPES", "Translator", "TurnUsage"]
```

- [ ] **Step 5: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code/test_protocol.py`
Expected: all pass.

- [ ] **Step 6: Commit.**

```
feat(w6): Orin's activity events as Claude Code's headless messages

claude-agent-sdk 0.2.159 is a dev dependency, built from its sdist (no bundled
binary): the tests read every message with the SDK's own parser.
```

### Task 10: `orin code -p` and stream-json runs

`HeadlessRun` runs turns on the local Orin and speaks the protocol: it resolves the session and the model, builds the turn's options from the flags (`--settings` JSON or file under the explicit flags; `--allowedTools`/`--disallowedTools`/`--add-dir` as permissions; `--max-turns`, `--max-budget-usd`, `--fallback-model`, `--effort` (or a thinking budget mapped to the smallest effort that holds it), `--autocompact`, `--system-prompt-snapshot` as settings; the prompt flags and files), sends **the permission mode on every run, Claude Code's `default` (ask) unless a flag says otherwise** (Orin Code's own default, acceptEdits, would let a write through that nobody approved), creates the conversation in the current folder (`code_mode: code`) or sends to the resumed one, and follows the conversation's SSE stream on a reader thread. Output: `text` prints the answer, `json` prints the result, `stream-json` prints every message. With `--input-format stream-json` (an SDK host) stdin is read on its own thread: user messages become turns one after the other, an `initialize` control request is answered (the rest of the control protocol is Batch C and answered with an error until then), and the run ends with exit code 0 when stdin closes. Exit codes: 0, or 1 for an error result in a single-prompt run and for every error. Until Batch C no permission prompt can reach the host, so every turn is non-interactive. Flags without support yet are refused before the runtime is touched (`UNSUPPORTED`). The live tests run the real `orin-code` executable against the live runtime of Task 5.

**Files:**
- Create: `src/agentos/cli_code/turn_options.py`, `src/agentos/cli_code/headless.py`
- Modify: `src/agentos/cli_code/main.py`
- Test: `tests/unit/cli_code/test_turn_options_builder.py`, `tests/integration/cli_code/test_headless.py`

**Interfaces:**
- Consumes: Tasks 4–9.
- Produces: `build_turn_options(options, cwd, *, host_answers_prompts) -> CliTurnOptions`, `permission_mode_for(options) -> str`, `settings_flag(value, cwd)`; `HeadlessRun(options, *, client, data_dir, cwd, stdin, stdout, stderr, clock=..., configured_model=None)` with `run() -> int`, `answer(prompt)`, `run_turn(prompt, *, final=True)`; `refuse_unsupported(options)`, `UNSUPPORTED`, `MAX_PROMPT_CHARS = 16000`; `main.run_headless` now drives `HeadlessRun` (stdin/stdout switched to UTF-8).

- [ ] **Step 1: Write the failing tests.**

`tests/unit/cli_code/test_turn_options_builder.py`:

```python
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


@pytest.mark.parametrize("argv", [["--fork-session"], ["--mcp-config", "{}"], ["--agents", "{}"], ["--bg"], ["--permission-prompt-tool", "mcp__p__ask"], ["--thinking-display", "summarized"]])
def test_flags_without_support_yet_are_refused(argv: list[str]) -> None:
    with pytest.raises(CliUsageError):
        refuse_unsupported(_options(*argv))
```

- [ ] **Step 2: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_turn_options_builder.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.cli_code.turn_options'`.

- [ ] **Step 3: Implement.**

Create `src/agentos/cli_code/turn_options.py`:

```python
"""From ``orin code`` flags to the options a turn carries (``CliTurnOptions``) and the conversation's permission mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.code_mode.cli_options import CliTurnOptions
from agentos.code_mode.models import normalize_permission_mode

from .args import CliUsageError, CodeOptions

# Effort budgets (agentos.agentic.effort.THINKING_BUDGET), smallest first: a thinking budget becomes the smallest
# effort level that holds it.
_EFFORT_BY_BUDGET: tuple[tuple[int, str], ...] = ((2_048, "low"), (6_000, "medium"), (12_000, "high"), (20_000, "xhigh"), (28_000, "max"))
_MAX_FILE_BYTES = 65536


def _read_text_file(path: str, cwd: Path, flag: str) -> str:
    target = Path(path).expanduser()
    target = target if target.is_absolute() else cwd / target
    try:
        data = target.read_bytes()
    except OSError as error:
        raise CliUsageError(f"{flag}: {target} could not be read ({error.strerror or error})") from error
    if len(data) > _MAX_FILE_BYTES:
        raise CliUsageError(f"{flag}: {target} is larger than {_MAX_FILE_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def settings_flag(value: str | None, cwd: Path) -> dict[str, Any]:
    """``--settings``: a JSON object, or the path of a JSON file."""
    if value is None or not value.strip():
        return {}
    text = value.strip()
    if not text.startswith("{"):
        text = _read_text_file(text, cwd, "--settings")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CliUsageError("--settings is neither a JSON object nor a JSON file") from error
    if not isinstance(data, dict):
        raise CliUsageError("--settings must be a JSON object")
    return data


def _effort(options: CodeOptions) -> str | None:
    if options.effort:
        return options.effort
    if options.thinking == "disabled":
        return None
    budget = options.max_thinking_tokens
    if budget is not None and budget > 0:
        return next((level for limit, level in _EFFORT_BY_BUDGET if budget <= limit), "max")
    return None


def _autocompact(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"off", "false", "0", "no"}:
        return False
    if text in {"auto", "on", "true"}:
        return "auto"
    if text.isdigit() and int(text) > 0:
        return int(text)
    raise CliUsageError("--autocompact takes auto, off or a token count")


def _extend(layer: dict[str, Any], dotted: str, items: list[str]) -> None:
    if not items:
        return
    head, _, tail = dotted.partition(".")
    section = layer.setdefault(head, {})
    if not isinstance(section, dict):
        section = layer[head] = {}
    current = section.setdefault(tail, [])
    if not isinstance(current, list):
        current = section[tail] = []
    for item in items:
        if item not in current:
            current.append(item)


def build_turn_options(options: CodeOptions, cwd: Path, *, host_answers_prompts: bool) -> CliTurnOptions:
    """The "cli" settings layer (``--settings`` first, the explicit flags over it) and the headless switches."""
    layer = settings_flag(options.settings, cwd)
    _extend(layer, "permissions.allow", options.allowed_tools)
    _extend(layer, "permissions.deny", options.disallowed_tools)
    _extend(layer, "permissions.additionalDirectories", [str((cwd / Path(item).expanduser()).resolve()) for item in options.add_dirs])
    if options.max_turns is not None:
        layer["maxTurns"] = options.max_turns
    if options.max_budget_usd is not None:
        layer["maxBudgetUsd"] = options.max_budget_usd
    if options.fallback_model:
        layer["fallbackModel"] = options.fallback_model
    effort = _effort(options)
    if effort:
        layer["effortLevel"] = effort
    compact = _autocompact(options.autocompact)
    if compact is not None:
        layer["autoCompact"] = compact
    if options.system_prompt_snapshot is not None:
        layer["systemPromptSnapshot"] = options.system_prompt_snapshot == "on"
    system_prompt = options.system_prompt
    if options.system_prompt_file:
        system_prompt = _read_text_file(options.system_prompt_file, cwd, "--system-prompt-file")
    append = options.append_system_prompt
    if options.append_system_prompt_file:
        appended = _read_text_file(options.append_system_prompt_file, cwd, "--append-system-prompt-file")
        append = f"{append}\n\n{appended}" if append else appended
    if options.json_schema:
        from .structured import load_schema, schema_instructions

        instructions = schema_instructions(load_schema(options.json_schema))
        append = f"{append}\n\n{instructions}" if append else instructions
    none = options.permission_prompts == "none" or not host_answers_prompts
    try:
        return CliTurnOptions(
            settings=layer, setting_sources=options.setting_sources, system_prompt=system_prompt,
            append_system_prompt=append, tools=options.tools, permission_prompts="none" if none else "host",
            transcript=True, headless=True, trust_workspace=options.trust,
        )
    except ValueError as error:
        raise CliUsageError(f"the run's options are not valid: {error}") from error


def permission_mode_for(options: CodeOptions) -> str:
    """Orin's name for the run's mode. Claude Code's default is ``default`` (ask), not Orin Code's acceptEdits: every
    run sends its mode, so a resumed conversation never keeps a more permissive one by accident."""
    if options.dangerously_skip_permissions:
        return "full_autonomy"
    return normalize_permission_mode(options.permission_mode or "default").value


__all__ = ["build_turn_options", "permission_mode_for", "settings_flag"]
```

Create `src/agentos/cli_code/headless.py`:

```python
"""``orin code -p`` and the SDK's stream-json mode: run turns on the local Orin and speak Claude Code's protocol.

One process may run several turns (stream-json input sends one user message per turn). Three things happen at once —
stdin (user messages and control requests), the conversation's event stream, and the current turn — so stdin and the
event stream each have a reader thread feeding one inbox, and only the main thread writes to stdout.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from agentos.code_mode.models import public_permission_mode
from agentos.version import __version__

from .args import EXIT_ERROR, EXIT_OK, CliUsageError, CodeOptions
from .errors import CliRuntimeError
from .protocol import Translator, TurnUsage
from .runtime import OrinClient
from .selection import ModelCatalog, Selection, choose_selection
from .sessions import CliSession, SessionIndex, is_uuid
from .turn_options import build_turn_options, permission_mode_for

MAX_PROMPT_CHARS = 16000  # what the API accepts for one message
_END_EVENTS = {"turn.completed", "turn.failed", "turn.waiting_user"}

# Flags this build parses and refuses, each with where it stands; never silently ignored.
UNSUPPORTED: dict[str, str] = {
    "mcp_configs": "--mcp-config", "strict_mcp_config": "--strict-mcp-config", "agents": "--agents", "agent": "--agent",
    "plugin_dirs": "--plugin-dir", "worktree": "--worktree", "background": "--bg", "fork_session": "--fork-session",
    "bare": "--bare", "safe_mode": "--safe-mode", "restricted": "--restricted", "init": "--init",
    "maintenance": "--maintenance", "desktop": "--desktop", "resume_session_at": "--resume-session-at",
    "resume_drops_turn": "--resume-drops-turn", "session_mirror": "--session-mirror", "task_budget": "--task-budget",
    "prompt_suggestions": "--prompt-suggestions", "exclude_dynamic_system_prompt_sections": "--exclude-dynamic-system-prompt-sections",
    "thinking_display": "--thinking-display",
}


def refuse_unsupported(options: CodeOptions) -> None:
    flags = [flag for name, flag in UNSUPPORTED.items() if getattr(options, name) not in (None, False, [], "")]
    if flags:
        raise CliUsageError(f"not supported by orin code yet: {', '.join(flags)}")
    if options.permission_prompt_tool not in (None, "stdio"):
        raise CliUsageError("--permission-prompt-tool accepts only stdio (an SDK host's can_use_tool callback)")
    if options.permission_prompt_tool == "stdio" and options.input_format != "stream-json":
        raise CliUsageError("--permission-prompt-tool stdio needs --input-format stream-json")


def _prompt_text(content: object) -> str:
    """A user message's text; images and documents are refused rather than dropped."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict):
                raise CliRuntimeError(f"orin code does not take {block.get('type')!r} blocks in a prompt yet")
        return "\n".join(parts)
    raise CliRuntimeError("a user message needs text content")


class _Output:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout
        self._lock = threading.Lock()

    def message(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._stdout.flush()

    def text(self, value: str) -> None:
        with self._lock:
            self._stdout.write(value)
            self._stdout.flush()


class HeadlessRun:
    def __init__(self, options: CodeOptions, *, client: OrinClient, data_dir: Path, cwd: Path, stdin: TextIO,
                 stdout: TextIO, stderr: TextIO, clock: Callable[[], float] = time.monotonic,
                 configured_model: str | None = None) -> None:
        self.options = options
        self.client = client
        self.cwd = cwd
        self.stdin = stdin
        self.stderr = stderr
        self.out = _Output(stdout)
        self.clock = clock
        self.index = SessionIndex(data_dir)
        self.catalog = ModelCatalog(client)
        self.configured_model = configured_model
        self.inbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stream_json = options.output_format == "stream-json"
        self.session: CliSession | None = None
        self.conversation_id: str | None = None
        self.selection: Selection | None = None
        self.translator: Translator | None = None
        self._events_started = False
        self._stop = threading.Event()
        self._stdin_closed = False
        self._pending_prompts: list[str] = []
        self._init_sent = False
        self._cursor = "0"
        self._session_id = ""
        self._debug = self._debug_sink()

    # -- plumbing -------------------------------------------------------------------------------------------------

    def _debug_sink(self) -> Callable[[str], None]:
        if self.options.debug is None and not self.options.debug_file:
            return lambda line: None
        target = self.options.debug_file

        def write(line: str) -> None:
            text = f"[orin code] {line}\n"
            if target:
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(text)
            else:
                self.stderr.write(text)
                self.stderr.flush()

        return write

    def _read_stdin(self) -> None:
        for raw in self.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                self.inbox.put(("bad_input", line[:200]))
                continue
            self.inbox.put(("stdin", item))
        self.inbox.put(("stdin_closed", None))

    def _follow_events(self, conversation_id: str, after: str) -> None:
        try:
            for event in self.client.events(conversation_id, after=after, stop=self._stop.is_set):
                self.inbox.put(("event", event))
        except Exception as error:  # noqa: BLE001 - after the run ends, closing the client may break the read
            if not self._stop.is_set():
                self.inbox.put(("stream_error", str(error)))

    def _start_events(self) -> None:
        if self._events_started or self.conversation_id is None:
            return
        self._events_started = True
        threading.Thread(target=self._follow_events, args=(self.conversation_id, self._cursor), daemon=True).start()

    # -- sessions and turns ---------------------------------------------------------------------------------------

    def _resolve_session(self) -> None:
        options = self.options
        if options.session_id is not None and not is_uuid(options.session_id):
            raise CliUsageError("--session-id must be a UUID")
        if options.continue_session:
            self.session = self.index.latest_for(self.cwd)
            if self.session is None:
                raise CliRuntimeError("no orin code session to continue in this folder")
        elif options.resume is not None:
            if not options.resume:
                raise CliUsageError("-r needs a session id or name in a headless run")
            self.session = self.index.find(options.resume)
            if self.session is None:
                raise CliRuntimeError(f"no orin code session {options.resume!r}")
        elif options.session_id is not None and self.index.find(options.session_id) is not None:
            raise CliRuntimeError(f"the session id {options.session_id} is already in use")
        remembered: Selection | None = None
        if self.session is not None:
            conversation = self.client.get(f"/v1/conversations/{self.session.conversation_id}")
            self.conversation_id = self.session.conversation_id
            self._cursor = str(conversation.get("activity_cursor") or "0") if isinstance(conversation, dict) else "0"
            if isinstance(conversation, dict) and conversation.get("provider") and conversation.get("model_id"):
                remembered = Selection(str(conversation["provider"]), str(conversation["model_id"]))
        self.selection = choose_selection(self.catalog, model=options.model, provider=options.provider,
                                          remembered=remembered, configured=self.configured_model)
        session_id = self.session.session_id if self.session is not None else (options.session_id or "").lower() or None
        self._session_id = session_id or str(uuid.uuid4())
        self.translator = Translator(session_id=self._session_id, model=self.selection.model_id,
                                     partial=options.include_partial_messages, hook_events=options.include_hook_events)

    def _send(self, prompt: str) -> str:
        if len(prompt) > MAX_PROMPT_CHARS:
            raise CliRuntimeError(f"the prompt has {len(prompt)} characters; Orin takes at most {MAX_PROMPT_CHARS} per message")
        # Nobody answers a permission prompt in this build yet (can_use_tool arrives with the control protocol), so
        # whatever would ask is refused at once rather than waiting for an answer that cannot come.
        host = False
        turn_options = build_turn_options(self.options, self.cwd, host_answers_prompts=host).model_dump()
        mode = permission_mode_for(self.options)
        body: dict[str, Any] = {"message": prompt, "selection": self.selection.as_request(), "code_mode": "code",
                                "cli": turn_options, "permission_mode": mode}
        if self.conversation_id is None:
            created = self.client.post("/v1/conversations", {**body, "workspace_path": str(self.cwd)})
            self.conversation_id = str(created["conversation_id"])
            turn_id = str(created["turn_id"])
        else:
            sent = self.client.post(f"/v1/conversations/{self.conversation_id}/messages", body)
            turn_id = str(sent["turn_id"])
        if not self.options.no_session_persistence:
            self.session = self.index.record(conversation_id=self.conversation_id, folder=self.cwd,
                                             session_id=self._session_id, name=self.options.name)
        self._debug(f"turn {turn_id} queued in {self.conversation_id}")
        return turn_id

    def _usage(self) -> TurnUsage:
        if self.conversation_id is None:
            return TurnUsage()
        try:
            overview = self.client.get(f"/v1/conversations/{self.conversation_id}/overview")
        except CliRuntimeError:
            return TurnUsage()
        usage = overview.get("token_usage") if isinstance(overview, dict) else None
        if not isinstance(usage, dict):
            return TurnUsage()
        return TurnUsage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), float(usage.get("estimated_cost_usd") or 0.0))

    def _send_init(self) -> None:
        if self._init_sent or not self.stream_json:
            return
        self._init_sent = True
        from agentos.agentic.cli_turn import tool_selection
        from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
        from agentos.agentic.tools.public_names import public_name

        turn_options = build_turn_options(self.options, self.cwd, host_answers_prompts=False)
        offered, withheld = tool_selection(turn_options, CODE_MODE_CORE_TOOLS)
        tools = sorted(public_name(name) for name in CODE_MODE_CORE_TOOLS if (offered is None or name in offered) and name not in withheld)
        mode = permission_mode_for(self.options)
        self.out.message(self.translator.init_message(
            cwd=str(self.cwd), tools=tools, permission_mode=public_permission_mode(mode), mcp_servers=[],
            slash_commands=[], version=__version__,
        ))

    def answer(self, prompt: str) -> dict[str, Any]:
        """One prompt's answer: a turn, or with ``--json-schema`` as many follow-ups as the answer needs (W3-R25)."""
        if not self.options.json_schema:
            return self.run_turn(prompt)
        from .structured import MAX_STRUCTURED_RETRIES, load_schema, problem, retry_prompt

        schema = load_schema(self.options.json_schema)
        started = self.clock()
        totals, model_calls, text = TurnUsage(), 0, prompt
        for attempt in range(MAX_STRUCTURED_RETRIES + 1):
            result = self.run_turn(text, final=False)
            totals = TurnUsage(totals.input_tokens + result["usage"]["input_tokens"], totals.output_tokens + result["usage"]["output_tokens"],
                               totals.cost_usd + float(result["total_cost_usd"]))
            model_calls += int(result["num_turns"])
            if result["is_error"]:
                break
            value, issue = problem(schema, str(result.get("result") or ""))
            if issue is None:
                result["structured_output"] = value
                break
            if attempt == MAX_STRUCTURED_RETRIES:
                result.update({"subtype": "error_max_structured_output_retries", "is_error": True, "errors": [issue], "stop_reason": None})
                break
            text = retry_prompt(issue)
        elapsed = int((self.clock() - started) * 1000)
        result.update({"num_turns": model_calls, "duration_ms": elapsed, "duration_api_ms": elapsed, "usage": totals.as_usage(),
                       "total_cost_usd": totals.cost_usd})
        result["modelUsage"] = {self.translator.model: {
            "inputTokens": totals.input_tokens, "outputTokens": totals.output_tokens, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": totals.cost_usd,
        }}
        if self.stream_json:
            self.out.message(result)
        return result

    def run_turn(self, prompt: str, *, final: bool = True) -> dict[str, Any]:
        """One turn, start to result; returns the result message (written to stdout only when ``final``)."""
        started = self.clock()
        before = self._usage()
        self._send_init()
        if self.options.replay_user_messages and self.stream_json:
            self.out.message({"type": "user", "session_id": self._session_id, "uuid": str(uuid.uuid4()),
                              "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None})
        turn_id = self._send(prompt)
        self._start_events()
        state, error_code = "failed", None
        while True:
            kind, item = self.inbox.get()
            if kind == "event":
                if str(item.get("turn_id") or "") != turn_id:
                    continue
                for message in self.translator.feed(item):
                    if self.stream_json:
                        self.out.message(message)
                event_type = str(item.get("event_type") or "")
                if event_type in _END_EVENTS:
                    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                    state = "completed" if event_type == "turn.completed" else str(payload.get("state") or "failed")
                    if event_type == "turn.waiting_user":
                        state = "completed"  # an approval Orin waits for ends the headless turn; the person resumes it
                    error_code = payload.get("error_code")
                    break
            elif kind == "stream_error":
                raise CliRuntimeError(str(item))
            else:
                self._handle_input(kind, item)
        after = self._usage()
        usage = TurnUsage(max(0, after.input_tokens - before.input_tokens), max(0, after.output_tokens - before.output_tokens),
                          max(0.0, after.cost_usd - before.cost_usd))
        elapsed = int((self.clock() - started) * 1000)
        closing = self.translator.result(state=state, error_code=str(error_code) if error_code else None, usage=usage,
                                         duration_ms=elapsed, duration_api_ms=elapsed)
        if self.stream_json:
            for message in closing if final else closing[:-1]:  # the last open block's assistant message, then the result
                self.out.message(message)
        return closing[-1]

    # -- stdin ----------------------------------------------------------------------------------------------------

    def _handle_input(self, kind: str, item: Any) -> None:
        if kind == "stdin_closed":
            self._stdin_closed = True
        elif kind == "bad_input":
            self.stderr.write(f"Error: a stdin line is not JSON: {item}\n")
        elif kind == "stdin" and isinstance(item, dict):
            if item.get("type") == "control_request":
                self._control(item)
            elif item.get("type") == "user":
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                self._pending_prompts.append(_prompt_text(message.get("content")))
            elif item.get("type") == "control_response":
                pass  # answers to requests this build does not send yet

    def _control(self, request: dict[str, Any]) -> None:
        request_id = str(request.get("request_id") or "")
        body = request.get("request") if isinstance(request.get("request"), dict) else {}
        subtype = str(body.get("subtype") or "")
        if subtype == "initialize":
            response = {"commands": [], "agents": [], "output_style": "default", "available_output_styles": ["default"],
                        "models": [], "account": {}, "pid": os.getpid()}
            self.out.message({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": response}})
            return
        self.out.message({"type": "control_response", "response": {
            "subtype": "error", "request_id": request_id, "error": f"orin code does not handle {subtype or 'this'} requests yet",
        }})

    # -- entry ----------------------------------------------------------------------------------------------------

    def run(self) -> int:
        refuse_unsupported(self.options)
        self._resolve_session()
        try:
            if self.options.input_format == "stream-json":
                return self._run_stream_input()
            prompt = self.options.prompt if self.options.prompt is not None else self.stdin.read()
            if not prompt.strip():
                raise CliUsageError("no prompt: pass one after -p or on stdin")
            result = self.answer(prompt)
            if self.options.output_format == "json":
                self.out.message(result)
            elif self.options.output_format == "text":
                if result["is_error"]:
                    self.stderr.write(f"Error: {'; '.join(result.get('errors') or [result['subtype']])}\n")
                else:
                    self.out.text(str(result.get("result") or "") + "\n")
            return EXIT_ERROR if result["is_error"] else EXIT_OK
        finally:
            self._stop.set()

    def _run_stream_input(self) -> int:
        threading.Thread(target=self._read_stdin, daemon=True).start()
        if self.options.prompt:
            self._pending_prompts.append(self.options.prompt)
        while True:
            while self._pending_prompts:
                self.answer(self._pending_prompts.pop(0))
            if self._stdin_closed:
                return EXIT_OK
            kind, item = self.inbox.get()
            if kind != "event":
                self._handle_input(kind, item)


__all__ = ["HeadlessRun", "MAX_PROMPT_CHARS", "UNSUPPORTED", "refuse_unsupported"]
```

In `src/agentos/cli_code/main.py` make these 1 replacement(s) (each old snippet occurs exactly once):

1. Replace

```python

def run_headless(options: CodeOptions, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError("headless runs are not available in this build yet")
```

with

```python

def _utf8(*streams: TextIO) -> None:
    # A Windows pipe would be cp1252: the protocol is UTF-8 JSON lines, so the real streams are switched to it.
    for stream in streams:
        if stream in (sys.stdin, sys.stdout, sys.stderr) and hasattr(stream, "reconfigure"):
            if stream is sys.stdin:
                stream.reconfigure(encoding="utf-8")
            else:
                stream.reconfigure(encoding="utf-8", newline="\n")


def _configured_model() -> str | None:
    """The person's own model setting (user and managed layers only: a repository never picks the model)."""
    try:
        from agentos.settings import load_effective_settings

        value = load_effective_settings(None, sources=("user",)).get("model")
    except Exception:  # noqa: BLE001 - unreadable settings mean no configured model
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def run_headless(options: CodeOptions, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    from pathlib import Path

    from agentos.installation import orin_paths

    from .headless import HeadlessRun, refuse_unsupported
    from .runtime import connect_local

    refuse_unsupported(options)  # before anything starts
    _utf8(stdin, stdout, stderr)
    paths = orin_paths()
    client = connect_local(paths)
    try:
        return HeadlessRun(options, client=client, data_dir=paths.data, cwd=Path.cwd().resolve(), stdin=stdin,
                           stdout=stdout, stderr=stderr, configured_model=_configured_model()).run()
    finally:
        client.close()
```


- [ ] **Step 4: Add the live tests** — create `tests/integration/cli_code/test_headless.py`:

```python
"""W6 Task 10: `orin code -p` and stream-json against a real local runtime (fake Ollama)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .live_runtime import LiveRuntime

MODEL = ["--model", "ollama/fake-coder:latest"]


def orin_code(runtime: LiveRuntime, cwd: Path, *args: str, stdin: str | None = None, env: dict | None = None,
              timeout: float = 180) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).parent / ("orin-code.exe" if os.name == "nt" else "orin-code")
    return subprocess.run([str(executable), *args], cwd=str(cwd), env=env or runtime.env, input=stdin if stdin is not None else "",
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def _lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_print_mode_answers_with_text(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("text"), "-p", "say hello", *MODEL)
    assert (done.returncode, done.stdout, done.stderr) == (0, "Hello from fake.\n", "")


def test_json_output_is_one_result(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("json"), "-p", "say hello", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    (result,) = _lines(done.stdout)
    assert result["type"] == "result" and result["subtype"] == "success" and result["result"] == "Hello from fake."
    assert uuid.UUID(result["session_id"]) and result["usage"]["output_tokens"] > 0


def test_stream_json_shows_init_assistant_and_result(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("stream"), "-p", "say hello", "--output-format", "stream-json", "--verbose", *MODEL)
    assert done.returncode == 0, done.stderr
    messages = _lines(done.stdout)
    assert [item["type"] for item in messages] == ["system", "assistant", "result"]
    init, assistant, result = messages
    assert init["subtype"] == "init" and "Read" in init["tools"] and init["permissionMode"] == "default"
    assert assistant["message"]["content"] == [{"type": "text", "text": "Hello from fake."}]
    assert init["session_id"] == assistant["session_id"] == result["session_id"]


def test_an_allowed_write_runs_and_is_shown_as_tool_use_and_result(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("write")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:made.txt]]", "--permission-mode", "acceptEdits",
                     "--output-format", "stream-json", "--verbose", *MODEL)
    assert done.returncode == 0, done.stderr
    assert (workspace / "made.txt").read_text(encoding="utf-8") == "hi\n"
    messages = _lines(done.stdout)
    use = next(block for item in messages if item["type"] == "assistant" for block in item["message"]["content"] if block["type"] == "tool_use")
    assert use["name"] == "Write" and use["input"]["file_path"] == "made.txt"
    result_block = next(block for item in messages if item["type"] == "user" for block in item["message"]["content"])
    assert result_block["tool_use_id"] == use["id"] and result_block["is_error"] is False
    assert messages[-1]["result"] == "Done." and messages[-1]["num_turns"] == 2


def test_without_a_host_a_write_that_would_ask_is_refused(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("refused")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:nope.txt]]", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    (result,) = _lines(done.stdout)
    assert not (workspace / "nope.txt").exists()
    assert result["permission_denials"] and result["permission_denials"][0]["tool_name"] == "Write"


def test_a_disallowed_tool_is_refused_even_in_accept_edits(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("disallowed")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:no.txt]]", "--permission-mode", "acceptEdits",
                     "--disallowedTools", "Write", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    assert not (workspace / "no.txt").exists()


def test_max_turns_ends_the_run_with_its_result_subtype(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("max-turns"), "-p", "please [[write:t.txt]]", "--permission-mode",
                     "acceptEdits", "--max-turns", "1", "--output-format", "json", *MODEL)
    (result,) = _lines(done.stdout)
    assert done.returncode == 1 and result["subtype"] == "error_max_turns" and result["is_error"] is True


def test_continue_resumes_the_folders_latest_session(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("continue")
    first = _lines(orin_code(orin_runtime, workspace, "-p", "say hello", "--output-format", "json", *MODEL).stdout)[0]
    again = orin_code(orin_runtime, workspace, "-c", "-p", "say hello again", "--output-format", "json")
    assert again.returncode == 0, again.stderr
    second = _lines(again.stdout)[0]
    assert second["session_id"] == first["session_id"]  # the model came from the resumed conversation
    by_id = _lines(orin_code(orin_runtime, workspace, "-r", first["session_id"], "-p", "third", "--output-format", "json").stdout)[0]
    assert by_id["session_id"] == first["session_id"]


def test_an_sdk_style_stream_json_session(orin_runtime: LiveRuntime) -> None:
    stdin = "\n".join(json.dumps(item) for item in (
        {"type": "control_request", "request_id": "req_1", "request": {"subtype": "initialize", "hooks": None}},
        {"type": "user", "session_id": "", "message": {"role": "user", "content": "say hello"}, "parent_tool_use_id": None},
        {"type": "control_request", "request_id": "req_2", "request": {"subtype": "mcp_status"}},
    )) + "\n"
    done = orin_code(orin_runtime, orin_runtime.workspace("sdk-style"), "--output-format", "stream-json", "--verbose",
                     "--input-format", "stream-json", "--system-prompt", "", *MODEL, stdin=stdin)
    assert done.returncode == 0, done.stderr
    messages = _lines(done.stdout)
    responses = {item["response"]["request_id"]: item["response"] for item in messages if item["type"] == "control_response"}
    assert responses["req_1"]["subtype"] == "success"
    assert responses["req_2"]["subtype"] == "error"  # Batch C answers mcp_status
    kinds = [item["type"] for item in messages if item["type"] != "control_response"]
    assert kinds == ["system", "assistant", "result"]


def test_a_shared_orin_is_refused(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("server"), "-p", "x", *MODEL, env={**orin_runtime.env, "ORIN_MODE": "server"})
    assert done.returncode == 1 and "local Orin only" in done.stderr


def test_flags_this_build_does_not_handle_are_refused_before_anything_runs(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("refuse"), "-p", "x", "--fork-session", *MODEL)
    assert done.returncode == 1 and "--fork-session" in done.stderr


def test_a_prompt_longer_than_orin_takes_is_an_error(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("long"), "-p", "x" * 16001, *MODEL)
    assert done.returncode == 1 and "16000" in done.stderr
```

- [ ] **Step 5: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code && uv run pytest -q tests/integration/cli_code (timeout 900000 ms)`
Expected: all pass.

- [ ] **Step 6: Commit.**

```
feat(w6): orin code -p and stream-json runs on the local Orin

text, json and stream-json output; stream-json input with initialize; the
permission mode is Claude Code's default unless a flag says otherwise.
```

### Task 11: `--json-schema` with retries (W3-R25)

The schema is checked when the run starts (a bad one is a usage error) and joins the turn's appended instructions. After a completed turn the answer's JSON value (the whole text, a fenced block, or the outermost object or array) is validated with `jsonschema` (a new runtime dependency, already in the lock through the SDK); an invalid answer gets a follow-up naming the problem, at most `MAX_STRUCTURED_RETRIES = 3` times, after which the run ends with `error_max_structured_output_retries`. The result carries `structured_output`, and its usage, cost and `num_turns` add up every attempt. (`HeadlessRun.answer` and the schema branch of `build_turn_options` already exist from Task 10 and import this module lazily.)

**Files:**
- Create: `src/agentos/cli_code/structured.py`
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/unit/cli_code/test_structured.py`, `tests/integration/cli_code/test_headless.py`

**Interfaces:**
- Produces: `load_schema(raw)`, `schema_instructions(schema)`, `extract_json(text)`, `problem(schema, text) -> (value, issue)`, `retry_prompt(issue)`, `MAX_STRUCTURED_RETRIES`.

- [ ] **Step 1: Dependency.** In `pyproject.toml` make these 1 replacement(s) (each old snippet occurs exactly once):

1. Replace

```toml
dependencies = [
    "SQLAlchemy>=2.0,<3",
```

with

```toml
dependencies = [
    # W6: `orin code --json-schema` validates the run's answer.
    "jsonschema>=4.20,<5",
    "SQLAlchemy>=2.0,<3",
```

Then run `uv lock` and `uv sync --all-groups` (both must succeed; `git diff --stat uv.lock` shows only added packages and the `agentos` entry, nothing removed or downgraded — if anything else changes, stop and report).

- [ ] **Step 2: Write the failing tests.**

`tests/unit/cli_code/test_structured.py`:

````python
"""W6 Task 11: --json-schema reads, validates and explains structured answers."""
from __future__ import annotations

import pytest

from agentos.cli_code.args import CliUsageError
from agentos.cli_code.structured import extract_json, load_schema, problem, retry_prompt, schema_instructions

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}


@pytest.mark.parametrize("text", ['{"n": 3}', '  {"n": 3}\n', 'Here:\n```json\n{"n": 3}\n```', 'The value is {"n": 3}.'])
def test_the_json_value_is_found(text: str) -> None:
    assert extract_json(text) == {"n": 3}


def test_validation_names_the_problem() -> None:
    assert problem(SCHEMA, '{"n": 3}') == ({"n": 3}, None)
    value, issue = problem(SCHEMA, '{"n": "three"}')
    assert value is None and issue.startswith("at n:")
    assert problem(SCHEMA, "no json here")[1] == "the answer has no JSON value"
    assert "only the JSON value" in retry_prompt("x") and '"required": ["n"]' in schema_instructions(SCHEMA)


@pytest.mark.parametrize("raw", ["{not json", "[1]", '{"type": 5}'])
def test_a_bad_schema_is_a_usage_error(raw: str) -> None:
    with pytest.raises(CliUsageError):
        load_schema(raw)
````

Append to `tests/integration/cli_code/test_headless.py`:

```python
SCHEMA = '{"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}'


def test_json_schema_returns_structured_output(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("schema-ok"), "-p", '[[say:{"n": 3}]]', "--json-schema", SCHEMA,
                     "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    (result,) = _lines(done.stdout)
    assert result["subtype"] == "success" and result["structured_output"] == {"n": 3}


def test_json_schema_gives_up_after_its_retries(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("schema-bad"), "-p", "[[say:not json]]", "--json-schema", SCHEMA,
                     "--output-format", "json", *MODEL)
    (result,) = _lines(done.stdout)
    assert done.returncode == 1 and result["subtype"] == "error_max_structured_output_retries"
    assert result["num_turns"] == 4  # the first answer and three follow-ups
    requests = [item for item in orin_runtime.fake.requests if any("does not satisfy the required JSON Schema" in str(m.get("content")) for m in item.get("messages", []))]
    assert requests
```

- [ ] **Step 3: Run them to see them fail.**

Run: `uv run pytest -q tests/unit/cli_code/test_structured.py`
Expected: collection error: `ModuleNotFoundError: No module named 'agentos.cli_code.structured'`.

- [ ] **Step 4: Implement.**

Create `src/agentos/cli_code/structured.py`:

````python
"""``--json-schema``: the run's answer must be one JSON value that satisfies the schema (W3-R25).

The model is told the schema with the turn's appended instructions. After a turn completes, its final text is parsed
and validated; an invalid answer gets a follow-up message naming the problem, up to ``MAX_STRUCTURED_RETRIES`` times,
after which the run ends with ``error_max_structured_output_retries``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import best_match

from .args import CliUsageError

MAX_STRUCTURED_RETRIES = 3
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def load_schema(raw: str) -> dict[str, Any]:
    try:
        schema = json.loads(raw)
    except ValueError as error:
        raise CliUsageError("--json-schema is not valid JSON") from error
    if not isinstance(schema, dict):
        raise CliUsageError("--json-schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CliUsageError(f"--json-schema is not a valid JSON Schema: {error.message}") from error
    return schema


def schema_instructions(schema: dict[str, Any]) -> str:
    return (
        "Your final answer in this run must be exactly one JSON value, with no other text and no code fence, that "
        "satisfies this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def extract_json(text: str) -> Any:
    """The answer's JSON value: the whole text, else a fenced block, else the outermost object or array."""
    candidate = (text or "").strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    fenced = _FENCE.search(candidate)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except ValueError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(candidate[start:end + 1])
            except ValueError:
                continue
    raise ValueError("the answer has no JSON value")


def problem(schema: dict[str, Any], text: str) -> tuple[Any, str | None]:
    """``(value, None)`` when the text holds a valid value, else ``(None, what is wrong)``."""
    try:
        value = extract_json(text)
    except ValueError as error:
        return None, str(error)
    error = best_match(Draft202012Validator(schema).iter_errors(value))
    if error is None:
        return value, None
    where = "/".join(str(part) for part in error.absolute_path) or "the top level"
    return None, f"at {where}: {error.message}"


def retry_prompt(issue: str) -> str:
    return f"That answer does not satisfy the required JSON Schema ({issue}). Reply again with only the JSON value."


__all__ = ["MAX_STRUCTURED_RETRIES", "extract_json", "load_schema", "problem", "retry_prompt", "schema_instructions"]
````

- [ ] **Step 5: Run the tests to see them pass.**

Run: `uv run pytest -q tests/unit/cli_code/test_structured.py && uv run pytest -q tests/integration/cli_code -k schema`
Expected: all pass.

- [ ] **Step 6: Commit.**

```
feat(w6): --json-schema answers are validated and retried
```

### Batch B close

- [ ] **Step 1:** `uv run pytest -q tests/unit` and `uv run pytest -q tests/integration` (timeout 600000 ms each): green; report the counts.
- [ ] **Step 2:** `wc -l src/agentos/agentic/agent_tools.py` is still 1699.
- [ ] **Step 3:** Append `## Batch B — Headless turns (Tasks 6–11)` to the ledger: commits, tests added, suite results, deviations from this plan, the expectation change in `test_w3_schema.py`, anything incomplete. Commit `docs(w6): Batch B ledger`.

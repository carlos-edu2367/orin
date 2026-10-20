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

# W0 — Fundações (Tool names públicos, Settings 5 camadas, Barramento de lifecycle) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow TDD strictly: write the test, see it FAIL, implement, see it PASS, commit.

**Goal:** Lay the three foundations every later wave depends on: (1) Claude Code–compatible public tool names and argument shapes exposed at the model boundary, (2) a 5-layer settings loader/writer with `.claude` interop and provenance, (3) a typed lifecycle bus emitting the hook events at the points that already exist.

**Architecture:** Internal tool ids stay the current snake_case names in W0 (they become the PascalCase canonical ones when tools are rewritten in W4). A translator (`PublicToolNames`) converts names/arguments at the model boundary (schemas out, messages out, tool calls in) when `experimental.claudeToolNames` is on; default off, so behaviour is unchanged unless enabled. Settings live in a new `agentos.settings` package. The lifecycle bus (`agentos.agentic.lifecycle`) only *emits* in W0; W2 attaches hook handlers and applies decisions.

**Tech Stack:** Python 3.13, pytest, stdlib only (json, os, threading, dataclasses, enum).

**Spec:** `docs/superpowers/specs/2026-10-18-orin-code-claude-code-full-parity-design.md` (sections 3, 5.4, 16).

## Global Constraints

- Python `>=3.13`; no new third-party dependencies in W0.
- Never copy Anthropic prompt text or `sdk-tools.d.ts` into the repo. Descriptions in public schemas are written by us.
- `.orin` files override `.claude` files in the same layer; writes always go to `.orin` (or `~/.orin`).
- Default behaviour must not change when `experimental.claudeToolNames` is absent/false: the whole existing suite must stay green.
- Run tests with: `uv run pytest -q tests/unit` (baseline before W0: 2393 passed, 49 skipped).
- Commit after each task with a conventional message ending in the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Never push.**
- Windows is the primary dev platform: use `pathlib`, never hardcode `/` separators in filesystem paths.

## Deviations from the master spec (intentional, documented)

1. The god class `AgentToolset` is **not** split in W0. Physical split happens in W4, when each tool is rewritten to Claude Code parity (doing a mechanical split now and a rewrite later would be double work). W0 delivers the registry *metadata* (`public_name`, `internal_name`, `permission_category`).
2. User settings live in `~/.orin/` (override with env `ORIN_USER_DIR`), not in `ORIN_CONFIG_DIR`: in a source checkout `ORIN_CONFIG_DIR` is the repository root, which would make a repo-root `settings.json` look like user settings.
3. No HTTP endpoint for effective settings yet (the UI that needs it is W7).

## File Structure

| File | Responsibility |
|---|---|
| Create `src/agentos/agentic/tools/__init__.py` | Package marker, re-exports |
| Create `src/agentos/agentic/tools/public_names.py` | Name map, argument translation in/out, public schemas, `PublicToolNames` translator, `permission_category` |
| Modify `src/agentos/agentic/agent_tools.py` | `public_tool_names` + `lifecycle` constructor params, `resolve` accepts public names, `invoke` wrapper emitting events |
| Modify `src/agentos/agentic/runtime.py` | Use translator on schemas/messages/calls; `lifecycle` param; emit PostToolBatch, Stop, StopFailure, PreCompact, PostCompact |
| Modify `src/agentos/agentic/session.py` | Load effective settings, create bus, pass flag and bus, emit SessionStart, UserPromptSubmit, InstructionsLoaded, ConfigChange, SubagentStart/Stop |
| Create `src/agentos/settings/__init__.py` | Re-exports |
| Create `src/agentos/settings/paths.py` | Where each layer's files live (Orin + Claude interop) |
| Create `src/agentos/settings/merge.py` | Deep merge with array union |
| Create `src/agentos/settings/loader.py` | `load_effective_settings`, `EffectiveSettings`, project security filter |
| Create `src/agentos/settings/writer.py` | Atomic, locked JSON writes; `add_permission_rule` |
| Create `src/agentos/settings/watcher.py` | mtime snapshots → changed files (ConfigChange) |
| Create `src/agentos/agentic/lifecycle.py` | `HookEvent`, `LifecycleEvent`, `LifecycleDecision`, `LifecycleBus` |
| Create `tests/conftest.py` | Autouse isolation of user/managed settings dirs for every test |
| Create tests under `tests/unit/agentic/tools/`, `tests/unit/settings/`, `tests/unit/agentic/test_lifecycle*.py` | Tests per task |

---

### Task 1: Test isolation for settings directories

Every later test (and the real runtime) reads `~/.orin` and `~/.claude`. Tests must never see the developer's real files.

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/unit/settings/__init__.py`, `tests/unit/settings/test_isolation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/settings/__init__.py` as an empty file, then `tests/unit/settings/test_isolation.py`:

```python
from __future__ import annotations

import os
from pathlib import Path


def test_settings_directories_are_isolated_per_test(tmp_path: Path) -> None:
    for name in ("ORIN_USER_DIR", "ORIN_CLAUDE_HOME", "ORIN_MANAGED_DIR", "ORIN_CLAUDE_MANAGED_DIR"):
        value = os.environ.get(name)
        assert value, f"{name} must be set by tests/conftest.py"
        assert Path(value).resolve() != (Path.home() / ".orin").resolve()
        assert Path(value).resolve() != (Path.home() / ".claude").resolve()
        assert Path(value).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/settings/test_isolation.py`
Expected: FAIL with `ORIN_USER_DIR must be set by tests/conftest.py`

- [ ] **Step 3: Write minimal implementation**

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the developer's real ~/.orin and ~/.claude.

    The settings loader reads user and managed layers from the home directory
    and from machine-wide folders. A test that picked those up would pass or
    fail depending on whose machine runs it.
    """
    root: Path = tmp_path_factory.mktemp("settings-home")
    for name, sub in (
        ("ORIN_USER_DIR", "orin-user"),
        ("ORIN_CLAUDE_HOME", "claude-user"),
        ("ORIN_MANAGED_DIR", "orin-managed"),
        ("ORIN_CLAUDE_MANAGED_DIR", "claude-managed"),
    ):
        target = root / sub
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/settings/test_isolation.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/unit/settings/__init__.py tests/unit/settings/test_isolation.py
git commit -m "test: isolate user and managed settings dirs in every test"
```

---

### Task 2: Public tool names and argument translation (pure module)

**Files:**
- Create: `src/agentos/agentic/tools/__init__.py`
- Create: `src/agentos/agentic/tools/public_names.py`
- Test: `tests/unit/agentic/tools/__init__.py`, `tests/unit/agentic/tools/test_public_names.py`

**Interfaces:**
- Produces:
  - `PUBLIC_NAMES: dict[str, str]` (internal → public)
  - `public_name(internal: str) -> str`
  - `internal_name(name: str) -> str` (accepts public, internal, case-insensitive public; `mcp__*` unchanged)
  - `arguments_in(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]` (public shape → internal shape; idempotent on internal-shaped input)
  - `arguments_out(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]` (internal shape → public shape; idempotent)
  - `public_argument_names(internal: str) -> frozenset[str] | None`
  - `public_schema(internal: str) -> tuple[str, dict[str, Any]] | None` (description, JSON schema)
  - `permission_category(internal: str, *, kind: str, read_only: bool, policy_tags: tuple[str, ...]) -> str`
  - `class PublicToolNames` with methods `name_in(name) -> str`, `arguments_in(internal, args) -> dict`, `argument_names(internal) -> frozenset[str] | None`, `schemas_out(schemas: list[dict]) -> list[dict]`, `messages_out(messages: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create empty `tests/unit/agentic/tools/__init__.py`, then `tests/unit/agentic/tools/test_public_names.py`:

```python
from __future__ import annotations

import json

import pytest

from agentos.agentic.tools.public_names import (
    PUBLIC_NAMES,
    PublicToolNames,
    arguments_in,
    arguments_out,
    internal_name,
    permission_category,
    public_argument_names,
    public_name,
    public_schema,
)


def test_core_names_follow_claude_code() -> None:
    expected = {
        "read_file": "Read", "write_file": "Write", "edit_file": "Edit", "notebook_edit": "NotebookEdit",
        "glob_files": "Glob", "search_files": "Grep", "run_command": "Bash", "todo_write": "TodoWrite",
        "task_create": "TaskCreate", "task_update": "TaskUpdate", "task_get": "TaskGet", "task_list": "TaskList",
        "read_process_output": "TaskOutput", "stop_process": "TaskStop", "ask_user": "AskUserQuestion",
        "enter_plan_mode": "EnterPlanMode", "exit_plan_mode": "ExitPlanMode", "enter_worktree": "EnterWorktree",
        "exit_worktree": "ExitWorktree", "fetch_url": "WebFetch", "web_search": "WebSearch",
        "run_subagent": "Agent", "use_skill": "Skill", "search_mcp_tools": "ToolSearch",
    }
    for internal, public in expected.items():
        assert PUBLIC_NAMES[internal] == public
        assert public_name(internal) == public
        assert internal_name(public) == internal
        assert internal_name(internal) == internal


def test_orin_extras_get_pascal_case_names_and_unknown_names_pass_through() -> None:
    assert public_name("search_code") == "SearchCode"
    assert public_name("remember") == "Remember"
    assert public_name("generate_image") == "GenerateImage"
    assert internal_name("bash") == "run_command"  # case-insensitive public
    assert internal_name("mcp__github__list_issues") == "mcp__github__list_issues"
    assert public_name("mcp__github__list_issues") == "mcp__github__list_issues"
    assert internal_name("NoSuchTool") == "NoSuchTool"
    assert public_name("no_such_tool") == "no_such_tool"
    assert len(set(PUBLIC_NAMES.values())) == len(PUBLIC_NAMES), "public names must be unique"


def test_read_write_edit_arguments_round_trip() -> None:
    assert arguments_in("read_file", {"file_path": "a.py", "offset": 3, "limit": 10}) == {"path": "a.py", "offset": 3, "limit": 10}
    assert arguments_in("write_file", {"file_path": "a.py", "content": "x"}) == {"path": "a.py", "content": "x"}
    edit = arguments_in("edit_file", {"file_path": "a.py", "old_string": "a", "new_string": "b", "replace_all": True})
    assert edit == {"path": "a.py", "old_text": "a", "new_text": "b", "replace_all": True}
    assert arguments_out("edit_file", edit) == {"file_path": "a.py", "old_string": "a", "new_string": "b", "replace_all": True}
    # idempotent on already-internal input
    assert arguments_in("read_file", {"path": "a.py"}) == {"path": "a.py"}
    # batch extension keeps working
    batch = arguments_in("edit_file", {"file_path": "a.py", "edits": [{"old_string": "a", "new_string": "b"}]})
    assert batch == {"path": "a.py", "edits": [{"old_text": "a", "new_text": "b"}]}


def test_bash_timeout_is_milliseconds_publicly_and_seconds_internally() -> None:
    assert arguments_in("run_command", {"command": "ls", "timeout": 90_500, "run_in_background": True}) == {
        "command": "ls", "timeout_seconds": 91, "background": True,
    }
    assert arguments_in("run_command", {"command": "ls", "timeout": 10_000_000})["timeout_seconds"] == 600
    assert "timeout_seconds" not in arguments_in("run_command", {"command": "ls", "timeout": "soon"})
    assert arguments_in("run_command", {"command": "ls", "dangerouslyDisableSandbox": True}) == {"command": "ls"}
    assert arguments_out("run_command", {"command": "ls", "timeout_seconds": 30, "background": False}) == {
        "command": "ls", "timeout": 30_000, "run_in_background": False,
    }


def test_grep_flags_map_to_internal_names() -> None:
    assert arguments_in("search_files", {"pattern": "x", "-A": 2, "-B": 1, "-C": 3, "-i": True, "-n": True}) == {
        "pattern": "x", "after_context": 2, "before_context": 1, "context_lines": 3, "ignore_case": True,
    }


def test_task_output_and_stop_take_string_ids() -> None:
    assert arguments_in("read_process_output", {"task_id": "4242"}) == {"pid": 4242}
    assert arguments_in("stop_process", {"task_id": "17"}) == {"pid": 17}
    assert arguments_in("stop_process", {"shell_id": "18"}) == {"pid": 18}
    assert arguments_out("stop_process", {"pid": 17}) == {"task_id": "17"}


def test_agent_maps_types_and_prompt() -> None:
    assert arguments_in("run_subagent", {"description": "Find it", "prompt": "Look", "subagent_type": "Explore"}) == {
        "description": "Find it", "task": "Look", "subagent_type": "explore",
    }
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": "general-purpose"})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p"})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": "my-custom"})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "resume": "agent-1", "model": "opus"}) == {
        "description": "d", "task": "p", "subagent_type": "general", "agent_id": "agent-1",
    }
    assert arguments_out("run_subagent", {"subagent_type": "plan", "task": "p", "description": "d"}) == {
        "subagent_type": "Plan", "prompt": "p", "description": "d",
    }


def test_ask_user_question_converts_to_orin_questions() -> None:
    converted = arguments_in("ask_user", {"questions": [
        {"question": "Which DB?", "header": "Database", "multiSelect": False,
         "options": [{"label": "SQLite", "description": "local"}, {"label": "Postgres"}]},
        {"question": "Features?", "header": "Features", "multiSelect": True,
         "options": [{"label": "Auth"}, {"label": "Billing"}]},
    ]})
    assert converted == {"questions": [
        {"id": "q1", "question": "Which DB?", "mode": "single_choice",
         "options": [{"id": "o1", "label": "SQLite — local"}, {"id": "o2", "label": "Postgres"}]},
        {"id": "q2", "question": "Features?", "mode": "checkbox",
         "options": [{"id": "o1", "label": "Auth"}, {"id": "o2", "label": "Billing"}]},
    ]}
    # a single option cannot be single_choice in Orin; it becomes a checkbox
    single = arguments_in("ask_user", {"questions": [{"question": "Ok?", "header": "Ok", "options": [{"label": "Yes"}]}]})
    assert single["questions"][0]["mode"] == "checkbox"
    # no options becomes a free-text question
    text = arguments_in("ask_user", {"questions": [{"question": "Name?", "header": "Name", "options": []}]})
    assert text["questions"][0]["mode"] == "text" and text["questions"][0]["options"] == []
    # already-internal input is left alone
    internal = {"questions": [{"id": "x", "question": "Q", "mode": "text", "options": []}]}
    assert arguments_in("ask_user", internal) == internal
    out = arguments_out("ask_user", converted)
    assert out["questions"][0] == {"question": "Which DB?", "header": "q1", "multiSelect": False,
                                   "options": [{"label": "SQLite — local"}, {"label": "Postgres"}]}
    assert out["questions"][1]["multiSelect"] is True


def test_exit_plan_mode_skill_toolsearch_and_web_tools() -> None:
    assert arguments_in("exit_plan_mode", {"plan": "# Plan"}) == {"summary": "# Plan"}
    assert arguments_in("exit_plan_mode", {"allowedPrompts": [{"tool": "Bash"}]}) == {"allowed_prompts": [{"tool": "Bash"}]}
    assert arguments_in("use_skill", {"skill": "pdf", "args": "x"}) == {"skill_id": "pdf"}
    assert arguments_in("search_mcp_tools", {"query": "issues", "max_results": 5}) == {"query": "issues", "limit": 5}
    assert arguments_in("fetch_url", {"url": "https://example.com", "prompt": "summary"}) == {"url": "https://example.com"}
    assert arguments_in("web_search", {"query": "q", "allowed_domains": ["a.com"]}) == {"query": "q"}
    # tools without a mapping pass arguments through unchanged
    assert arguments_in("remember", {"fact": "f"}) == {"fact": "f"}


def test_public_schemas_use_public_field_names() -> None:
    description, schema = public_schema("read_file")
    assert description and "file_path" in schema["properties"] and schema["required"] == ["file_path"]
    assert public_argument_names("read_file") == frozenset({"file_path", "offset", "limit", "pages"})
    _, bash = public_schema("run_command")
    assert {"command", "description", "timeout", "run_in_background"} <= set(bash["properties"])
    _, grep = public_schema("search_files")
    assert {"-A", "-B", "-C", "-i", "-n", "output_mode"} <= set(grep["properties"])
    _, ask = public_schema("ask_user")
    assert ask["properties"]["questions"]["maxItems"] == 4
    assert public_schema("remember") is None
    assert public_argument_names("remember") is None


def test_schemas_out_renames_and_replaces_core_tools_without_mutating_input() -> None:
    translator = PublicToolNames()
    original = [
        {"type": "function", "function": {"name": "read_file", "description": "old", "parameters": {"type": "object", "properties": {"path": {}}}}},
        {"type": "function", "function": {"name": "remember", "description": "Save", "parameters": {"type": "object", "properties": {"fact": {}}}}},
        {"type": "function", "function": {"name": "mcp__x__y", "description": "remote", "parameters": {"type": "object"}}},
    ]
    snapshot = json.dumps(original, sort_keys=True)
    out = translator.schemas_out(original)
    assert json.dumps(original, sort_keys=True) == snapshot
    assert [item["function"]["name"] for item in out] == ["Read", "Remember", "mcp__x__y"]
    assert "file_path" in out[0]["function"]["parameters"]["properties"]
    assert out[1]["function"]["parameters"] == original[1]["function"]["parameters"]


def test_messages_out_rewrites_openai_and_anthropic_tool_calls() -> None:
    translator = PublicToolNames()
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}},
            {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{broken"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "data"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "id": "t1", "name": "edit_file", "input": {"path": "a.py", "old_text": "a", "new_text": "b"}},
        ]},
    ]
    out = translator.messages_out(messages)
    first = out[1]["tool_calls"][0]["function"]
    assert first["name"] == "Read" and json.loads(first["arguments"]) == {"file_path": "a.py"}
    assert out[1]["tool_calls"][1]["function"] == {"name": "Bash", "arguments": "{broken"}
    assert out[3]["content"][1] == {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "a.py", "old_string": "a", "new_string": "b"}}
    # input untouched
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3]["content"][1]["name"] == "edit_file"


@pytest.mark.parametrize(
    ("internal", "kind", "read_only", "tags", "expected"),
    [
        ("read_file", "filesystem", True, (), "read"),
        ("edit_file", "filesystem", False, ("mutates",), "edit"),
        ("write_file", "filesystem", False, ("mutates",), "edit"),
        ("notebook_edit", "filesystem", False, ("mutates",), "edit"),
        ("run_command", "terminal", False, ("mutates",), "exec"),
        ("fetch_url", "web", True, ("network",), "network"),
        ("mcp__a__b", "mcp", False, ("mcp",), "mcp"),
        ("run_subagent", "agent", False, ("mutates",), "agent"),
        ("ask_user", "user_input", False, ("user_input",), "interaction"),
        ("todo_write", "planning", True, (), "meta"),
    ],
)
def test_permission_category(internal: str, kind: str, read_only: bool, tags: tuple[str, ...], expected: str) -> None:
    assert permission_category(internal, kind=kind, read_only=read_only, policy_tags=tags) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/tools/test_public_names.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.agentic.tools'`

- [ ] **Step 3: Write the implementation**

Create `src/agentos/agentic/tools/__init__.py`:

```python
"""Tool registry metadata and the Claude Code–compatible public tool surface."""

from .public_names import (
    PUBLIC_NAMES,
    PublicToolNames,
    arguments_in,
    arguments_out,
    internal_name,
    permission_category,
    public_argument_names,
    public_name,
    public_schema,
)

__all__ = [
    "PUBLIC_NAMES",
    "PublicToolNames",
    "arguments_in",
    "arguments_out",
    "internal_name",
    "permission_category",
    "public_argument_names",
    "public_name",
    "public_schema",
]
```

Create `src/agentos/agentic/tools/public_names.py`:

```python
"""Claude Code–compatible public names and argument shapes for Orin's tools.

Orin's tools keep their internal snake_case ids (``read_file``) until W4
rewrites them. What the *model*, permission rules and hooks see can already be
the Claude Code surface (``Read`` with ``file_path``): this module converts
between the two shapes at the model boundary.

Every conversion is idempotent: translating already-translated input is a
no-op, so a value that crosses the boundary twice is never corrupted.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from typing import Any

PUBLIC_NAMES: dict[str, str] = {
    # Claude Code core tools
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "notebook_edit": "NotebookEdit",
    "glob_files": "Glob",
    "search_files": "Grep",
    "run_command": "Bash",
    "read_process_output": "TaskOutput",
    "stop_process": "TaskStop",
    "todo_write": "TodoWrite",
    "task_create": "TaskCreate",
    "task_update": "TaskUpdate",
    "task_get": "TaskGet",
    "task_list": "TaskList",
    "ask_user": "AskUserQuestion",
    "enter_plan_mode": "EnterPlanMode",
    "exit_plan_mode": "ExitPlanMode",
    "enter_worktree": "EnterWorktree",
    "exit_worktree": "ExitWorktree",
    "fetch_url": "WebFetch",
    "web_search": "WebSearch",
    "run_subagent": "Agent",
    "use_skill": "Skill",
    "search_mcp_tools": "ToolSearch",
    # Orin extras
    "list_files": "ListFiles",
    "view_file": "ViewFile",
    "transcribe_pdf": "TranscribePdf",
    "search_code": "SearchCode",
    "project_map": "ProjectMap",
    "verify_project": "VerifyProject",
    "preview_start": "PreviewStart",
    "preview_stop": "PreviewStop",
    "preview_logs": "PreviewLogs",
    "remember": "Remember",
    "recall": "Recall",
    "create_agent": "CreateAgent",
    "ask_agent": "AskAgent",
    "ask_agents": "AskAgents",
    "check_subagent": "CheckSubagent",
    "search_skills": "SearchSkills",
    "list_skills": "ListSkills",
    "read_skill_resource": "ReadSkillResource",
    "create_skill": "CreateSkill",
    "edit_skill": "EditSkill",
    "list_mcp_catalog": "ListMcpCatalog",
    "list_mcp_servers": "ListMcpServers",
    "configure_mcp": "ConfigureMcp",
    "test_mcp_server": "TestMcpServer",
    "inspect_mcp_server": "InspectMcpServer",
    "call_mcp_tool": "CallMcpTool",
    "search_plugin": "SearchPlugin",
    "inspect_plugin": "InspectPlugin",
    "install_plugin": "InstallPlugin",
    "list_plugins": "ListPlugins",
    "uninstall_plugin": "UninstallPlugin",
    "write_contract": "WriteContract",
    "report_verification": "ReportVerification",
    "propose_code_session": "ProposeCodeSession",
    "list_projects": "ListProjects",
    "create_project_from_session": "CreateProjectFromSession",
    "move_session_to_project": "MoveSessionToProject",
    "read_project_conversation": "ReadProjectConversation",
    "generate_image": "GenerateImage",
    "generate_audio": "GenerateAudio",
    "generate_video": "GenerateVideo",
}

_INTERNAL_BY_PUBLIC: dict[str, str] = {public: internal for internal, public in PUBLIC_NAMES.items()}
_INTERNAL_BY_LOWER_PUBLIC: dict[str, str] = {public.lower(): internal for internal, public in PUBLIC_NAMES.items()}

MAX_BASH_TIMEOUT_SECONDS = 600

# public key -> internal key, per internal tool
_RENAMES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path"},
    "write_file": {"file_path": "path"},
    "edit_file": {"file_path": "path", "old_string": "old_text", "new_string": "new_text"},
    "search_files": {"-A": "after_context", "-B": "before_context", "-C": "context_lines", "-i": "ignore_case"},
    "run_command": {"run_in_background": "background"},
    "run_subagent": {"prompt": "task", "resume": "agent_id"},
    "use_skill": {"skill": "skill_id"},
    "search_mcp_tools": {"max_results": "limit"},
    "exit_plan_mode": {"plan": "summary", "allowedPrompts": "allowed_prompts"},
    "read_process_output": {"task_id": "pid"},
    "stop_process": {"task_id": "pid", "shell_id": "pid"},
}

# public keys Orin cannot honour yet (W4 implements them); dropped on the way in
_DROPPED: dict[str, frozenset[str]] = {
    "run_command": frozenset({"dangerouslyDisableSandbox"}),
    "search_files": frozenset({"-n"}),
    "fetch_url": frozenset({"prompt"}),
    "web_search": frozenset({"allowed_domains", "blocked_domains"}),
    "use_skill": frozenset({"args"}),
    "run_subagent": frozenset({"model"}),
}

_SUBAGENT_TYPES_IN = {
    "explore": "explore",
    "plan": "plan",
    "general-purpose": "general",
    "general": "general",
    "fork": "fork",
}
_SUBAGENT_TYPES_OUT = {"explore": "Explore", "plan": "Plan", "general": "general-purpose", "fork": "fork"}

_STRING = {"type": "string"}


def _obj(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required), "additionalProperties": False}


# Descriptions are Orin's own wording; field names follow Claude Code's public interface.
_PUBLIC_SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "read_file": (
        "Read a file from the workspace. Text comes back line-numbered; use offset and limit to page through a long "
        "file, and pages (e.g. '1-5') for a PDF. Read a file before you edit or overwrite it.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file, relative to the workspace or absolute inside it."},
            "offset": {"type": "integer", "minimum": 1, "description": "First line to read (1-based)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 800, "description": "How many lines to read."},
            "pages": {**_STRING, "description": "PDF page range such as '1-5' or '3'."},
        }, ("file_path",)),
    ),
    "write_file": (
        "Create a file, or replace an existing one you have already read in this session.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file to write."},
            "content": {**_STRING, "description": "The complete new content."},
        }, ("file_path", "content")),
    ),
    "edit_file": (
        "Replace an exact string in a file you have read. old_string must occur exactly once unless replace_all is true.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file to edit."},
            "old_string": {**_STRING, "description": "Exact text to replace, including indentation."},
            "new_string": {**_STRING, "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
        }, ("file_path", "old_string", "new_string")),
    ),
    "notebook_edit": (
        "Replace, insert or delete one cell of a Jupyter notebook.",
        _obj({
            "notebook_path": {**_STRING, "description": "Path of the .ipynb file."},
            "new_source": {**_STRING, "description": "New cell source (replace and insert)."},
            "cell_id": {**_STRING, "description": "Cell id, or 0-based index as a string."},
            "cell_type": {"type": "string", "enum": ["code", "markdown"]},
            "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
        }, ("notebook_path",)),
    ),
    "glob_files": (
        "Find files by glob pattern (e.g. '**/*.py'), newest first.",
        _obj({
            "pattern": {**_STRING, "description": "Glob pattern."},
            "path": {**_STRING, "description": "Directory to search in. Defaults to the workspace root."},
        }, ("pattern",)),
    ),
    "search_files": (
        "Search file contents with a regular expression.",
        _obj({
            "pattern": {**_STRING, "description": "Regular expression to search for."},
            "path": {**_STRING, "description": "File or directory to search."},
            "glob": {**_STRING, "description": "Only search files matching this glob."},
            "type": {**_STRING, "description": "Only search this file type, e.g. 'py'."},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
            "-A": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines after each match."},
            "-B": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines before each match."},
            "-C": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines around each match."},
            "-i": {"type": "boolean", "description": "Case-insensitive search."},
            "-n": {"type": "boolean", "description": "Show line numbers (always on)."},
            "multiline": {"type": "boolean", "description": "Let patterns span lines."},
            "head_limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Return at most this many entries."},
            "offset": {"type": "integer", "minimum": 0, "description": "Skip this many entries first."},
        }, ("pattern",)),
    ),
    "run_command": (
        "Run a shell command in the workspace. Prefer the file tools over shell commands for reading, searching and "
        "editing files. Give a short description. Use run_in_background for long-lived processes such as dev servers.",
        _obj({
            "command": {**_STRING, "description": "The command to run."},
            "description": {**_STRING, "description": "What the command does, in 5-10 words."},
            "timeout": {"type": "integer", "minimum": 1000, "maximum": MAX_BASH_TIMEOUT_SECONDS * 1000, "description": "Timeout in milliseconds."},
            "run_in_background": {"type": "boolean", "description": "Start it in the background and return immediately."},
        }, ("command",)),
    ),
    "read_process_output": (
        "Read the output of a background command started with run_in_background.",
        _obj({"task_id": {**_STRING, "description": "Id returned when the command started."}}, ("task_id",)),
    ),
    "stop_process": (
        "Stop a background command.",
        _obj({"task_id": {**_STRING, "description": "Id of the background command."}}, ("task_id",)),
    ),
    "todo_write": (
        "Write the session's todo list. Keep exactly one item in_progress while you work.",
        _obj({
            "todos": {
                "type": "array",
                "maxItems": 40,
                "items": _obj({
                    "content": {**_STRING, "description": "The task, in imperative form."},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    "activeForm": {**_STRING, "description": "Present-continuous label shown while in progress."},
                }, ("content", "status")),
            },
        }, ("todos",)),
    ),
    "task_create": (
        "Create a task in the session task list.",
        _obj({
            "subject": {**_STRING, "description": "Short imperative title."},
            "description": {**_STRING, "description": "What the task involves."},
            "activeForm": {**_STRING, "description": "Present-continuous label shown while in progress."},
            "metadata": {"type": "object", "additionalProperties": True},
        }, ("subject",)),
    ),
    "task_update": (
        "Update a task: status, text, ownership or dependencies.",
        _obj({
            "taskId": _STRING,
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]},
            "subject": _STRING,
            "description": _STRING,
            "activeForm": _STRING,
            "owner": _STRING,
            "addBlocks": {"type": "array", "items": _STRING},
            "addBlockedBy": {"type": "array", "items": _STRING},
            "metadata": {"type": "object", "additionalProperties": True},
        }, ("taskId",)),
    ),
    "task_get": ("Get one task by id.", _obj({"taskId": _STRING}, ("taskId",))),
    "task_list": ("List the session's tasks.", _obj({})),
    "ask_user": (
        "Ask the user 1-4 multiple-choice questions when you are blocked on a decision only they can make, then stop "
        "and wait for the answer.",
        _obj({
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": _obj({
                    "question": {**_STRING, "description": "The full question."},
                    "header": {**_STRING, "maxLength": 12, "description": "Very short label for the question."},
                    "multiSelect": {"type": "boolean", "description": "Allow several answers."},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": _obj({
                            "label": {**_STRING, "description": "Option text (1-5 words)."},
                            "description": {**_STRING, "description": "What choosing it means."},
                        }, ("label",)),
                    },
                }, ("question", "header", "options", "multiSelect")),
            },
        }, ("questions",)),
    ),
    "enter_plan_mode": ("Switch to plan mode: explore read-only and prepare a plan before changing code.", _obj({})),
    "exit_plan_mode": (
        "Present the finished plan to the user for approval and leave plan mode.",
        _obj({
            "plan": {**_STRING, "description": "The plan, in Markdown."},
            "allowedPrompts": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        }),
    ),
    "enter_worktree": (
        "Create (or switch to) an isolated git worktree for this session.",
        _obj({
            "name": {**_STRING, "description": "Optional worktree name."},
            "base_branch": {**_STRING, "description": "Git ref to branch from. Defaults to HEAD."},
        }),
    ),
    "exit_worktree": (
        "Leave the current worktree, keeping or removing it.",
        _obj({
            "action": {"type": "string", "enum": ["keep", "remove"]},
            "force": {"type": "boolean", "description": "Remove even with uncommitted changes."},
        }),
    ),
    "fetch_url": (
        "Fetch a public URL and return its readable content.",
        _obj({
            "url": {**_STRING, "description": "The URL to fetch."},
            "prompt": {**_STRING, "description": "What you want to learn from the page."},
        }, ("url",)),
    ),
    "web_search": (
        "Search the web and return titles, URLs and snippets.",
        _obj({
            "query": {**_STRING, "description": "Search query."},
            "allowed_domains": {"type": "array", "items": _STRING},
            "blocked_domains": {"type": "array", "items": _STRING},
        }, ("query",)),
    ),
    "run_subagent": (
        "Delegate a self-contained task to a subagent. Types: 'Explore' (fast read-only search), 'Plan' (read-only "
        "design), 'general-purpose' (full toolset), 'fork' (inherits this conversation).",
        _obj({
            "description": {**_STRING, "description": "3-5 word summary."},
            "prompt": {**_STRING, "description": "Complete instructions; the subagent does not see this conversation."},
            "subagent_type": {**_STRING, "description": "Explore, Plan, general-purpose or fork."},
            "model": {**_STRING, "description": "Optional model alias."},
            "run_in_background": {"type": "boolean", "description": "Return immediately and notify on completion."},
            "resume": {**_STRING, "description": "Id of a previous subagent to continue."},
        }, ("description", "prompt")),
    ),
    "use_skill": (
        "Load a skill's instructions when the task matches it.",
        _obj({"skill": {**_STRING, "description": "Skill name."}, "args": {**_STRING, "description": "Optional arguments."}}, ("skill",)),
    ),
    "search_mcp_tools": (
        "Find deferred MCP tools by keyword and load their schemas so they can be called.",
        _obj({
            "query": {**_STRING, "description": "Keywords or a tool name."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ("query",)),
    ),
}


def public_name(internal: str) -> str:
    if not internal or internal.startswith("mcp__"):
        return internal
    return PUBLIC_NAMES.get(internal, internal)


def internal_name(name: str) -> str:
    if not name or name.startswith("mcp__") or name in PUBLIC_NAMES:
        return name
    exact = _INTERNAL_BY_PUBLIC.get(name)
    if exact is not None:
        return exact
    return _INTERNAL_BY_LOWER_PUBLIC.get(name.lower(), name)


def public_schema(internal: str) -> tuple[str, dict[str, Any]] | None:
    entry = _PUBLIC_SCHEMAS.get(internal)
    if entry is None:
        return None
    description, schema = entry
    return description, copy.deepcopy(schema)


def public_argument_names(internal: str) -> frozenset[str] | None:
    entry = _PUBLIC_SCHEMAS.get(internal)
    if entry is None:
        return None
    return frozenset(entry[1].get("properties", {}))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _ask_user_in(arguments: dict[str, Any]) -> dict[str, Any]:
    questions = arguments.get("questions")
    if not isinstance(questions, list):
        return arguments
    if all(isinstance(item, Mapping) and "mode" in item and "id" in item for item in questions):
        return arguments  # already Orin-shaped
    converted: list[dict[str, Any]] = []
    for index, raw in enumerate(questions, start=1):
        if not isinstance(raw, Mapping):
            converted.append(raw)  # let the tool's own validation report it
            continue
        options: list[dict[str, str]] = []
        for option_index, option in enumerate(raw.get("options") or [], start=1):
            if not isinstance(option, Mapping):
                continue
            label = str(option.get("label") or "").strip()
            detail = str(option.get("description") or "").strip()
            text = f"{label} — {detail}" if label and detail else label
            options.append({"id": f"o{option_index}", "label": text[:240]})
        if not options:
            mode = "text"
        elif bool(raw.get("multiSelect")) or len(options) < 2:
            mode = "checkbox"
        else:
            mode = "single_choice"
        converted.append({"id": f"q{index}", "question": str(raw.get("question") or ""), "mode": mode, "options": options})
    return {**{k: v for k, v in arguments.items() if k != "questions"}, "questions": converted}


def _ask_user_out(arguments: dict[str, Any]) -> dict[str, Any]:
    questions = arguments.get("questions")
    if not isinstance(questions, list):
        return arguments
    out: list[Any] = []
    for raw in questions:
        if not isinstance(raw, Mapping) or "mode" not in raw:
            out.append(raw)
            continue
        out.append({
            "question": raw.get("question", ""),
            "header": str(raw.get("id", ""))[:12],
            "multiSelect": raw.get("mode") == "checkbox",
            "options": [
                {"label": option.get("label", "")}
                for option in raw.get("options") or []
                if isinstance(option, Mapping)
            ],
        })
    return {**{k: v for k, v in arguments.items() if k != "questions"}, "questions": out}


def arguments_in(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Public (Claude Code) argument shape -> Orin's internal shape."""
    result: dict[str, Any] = dict(arguments or {})
    for public_key, internal_key in _RENAMES.get(internal, {}).items():
        if public_key in result:
            value = result.pop(public_key)
            result.setdefault(internal_key, value)
    for key in _DROPPED.get(internal, ()):
        result.pop(key, None)
    if internal == "run_command" and "timeout" in result:
        milliseconds = _as_int(result.pop("timeout"))
        if milliseconds is not None and milliseconds > 0 and "timeout_seconds" not in result:
            result["timeout_seconds"] = max(1, min(MAX_BASH_TIMEOUT_SECONDS, math.ceil(milliseconds / 1000)))
    elif internal == "edit_file" and isinstance(result.get("edits"), list):
        result["edits"] = [
            arguments_in("edit_file", item) if isinstance(item, Mapping) else item
            for item in result["edits"]
        ]
    elif internal in {"read_process_output", "stop_process"} and "pid" in result:
        number = _as_int(result["pid"])
        if number is not None:
            result["pid"] = number
    elif internal == "run_subagent":
        raw_type = str(result.get("subagent_type") or "").strip().lower()
        result["subagent_type"] = _SUBAGENT_TYPES_IN.get(raw_type, "general")
    elif internal == "ask_user":
        result = _ask_user_in(result)
    return result


def arguments_out(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Orin's internal argument shape -> public (Claude Code) shape. Used for history and hook payloads."""
    result: dict[str, Any] = dict(arguments or {})
    reverse: dict[str, str] = {}
    for public_key, internal_key in _RENAMES.get(internal, {}).items():
        reverse.setdefault(internal_key, public_key)
    for internal_key, public_key in reverse.items():
        if internal_key in result:
            value = result.pop(internal_key)
            result.setdefault(public_key, value)
    if internal == "run_command" and "timeout_seconds" in result:
        seconds = _as_int(result.pop("timeout_seconds"))
        if seconds is not None and "timeout" not in result:
            result["timeout"] = seconds * 1000
    elif internal == "edit_file" and isinstance(result.get("edits"), list):
        result["edits"] = [
            arguments_out("edit_file", item) if isinstance(item, Mapping) else item
            for item in result["edits"]
        ]
    elif internal in {"read_process_output", "stop_process"} and "task_id" in result:
        result["task_id"] = str(result["task_id"])
    elif internal == "run_subagent" and "subagent_type" in result:
        raw_type = str(result.get("subagent_type") or "").strip().lower()
        result["subagent_type"] = _SUBAGENT_TYPES_OUT.get(raw_type, result["subagent_type"])
    elif internal == "ask_user":
        result = _ask_user_out(result)
    return result


def permission_category(internal: str, *, kind: str, read_only: bool, policy_tags: tuple[str, ...]) -> str:
    """Coarse family used by permission rules and hooks."""
    if internal.startswith("mcp__") or kind == "mcp" or "mcp" in policy_tags:
        return "mcp"
    if internal in {"edit_file", "write_file", "notebook_edit"}:
        return "edit"
    if kind == "terminal" or internal == "run_command":
        return "exec" if not read_only else "read"
    if "network" in policy_tags or kind == "web":
        return "network"
    if kind == "agent":
        return "agent"
    if kind in {"user_input", "code_handoff"} or "user_input" in policy_tags:
        return "interaction"
    if kind in {"planning", "skill"}:
        return "meta"
    return "read" if read_only else "edit"


def _translate_tool_call_arguments(internal: str, raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        if not isinstance(parsed, Mapping):
            return raw
        return json.dumps(arguments_out(internal, parsed), ensure_ascii=False)
    if isinstance(raw, Mapping):
        return arguments_out(internal, raw)
    return raw


class PublicToolNames:
    """Translator the runtime applies at the model boundary."""

    def name_in(self, name: str) -> str:
        return internal_name(name)

    def arguments_in(self, internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return arguments_in(internal, arguments)

    def argument_names(self, internal: str) -> frozenset[str] | None:
        return public_argument_names(internal)

    def schemas_out(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, Mapping) else None
            if not isinstance(function, Mapping):
                out.append(schema)
                continue
            internal = str(function.get("name") or "")
            replacement = public_schema(internal)
            new_function = copy.deepcopy(dict(function))
            new_function["name"] = public_name(internal)
            if replacement is not None:
                new_function["description"], new_function["parameters"] = replacement
            out.append({**schema, "function": new_function})
        return out

    def messages_out(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                out.append(message)
                continue
            changed = dict(message)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                new_calls = []
                for call in tool_calls:
                    function = call.get("function") if isinstance(call, Mapping) else None
                    if not isinstance(function, Mapping):
                        new_calls.append(call)
                        continue
                    internal = str(function.get("name") or "")
                    new_function = {
                        **function,
                        "name": public_name(internal),
                        "arguments": _translate_tool_call_arguments(internal, function.get("arguments")),
                    }
                    new_calls.append({**call, "function": new_function})
                changed["tool_calls"] = new_calls
            content = message.get("content")
            if isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, Mapping) and block.get("type") == "tool_use":
                        internal = str(block.get("name") or "")
                        new_blocks.append({
                            **block,
                            "name": public_name(internal),
                            "input": _translate_tool_call_arguments(internal, block.get("input") or {}),
                        })
                    else:
                        new_blocks.append(block)
                changed["content"] = new_blocks
            out.append(changed)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/agentic/tools/test_public_names.py`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools tests/unit/agentic/tools
git commit -m "feat(tools): Claude Code public tool names and argument translation"
```

---

### Task 3: Expose public names through AgentToolset and the runtime

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (constructor ~line 607-668; `resolve` ~line 1780; `invoke` ~line 1842)
- Modify: `src/agentos/agentic/runtime.py` (`__init__` ~line 217; `run` ~lines 351-355 and 492-494; `_run_toolset` ~lines 1430-1439)
- Test: `tests/unit/agentic/tools/test_public_names_runtime.py`

**Interfaces:**
- Consumes: `PublicToolNames`, `internal_name`, `arguments_in` from Task 2.
- Produces: `AgentToolset(..., public_tool_names: bool = False)`; attribute `AgentToolset.name_translator: PublicToolNames | None`; `AgentToolset.resolve(name)` accepts public names; `AgentToolset.invoke("Read", {"file_path": ...})` works.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agentic/tools/test_public_names_runtime.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "workspace_id": "workspace-1", "agent_id": "agent-1", "execution_id": "execution-1",
        "provider": "openrouter", "model_id": "local-model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.finished: list[tuple[bool, str | None]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": "read a.txt"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, payload))

    def delta(self, turn: dict[str, object], text: str) -> None:
        pass

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))


class Provider:
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.requests: list[dict[str, object]] = []
        self.tool_name = tool_name
        self.arguments = arguments

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        if len(self.requests) == 1:
            args = json.dumps(json.dumps(self.arguments))[1:-1]
            line = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"'
                + self.tool_name + '","arguments":"' + args + '"}}]},"finish_reason":"tool_calls"}]}'
            )
            return normalize_sse([line, "data: [DONE]"], provider="openrouter")
        return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


def _workspace(tmp_path: Path) -> ConversationWorkspace:
    workspace = ConversationWorkspace(tmp_path, "conv-public-names")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    return workspace


def test_toolset_resolves_and_invokes_public_names(tmp_path: Path) -> None:
    toolset = AgentToolset(_workspace(tmp_path), public_tool_names=True)
    assert toolset.resolve("Read").name == "read_file"
    assert toolset.is_read_only("Read") is True
    outcome = toolset.invoke("Read", {"file_path": "a.txt"})
    assert outcome.status == "succeeded"
    assert "hello" in outcome.content


def test_flag_off_keeps_internal_names_for_the_model(tmp_path: Path) -> None:
    toolset = AgentToolset(_workspace(tmp_path))
    assert toolset.name_translator is None
    provider = Provider("read_file", {"path": "a.txt"})
    result = AgenticTurnRuntime(store=Store(), provider=provider, toolset=toolset).run("turn-1")
    assert result.state == "completed"
    names = {item["function"]["name"] for item in provider.requests[0]["tools"]}
    assert "read_file" in names and "Read" not in names


def test_runtime_speaks_public_names_to_the_model_and_internal_names_to_orin(tmp_path: Path) -> None:
    store = Store()
    toolset = AgentToolset(_workspace(tmp_path), public_tool_names=True)
    provider = Provider("Read", {"file_path": "a.txt"})
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=toolset).run("turn-1")

    assert result.state == "completed"
    tools = {item["function"]["name"]: item["function"] for item in provider.requests[0]["tools"]}
    assert "Read" in tools and "read_file" not in tools
    assert "file_path" in tools["Read"]["parameters"]["properties"]
    # the second request replays the call under its public name and shape
    assistant = next(m for m in provider.requests[1]["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
    call = assistant["tool_calls"][0]["function"]
    assert call["name"] == "Read"
    assert json.loads(call["arguments"]) == {"file_path": "a.txt"}
    tool_message = next(m for m in provider.requests[1]["messages"] if m.get("role") == "tool")
    assert "hello" in str(tool_message["content"])
    # Orin's own activity keeps the internal id the UI understands
    finished = [payload for state, payload in store.events if state == "tool_finished"]
    assert finished and finished[0]["tool_name"] == "read_file" and finished[0]["status"] == "succeeded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/tools/test_public_names_runtime.py`
Expected: FAIL with `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'public_tool_names'`

- [ ] **Step 3: Implement in `agent_tools.py`**

3a. Add the import near the other `from .` imports at the top of `src/agentos/agentic/agent_tools.py`:

```python
from .tools.public_names import PublicToolNames, arguments_in as _public_arguments_in, internal_name as _internal_tool_name
```

3b. In `AgentToolset.__init__`, add a keyword parameter at the end of the signature (after `media_client: object | None = None,`):

```python
        public_tool_names: bool = False,
```

and at the end of the constructor body (after `self._active_mcp_tool_names: set[str] = set()`):

```python
        # Claude Code-compatible names/arguments at the model boundary
        # (experimental.claudeToolNames). Internal ids stay snake_case.
        self.name_translator: PublicToolNames | None = PublicToolNames() if public_tool_names else None
```

3c. Replace the body of `resolve`:

```python
    def resolve(self, name: str) -> ToolDefinition:
        self.definitions()
        definition = self._by_name.get(name)
        if definition is None:
            definition = self._by_name.get(_internal_tool_name(name))
        if definition is None:
            raise AgentToolError(f"Unknown tool '{name}'.")
        return definition
```

3d. At the very beginning of `invoke` (before `self._active_invocation_id = ...`), add:

```python
        requested_name = name
        name = _internal_tool_name(name)
        if requested_name != name:
            arguments = _public_arguments_in(name, dict(arguments or {}))
```

- [ ] **Step 4: Implement in `runtime.py`**

4a. In `AgenticTurnRuntime.__init__`, after `self.toolset = toolset`, add:

```python
        # Present when the toolset speaks Claude Code tool names to the model.
        self._names = getattr(toolset, "name_translator", None)
```

4b. In `run`, replace:

```python
            tool_schemas = self._tool_schemas(turn)
```

with:

```python
            tool_schemas = self._tool_schemas(turn)
            if self._names is not None:
                tool_schemas = self._names.schemas_out(tool_schemas)
```

and replace:

```python
            window = self._request_messages(messages)
```

with:

```python
            window = self._request_messages(messages)
            if self._names is not None:
                window = self._names.messages_out(window)
```

4c. In the `StreamKind.TOOL_CALL` branch of `run`, replace:

```python
                        current = calls.setdefault(call_id, {"id": call_id, "name": event.tool_name or "", "arguments": ""})
                        if event.tool_name:
                            current["name"] = event.tool_name
```

with:

```python
                        incoming_name = event.tool_name or ""
                        if incoming_name and self._names is not None:
                            incoming_name = self._names.name_in(incoming_name)
                        current = calls.setdefault(call_id, {"id": call_id, "name": incoming_name, "arguments": ""})
                        if incoming_name:
                            current["name"] = incoming_name
```

4d. In `_run_toolset`, replace:

```python
            try:
                parsed_args = parse_arguments(
                    call.get("arguments") or "{}",
                    argument_names(name) if callable(argument_names) else None,
                )
            except AgentToolError as error:
```

with:

```python
            names_translator = getattr(self, "_names", None)
            known_keys = names_translator.argument_names(name) if names_translator is not None else None
            if known_keys is None and callable(argument_names):
                known_keys = argument_names(name)
            try:
                parsed_args = parse_arguments(call.get("arguments") or "{}", known_keys)
                if names_translator is not None and isinstance(parsed_args, dict):
                    parsed_args = names_translator.arguments_in(name, parsed_args)
            except AgentToolError as error:
```

- [ ] **Step 5: Run the new tests and the agentic suite**

Run: `uv run pytest -q tests/unit/agentic/tools/test_public_names_runtime.py`
Expected: PASS

Run: `uv run pytest -q tests/unit/agentic`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/agent_tools.py src/agentos/agentic/runtime.py tests/unit/agentic/tools/test_public_names_runtime.py
git commit -m "feat(tools): speak Claude Code tool names at the model boundary behind a flag"
```

---

### Task 4: Settings — paths, merge, loader with provenance and project security filter

Amended after review W0-R3: fail closed until W1 workspace trust.

**Files:**
- Create: `src/agentos/settings/__init__.py`, `src/agentos/settings/paths.py`, `src/agentos/settings/merge.py`, `src/agentos/settings/loader.py`
- Test: `tests/unit/settings/test_loader.py`

**Interfaces:**
- Produces:
  - `paths.orin_user_dir() -> Path`, `claude_user_dir()`, `orin_managed_dir()`, `claude_managed_dir()`
  - `paths.SettingsPaths(project_root: Path | None)` with `files_for(layer: str) -> tuple[Path, ...]` (lowest precedence first) and `writable_file(layer: str) -> Path`
  - `merge.merge_settings(base: Mapping, overlay: Mapping) -> dict` (overlay wins; lists unioned, overlay items first)
  - `loader.load_effective_settings(project_root: Path | str | None, *, cli: Mapping | None = None, sources: Collection[str] | None = None, trusted: bool = False) -> EffectiveSettings`
  - `EffectiveSettings` fields: `data`, `provenance: Mapping[str, str]`, `layers: tuple[SettingsLayer, ...]`, `errors: tuple[SettingsError, ...]`, `trusted: bool`, `candidate_files: tuple[Path, ...]`; methods `get(dotted, default=None)`, `experimental(name, default=False) -> bool`, `permission_rules(kind) -> tuple[tuple[str, str], ...]` returning `(rule, layer)` highest precedence first
  - `LAYER_ORDER = ("user", "project", "local", "cli", "managed")` (lowest → highest)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/settings/test_loader.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

from agentos.settings import load_effective_settings, merge_settings
from agentos.settings.paths import SettingsPaths, claude_user_dir, orin_managed_dir, orin_user_dir


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def test_merge_scalars_objects_and_lists() -> None:
    base = {"model": "a", "env": {"A": "1", "B": "2"}, "permissions": {"allow": ["Read", "Bash(ls)"]}}
    overlay = {"model": "b", "env": {"B": "3"}, "permissions": {"allow": ["Bash(ls)", "Edit"]}}
    merged = merge_settings(base, overlay)
    assert merged == {"model": "b", "env": {"A": "1", "B": "3"}, "permissions": {"allow": ["Bash(ls)", "Edit", "Read"]}}
    assert base["permissions"]["allow"] == ["Read", "Bash(ls)"]  # inputs untouched


def test_precedence_managed_cli_local_project_user(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"model": "user", "a": "user", "b": "user", "c": "user", "d": "user"})
    _write(project / ".orin" / "settings.json", {"a": "project", "b": "project", "c": "project", "d": "project"})
    _write(project / ".orin" / "settings.local.json", {"b": "local", "c": "local", "d": "local"})
    _write(orin_managed_dir() / "managed-settings.json", {"d": "managed"})
    settings = load_effective_settings(project, cli={"c": "cli", "d": "cli"})
    assert settings.get("model") == "user"
    assert settings.get("a") == "project"
    assert settings.get("b") == "local"
    assert settings.get("c") == "cli"
    assert settings.get("d") == "managed"
    assert settings.provenance["model"] == "user"
    assert settings.provenance["a"] == "project"
    assert settings.provenance["d"] == "managed"


def test_orin_beats_claude_in_the_same_layer_and_both_are_read(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".claude" / "settings.json", {"model": "claude", "outputStyle": "Explanatory", "permissions": {"allow": ["Read"]}})
    _write(project / ".orin" / "settings.json", {"model": "orin", "permissions": {"allow": ["Edit"]}})
    _write(claude_user_dir() / "settings.json", {"permissions": {"deny": ["Bash(rm -rf *)"]}})
    settings = load_effective_settings(project)
    assert settings.get("model") == "orin"
    assert settings.get("outputStyle") == "Explanatory"
    assert set(settings.get("permissions.allow")) == {"Read", "Edit"}
    assert settings.get("permissions.deny") == ["Bash(rm -rf *)"]


def test_managed_dropins_are_sorted_and_dotfiles_ignored() -> None:
    managed = orin_managed_dir()
    _write(managed / "managed-settings.json", {"x": "base"})
    _write(managed / "managed-settings.d" / "20-b.json", {"x": "b"})
    _write(managed / "managed-settings.d" / "10-a.json", {"x": "a", "y": "a"})
    _write(managed / "managed-settings.d" / ".hidden.json", {"x": "hidden"})
    _write(managed / "managed-settings.d" / "notes.txt", "ignored")
    settings = load_effective_settings(None)
    assert settings.get("x") == "b"
    assert settings.get("y") == "a"


def test_invalid_files_are_reported_and_skipped(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", "{not json")
    _write(project / ".orin" / "settings.local.json", "[1, 2]")
    _write(orin_user_dir() / "settings.json", {"model": "ok"})
    settings = load_effective_settings(project)
    assert settings.get("model") == "ok"
    paths = {Path(error.path).name for error in settings.errors}
    assert paths == {"settings.json", "settings.local.json"}


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", '{"pad": "' + ("x" * (1024 * 1024 + 10)) + '"}')
    settings = load_effective_settings(project)
    assert settings.get("pad") is None
    assert settings.errors


def test_project_layers_cannot_inject_privileged_keys(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".claude" / "settings.json", {
        "claudeMd": "evil", "allowedMcpServers": [{"serverName": "x"}], "memory": "/etc",
        "permissions": {"allowBypassPermissionsMode": True, "allow": ["Bash(*)"], "deny": ["Read(.env)"]},
    })
    _write(orin_user_dir() / "settings.json", {"disableAllHooks": True})
    _write(project / ".orin" / "settings.local.json", {"disableAllHooks": False})
    settings = load_effective_settings(project, trusted=True)
    assert settings.get("claudeMd") is None
    assert settings.get("allowedMcpServers") is None
    assert settings.get("memory") is None
    assert settings.get("permissions.allowBypassPermissionsMode") is None
    assert settings.get("disableAllHooks") is True
    assert settings.get("permissions.allow") == ["Bash(*)"]  # trusted workspace keeps allow rules
    assert settings.get("permissions.deny") == ["Read(.env)"]


def test_untrusted_workspace_drops_executable_and_widening_keys(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", {
        "hooks": {"PreToolUse": []}, "apiKeyHelper": "x", "statusLine": {"type": "command"}, "env": {"A": "1"},
        "permissions": {"allow": ["Bash(*)"], "ask": ["Edit"], "deny": ["Read(.env)"], "additionalDirectories": ["/"]},
        "enableAllProjectMcpServers": True, "model": "kept",
    })
    settings = load_effective_settings(project, trusted=False)
    for key in ("hooks", "apiKeyHelper", "statusLine", "env", "permissions.allow", "permissions.additionalDirectories", "enableAllProjectMcpServers"):
        assert settings.get(key) is None, key
    assert settings.get("permissions.ask") == ["Edit"]
    assert settings.get("permissions.deny") == ["Read(.env)"]
    assert settings.get("model") == "kept"
    assert settings.trusted is False


def test_setting_sources_filter_layers(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"u": 1})
    _write(project / ".orin" / "settings.json", {"p": 1})
    _write(project / ".orin" / "settings.local.json", {"l": 1})
    _write(orin_managed_dir() / "managed-settings.json", {"m": 1})
    settings = load_effective_settings(project, sources={"project"}, cli={"c": 1})
    assert settings.get("u") is None and settings.get("l") is None
    assert settings.get("p") == 1 and settings.get("m") == 1 and settings.get("c") == 1


def test_experimental_flags_and_permission_rules_with_sources(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"experimental": {"claudeToolNames": True}, "permissions": {"allow": ["Read", "Edit"]}})
    _write(project / ".orin" / "settings.json", {"experimental": {"other": "yes"}, "permissions": {"allow": ["Read", "Bash(npm test)"]}})
    settings = load_effective_settings(project)
    assert settings.experimental("claudeToolNames") is True
    assert settings.experimental("other") is False  # non-bool is not a flag
    assert settings.experimental("missing") is False
    assert settings.permission_rules("allow") == (("Read", "project"), ("Bash(npm test)", "project"), ("Edit", "user"))
    assert settings.permission_rules("deny") == ()


def test_candidate_files_include_missing_ones(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    settings = load_effective_settings(project)
    names = {str(path) for path in settings.candidate_files}
    assert str(project / ".orin" / "settings.json") in names
    assert str(project / ".claude" / "settings.local.json") in names
    assert str(orin_user_dir() / "settings.json") in names


def test_writable_files_are_orin_files(tmp_path: Path) -> None:
    paths = SettingsPaths(tmp_path)
    assert paths.writable_file("project") == tmp_path / ".orin" / "settings.json"
    assert paths.writable_file("local") == tmp_path / ".orin" / "settings.local.json"
    assert paths.writable_file("user") == orin_user_dir() / "settings.json"


def test_user_dir_env_override_is_respected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "custom"))
    assert orin_user_dir() == tmp_path / "custom"
    monkeypatch.delenv("ORIN_USER_DIR")
    assert orin_user_dir() == Path.home() / ".orin"
    assert os.environ.get("ORIN_USER_DIR") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/settings/test_loader.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.settings'`

- [ ] **Step 3: Write the implementation**

`src/agentos/settings/paths.py`:

```python
"""Where each settings layer lives, for Orin and (read-only) for Claude Code."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def orin_user_dir() -> Path:
    return _env_path("ORIN_USER_DIR") or Path.home() / ".orin"


def claude_user_dir() -> Path:
    return _env_path("ORIN_CLAUDE_HOME") or Path.home() / ".claude"


def orin_managed_dir() -> Path:
    override = _env_path("ORIN_MANAGED_DIR")
    if override is not None:
        return override
    if os.name == "nt":
        return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Orin"
    return Path("/etc/orin")


def claude_managed_dir() -> Path:
    override = _env_path("ORIN_CLAUDE_MANAGED_DIR")
    if override is not None:
        return override
    if os.name == "nt":
        return Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "ClaudeCode"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    return Path("/etc/claude-code")


def _managed_files(directory: Path) -> list[Path]:
    files = [directory / "managed-settings.json"]
    dropins = directory / "managed-settings.d"
    try:
        extra = sorted(
            (item for item in dropins.iterdir() if item.is_file() and item.suffix == ".json" and not item.name.startswith(".")),
            key=lambda item: item.name,
        )
    except OSError:
        extra = []
    files.extend(extra)
    return files


@dataclass(frozen=True, slots=True)
class SettingsPaths:
    project_root: Path | None

    def files_for(self, layer: str) -> tuple[Path, ...]:
        """Files of one layer, lowest precedence first (Claude before Orin)."""
        if layer == "user":
            return (claude_user_dir() / "settings.json", orin_user_dir() / "settings.json")
        if layer == "managed":
            return (*_managed_files(claude_managed_dir()), *_managed_files(orin_managed_dir()))
        if self.project_root is None:
            return ()
        root = Path(self.project_root)
        if layer == "project":
            return (root / ".claude" / "settings.json", root / ".orin" / "settings.json")
        if layer == "local":
            return (root / ".claude" / "settings.local.json", root / ".orin" / "settings.local.json")
        return ()

    def writable_file(self, layer: str) -> Path:
        if layer == "user":
            return orin_user_dir() / "settings.json"
        if self.project_root is None:
            raise ValueError("a project root is required for project and local settings")
        if layer == "project":
            return Path(self.project_root) / ".orin" / "settings.json"
        if layer == "local":
            return Path(self.project_root) / ".orin" / "settings.local.json"
        raise ValueError(f"settings layer '{layer}' is not writable")
```

`src/agentos/settings/merge.py`:

```python
"""Deep merge of settings layers."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any


def _identity(item: Any) -> str:
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(item)


def _union(first: list[Any], second: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in [*first, *second]:
        key = _identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(item))
    return result


def merge_settings(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """``overlay`` has higher precedence. Objects merge, lists union (overlay first), scalars replace."""
    result: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[key] = merge_settings(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = _union(value, current)
        else:
            result[key] = copy.deepcopy(value)
    return result
```

`src/agentos/settings/loader.py`:

```python
"""Effective settings for a workspace: 5 layers, provenance and a project security filter."""

from __future__ import annotations

import copy
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .merge import merge_settings
from .paths import SettingsPaths

LAYER_ORDER = ("user", "project", "local", "cli", "managed")  # lowest -> highest
MAX_SETTINGS_BYTES = 1024 * 1024
_FILTERABLE_SOURCES = {"user", "project", "local"}

# Never honoured from a repository's settings: they are organisation/user decisions.
ALWAYS_IGNORED_FROM_PROJECT = (
    "claudeMd",
    "memory",
    "autoMemoryDirectory",
    "allowedMcpServers",
    "deniedMcpServers",
    "managedMcpServers",
)
# Ignored until the user trusts the workspace: they run code or widen permissions.
UNTRUSTED_IGNORED_FROM_PROJECT = (
    "hooks",
    "apiKeyHelper",
    "statusLine",
    "fileSuggestion",
    "env",
    "awsAuthRefresh",
    "awsCredentialExport",
    "otelHeadersHelper",
    "enableAllProjectMcpServers",
    "enabledMcpjsonServers",
    "extraKnownMarketplaces",
    "enabledPlugins",
    "permissions.allow",
    "permissions.additionalDirectories",
)
# A repository can never turn these on.
PROJECT_TRUE_IGNORED = ("permissions.allowBypassPermissionsMode",)
# A repository can never turn these off.
PROJECT_FALSE_IGNORED = ("disableAllHooks",)


@dataclass(frozen=True, slots=True)
class SettingsError:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class SettingsLayer:
    name: str
    path: Path | None
    data: Mapping[str, Any]


def _split(dotted: str) -> list[str]:
    return [part for part in dotted.split(".") if part]


def _get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in _split(dotted):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _drop(data: dict[str, Any], dotted: str) -> None:
    parts = _split(dotted)
    current: Any = data
    for part in parts[:-1]:
        current = current.get(part) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            return
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _filter_project(data: dict[str, Any], *, trusted: bool) -> dict[str, Any]:
    filtered = copy.deepcopy(data)
    for key in ALWAYS_IGNORED_FROM_PROJECT:
        _drop(filtered, key)
    if not trusted:
        for key in UNTRUSTED_IGNORED_FROM_PROJECT:
            _drop(filtered, key)
    for key in PROJECT_TRUE_IGNORED:
        if _get(filtered, key) is True:
            _drop(filtered, key)
    for key in PROJECT_FALSE_IGNORED:
        if _get(filtered, key) is False:
            _drop(filtered, key)
    return filtered


def _read(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not path.is_file():
            return None, None
        size = path.stat().st_size
        if size > MAX_SETTINGS_BYTES:
            return None, f"file is larger than {MAX_SETTINGS_BYTES} bytes"
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        return None, f"cannot read file: {error}"
    if not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, f"invalid JSON at line {error.lineno} column {error.colno}: {error.msg}"
    if not isinstance(parsed, dict):
        return None, "the top level must be a JSON object"
    return parsed, None


def _flatten(data: Mapping[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            keys.extend(_flatten(value, dotted))
        else:
            keys.append(dotted)
    return keys


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    data: Mapping[str, Any]
    provenance: Mapping[str, str]
    layers: tuple[SettingsLayer, ...]
    errors: tuple[SettingsError, ...]
    trusted: bool
    candidate_files: tuple[Path, ...] = field(default_factory=tuple)

    def get(self, dotted: str, default: Any = None) -> Any:
        value = _get(self.data, dotted)
        return default if value is None else value

    def experimental(self, name: str, default: bool = False) -> bool:
        value = _get(self.data, f"experimental.{name}")
        return value if isinstance(value, bool) else default

    def permission_rules(self, kind: str) -> tuple[tuple[str, str], ...]:
        """``(rule, layer)`` for one of allow/ask/deny, highest precedence first, deduplicated."""
        seen: set[str] = set()
        rules: list[tuple[str, str]] = []
        for layer in reversed(self.layers):
            values = _get(layer.data, f"permissions.{kind}")
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value.strip() and value.strip() not in seen:
                    seen.add(value.strip())
                    rules.append((value.strip(), layer.name))
        return tuple(rules)


def empty_settings() -> EffectiveSettings:
    return EffectiveSettings({}, {}, (), (), True, ())


def load_effective_settings(
    project_root: Path | str | None,
    *,
    cli: Mapping[str, Any] | None = None,
    sources: Collection[str] | None = None,
    trusted: bool = False,
) -> EffectiveSettings:
    paths = SettingsPaths(Path(project_root) if project_root is not None else None)
    allowed_sources = set(sources) if sources is not None else set(_FILTERABLE_SOURCES)
    layers: list[SettingsLayer] = []
    errors: list[SettingsError] = []
    candidates: list[Path] = []
    for layer in LAYER_ORDER:
        if layer == "cli":
            if cli:
                layers.append(SettingsLayer("cli", None, copy.deepcopy(dict(cli))))
            continue
        files = paths.files_for(layer)
        candidates.extend(files)
        if layer in _FILTERABLE_SOURCES and layer not in allowed_sources:
            continue
        for path in files:
            data, error = _read(path)
            if error is not None:
                errors.append(SettingsError(str(path), error))
                continue
            if data is None:
                continue
            if layer in {"project", "local"}:
                data = _filter_project(data, trusted=trusted)
            layers.append(SettingsLayer(layer, path, data))
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for item in layers:
        merged = merge_settings(merged, item.data)
        for key in _flatten(item.data):
            provenance[key] = item.name
    return EffectiveSettings(merged, provenance, tuple(layers), tuple(errors), bool(trusted), tuple(candidates))
```

`src/agentos/settings/__init__.py`:

```python
"""Layered settings: managed > CLI > local > project > user, with Claude Code interop."""

from .loader import (
    LAYER_ORDER,
    EffectiveSettings,
    SettingsError,
    SettingsLayer,
    empty_settings,
    load_effective_settings,
)
from .merge import merge_settings
from .paths import SettingsPaths

__all__ = [
    "LAYER_ORDER",
    "EffectiveSettings",
    "SettingsError",
    "SettingsLayer",
    "SettingsPaths",
    "empty_settings",
    "load_effective_settings",
    "merge_settings",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/settings`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/settings tests/unit/settings/test_loader.py
git commit -m "feat(settings): 5-layer effective settings with .claude interop and project filter"
```

---

### Task 5: Settings — atomic locked writer and change detection

**Files:**
- Create: `src/agentos/settings/writer.py`, `src/agentos/settings/watcher.py`
- Modify: `src/agentos/settings/__init__.py` (re-exports)
- Test: `tests/unit/settings/test_writer.py`, `tests/unit/settings/test_watcher.py`

**Interfaces:**
- Produces:
  - `writer.SettingsWriteError(RuntimeError)`
  - `writer.update_settings_file(path: Path | str, mutate: Callable[[dict], None]) -> dict`
  - `writer.add_permission_rule(path: Path | str, kind: str, rule: str) -> bool` (True when added, False when already present)
  - `watcher.detect_changes(key: str, paths: Iterable[Path]) -> tuple[Path, ...]` (first call per key returns `()`; later calls return the files whose mtime/size/existence changed)
  - `watcher.reset_snapshots() -> None` (tests)

- [ ] **Step 1: Write the failing tests**

`tests/unit/settings/test_writer.py`:

```python
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agentos.settings.writer import SettingsWriteError, add_permission_rule, update_settings_file


def test_creates_file_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "settings.json"
    result = update_settings_file(target, lambda data: data.setdefault("model", "x"))
    assert result == {"model": "x"}
    assert json.loads(target.read_text(encoding="utf-8")) == {"model": "x"}
    assert not list(target.parent.glob("*.tmp")) and not list(target.parent.glob("*.lock"))


def test_refuses_to_overwrite_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(SettingsWriteError):
        update_settings_file(target, lambda data: data.update(model="x"))
    assert target.read_text(encoding="utf-8") == "{broken"


def test_refuses_non_object_root(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("[1]", encoding="utf-8")
    with pytest.raises(SettingsWriteError):
        update_settings_file(target, lambda data: None)


def test_mutation_error_leaves_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"keep": true}', encoding="utf-8")

    def boom(data: dict) -> None:
        data["keep"] = False
        raise ValueError("boom")

    with pytest.raises(ValueError):
        update_settings_file(target, boom)
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}
    assert not (tmp_path / "settings.json.lock").exists()


def test_concurrent_writers_do_not_lose_updates(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"

    def worker(index: int) -> None:
        for step in range(10):
            add_permission_rule(target, "allow", f"Bash(task-{index}-{step})")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rules = json.loads(target.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert len(rules) == 40 and len(set(rules)) == 40


def test_add_permission_rule_validates_and_deduplicates(tmp_path: Path) -> None:
    target = tmp_path / "settings.local.json"
    assert add_permission_rule(target, "allow", "Bash(npm test)") is True
    assert add_permission_rule(target, "allow", "Bash(npm test)") is False
    with pytest.raises(ValueError):
        add_permission_rule(target, "maybe", "Read")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "Bash(a)\nBash(b)")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "x" * 1001)
    assert json.loads(target.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Bash(npm test)"]}}


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    import os
    import time

    target = tmp_path / "settings.json"
    lock = tmp_path / "settings.json.lock"
    lock.write_text("999999", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))
    update_settings_file(target, lambda data: data.update(ok=True))
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
```

`tests/unit/settings/test_watcher.py`:

```python
from __future__ import annotations

import os
import time
from pathlib import Path

from agentos.settings.watcher import detect_changes, reset_snapshots


def test_first_call_records_and_later_calls_report_changes(tmp_path: Path) -> None:
    reset_snapshots()
    existing = tmp_path / "a.json"
    existing.write_text("{}", encoding="utf-8")
    missing = tmp_path / "b.json"
    assert detect_changes("k", [existing, missing]) == ()
    assert detect_changes("k", [existing, missing]) == ()
    existing.write_text('{"x": 1}', encoding="utf-8")
    later = time.time() + 5
    os.utime(existing, (later, later))
    missing.write_text("{}", encoding="utf-8")
    changed = detect_changes("k", [existing, missing])
    assert set(changed) == {existing, missing}
    assert detect_changes("k", [existing, missing]) == ()
    missing.unlink()
    assert detect_changes("k", [existing, missing]) == (missing,)


def test_keys_are_independent(tmp_path: Path) -> None:
    reset_snapshots()
    path = tmp_path / "s.json"
    assert detect_changes("one", [path]) == ()
    path.write_text("{}", encoding="utf-8")
    assert detect_changes("two", [path]) == ()
    assert detect_changes("one", [path]) == (path,)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/settings/test_writer.py tests/unit/settings/test_watcher.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.settings.writer'`

- [ ] **Step 3: Write the implementation**

`src/agentos/settings/writer.py`:

```python
"""Atomic, locked writes of settings JSON files."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 30.0
MAX_RULE_LENGTH = 1000
RULE_KINDS = ("allow", "ask", "deny")

# Serialises writers inside this process; the lock file serialises processes.
_process_lock = threading.Lock()


class SettingsWriteError(RuntimeError):
    pass


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue
            if age > STALE_LOCK_SECONDS:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise SettingsWriteError(f"settings file is locked by another writer: {path}") from None
            time.sleep(0.02)
            continue
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _replace(source: Path, target: Path) -> None:
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows refuses the rename while another process has the file open.
            if attempt == 9:
                raise
            time.sleep(0.05)


def _read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SettingsWriteError(f"refusing to overwrite invalid JSON in {path}: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise SettingsWriteError(f"refusing to overwrite {path}: the top level is not a JSON object")
    return parsed


def update_settings_file(path: Path | str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock, _file_lock(target):
        current = _read_object(target)
        updated = copy.deepcopy(current)
        mutate(updated)
        if not isinstance(updated, dict):
            raise SettingsWriteError("settings must remain a JSON object")
        text = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            _replace(temporary, target)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return updated


def add_permission_rule(path: Path | str, kind: str, rule: str) -> bool:
    if kind not in RULE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(RULE_KINDS)}")
    clean = str(rule or "").strip()
    if not clean or len(clean) > MAX_RULE_LENGTH or "\n" in clean or "\r" in clean:
        raise ValueError("a permission rule must be one line of at most 1000 characters")
    added = False

    def mutate(data: dict[str, Any]) -> None:
        nonlocal added
        permissions = data.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
            data["permissions"] = permissions
        rules = permissions.get(kind)
        if not isinstance(rules, list):
            rules = []
            permissions[kind] = rules
        if clean not in rules:
            rules.append(clean)
            added = True

    update_settings_file(path, mutate)
    return added
```

`src/agentos/settings/watcher.py`:

```python
"""Detect settings files that changed between two turns (ConfigChange)."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

_Stamp = tuple[int, int] | None
_snapshots: dict[str, dict[Path, _Stamp]] = {}
_lock = threading.Lock()


def _stamp(path: Path) -> _Stamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def detect_changes(key: str, paths: Iterable[Path]) -> tuple[Path, ...]:
    current = {Path(path): _stamp(Path(path)) for path in paths}
    with _lock:
        previous = _snapshots.get(key)
        _snapshots[key] = current
    if previous is None:
        return ()
    return tuple(path for path, stamp in current.items() if previous.get(path, None) != stamp)


def reset_snapshots() -> None:
    with _lock:
        _snapshots.clear()
```

Append to `src/agentos/settings/__init__.py` imports and `__all__`:

```python
from .watcher import detect_changes
from .writer import SettingsWriteError, add_permission_rule, update_settings_file
```

and add `"SettingsWriteError"`, `"add_permission_rule"`, `"detect_changes"`, `"update_settings_file"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/settings`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/settings tests/unit/settings
git commit -m "feat(settings): atomic locked writer and change detection"
```

---

### Task 6: Lifecycle bus

**Files:**
- Create: `src/agentos/agentic/lifecycle.py`
- Test: `tests/unit/agentic/test_lifecycle_bus.py`

**Interfaces:**
- Produces:
  - `class HookEvent(StrEnum)` with the 33 Claude Code events + `STATUS_LINE`, `FILE_SUGGESTION`
  - `CLAUDE_HOOK_EVENTS: tuple[HookEvent, ...]` (the 33, in canonical order)
  - `NON_BLOCKING_EVENTS: frozenset[HookEvent]`
  - `@dataclass(frozen=True) LifecycleEvent(name: HookEvent, payload: Mapping[str, Any])`
  - `@dataclass LifecycleDecision(permission_decision: str | None, reason: str | None, updated_input: dict | None, blocked: bool, stop: bool, stop_reason: str | None, additional_context: list[str], system_messages: list[str])` with `merge(other)`
  - `class LifecycleBus` with `bind(**values)`, `subscribe(fn) -> Callable[[], None]`, `emit(name, /, **payload) -> LifecycleDecision`

- [ ] **Step 1: Write the failing tests**

`tests/unit/agentic/test_lifecycle_bus.py`:

```python
from __future__ import annotations

import threading

from agentos.agentic.lifecycle import (
    CLAUDE_HOOK_EVENTS,
    NON_BLOCKING_EVENTS,
    HookEvent,
    LifecycleBus,
    LifecycleDecision,
)


def test_catalog_matches_claude_code() -> None:
    assert [event.value for event in CLAUDE_HOOK_EVENTS] == [
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "Notification",
        "UserPromptSubmit", "UserPromptExpansion", "SessionStart", "SessionEnd", "Stop", "StopFailure",
        "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "PreModelSwitch", "PostModelSwitch",
        "PermissionRequest", "PermissionDenied", "Setup", "TeammateIdle", "TaskCreated", "TaskCompleted",
        "Elicitation", "ElicitationResult", "ConfigChange", "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded", "CwdChanged", "FileChanged", "DirectoryAdded", "MessageDisplay",
    ]
    assert len(set(HookEvent)) == 35
    assert HookEvent.STATUS_LINE.value == "StatusLine" and HookEvent.FILE_SUGGESTION.value == "FileSuggestion"
    assert {event.value for event in NON_BLOCKING_EVENTS} == {
        "Notification", "SessionStart", "SessionEnd", "Setup", "StopFailure", "SubagentStart",
        "PostToolUseFailure", "PostCompact", "PostModelSwitch", "PermissionDenied", "WorktreeCreate",
        "WorktreeRemove", "InstructionsLoaded", "CwdChanged", "FileChanged", "DirectoryAdded",
        "MessageDisplay", "StatusLine", "FileSuggestion",
    }


def test_emit_merges_base_payload_and_event_name() -> None:
    bus = LifecycleBus(base={"session_id": "s1"})
    bus.bind(cwd="/w", permission_mode="default", ignored=None)
    seen = []
    bus.subscribe(seen.append)
    decision = bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "a"})
    assert isinstance(decision, LifecycleDecision) and decision.permission_decision is None
    payload = dict(seen[0].payload)
    assert seen[0].name is HookEvent.PRE_TOOL_USE
    assert payload == {
        "session_id": "s1", "cwd": "/w", "permission_mode": "default", "transcript_path": "",
        "tool_name": "Read", "tool_input": {"file_path": "a"}, "hook_event_name": "PreToolUse",
    }
    assert bus.emit("PreToolUse").permission_decision is None  # string names accepted


def test_decisions_merge_deny_over_ask_over_allow() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="allow", updated_input={"a": 1}, additional_context=["one"]))
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="deny", reason="no", additional_context=["two"]))
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="ask", updated_input={"a": 2}))
    decision = bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash")
    assert decision.permission_decision == "deny"
    assert decision.reason == "no"
    assert decision.updated_input == {"a": 1}
    assert decision.additional_context == ["one", "two"]


def test_blocked_and_stop_are_sticky() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="keep going"))
    bus.subscribe(lambda event: LifecycleDecision(stop=True, stop_reason="halt", system_messages=["note"]))
    decision = bus.emit(HookEvent.STOP)
    assert decision.blocked and decision.stop and decision.stop_reason == "halt"
    assert decision.system_messages == ["note"]


def test_non_blocking_events_discard_control_but_keep_session_start_context() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, permission_decision="deny", additional_context=["ctx"], system_messages=["m"]))
    post = bus.emit(HookEvent.POST_COMPACT)
    assert not post.blocked and post.permission_decision is None and post.additional_context == []
    assert post.system_messages == ["m"]
    start = bus.emit(HookEvent.SESSION_START)
    assert not start.blocked and start.additional_context == ["ctx"]


def test_a_failing_subscriber_never_breaks_the_emitter() -> None:
    bus = LifecycleBus()
    seen = []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(seen.append)
    bus.subscribe(lambda event: "not a decision")
    decision = bus.emit(HookEvent.POST_TOOL_USE)
    assert len(seen) == 1 and decision.permission_decision is None


def test_unsubscribe_and_thread_safety() -> None:
    bus = LifecycleBus()
    counter = {"n": 0}
    lock = threading.Lock()

    def count(event):
        with lock:
            counter["n"] += 1

    unsubscribe = bus.subscribe(count)
    threads = [threading.Thread(target=lambda: [bus.emit(HookEvent.POST_TOOL_USE) for _ in range(50)]) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counter["n"] == 200
    unsubscribe()
    unsubscribe()  # idempotent
    bus.emit(HookEvent.POST_TOOL_USE)
    assert counter["n"] == 200


def test_payload_is_read_only() -> None:
    bus = LifecycleBus()
    seen = []
    bus.subscribe(seen.append)
    bus.emit(HookEvent.STOP, value=1)
    try:
        seen[0].payload["value"] = 2  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("payload must be immutable")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_bus.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.agentic.lifecycle'`

- [ ] **Step 3: Write the implementation**

`src/agentos/agentic/lifecycle.py`:

```python
"""Typed lifecycle events at the points Claude Code exposes as hooks.

W0 only emits. W2 subscribes the hook engine and applies the returned
decisions. Emitting is always safe: a failing subscriber is logged and
skipped, never raised into the turn.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(StrEnum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    POST_TOOL_BATCH = "PostToolBatch"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    USER_PROMPT_EXPANSION = "UserPromptExpansion"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PRE_MODEL_SWITCH = "PreModelSwitch"
    POST_MODEL_SWITCH = "PostModelSwitch"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    DIRECTORY_ADDED = "DirectoryAdded"
    MESSAGE_DISPLAY = "MessageDisplay"
    STATUS_LINE = "StatusLine"
    FILE_SUGGESTION = "FileSuggestion"


CLAUDE_HOOK_EVENTS: tuple[HookEvent, ...] = tuple(
    event for event in HookEvent if event not in {HookEvent.STATUS_LINE, HookEvent.FILE_SUGGESTION}
)

NON_BLOCKING_EVENTS: frozenset[HookEvent] = frozenset({
    HookEvent.NOTIFICATION, HookEvent.SESSION_START, HookEvent.SESSION_END, HookEvent.SETUP,
    HookEvent.STOP_FAILURE, HookEvent.SUBAGENT_START, HookEvent.POST_TOOL_USE_FAILURE,
    HookEvent.POST_COMPACT, HookEvent.POST_MODEL_SWITCH, HookEvent.PERMISSION_DENIED,
    HookEvent.WORKTREE_CREATE, HookEvent.WORKTREE_REMOVE, HookEvent.INSTRUCTIONS_LOADED,
    HookEvent.CWD_CHANGED, HookEvent.FILE_CHANGED, HookEvent.DIRECTORY_ADDED,
    HookEvent.MESSAGE_DISPLAY, HookEvent.STATUS_LINE, HookEvent.FILE_SUGGESTION,
})

# Non-blocking events that may still inject context into the conversation.
_CONTEXT_EVENTS = frozenset({HookEvent.SESSION_START, HookEvent.SUBAGENT_START})
_DECISION_RANK = {"allow": 1, "ask": 2, "deny": 3}


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    name: HookEvent
    payload: Mapping[str, Any]


@dataclass(slots=True)
class LifecycleDecision:
    permission_decision: str | None = None
    reason: str | None = None
    updated_input: dict[str, Any] | None = None
    blocked: bool = False
    stop: bool = False
    stop_reason: str | None = None
    additional_context: list[str] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)

    def merge(self, other: "LifecycleDecision") -> None:
        incoming = other.permission_decision if other.permission_decision in _DECISION_RANK else None
        if incoming is not None and _DECISION_RANK[incoming] > _DECISION_RANK.get(self.permission_decision or "", 0):
            self.permission_decision = incoming
            self.reason = other.reason or self.reason
        elif other.reason and not self.reason:
            self.reason = other.reason
        if self.updated_input is None and other.updated_input is not None:
            self.updated_input = dict(other.updated_input)
        self.blocked = self.blocked or other.blocked
        if other.stop and not self.stop:
            self.stop = True
            self.stop_reason = other.stop_reason
        self.additional_context.extend(item for item in other.additional_context if item)
        self.system_messages.extend(item for item in other.system_messages if item)


Subscriber = Callable[[LifecycleEvent], object]


class LifecycleBus:
    def __init__(self, *, base: Mapping[str, Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []
        self._base: dict[str, Any] = {"transcript_path": ""}
        self._base.update(dict(base or {}))

    def bind(self, **values: Any) -> None:
        with self._lock:
            self._base.update({key: value for key, value in values.items() if value is not None})

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def emit(self, name: HookEvent | str, /, **payload: Any) -> LifecycleDecision:
        event_name = HookEvent(name)
        with self._lock:
            subscribers = list(self._subscribers)
            body = dict(self._base)
        body.update(payload)
        body["hook_event_name"] = event_name.value
        event = LifecycleEvent(event_name, MappingProxyType(body))
        decision = LifecycleDecision()
        for subscriber in subscribers:
            try:
                result = subscriber(event)
            except Exception:  # noqa: BLE001 - a subscriber never breaks the turn
                logger.exception("lifecycle subscriber failed for %s", event_name.value)
                continue
            if isinstance(result, LifecycleDecision):
                decision.merge(result)
        if event_name in NON_BLOCKING_EVENTS:
            return LifecycleDecision(
                additional_context=decision.additional_context if event_name in _CONTEXT_EVENTS else [],
                system_messages=decision.system_messages,
            )
        return decision
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_bus.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/lifecycle.py tests/unit/agentic/test_lifecycle_bus.py
git commit -m "feat(lifecycle): typed lifecycle bus with the Claude Code hook catalog"
```

---

### Task 7: Emit lifecycle events from the toolset

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Test: `tests/unit/agentic/test_lifecycle_toolset.py`

**Interfaces:**
- Consumes: `LifecycleBus`, `HookEvent` (Task 6); `public_name`, `arguments_out` (Task 2).
- Produces: `AgentToolset(..., lifecycle: LifecycleBus | None = None)`; `invoke` emits `PreToolUse`, then exactly one of `PostToolUse` / `PostToolUseFailure` / `PermissionDenied`; plus side events `TaskCreated`, `TaskCompleted`, `WorktreeCreate`, `WorktreeRemove`, `CwdChanged`; `PermissionRequest` before an interactive permission wait. Tool names in payloads are **public** names; `tool_input` is the **public** argument shape.

- [ ] **Step 1: Write the failing tests**

`tests/unit/agentic/test_lifecycle_toolset.py`:

```python
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.workspace import ConversationWorkspace


def _setup(tmp_path: Path, **kwargs):
    workspace = ConversationWorkspace(tmp_path, "conv-lifecycle")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    (workspace.root / "sub").mkdir()
    bus = LifecycleBus(base={"session_id": "conv-lifecycle"})
    events = []
    bus.subscribe(events.append)
    return AgentToolset(workspace, lifecycle=bus, **kwargs), events


def _names(events):
    return [event.name for event in events]


def test_successful_call_emits_pre_and_post_with_public_shapes(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("read_file", {"path": "a.txt"}, call_id="call-7")
    assert outcome.status == "succeeded"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE]
    pre, post = (dict(event.payload) for event in events)
    assert pre["tool_name"] == "Read" and pre["tool_input"] == {"file_path": "a.txt"} and pre["tool_use_id"] == "call-7"
    assert post["tool_name"] == "Read" and post["tool_response"]["status"] == "succeeded"
    assert pre["session_id"] == "conv-lifecycle"


def test_failed_call_emits_post_tool_use_failure(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("read_file", {"path": "missing.txt"})
    assert outcome.status == "failed"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE]
    assert dict(events[1].payload)["error"]


def test_unknown_tool_still_emits_and_never_raises(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("NoSuchTool", {})
    assert outcome.error_code == "UNKNOWN_TOOL"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE]


def test_plan_mode_denial_emits_permission_denied(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path, code_mode_active=True, permission_mode="plan_mode")
    outcome = toolset.invoke("write_file", {"path": "b.txt", "content": "x"})
    assert outcome.status == "failed"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.PERMISSION_DENIED]
    assert dict(events[1].payload)["tool_name"] == "Write"


def test_task_events(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    created = toolset.invoke("task_create", {"subject": "Write tests"})
    assert created.status == "succeeded"
    assert HookEvent.TASK_CREATED in _names(events)
    created_payload = dict(next(e for e in events if e.name is HookEvent.TASK_CREATED).payload)
    assert created_payload["task_subject"] == "Write tests"
    task_id = created_payload["task_id"]
    assert task_id
    events.clear()
    toolset.invoke("task_update", {"taskId": task_id, "status": "completed"})
    assert HookEvent.TASK_COMPLETED in _names(events)
    assert dict(next(e for e in events if e.name is HookEvent.TASK_COMPLETED).payload)["task_id"] == task_id


def test_cwd_change_emits_cwd_changed(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("run_command", {"command": "cd sub"})
    assert outcome.status == "succeeded", outcome.content
    changed = [dict(e.payload) for e in events if e.name is HookEvent.CWD_CHANGED]
    assert len(changed) == 1
    assert changed[0]["new_cwd"].endswith("sub")


def test_no_bus_means_no_overhead_and_same_result(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-nobus")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    assert AgentToolset(workspace).invoke("read_file", {"path": "a.txt"}).status == "succeeded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_toolset.py`
Expected: FAIL with `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'lifecycle'`

- [ ] **Step 3: Implement**

3a. Extend the Task 3 import in `agent_tools.py`:

```python
from .lifecycle import HookEvent, LifecycleBus
from .tools.public_names import (
    PublicToolNames,
    arguments_in as _public_arguments_in,
    arguments_out as _public_arguments_out,
    internal_name as _internal_tool_name,
    public_name as _public_tool_name,
)
```

and add this module-level constant after `MAX_TOOL_RESULT_CHARS`-related constants (anywhere above `class AgentToolset`):

```python
# Error codes that mean the call was refused by policy rather than failing.
PERMISSION_DENIAL_CODES = frozenset({
    "CODE_PERMISSION_DENIED",
    "CODE_PLAN_MODE_READONLY",
    "READ_ONLY_SUBAGENT",
    "CODE_PLAN_APPROVAL_REQUIRED",
    "CODE_DEPLOY_CONFIRMATION_REQUIRED",
    "CODE_PUSH_CONFIRMATION_REQUIRED",
})
```

3b. Add constructor parameter (after `public_tool_names: bool = False,`):

```python
        lifecycle: LifecycleBus | None = None,
```

and at the end of `__init__`:

```python
        self._lifecycle: LifecycleBus | None = lifecycle
```

3c. Rename the existing `def invoke(` method to `def _invoke_unobserved(` (keep its body exactly as it is after Task 3). Then add, directly above it, the new public `invoke`:

```python
    def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str | None = None,
        invocation_id: str | None = None,
    ) -> ToolOutcome:
        bus = self._lifecycle
        if bus is None:
            return self._invoke_unobserved(name, arguments, call_id=call_id, invocation_id=invocation_id)
        internal = _internal_tool_name(name)
        if internal != name:
            arguments = _public_arguments_in(internal, dict(arguments or {}))
        tool_name = _public_tool_name(internal)
        tool_input = _public_arguments_out(internal, dict(arguments or {}))
        tool_use_id = str(call_id or invocation_id or getattr(self, "_active_invocation_id", None) or "")
        bus.emit(HookEvent.PRE_TOOL_USE, tool_name=tool_name, tool_input=tool_input, tool_use_id=tool_use_id)
        cwd_before = self._current_cwd
        outcome = self._invoke_unobserved(internal, arguments, call_id=call_id, invocation_id=invocation_id)
        common = {"tool_name": tool_name, "tool_input": tool_input, "tool_use_id": tool_use_id}
        if outcome.error_code in PERMISSION_DENIAL_CODES:
            bus.emit(HookEvent.PERMISSION_DENIED, **common, reason=outcome.content[:2000])
        elif outcome.status == "succeeded":
            bus.emit(
                HookEvent.POST_TOOL_USE,
                **common,
                tool_response={"status": outcome.status, "summary": outcome.summary, "content": outcome.content[:4000]},
            )
            self._emit_tool_side_events(bus, internal, dict(arguments or {}), outcome, cwd_before)
        else:
            bus.emit(
                HookEvent.POST_TOOL_USE_FAILURE,
                **common,
                error=outcome.content[:2000] or outcome.summary,
                error_code=outcome.error_code,
            )
        return outcome

    def _emit_tool_side_events(
        self,
        bus: LifecycleBus,
        internal: str,
        arguments: Mapping[str, Any],
        outcome: ToolOutcome,
        cwd_before: Path,
    ) -> None:
        payload = outcome.payload or {}
        if internal == "task_create":
            task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
            bus.emit(
                HookEvent.TASK_CREATED,
                task_id=str(task.get("id") or payload.get("task_id") or ""),
                task_subject=str(arguments.get("subject") or arguments.get("content") or ""),
                task_description=str(arguments.get("description") or ""),
            )
        elif internal == "task_update" and str(arguments.get("status") or "") == "completed":
            bus.emit(
                HookEvent.TASK_COMPLETED,
                task_id=str(arguments.get("taskId") or arguments.get("task_id") or arguments.get("id") or ""),
            )
        elif internal == "enter_worktree":
            bus.emit(HookEvent.WORKTREE_CREATE, worktree=dict(payload.get("worktree") or {}))
        elif internal == "exit_worktree" and str(arguments.get("action") or "keep") == "remove":
            bus.emit(HookEvent.WORKTREE_REMOVE, worktree_name=str(self._active_worktree_name or ""))
        if self._current_cwd != cwd_before:
            bus.emit(HookEvent.CWD_CHANGED, old_cwd=str(cwd_before), new_cwd=str(self._current_cwd))
```

3d. Find where `task_create` builds its return payload (`def task_create(` around line 5244) and make sure the returned `"payload"` dict contains the created task under the key `"task"` (a dict with at least `"id"`). If the payload already exposes the task under another key, add `"task": <that dict>` alongside it — do not remove existing keys. Verify by running:

Run: `uv run python -c "from pathlib import Path; import tempfile; from agentos.agentic.agent_tools import AgentToolset; from agentos.agentic.workspace import ConversationWorkspace; t=AgentToolset(ConversationWorkspace(Path(tempfile.mkdtemp()),'x')); print(t.invoke('task_create', {'subject':'s'}).payload)"`
Expected: a dict that contains `'task': {... 'id': ...}`

3e. Inside `_invoke_unobserved`, just before the line `perm_result = self._permission_broker.request_and_wait(`, add:

```python
                    if self._lifecycle is not None:
                        self._lifecycle.emit(
                            HookEvent.PERMISSION_REQUEST,
                            tool_name=_public_tool_name(name),
                            tool_input=_public_arguments_out(name, dict(arguments)),
                            permission_suggestions=[],
                        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_toolset.py tests/unit/agentic`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/agent_tools.py tests/unit/agentic/test_lifecycle_toolset.py
git commit -m "feat(lifecycle): emit tool, permission, task, worktree and cwd events from the toolset"
```

---

### Task 8: Emit lifecycle events from the runtime

**Files:**
- Modify: `src/agentos/agentic/runtime.py`
- Test: `tests/unit/agentic/test_lifecycle_runtime.py`

**Interfaces:**
- Produces: `AgenticTurnRuntime(..., lifecycle: LifecycleBus | None = None)`; emits `PostToolBatch` (after each tool batch), `Stop` (on successful completion), `StopFailure` (in `_fail`), `PreCompact`/`PostCompact` (around `_run_compaction` when a compaction actually happens). Public tool names in `PostToolBatch.tool_calls`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/agentic/test_lifecycle_runtime.py`:

```python
from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "workspace_id": "workspace-1", "agent_id": "agent-1", "execution_id": "execution-1",
        "provider": "openrouter", "model_id": "m", "user_message_id": "u1", "assistant_message_id": "a1",
    }


class Store:
    def __init__(self) -> None:
        self.turn = _turn()

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass


class ToolThenText:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        self.calls += 1
        if self.calls == 1:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse(['data: {"choices":[{"delta":{"content":"final answer"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


class Toolset:
    def schemas(self, allowed=None): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})
    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


class Broken:
    def stream(self, request):
        raise RuntimeError("down")


def _bus():
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    return bus, events


def test_batch_and_stop_are_emitted() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset(), lifecycle=bus).run("turn-1")
    assert result.state == "completed"
    names = [event.name for event in events]
    assert names == [HookEvent.POST_TOOL_BATCH, HookEvent.STOP]
    batch = dict(events[0].payload)
    assert batch["tool_calls"] == [{"tool_name": "Read", "tool_use_id": "c1", "status": "succeeded"}]
    stop = dict(events[1].payload)
    assert stop["stop_hook_active"] is False and stop["last_assistant_message"] == "final answer"


def test_failure_emits_stop_failure() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=Broken(), toolset=Toolset(), lifecycle=bus).run("turn-1")
    assert result.state == "failed"
    assert [event.name for event in events] == [HookEvent.STOP_FAILURE]
    assert dict(events[0].payload)["error_code"] == result.error_code


def test_compaction_emits_pre_and_post() -> None:
    bus, events = _bus()
    runtime = AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset(), lifecycle=bus)
    messages: list[dict[str, object]] = [{"role": "user", "content": "task"}]
    for index in range(12):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "x" * 200})
    runtime._request_compaction_summary = lambda source, turn, code_mode=False: (
        "## Arquivos tocados\n- a\n## Decisões\n- b\n## Dados apurados\n- c\n## Pendências\n- d"
    )
    runtime._run_compaction(messages, _turn())
    names = [event.name for event in events]
    assert names == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT]
    assert dict(events[0].payload)["trigger"] == "auto"


def test_no_bus_is_fine() -> None:
    assert AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset()).run("turn-1").state == "completed"
```

Note for the implementer: if `_has_sections` in `runtime.py` requires different headings than the four used in `test_compaction_emits_pre_and_post`, read `_has_sections` (around line 1075) and use exactly the headings it checks for. The test only cares that the compaction completes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_runtime.py`
Expected: FAIL with `TypeError: AgenticTurnRuntime.__init__() got an unexpected keyword argument 'lifecycle'`

- [ ] **Step 3: Implement in `runtime.py`**

3a. Imports at top:

```python
from .lifecycle import HookEvent, LifecycleBus
from .tools.public_names import public_name as _public_tool_name
```

3b. `__init__`: add parameter `lifecycle: LifecycleBus | None = None,` after `learning_sink`, and in the body:

```python
        self.lifecycle = lifecycle
```

3c. Add a helper method next to `_hooks`:

```python
    def _emit(self, name: HookEvent, **payload: object):
        bus = getattr(self, "lifecycle", None)
        if bus is None:
            return None
        try:
            return bus.emit(name, **payload)
        except Exception:  # noqa: BLE001 - lifecycle never breaks a turn
            return None
```

3d. At the end of `_run_toolset`, replace the final `return results` with:

```python
        self._emit(
            HookEvent.POST_TOOL_BATCH,
            tool_calls=[
                {"tool_name": _public_tool_name(str(item.get("name") or "")), "tool_use_id": str(item.get("id") or ""), "status": str(item.get("status") or "")}
                for item in results
            ],
        )
        return results
```

3e. In `run`, in the successful completion branch, directly before `self._settle_remaining_todos(turn, str(turn_id))` that precedes `self._life(turn, "completed")`, add:

```python
                self._emit(HookEvent.STOP, stop_hook_active=False, last_assistant_message="".join(text_parts)[:4000])
```

(Only the branch that ends with `self._life(turn, "completed")`; not the `completed_with_caveats` branch.)

3f. In `_fail`, as its first line:

```python
        self._emit(HookEvent.STOP_FAILURE, error_code=code)
```

3g. In `_run_compaction`, right after `if not compact_indices: return` add:

```python
        self._emit(HookEvent.PRE_COMPACT, trigger="auto", custom_instructions="")
```

and right after the existing `self._hooks(turn, "PostCompact", compaction_count=self._compaction_count)` add:

```python
        self._emit(HookEvent.POST_COMPACT, trigger="auto", compaction_count=self._compaction_count)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_runtime.py tests/unit/agentic`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_lifecycle_runtime.py
git commit -m "feat(lifecycle): emit batch, stop, failure and compaction events from the runtime"
```

---

### Task 9: Wire settings and the bus into TurnSession

**Files:**
- Modify: `src/agentos/agentic/session.py` (`_workspace_guidance` ~line 545; `_toolset` ~line 1976; `build_runtime` ~line 2113; `_execute_subagent_run` ~line 1469)
- Test: `tests/unit/agentic/test_lifecycle_session.py`

**Interfaces:**
- Consumes: `load_effective_settings`, `detect_changes` (Tasks 4-5); `LifecycleBus`, `HookEvent` (Task 6); toolset/runtime params (Tasks 3, 7, 8).
- Produces:
  - `TurnSession.effective_settings: EffectiveSettings` (set in `build_runtime`)
  - `TurnSession.lifecycle: LifecycleBus` (set in `build_runtime`; also accepted as optional constructor param `lifecycle: LifecycleBus | None = None` so W2 can pass a pre-subscribed bus)
  - `_workspace_guidance_files(root: Path) -> tuple[Path, ...]` module function
  - Events from the session: `SessionStart` (first user message of the conversation only, `source="startup"`), `UserPromptSubmit` (every turn, `prompt=<task>`), `InstructionsLoaded` (when instruction files exist, `file_paths=[...]`), `ConfigChange` (when a settings file changed since the previous turn in this workspace, `file_paths=[...]`), `SubagentStart`/`SubagentStop` around every subagent run.

- [ ] **Step 1: Note how sessions are built in tests**

A `TurnSession(workspace_root=tmp_path, ...)` uses a *managed* workspace at `tmp_path / <conversation dir>`, so tests must use `session.workspace.root` (not `tmp_path`) when they place files in the workspace. The fakes below are trimmed copies of the ones in `tests/unit/agentic/test_turn_session.py`.

- [ ] **Step 2: Write the failing tests**

`tests/unit/agentic/test_lifecycle_session.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.session import TurnSession, _workspace_guidance_files
from agentos.settings.paths import orin_user_dir
from agentos.settings.watcher import reset_snapshots

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_session", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant",
}


class Store:
    def __init__(self, history: list[dict[str, object]]) -> None:
        self.history = history
        self.activity: list[tuple[AgentActivityEventType, str]] = []

    def load(self, turn_id: str) -> dict:
        return TURN

    def history_for_turn(self, turn) -> list[dict[str, object]]:
        return list(self.history)

    def delta(self, turn, text: str) -> None:
        pass

    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        self.activity.append((event_type, summary))

    def main_agent_id(self, turn) -> str:
        return f"agent:{turn['conversation_id']}:main"


class Agents:
    def list(self) -> list[dict]:
        return []


def _make_session(tmp_path: Path, history: list[dict[str, object]], **kwargs) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=Store(history), agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, **kwargs,
    )


def _bus():
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    return bus, events


def test_guidance_files_are_listed(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("more", encoding="utf-8")
    (tmp_path / "CONVENTIONS.md").write_text("", encoding="utf-8")  # empty files are not instructions
    assert {path.name for path in _workspace_guidance_files(tmp_path)} == {"CLAUDE.md", "AGENTS.md"}


def test_first_turn_emits_session_start_prompt_and_instructions(tmp_path: Path) -> None:
    reset_snapshots()
    bus, events = _bus()
    session = _make_session(tmp_path, [{"role": "user", "content": "build it"}], lifecycle=bus)
    (session.workspace.root / "CLAUDE.md").write_text("rules", encoding="utf-8")
    session.build_runtime()
    names = [event.name for event in events]
    assert HookEvent.SESSION_START in names
    assert HookEvent.USER_PROMPT_SUBMIT in names
    assert HookEvent.INSTRUCTIONS_LOADED in names
    prompt = dict(next(e for e in events if e.name is HookEvent.USER_PROMPT_SUBMIT).payload)
    assert prompt["prompt"] == "build it"
    assert dict(next(e for e in events if e.name is HookEvent.SESSION_START).payload)["source"] == "startup"


def test_later_turn_has_no_session_start(tmp_path: Path) -> None:
    bus, events = _bus()
    history = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "two"}]
    session = _make_session(tmp_path, history, lifecycle=bus)
    session.build_runtime()
    names = [event.name for event in events]
    assert HookEvent.SESSION_START not in names
    assert HookEvent.USER_PROMPT_SUBMIT in names


def test_experimental_flag_turns_on_public_names(tmp_path: Path) -> None:
    settings_file = orin_user_dir() / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"experimental": {"claudeToolNames": True}}), encoding="utf-8")
    session = _make_session(tmp_path, [{"role": "user", "content": "x"}])
    runtime = session.build_runtime()
    assert runtime.toolset.name_translator is not None
    assert session.effective_settings.experimental("claudeToolNames") is True


def test_config_change_between_turns(tmp_path: Path) -> None:
    reset_snapshots()
    bus, events = _bus()
    first = _make_session(tmp_path, [{"role": "user", "content": "x"}], lifecycle=bus)
    first.build_runtime()
    assert HookEvent.CONFIG_CHANGE not in [event.name for event in events]
    (first.workspace.root / ".orin").mkdir(exist_ok=True)
    (first.workspace.root / ".orin" / "settings.json").write_text("{}", encoding="utf-8")
    events.clear()
    _make_session(tmp_path, [{"role": "user", "content": "y"}], lifecycle=bus).build_runtime()
    change = [dict(e.payload) for e in events if e.name is HookEvent.CONFIG_CHANGE]
    assert change and any(path.endswith("settings.json") for path in change[0]["file_paths"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_session.py`
Expected: FAIL with `ImportError: cannot import name '_workspace_guidance_files'`

- [ ] **Step 4: Implement in `session.py`**

4a. Imports at top of `session.py`:

```python
from agentos.settings import EffectiveSettings, detect_changes, empty_settings, load_effective_settings

from .lifecycle import HookEvent, LifecycleBus
```

4b. Add the module function right above `_workspace_guidance`:

```python
def _workspace_guidance_files(root: Path) -> tuple[Path, ...]:
    """The conventional instruction files present at the workspace root."""
    found: list[Path] = []
    for name in WORKSPACE_INSTRUCTION_FILES:
        path = root / name
        try:
            if path.is_file() and path.stat().st_size > 0:
                found.append(path)
        except OSError:
            continue
    return tuple(found)
```

4c. `TurnSession.__init__`: add parameter `lifecycle: LifecycleBus | None = None,` at the end of the signature and in the body:

```python
        self.lifecycle: LifecycleBus | None = lifecycle
        self.effective_settings: EffectiveSettings = empty_settings()
```

4d. In `_toolset(...)`, add two keyword arguments to the `AgentToolset(` call:

```python
            public_tool_names=self.effective_settings.experimental("claudeToolNames"),
            lifecycle=self.lifecycle,
```

4e. At the very beginning of `build_runtime` (after the imports inside the method), add:

```python
        try:
            self.effective_settings = load_effective_settings(self.workspace.root)
        except Exception:  # noqa: BLE001 - settings never stop a turn from starting
            self.effective_settings = empty_settings()
        if self.lifecycle is None:
            self.lifecycle = LifecycleBus()
        self.lifecycle.bind(
            session_id=str(self.turn.get("conversation_id") or ""),
            cwd=str(self.workspace.root),
        )
```

4f. After `self._in_plan_mode = self.permission_mode == "plan_mode"` in `build_runtime`, add:

```python
        self.lifecycle.bind(permission_mode=self.permission_mode)
```

4g. After the line `workspace_guidance = _workspace_guidance(self.workspace.root)` and after `history`/`task` are computed (i.e. after `task = next(...)`), add:

```python
        self._emit_turn_start_events(history, task)
```

and add the method to the class:

```python
    def _emit_turn_start_events(self, history: list[Mapping[str, object]], task: str) -> None:
        bus = self.lifecycle
        if bus is None:
            return
        try:
            changed = detect_changes(str(self.workspace.root), self.effective_settings.candidate_files)
            if changed:
                bus.emit(HookEvent.CONFIG_CHANGE, source="settings", file_paths=[str(path) for path in changed])
            files = _workspace_guidance_files(self.workspace.root)
            if files:
                bus.emit(HookEvent.INSTRUCTIONS_LOADED, file_paths=[str(path) for path in files], memory_type="Project")
            user_messages = sum(1 for item in history if item.get("role") == "user" and isinstance(item.get("content"), str))
            if user_messages <= 1:
                bus.emit(HookEvent.SESSION_START, source="startup")
            bus.emit(HookEvent.USER_PROMPT_SUBMIT, prompt=task)
        except Exception:  # noqa: BLE001 - lifecycle never stops a turn
            return
```

4h. Wrap `_execute_subagent_run` so the subagent lifecycle is visible. Rename the existing method `_execute_subagent_run` to `_execute_subagent_run_inner` (body unchanged) and add:

```python
    def _execute_subagent_run(self, **kwargs: Any) -> ToolOutcome:
        bus = self.lifecycle
        agent_id = str(kwargs.get("agent_id") or "")
        agent_type = str(kwargs.get("builtin_key") or kwargs.get("clean") or "")
        if bus is not None:
            bus.emit(HookEvent.SUBAGENT_START, agent_id=agent_id, agent_type=agent_type)
        outcome = self._execute_subagent_run_inner(**kwargs)
        if bus is not None:
            bus.emit(
                HookEvent.SUBAGENT_STOP,
                agent_id=agent_id,
                agent_type=agent_type,
                stop_hook_active=False,
                status=str(getattr(outcome, "status", "")),
            )
        return outcome
```

Check every call site of `_execute_subagent_run` passes keyword arguments only (they do in the current file: `grep -n "_execute_subagent_run(" src/agentos/agentic/session.py`). If any positional call exists, convert it to keywords.

4i. In `build_runtime`, add `lifecycle=self.lifecycle,` to the main `AgenticTurnRuntime(` call (the one that passes `store=_MainAgentStore(...)`, ~line 2323). Do **not** add it to the subagent runtimes (~lines 1613 and 1668): subagent completion is reported as `SubagentStop`, not `Stop`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q tests/unit/agentic/test_lifecycle_session.py tests/unit/agentic`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_lifecycle_session.py
git commit -m "feat(session): load effective settings and emit session lifecycle events"
```

---

### Task 10: Full regression and W0 ledger

**Files:**
- Create: `docs/superpowers/plans/2026-10-18-w0-foundations-ledger.md`

- [ ] **Step 1: Run the full unit suite**

Run: `uv run pytest -q tests/unit`
Expected: all previous tests pass (baseline 2393 passed, 49 skipped) plus the new ones. If anything fails, fix it before continuing — do not skip or delete tests.

- [ ] **Step 2: Run the frontend unit tests only if any frontend file changed**

Run: `git diff --name-only main...HEAD -- frontend` — expected: empty for W0. If not empty, run `npm --prefix frontend test`.

- [ ] **Step 3: Write the ledger**

Create `docs/superpowers/plans/2026-10-18-w0-foundations-ledger.md` with a table of which of the 35 `HookEvent`s are emitted in W0 and from where, and which are deferred:

| Event | Emitted in W0 | Where | Deferred to |
|---|---|---|---|
| PreToolUse | yes | `AgentToolset.invoke` | — |
| PostToolUse | yes | `AgentToolset.invoke` | — |
| PostToolUseFailure | yes | `AgentToolset.invoke` | — |
| PostToolBatch | yes | `AgenticTurnRuntime._run_toolset` | — |
| Notification | no | — | W7 |
| UserPromptSubmit | yes | `TurnSession._emit_turn_start_events` | — |
| UserPromptExpansion | no | — | W7 |
| SessionStart | yes | `TurnSession._emit_turn_start_events` | — |
| SessionEnd | no | — | W3 |
| Stop | yes | `AgenticTurnRuntime.run` | — |
| StopFailure | yes | `AgenticTurnRuntime._fail` | — |
| SubagentStart | yes | `TurnSession._execute_subagent_run` | — |
| SubagentStop | yes | `TurnSession._execute_subagent_run` | — |
| PreCompact | yes | `AgenticTurnRuntime._run_compaction` | — |
| PostCompact | yes | `AgenticTurnRuntime._run_compaction` | — |
| PreModelSwitch | no | — | W3 |
| PostModelSwitch | no | — | W3 |
| PermissionRequest | yes | `AgentToolset._invoke_unobserved` | — |
| PermissionDenied | yes | `AgentToolset.invoke` | — |
| Setup | no | — | W6 |
| TeammateIdle | no | — | W5 |
| TaskCreated | yes | `AgentToolset._emit_tool_side_events` | — |
| TaskCompleted | yes | `AgentToolset._emit_tool_side_events` | — |
| Elicitation | no | — | W5 |
| ElicitationResult | no | — | W5 |
| ConfigChange | yes | `TurnSession._emit_turn_start_events` | — |
| WorktreeCreate | yes | `AgentToolset._emit_tool_side_events` | — |
| WorktreeRemove | yes | `AgentToolset._emit_tool_side_events` | — |
| InstructionsLoaded | yes | `TurnSession._emit_turn_start_events` | — |
| CwdChanged | yes | `AgentToolset._emit_tool_side_events` | — |
| FileChanged | no | — | W3 |
| DirectoryAdded | no | — | W1 |
| MessageDisplay | no | — | W7 |
| StatusLine | no | — | W7 |
| FileSuggestion | no | — | W7 |

Also list the three intentional deviations from the "Deviations" section at the top of this plan.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-10-18-w0-foundations-ledger.md
git commit -m "docs(w0): lifecycle emission ledger and deviations"
```

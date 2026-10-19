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
        "line_numbers": True,
    }


def test_task_output_and_stop_take_string_ids() -> None:
    assert arguments_in("read_process_output", {"task_id": "4242"}) == {"pid": 4242, "block": True}
    assert arguments_in("stop_process", {"task_id": "17"}) == {"pid": 17}
    assert arguments_in("stop_process", {"shell_id": "18"}) == {"pid": 18}
    assert arguments_out("stop_process", {"pid": 17}) == {"task_id": "17"}


def test_agent_maps_types_and_prompt() -> None:
    assert arguments_in("run_subagent", {"description": "Find it", "prompt": "Look", "subagent_type": "Explore"}) == {
        "description": "Find it", "task": "Look", "subagent_type": "explore",
    }
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": "general-purpose"})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p"})["subagent_type"] == "general"
    # an unknown type is passed through for the internal validation to reject, never widened to general
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": "my-custom"})["subagent_type"] == "my-custom"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "resume": "agent-1", "model": "opus"}) == {
        "description": "d", "task": "p", "subagent_type": "general", "agent_id": "agent-1", "model_id": "opus",
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
    assert arguments_in("fetch_url", {"url": "https://example.com", "prompt": "summary"}) == {"url": "https://example.com", "prompt": "summary"}
    assert arguments_in("web_search", {"query": "q", "allowed_domains": ["a.com"]}) == {"query": "q"}
    # tools without a mapping pass arguments through unchanged
    assert arguments_in("remember", {"fact": "f"}) == {"fact": "f"}


def test_public_schemas_use_public_field_names() -> None:
    description, schema = public_schema("read_file")
    assert description and "file_path" in schema["properties"] and schema["required"] == ["file_path"]
    assert public_argument_names("read_file") == frozenset({"file_path", "offset", "limit", "pages", "force_reload", "allow_large"})
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


def _id(value: object) -> str:
    return repr(value)[:40]


HOSTILE_VALUES = ["--5", "²", "9" * 5000, 10**400, float("inf"), float("nan"), [], {}, None, True]


@pytest.mark.parametrize("value", HOSTILE_VALUES, ids=_id)
def test_run_command_timeout_never_raises_on_hostile_values(value: object) -> None:
    result = arguments_in("run_command", {"command": "ls", "timeout": value})
    assert isinstance(result, dict)
    result = arguments_in("run_command", {"command": "ls", "timeout_seconds": value})
    assert isinstance(result, dict)
    assert isinstance(arguments_out("run_command", {"command": "ls", "timeout_seconds": value}), dict)


@pytest.mark.parametrize("value", HOSTILE_VALUES, ids=_id)
def test_pid_and_task_id_never_raise_on_hostile_values(value: object) -> None:
    assert isinstance(arguments_in("read_process_output", {"task_id": value}), dict)
    assert isinstance(arguments_in("stop_process", {"pid": value}), dict)
    assert isinstance(arguments_out("read_process_output", {"pid": value}), dict)


@pytest.mark.parametrize("options", [5, True, "abc", None, {"label": "x"}], ids=_id)
def test_ask_user_options_never_raise_on_hostile_values(options: object) -> None:
    public = {"questions": [{"question": "Q?", "header": "H", "options": options, "multiSelect": False}]}
    assert isinstance(arguments_in("ask_user", public), dict)
    internal = {"questions": [{"id": "q1", "question": "Q?", "mode": "single_choice", "options": options}]}
    assert isinstance(arguments_out("ask_user", internal), dict)


def test_messages_out_keeps_poisoned_history_instead_of_raising() -> None:
    messages = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "run_command", "arguments": json.dumps({"command": "ls", "timeout_seconds": "--5"})},
        }],
    }]
    out = PublicToolNames().messages_out(messages)
    call = out[0]["tool_calls"][0]["function"]
    assert call["name"] == "Bash"
    assert json.loads(call["arguments"])["command"] == "ls"


def test_messages_out_survives_unparseable_tool_use_block() -> None:
    deep = "[" * 5000
    messages = [{
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "run_command", "input": {"command": "ls", "timeout_seconds": "--5"}},
            {"type": "tool_use", "id": "t2", "name": "read_file", "input": deep},
        ],
    }]
    out = PublicToolNames().messages_out(messages)
    names = [block["name"] for block in out[0]["content"]]
    assert names[0] == "Bash"
    assert len(out[0]["content"]) == 2


@pytest.mark.parametrize("raw", ["explorer", "code-reviewer", ["x"], 7], ids=repr)
def test_unknown_subagent_type_is_never_widened_to_general(raw: object) -> None:
    result = arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": raw})
    assert result["subagent_type"] == raw


def test_missing_subagent_type_defaults_to_general_and_known_names_map() -> None:
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p"})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": ""})["subagent_type"] == "general"
    assert arguments_in("run_subagent", {"description": "d", "prompt": "p", "subagent_type": "Explore"})["subagent_type"] == "explore"

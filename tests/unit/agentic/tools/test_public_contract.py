"""W4: the public schema of every Claude Code tool comes from its Input model."""
from __future__ import annotations

import pytest

from agentos.agentic.tools.public_names import arguments_in, public_argument_names, public_schema

EXPECTED = {
    "read_file": ({"file_path", "offset", "limit", "pages", "force_reload", "allow_large"}, ["file_path"]),
    "write_file": ({"file_path", "content", "mode"}, ["file_path", "content"]),
    "edit_file": ({"file_path", "old_string", "new_string", "replace_all", "edits"}, ["file_path", "old_string", "new_string"]),
    "notebook_edit": ({"notebook_path", "new_source", "cell_id", "cell_type", "edit_mode"}, ["notebook_path", "new_source"]),
    "glob_files": ({"pattern", "path"}, ["pattern"]),
    "search_files": (
        {"pattern", "path", "glob", "type", "output_mode", "-A", "-B", "-C", "-i", "-n", "-o", "context", "multiline", "head_limit", "offset"},
        ["pattern"],
    ),
    "run_command": ({"command", "description", "timeout", "run_in_background", "cwd"}, ["command"]),
    "read_process_output": ({"task_id"}, ["task_id"]),
    "stop_process": ({"task_id", "shell_id"}, []),
    "todo_write": ({"todos"}, ["todos"]),
    "task_create": ({"subject", "description", "activeForm", "metadata"}, ["subject", "description"]),
    "task_update": (
        {"taskId", "status", "subject", "description", "activeForm", "owner", "addBlocks", "addBlockedBy", "metadata"},
        ["taskId"],
    ),
    "task_get": ({"taskId"}, ["taskId"]),
    "task_list": (set(), []),
    "ask_user": ({"questions"}, ["questions"]),
    "enter_plan_mode": (set(), []),
    "exit_plan_mode": ({"plan", "allowedPrompts"}, []),
    "enter_worktree": ({"name", "base_branch"}, []),
    "exit_worktree": ({"action", "discard_changes"}, ["action"]),
    "fetch_url": ({"url", "prompt"}, ["url"]),
    "web_search": ({"query", "allowed_domains", "blocked_domains"}, ["query"]),
    "run_subagent": ({"description", "prompt", "subagent_type", "model", "run_in_background", "resume"}, ["description", "prompt"]),
    "use_skill": ({"skill", "args"}, ["skill"]),
    "search_mcp_tools": ({"query", "max_results"}, ["query"]),
}


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_public_fields_and_required_lists(key: str) -> None:
    fields, required = EXPECTED[key]
    description, schema = public_schema(key)
    assert description.strip()
    assert set(schema["properties"]) == fields
    assert schema["required"] == required
    assert schema["additionalProperties"] is False
    assert "'title':" not in repr(schema) and "$ref" not in repr(schema)
    assert public_argument_names(key) == frozenset(fields)


def test_nested_items_keep_their_contract() -> None:
    _, todo = public_schema("todo_write")
    assert todo["properties"]["todos"]["items"]["required"] == ["content", "status", "activeForm"]
    _, ask = public_schema("ask_user")
    question = ask["properties"]["questions"]["items"]
    assert question["required"] == ["question", "header", "options", "multiSelect"]
    assert question["properties"]["options"]["items"]["required"] == ["label", "description"]
    _, edit = public_schema("edit_file")
    assert edit["properties"]["edits"]["items"]["required"] == ["old_string", "new_string"]


def test_orin_only_tools_have_no_public_schema() -> None:
    assert public_schema("remember") is None and public_argument_names("remember") is None


def test_discard_changes_maps_to_force() -> None:
    assert arguments_in("exit_worktree", {"action": "remove", "discard_changes": True}) == {"action": "remove", "force": True}


def test_a_returned_schema_cannot_corrupt_the_cache() -> None:
    _, first = public_schema("read_file")
    first["properties"].clear()
    _, second = public_schema("read_file")
    assert "file_path" in second["properties"]

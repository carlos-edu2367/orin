"""W4: file tools report Claude Code-shaped outputs, validated by their Output models, and hooks receive them."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.tools.files.edit import EditOutput
from agentos.agentic.tools.files.notebook import NotebookEditOutput
from agentos.agentic.tools.files.read import ReadOutput
from agentos.agentic.tools.files.write import WriteOutput
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path, **options) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), **options)


def test_write_reports_create_then_update(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    created = toolset.invoke("Write", {"file_path": "a.txt", "content": "one\n"}).data["tool_output"]
    assert created["type"] == "create" and created["originalFile"] is None and created["content"] == "one\n"
    WriteOutput.model_validate(created)
    toolset.invoke("Read", {"file_path": "a.txt"})
    updated = toolset.invoke("Write", {"file_path": "a.txt", "content": "two\n"}).data["tool_output"]
    assert updated["type"] == "update" and updated["originalFile"] == "one\n"
    assert updated["structuredPatch"] and updated["userModified"] is False
    WriteOutput.model_validate(updated)


def test_edit_reports_the_patch_and_the_original(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "a.py").write_bytes(b"x = 1\ny = 2\n")  # bytes: no CRLF translation on Windows
    toolset.invoke("Read", {"file_path": "a.py"})
    output = toolset.invoke("Edit", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 10"}).data["tool_output"]
    assert output["oldString"] == "x = 1" and output["newString"] == "x = 10"
    assert output["originalFile"] == "x = 1\ny = 2\n" and output["replaceAll"] is False and output["userModified"] is False
    assert output["structuredPatch"][0]["lines"]
    assert Path(output["filePath"]) == (toolset.workspace.root / "a.py").resolve()
    EditOutput.model_validate(output)


def test_an_edit_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    outcome = toolset.invoke("Edit", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 1"})
    assert outcome.status == "failed" and "identical" in outcome.content


def test_a_large_original_is_left_out(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "big.txt").write_text("a" * 150_000 + "\nend\n", encoding="utf-8")
    output = toolset.invoke("Edit", {"file_path": "big.txt", "old_string": "end", "new_string": "fim"}).data["tool_output"]
    assert output["originalFile"] is None


def test_notebook_edit_reports_old_and_new_source(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    notebook = {"cells": [{"cell_type": "code", "id": "c1", "metadata": {}, "source": ["print(1)\n"], "outputs": [], "execution_count": None}],
                "metadata": {"language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    (toolset.workspace.root / "n.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    output = toolset.invoke("NotebookEdit", {"notebook_path": "n.ipynb", "cell_id": "c1", "new_source": "print(2)\n"}).data["tool_output"]
    assert output["old_source"] == "print(1)\n" and output["new_source"] == "print(2)\n"
    assert output["cell_type"] == "code" and output["language"] == "python" and output["edit_mode"] == "replace"
    assert json.loads(output["updated_file"])["cells"][0]["source"] == ["print(2)\n"]
    NotebookEditOutput.model_validate(output)


def test_read_outputs_validate_against_the_model(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "a.txt").write_text("one\n", encoding="utf-8")
    ReadOutput.model_validate(toolset.invoke("Read", {"file_path": "a.txt"}).data["tool_output"])
    ReadOutput.model_validate(toolset.invoke("Read", {"file_path": "a.txt"}).data["tool_output"])  # file_unchanged


def test_specs_declare_their_output_models() -> None:
    from agentos.agentic.tools.registry import default_registry

    registry = default_registry()
    assert registry.get("Edit").output_model is EditOutput
    assert registry.get("Write").output_model is WriteOutput
    assert registry.get("NotebookEdit").output_model is NotebookEditOutput
    assert registry.get("Read").output_model is ReadOutput


def test_post_tool_use_receives_the_claude_code_output(tmp_path: Path) -> None:
    bus = LifecycleBus()
    events: list = []
    bus.subscribe(events.append)
    toolset = _toolset(tmp_path, lifecycle=bus)
    toolset.invoke("Write", {"file_path": "a.txt", "content": "hello\n"})
    post = next(dict(event.payload) for event in events if event.name == HookEvent.POST_TOOL_USE)
    response = post["tool_response"]
    assert response["status"] == "succeeded" and response["type"] == "create"
    assert Path(response["filePath"]) == (toolset.workspace.root / "a.txt").resolve()
    assert response["content"] == "hello\n"

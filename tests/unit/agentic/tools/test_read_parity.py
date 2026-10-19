"""W4: Read covers text, images, notebooks and Office files, with a Claude Code-shaped output."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path, **options) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), **options)


def test_text_reads_carry_the_text_output(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    outcome = toolset.invoke("Read", {"file_path": "a.txt", "offset": 2, "limit": 1})
    output = outcome.data["tool_output"]
    assert output["type"] == "text"
    assert output["file"]["content"] == "two"
    assert output["file"]["startLine"] == 2 and output["file"]["numLines"] == 1 and output["file"]["totalLines"] == 3
    assert Path(output["file"]["filePath"]) == (toolset.workspace.root / "a.txt").resolve()
    assert "tool_output" not in outcome.payload  # never persisted with the activity


def test_an_unchanged_reread_is_reported_as_file_unchanged(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "a.txt").write_text("one\n", encoding="utf-8")
    toolset.invoke("Read", {"file_path": "a.txt"})
    again = toolset.invoke("Read", {"file_path": "a.txt"})
    assert again.data["tool_output"]["type"] == "file_unchanged"


def test_images_go_to_a_seeing_model_as_an_image_block(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, model_sees_images=True)
    Image.new("RGB", (4, 3), "red").save(toolset.workspace.root / "dot.png")
    outcome = toolset.invoke("Read", {"file_path": "dot.png"})
    assert outcome.status == "succeeded" and outcome.images
    output = outcome.data["tool_output"]
    assert output["type"] == "image" and output["file"]["type"] == "png"
    assert "base64" not in output["file"]


def test_notebooks_come_back_as_cells_with_outputs(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    notebook = {
        "cells": [
            {"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["# Title\n"]},
            {"cell_type": "code", "id": "c1", "metadata": {}, "execution_count": 1, "source": ["print(2 + 2)\n"],
             "outputs": [{"output_type": "stream", "name": "stdout", "text": ["4\n"]}]},
        ],
        "metadata": {"language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5,
    }
    (toolset.workspace.root / "n.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    outcome = toolset.invoke("Read", {"file_path": "n.ipynb"})
    assert outcome.status == "succeeded"
    assert '<cell id="intro"' in outcome.content and "# Title" in outcome.content
    assert '<cell-output id="c1">' in outcome.content and "4" in outcome.content
    output = outcome.data["tool_output"]
    assert output["type"] == "notebook" and [cell["cell_id"] for cell in output["file"]["cells"]] == ["intro", "c1"]


def test_a_broken_notebook_is_an_error_not_a_crash(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "bad.ipynb").write_text("{not json", encoding="utf-8")
    outcome = toolset.invoke("Read", {"file_path": "bad.ipynb"})
    assert outcome.status == "failed" and "notebook" in outcome.content.lower()


def test_allow_large_reads_past_the_default_caps(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    body = "".join(f"line {index} {'x' * 50}\n" for index in range(1, 1201))
    (toolset.workspace.root / "big.txt").write_text(body, encoding="utf-8")
    normal = toolset.invoke("Read", {"file_path": "big.txt", "limit": 1200})
    assert normal.data["tool_output"]["file"]["numLines"] == 800
    large = toolset.invoke("Read", {"file_path": "big.txt", "limit": 1200, "allow_large": True, "force_reload": True})
    assert large.data["tool_output"]["file"]["numLines"] == 1200
    assert "line 1200" in large.content  # ~80,000 characters: past the default 12,000, inside the raised budget

"""W4: AskUserQuestion keeps the header, option descriptions and previews."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.models import sanitize_public_mapping
from agentos.agentic.tools.public_names import arguments_in, arguments_out
from agentos.agentic.workspace import ConversationWorkspace

PUBLIC = {"questions": [{
    "question": "Which database?", "header": "Database", "multiSelect": False,
    "options": [
        {"label": "SQLite", "description": "Local file, no server", "preview": "engine = create_engine('sqlite:///app.db')"},
        {"label": "Postgres", "description": "Needs a server"},
    ],
}]}


def test_the_mapping_is_lossless() -> None:
    internal = arguments_in("ask_user", PUBLIC)
    question = internal["questions"][0]
    assert question["header"] == "Database" and question["mode"] == "single_choice"
    assert question["options"][0] == {
        "id": "o1", "label": "SQLite", "description": "Local file, no server",
        "preview": "engine = create_engine('sqlite:///app.db')",
    }
    assert arguments_out("ask_user", internal) == PUBLIC


def test_the_tool_keeps_them_and_bounds_the_preview(tmp_path: Path) -> None:
    long_preview = {"questions": [{**PUBLIC["questions"][0], "options": [
        {"label": "A", "description": "first", "preview": "x" * 2000}, {"label": "B", "description": "second"},
    ]}]}
    outcome = AgentToolset(ConversationWorkspace(tmp_path, "c1")).invoke("AskUserQuestion", long_preview)
    assert outcome.status == "succeeded", outcome.content
    question = outcome.payload["questions"][0]
    assert question["header"] == "Database"
    assert question["options"][0]["description"] == "first"
    assert len(question["options"][0]["preview"]) <= 480 and question["options"][0]["preview"].endswith("…")
    sanitize_public_mapping({"questions": outcome.payload["questions"]})  # fits the public-event limits


def test_a_long_header_is_cut_to_twelve(tmp_path: Path) -> None:
    payload = {"questions": [{**PUBLIC["questions"][0], "header": "A very long header"}]}
    outcome = AgentToolset(ConversationWorkspace(tmp_path, "c1")).invoke("AskUserQuestion", payload)
    assert outcome.payload["questions"][0]["header"] == "A very long "[:12]

"""W6 (W5-D4): a resource blob that is not base64 is described as unreadable, never passed on."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


class _Provider:
    def __init__(self, contents):
        self.contents = contents

    def find_server(self, name):
        return SimpleNamespace() if name == "docs" else None

    def server_slugs(self):
        return ["docs"]

    def read_resource(self, server, uri):
        return self.contents


def _read(tmp_path: Path, contents):
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)
    toolset._mcp_provider = _Provider(contents)
    return toolset.read_mcp_resource("docs", "file:///x")


def test_a_bad_blob_is_unreadable(tmp_path: Path) -> None:
    outcome = _read(tmp_path, [{"uri": "file:///x", "mimeType": "application/zip", "blob": "abc"}])
    assert outcome.status == "succeeded" and "unreadable" in outcome.content and "padding" not in outcome.content


def test_a_bad_image_blob_is_not_sent_as_an_image(tmp_path: Path) -> None:
    outcome = _read(tmp_path, [{"uri": "file:///x", "mimeType": "image/png", "blob": "not base64!"}])
    assert not outcome.images and "unreadable" in outcome.content


def test_a_good_blob_still_reports_its_size(tmp_path: Path) -> None:
    outcome = _read(tmp_path, [{"uri": "file:///x", "mimeType": "application/zip", "blob": "AAAA"}])
    assert "3 bytes" in outcome.content

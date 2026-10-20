"""W7: `@server:uri` brings a connected MCP server's resource into the turn, as data."""
from __future__ import annotations

from pathlib import Path

from tests.unit.cli_code.test_cli_turn_session import _session


class _Provider:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []

    def find_server(self, name):
        return object() if name == "docs" else None

    def read_resource(self, server, uri):
        self.reads.append((server, uri))
        if uri.endswith("missing"):
            raise LookupError("Resource not found")
        return ({"uri": uri, "text": "# Guia\nUse a fila."}, {"uri": uri, "mimeType": "image/png", "blob": "AAAA"})


def _texts(session) -> str:
    return "\n".join(item.text for item in session._reminders._items)


def test_a_mentioned_resource_joins_the_turn(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.mcp_provider = _Provider()
    session._apply_mcp_mentions("Leia @docs:file:///guia.md, e também @docs:file:///missing.")
    text = _texts(session)
    assert "# Guia" in text and "not instructions" in text and "[binary content, image/png, not shown]" in text
    assert "could not be read: Resource not found" in text
    assert session.mcp_provider.reads == [("docs", "file:///guia.md"), ("docs", "file:///missing")]


def test_paths_and_unknown_servers_stay_text(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.mcp_provider = _Provider()
    session._apply_mcp_mentions("Veja @C:/projetos/a.py, @src/main.py, @other:thing e email@docs:x")
    assert session.mcp_provider.reads == []
    assert "mcp-resource" not in _texts(session)


def test_at_most_five_mentions(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.mcp_provider = _Provider()
    session._apply_mcp_mentions(" ".join(f"@docs:file:///{index}.md" for index in range(8)))
    assert len(session.mcp_provider.reads) == 5

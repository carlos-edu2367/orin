from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.retrieval.models import IndexStatus
from agentos.retrieval.service import SearchResult


class _EmptyIndexingRetrieval:
    def __init__(self, *, indexing: bool = True, entries: list[dict] | None = None) -> None:
        self.indexing = indexing
        self._entries = entries if entries is not None else []
        self._status = IndexStatus(files=0, chunks=0, vectors=0, last_scan_at=None)

    def project_map(self, *, limit: int = 20, focus_paths=(), query: str = "", max_tokens: int = 1200):
        return list(self._entries)

    def search(self, query: str, *, limit: int = 8):
        return SearchResult(hits=[], mode="lexical", status=self._status)

    def status(self):
        return self._status


def _populate_workspace(root: Path) -> None:
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "src" / "sub").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "sub" / "deep.py").write_text("x = 1\n", encoding="utf-8")
    for ignored in (".git", "node_modules", ".orin", ".venv"):
        (root / ignored).mkdir(parents=True, exist_ok=True)
        (root / ignored / "secret.txt").write_text("ignore me\n", encoding="utf-8")


def test_project_map_empty_index_falls_back_to_workspace_entries_and_public_names_in_code_mode(tmp_path: Path) -> None:
    ws = ConversationWorkspace.at_root(tmp_path)
    _populate_workspace(ws.root)
    toolset = AgentToolset(ws, retrieval=_EmptyIndexingRetrieval(indexing=True), code_mode_active=True, public_tool_names=True)

    outcome = toolset.invoke("ProjectMap", {})
    assert outcome.status == "succeeded"
    assert outcome.payload["count"] == 0
    assert "Glob" in outcome.content and "Grep" in outcome.content
    assert "glob_files" not in outcome.content
    assert "search_files" not in outcome.content
    assert "README.md" in outcome.content
    assert "pyproject.toml" in outcome.content
    assert "src/app.py" in outcome.content
    # Depth > 2 and ignored directories must not appear
    assert "deep.py" not in outcome.content
    for ignored in (".git", "node_modules", ".orin", ".venv"):
        assert ignored not in outcome.content


def test_project_map_empty_index_uses_internal_names_outside_public_mode(tmp_path: Path) -> None:
    ws = ConversationWorkspace.at_root(tmp_path)
    _populate_workspace(ws.root)
    toolset = AgentToolset(ws, retrieval=_EmptyIndexingRetrieval(indexing=True), code_mode_active=False, public_tool_names=False)

    outcome = toolset.invoke("project_map", {})
    assert outcome.status == "succeeded"
    assert "glob_files/search_files" in outcome.content
    assert "README.md" in outcome.content
    assert "src/app.py" in outcome.content


def test_search_code_index_building_note_translates_tool_names_in_code_mode(tmp_path: Path) -> None:
    ws = ConversationWorkspace.at_root(tmp_path)
    retrieval = _EmptyIndexingRetrieval(indexing=True)

    code_toolset = AgentToolset(ws, retrieval=retrieval, code_mode_active=True, public_tool_names=True)
    code_out = code_toolset.invoke("SearchCode", {"query": "anything"})
    assert "Glob" in code_out.content and "Grep" in code_out.content
    assert "glob_files" not in code_out.content and "search_files" not in code_out.content

    chat_toolset = AgentToolset(ws, retrieval=retrieval, code_mode_active=False, public_tool_names=False)
    chat_out = chat_toolset.invoke("search_code", {"query": "anything"})
    assert "glob_files/search_files" in chat_out.content


def test_project_map_fallback_caps_at_40_entries_and_handles_empty_workspace(tmp_path: Path) -> None:
    many_dir = tmp_path / "many"
    many_dir.mkdir()
    for idx in range(55):
        (many_dir / f"file_{idx:02d}.txt").write_text("x\n", encoding="utf-8")

    ws_many = ConversationWorkspace.at_root(many_dir)
    toolset_many = AgentToolset(ws_many, retrieval=_EmptyIndexingRetrieval(indexing=False), code_mode_active=True, public_tool_names=True)
    out_many = toolset_many.invoke("ProjectMap", {})
    listed_lines = [line for line in out_many.content.splitlines() if line.startswith(("f ", "d "))]
    assert len(listed_lines) == 40

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    ws_empty = ConversationWorkspace.at_root(empty_dir)
    toolset_empty = AgentToolset(ws_empty, retrieval=_EmptyIndexingRetrieval(indexing=False), code_mode_active=True, public_tool_names=True)
    out_empty = toolset_empty.invoke("ProjectMap", {})
    assert "[the project index is empty]" in out_empty.content


def test_project_map_non_empty_index_does_not_include_fallback_listing(tmp_path: Path) -> None:
    ws = ConversationWorkspace.at_root(tmp_path)
    (ws.root / "extra_unindexed.txt").write_text("hi\n", encoding="utf-8")
    retrieval = _EmptyIndexingRetrieval(
        indexing=False,
        entries=[{"path": "src/main.py", "imported_by": 2, "symbols": ["main"]}],
    )
    toolset = AgentToolset(ws, retrieval=retrieval, code_mode_active=True, public_tool_names=True)
    outcome = toolset.invoke("ProjectMap", {})
    assert "src/main.py" in outcome.content
    assert "extra_unindexed.txt" not in outcome.content

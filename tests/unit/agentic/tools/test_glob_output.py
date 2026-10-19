"""W4: Glob reports Claude Code's output shape."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.search.glob import GlobOutput
from agentos.agentic.workspace import ConversationWorkspace


def test_glob_output(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    for name in ("a.py", "b.py", "c.txt"):
        (workspace.root / name).write_bytes(b"x")
    toolset = AgentToolset(workspace)
    outcome = toolset.invoke("Glob", {"pattern": "*.py"})
    output = outcome.data["tool_output"]
    GlobOutput.model_validate(output)
    assert output["numFiles"] == 2 and output["truncated"] is False and output["totalMatches"] == 2
    assert sorted(Path(name).name for name in output["filenames"]) == ["a.py", "b.py"]
    assert all(Path(name).is_absolute() for name in output["filenames"])
    assert "c.txt" not in outcome.content and output["durationMs"] >= 0


def test_a_capped_glob_says_so(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    for index in range(5):
        (workspace.root / f"f{index}.py").write_bytes(b"x")
    output = AgentToolset(workspace).glob_files("*.py", max_results=2)["tool_output"]
    assert output["numFiles"] == 2 and output["truncated"] is True and output["totalMatches"] == 5

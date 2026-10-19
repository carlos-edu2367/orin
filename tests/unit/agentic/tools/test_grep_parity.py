"""W4: Grep runs ripgrep when one is configured, falls back to Python otherwise, and speaks Claude Code."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.ripgrep import ripgrep_binary
from agentos.agentic.tools.search.grep import GrepOutput
from agentos.agentic.workspace import ConversationWorkspace

RG = shutil.which("rg")  # only the tests look on PATH; Orin itself never does


def _toolset(tmp_path: Path) -> AgentToolset:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    (workspace.root / "a.py").write_bytes(b"alpha = 1\nbeta = 2\nalpha_beta = 3\n")
    (workspace.root / "sub").mkdir()
    (workspace.root / "sub" / "b.py").write_bytes(b"gamma\nalpha again\n")
    (workspace.root / ".git").mkdir()
    (workspace.root / ".git" / "config").write_bytes(b"alpha in git\n")
    return AgentToolset(workspace)


def test_line_numbers_can_be_turned_off(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    numbered = toolset.invoke("Grep", {"pattern": "beta", "output_mode": "content"})
    assert "a.py:2: beta = 2" in numbered.content
    plain = toolset.invoke("Grep", {"pattern": "beta", "output_mode": "content", "-n": False})
    assert "a.py: beta = 2" in plain.content and ":2:" not in plain.content


def test_only_matching_prints_the_match(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("Grep", {"pattern": "alpha_?\\w*", "output_mode": "content", "-o": True, "-i": False})
    assert "a.py:1: alpha" in outcome.content and "a.py:3: alpha_beta" in outcome.content
    assert "= 3" not in outcome.content


def test_context_is_an_alias_of_dash_c(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("Grep", {"pattern": "beta = 2", "output_mode": "content", "context": 1})
    assert "a.py-1: alpha = 1" in outcome.content and "a.py+3: alpha_beta = 3" in outcome.content


def test_outputs_validate_for_every_mode(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    for mode in ("content", "files_with_matches", "count"):
        output = toolset.invoke("Grep", {"pattern": "alpha", "output_mode": mode}).data["tool_output"]
        GrepOutput.model_validate(output)
        assert output["mode"] == mode and output["numFiles"] == 2
        assert ".git/config" not in output["filenames"]


def test_ripgrep_is_never_taken_from_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / ("rg.exe" if os.name == "nt" else "rg")).write_bytes(b"")
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    assert ripgrep_binary(tmp_path) is None


def test_a_ripgrep_inside_the_workspace_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planted = tmp_path / "w" / "rg.exe"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(b"")
    monkeypatch.setenv("ORIN_RIPGREP_PATH", str(planted))
    assert ripgrep_binary(tmp_path / "w") is None


@pytest.mark.skipif(RG is None, reason="no ripgrep on this machine")
class TestWithRipgrep:
    @pytest.fixture(autouse=True)
    def _use_ripgrep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORIN_RIPGREP_PATH", str(RG))

    def test_ripgrep_gives_the_same_answer_as_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toolset = _toolset(tmp_path)
        arguments = {"pattern": "alpha", "output_mode": "content", "-B": 1}
        assert ripgrep_binary(toolset.workspace.root) is not None
        with_ripgrep = toolset.invoke("Grep", arguments).content
        monkeypatch.delenv("ORIN_RIPGREP_PATH")
        with_python = toolset.invoke("Grep", arguments).content
        assert with_ripgrep == with_python

    def test_files_and_counts(self, tmp_path: Path) -> None:
        toolset = _toolset(tmp_path)
        assert toolset.invoke("Grep", {"pattern": "alpha", "output_mode": "files_with_matches"}).content.splitlines() == ["a.py", "sub/b.py"]
        assert toolset.invoke("Grep", {"pattern": "alpha", "output_mode": "count"}).content.splitlines() == ["a.py:2", "sub/b.py:1"]

    def test_a_pattern_ripgrep_cannot_parse_falls_back(self, tmp_path: Path) -> None:
        outcome = _toolset(tmp_path).invoke("Grep", {"pattern": "(?<=alpha )again", "output_mode": "content"})
        assert "sub/b.py:2: alpha again" in outcome.content

    def test_a_pattern_that_looks_like_a_flag_is_a_pattern(self, tmp_path: Path) -> None:
        toolset = _toolset(tmp_path)
        (toolset.workspace.root / "flags.txt").write_bytes(b"--version\n")
        outcome = toolset.invoke("Grep", {"pattern": "--version", "output_mode": "content"})
        assert "flags.txt:1: --version" in outcome.content

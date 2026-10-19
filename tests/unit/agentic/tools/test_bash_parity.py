"""W4: Bash keeps the directory across turns, saves long output, and reports Claude Code's output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.shell.bash import BashOutput
from agentos.agentic.workspace import ConversationWorkspace

PY = f'"{sys.executable}"'


def _workspace(tmp_path: Path) -> ConversationWorkspace:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    (workspace.root / "sub").mkdir()
    return workspace


def test_a_successful_call_reports_the_output(tmp_path: Path) -> None:
    outcome = AgentToolset(_workspace(tmp_path)).invoke("Bash", {"command": f"{PY} -c \"print('hi')\""})
    output = outcome.data["tool_output"]
    BashOutput.model_validate(output)
    assert output["stdout"].strip() == "hi" and output["exitCode"] == 0 and output["interrupted"] is False


def test_a_known_exit_code_is_interpreted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.root / "a.txt").write_bytes(b"one\n")
    command = "findstr nothing-here a.txt" if sys.platform == "win32" else "grep nothing-here a.txt"
    output = AgentToolset(workspace).invoke("Bash", {"command": command}).data.get("tool_output") or {}
    assert output.get("returnCodeInterpretation") == "No matches found"


def test_the_directory_survives_into_the_next_turn(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    AgentToolset(workspace).invoke("Bash", {"command": "cd sub"})
    assert json.loads((workspace.root / ".orin" / "state" / "cwd.json").read_text(encoding="utf-8")) == {"cwd": "sub"}
    next_turn = AgentToolset(workspace)
    assert next_turn._current_cwd == (workspace.root / "sub").resolve()


def test_a_planted_directory_outside_the_workspace_is_ignored(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    state = workspace.root / ".orin" / "state"
    state.mkdir(parents=True)
    (state / "cwd.json").write_text(json.dumps({"cwd": "../../.."}), encoding="utf-8")
    assert AgentToolset(workspace)._current_cwd == workspace.root
    (state / "cwd.json").write_text("{not json", encoding="utf-8")
    assert AgentToolset(workspace)._current_cwd == workspace.root


def test_long_output_is_saved_whole(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outcome = AgentToolset(workspace).invoke("Bash", {"command": f"{PY} -c \"print('x' * 30000)\""})
    output = outcome.data["tool_output"]
    saved = Path(output["persistedOutputPath"])
    assert saved.is_file() and saved.read_text(encoding="utf-8").count("x") >= 30000
    assert output["persistedOutputSize"] >= 30000
    assert "is saved at" in outcome.content


def test_a_background_command_reports_its_task_id(tmp_path: Path) -> None:
    toolset = AgentToolset(_workspace(tmp_path))
    outcome = toolset.invoke("Bash", {"command": f"{PY} -c \"import time; time.sleep(5)\"", "run_in_background": True})
    output = outcome.data["tool_output"]
    assert output["backgroundTaskId"].startswith("bash_")
    toolset.invoke("TaskStop", {"task_id": str(outcome.payload["pid"])})

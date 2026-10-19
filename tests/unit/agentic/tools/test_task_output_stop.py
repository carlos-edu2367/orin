"""W4: TaskOutput/TaskStop accept bash_<pid> and subagent ids, and report Claude Code-style outputs."""
from __future__ import annotations

import sys
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.tools.public_names import arguments_in
from agentos.agentic.tools.shell.tasks import TaskOutputOutput, TaskStopOutput
from agentos.agentic.workspace import ConversationWorkspace

PY = f'"{sys.executable}"'


def _toolset(tmp_path: Path, **options) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), **options)


def test_public_ids_and_waiting_map_to_the_handler() -> None:
    assert arguments_in("read_process_output", {"task_id": "bash_4242"}) == {"pid": 4242, "block": True}
    assert arguments_in("read_process_output", {"task_id": "4242", "block": False, "timeout": 2500}) == {
        "pid": 4242, "block": False, "wait_seconds": 3,
    }
    assert arguments_in("read_process_output", {"task_id": "agent-7"}) == {"pid": "agent-7", "block": True}
    assert arguments_in("read_process_output", {"pid": 17}) == {"pid": 17}  # Orin's own shape: no wait added
    assert arguments_in("stop_process", {"shell_id": "bash_18"}) == {"pid": 18}


def test_task_output_waits_for_a_background_command(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    started = toolset.invoke("Bash", {"command": f"{PY} -c \"import time; time.sleep(0.5); print('done')\"", "run_in_background": True})
    task_id = started.data["tool_output"]["backgroundTaskId"]
    outcome = toolset.invoke("TaskOutput", {"task_id": task_id, "timeout": 20000})
    output = outcome.data["tool_output"]
    TaskOutputOutput.model_validate(output)
    assert output["task_id"] == task_id and output["task_type"] == "local_bash"
    assert output["status"] == "exited" and "done" in output["output"]


def test_task_stop_takes_the_bash_id(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    started = toolset.invoke("Bash", {"command": f"{PY} -c \"import time; time.sleep(30)\"", "run_in_background": True})
    task_id = started.data["tool_output"]["backgroundTaskId"]
    outcome = toolset.invoke("TaskStop", {"task_id": task_id})
    assert outcome.status == "succeeded", outcome.content
    output = outcome.data["tool_output"]
    TaskStopOutput.model_validate(output)
    assert output["task_id"] == task_id and output["task_type"] == "local_bash"


def test_task_output_reads_a_background_subagent(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def check(agent_id: str, wait_seconds: int = 0) -> ToolOutcome:
        calls.append((agent_id, wait_seconds))
        return ToolOutcome("succeeded", "pronto", "the subagent's answer", {"status": "completed", "agent_id": agent_id})

    toolset = _toolset(tmp_path, check_subagent=check)
    outcome = toolset.invoke("TaskOutput", {"task_id": "agent-7", "timeout": 5000})
    assert outcome.status == "succeeded" and "the subagent's answer" in outcome.content
    assert calls == [("agent-7", 5)]
    output = outcome.data["tool_output"]
    assert output["task_type"] == "local_agent" and output["status"] == "completed"


def test_unknown_ids_and_agent_stops_are_refused(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    assert toolset.invoke("TaskOutput", {"task_id": "agent-7"}).status == "failed"
    assert toolset.invoke("TaskStop", {"task_id": "agent-7"}).status == "failed"
    assert toolset.invoke("TaskStop", {"task_id": "bash_999999"}).status == "failed"

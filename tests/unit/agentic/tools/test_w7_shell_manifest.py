"""W7: a background shell's manifest entry carries its creation time, so the tasks panel can stop only that process."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.process_identity import process_created


def test_a_background_shell_records_its_creation_time(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "chat_abc"))
    started = toolset.invoke("run_command", {"command": "python -c \"import time; time.sleep(30)\"", "background": True})
    assert started.status == "succeeded"
    pid = started.payload["pid"]
    try:
        manifest = json.loads((Path(toolset.workspace.root) / ".orin" / "logs" / "processes.json").read_text(encoding="utf-8"))
        assert manifest[str(pid)]["created"] is not None
        assert manifest[str(pid)]["created"] == process_created(pid)
    finally:
        toolset.invoke("stop_process", {"pid": pid})

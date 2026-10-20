"""W6-R2: a killed `orin code` process (TerminateProcess: nothing can catch it) ends its turn through the lease."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from agentos.cli_code.sessions import SessionIndex

from .live_runtime import LiveRuntime
from .test_headless import MODEL

# Thirty seconds of answer: the lease (15 s after the last heartbeat) ends the turn well before it would finish.
SLOW_PROMPT = "[[slow:120]] keep going"


def _executable() -> str:
    return str(Path(sys.executable).parent / ("orin-code.exe" if sys.platform == "win32" else "orin-code"))


def _conversation_of(runtime: LiveRuntime, workspace: Path, deadline: float) -> str | None:
    while time.monotonic() < deadline:
        session = SessionIndex(runtime.paths.data).latest_for(workspace.resolve())
        if session is not None:
            return session.conversation_id
        time.sleep(0.25)
    return None


def test_a_killed_cli_process_ends_its_turn_through_the_lease(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("killed-cli")
    process = subprocess.Popen([_executable(), "-p", SLOW_PROMPT, *MODEL], cwd=str(workspace), env=orin_runtime.env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        conversation_id = _conversation_of(orin_runtime, workspace, time.monotonic() + 30)
        assert conversation_id is not None
        time.sleep(2.0)  # the turn is streaming when the process dies
        process.kill()  # TerminateProcess on Windows: no handler runs, no cancel is posted
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    deadline = time.monotonic() + 30
    turn = None
    with orin_runtime.api() as api:
        while time.monotonic() < deadline:
            conversation = api.get(f"/v1/conversations/{conversation_id}").json()
            turn = conversation["turns"][-1] if conversation["turns"] else None
            if turn and turn["state"] in {"cancelled", "completed", "failed"}:
                break
            time.sleep(0.5)
        answers = [item["content"] for item in conversation["messages"] if item["role"] == "assistant"]
    assert turn is not None and turn["state"] == "cancelled", turn
    assert answers and "word119" not in answers[-1]

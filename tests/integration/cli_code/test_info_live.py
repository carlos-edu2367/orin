"""W6 Task 20: `orin code doctor` and background runs, against the live runtime."""
from __future__ import annotations

import json
import sys
import time

from .live_runtime import LiveRuntime
from .test_headless import MODEL, orin_code


def _no_browser(runtime: LiveRuntime) -> dict[str, str]:
    # webbrowser runs $BROWSER with the URL: a Python that does nothing, so no window opens during the tests.
    return {**runtime.env, "BROWSER": f'"{sys.executable.replace(chr(92), "/")}" -c "import sys" %s'}



def test_doctor_reports_a_healthy_local_orin(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("doctor"), "doctor")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok   runtime" in done.stdout and "ok   providers: ollama" in done.stdout


def test_a_background_run_and_its_log(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("background")
    started = orin_code(orin_runtime, workspace, "-p", "[[say:background done]]", "--bg", *MODEL)
    assert started.returncode == 0, started.stderr
    run_id = started.stdout.strip()
    assert run_id.startswith("bg_")
    attached = orin_code(orin_runtime, workspace, "attach", run_id, timeout=180)
    assert attached.returncode == 0 and "background done" in attached.stdout
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        listed = json.loads(orin_code(orin_runtime, workspace, "agents", "--json").stdout)
        if all(item["state"] == "finished" for item in listed["background"] if item["id"] == run_id):
            break
        time.sleep(0.5)
    assert orin_code(orin_runtime, workspace, "logs", run_id).stdout.strip() == "background done"
    assert orin_code(orin_runtime, workspace, "rm", run_id).returncode == 0


def test_stopping_a_background_run_cancels_its_turn(orin_runtime: LiveRuntime) -> None:
    from agentos.cli_code.sessions import SessionIndex

    workspace = orin_runtime.workspace("background-stop")
    run_id = orin_code(orin_runtime, workspace, "-p", "[[slow]]", "--bg", *MODEL).stdout.strip()
    record = json.loads((orin_runtime.paths.data / "cli-background" / f"{run_id}.json").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 60
    session = None
    while session is None and time.monotonic() < deadline:
        session = SessionIndex(orin_runtime.paths.data).find(record["session_id"])
        time.sleep(0.5)
    assert session is not None
    assert "stopped" in orin_code(orin_runtime, workspace, "stop", run_id).stdout
    with orin_runtime.api() as api:
        while time.monotonic() < deadline:
            turns = api.get(f"/v1/conversations/{session.conversation_id}").json()["turns"]
            if turns and turns[-1]["state"] in {"cancelled", "completed", "failed"}:
                break
            time.sleep(0.5)
    assert turns[-1]["state"] == "cancelled"
    # The run's process is gone too (W6-R3: stop kills the validated process, nothing else).
    from agentos.launcher.state import process_is_alive

    gone_by = time.monotonic() + 30
    while process_is_alive(int(record["pid"])) and time.monotonic() < gone_by:
        time.sleep(0.2)
    assert not process_is_alive(int(record["pid"]))

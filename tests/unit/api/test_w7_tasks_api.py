"""W7: the background tasks panel lists the workspace's shells and stops only the process a record names."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from agentos.process_identity import process_created

from tests.unit.api.test_w7_settings_api import AUTH, _client

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def _root(client) -> Path:
    return Path(client.get("/v1/conversations/c1/workspace-trust", headers=AUTH).json()["path"])


def _manifest(root: Path, entries: dict) -> None:
    path = root / ".orin" / "logs" / "processes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_shells_are_listed_with_a_state_that_follows_the_creation_time(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = _root(client)
    live = subprocess.Popen(SLEEP)
    try:
        (root / ".orin" / "logs").mkdir(parents=True, exist_ok=True)
        (root / ".orin" / "logs" / "a.log").write_text("ready on port 3000\nGITHUB_TOKEN=ghp_0123456789abcdefghijABCD\n", encoding="utf-8")
        _manifest(root, {
            str(live.pid): {"command": "npm run dev", "log_path": ".orin/logs/a.log", "started_at": "2026-10-20T10:00:00+00:00",
                            "created": process_created(live.pid)},
            "999999": {"command": "old", "started_at": "2026-10-19T10:00:00+00:00"},  # written before W7: no creation time
            str(live.pid + 0) + "0": {"command": "reused", "created": 1, "log_path": "../../outside.txt"},
        })
        tasks = {item["command"]: item for item in client.get("/v1/conversations/c1/tasks", headers=AUTH).json()["tasks"]}
        assert tasks["npm run dev"]["state"] == "running" and tasks["npm run dev"]["stoppable"] is True
        assert "ready on port 3000" in tasks["npm run dev"]["log_tail"] and "ghp_" not in tasks["npm run dev"]["log_tail"]
        assert tasks["old"]["state"] == "unknown" and tasks["old"]["stoppable"] is False
        assert tasks["reused"]["state"] == "finished" and tasks["reused"]["log_tail"] == ""
    finally:
        live.kill()


def test_stop_kills_only_a_verified_process(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = _root(client)
    live = subprocess.Popen(SLEEP)
    bystander = subprocess.Popen(SLEEP)
    try:
        _manifest(root, {
            str(live.pid): {"command": "server", "created": process_created(live.pid)},
            str(bystander.pid): {"command": "reused pid", "created": 12345},
        })
        headers = {**AUTH, "Idempotency-Key": "s1"}
        refused = client.post(f"/v1/conversations/c1/tasks/shell:{bystander.pid}/stop", headers=headers)
        assert refused.status_code == 200 and refused.json()["stopped"] is False
        time.sleep(0.3)
        assert bystander.poll() is None
        stopped = client.post(f"/v1/conversations/c1/tasks/shell:{live.pid}/stop", headers=headers)
        assert stopped.json()["stopped"] is True
        assert live.wait(timeout=20) is not None
        assert client.post("/v1/conversations/c1/tasks/shell:1/stop", headers=headers).status_code == 404
        assert client.post("/v1/conversations/c1/tasks/agent:x/stop", headers=headers).status_code == 422
    finally:
        live.kill()
        bystander.kill()

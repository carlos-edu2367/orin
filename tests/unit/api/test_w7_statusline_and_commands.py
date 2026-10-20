"""W7: the status line runs the person's command with the session as JSON (trusted folders only), and the command
catalog lists the folder's skills and commands."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agentos.configuration.mode import RuntimeMode
from agentos.settings.paths import orin_user_dir

from tests.unit.api.test_w7_settings_api import AUTH, _client, _write

SCRIPT = "import sys, json; d = json.load(sys.stdin); print('model=' + d['model']['id']); print('dir=' + d['workspace']['current_dir'])"


def _statusline(client):
    return client.post("/v1/conversations/c1/statusline", headers={**AUTH, "Idempotency-Key": "s"}).json()


def test_the_status_line_waits_for_trust_then_prints_the_commands_lines(tmp_path: Path) -> None:
    script = tmp_path / "line.py"
    script.write_text(SCRIPT, encoding="utf-8")
    _write(orin_user_dir() / "settings.json", {"statusLine": {"type": "command", "command": f'"{sys.executable}" "{script}"'}})
    client = _client(tmp_path)
    assert _statusline(client) == {"enabled": False, "lines": [], "error": "workspace_not_trusted"}
    client.post("/v1/conversations/c1/workspace-trust", headers={**AUTH, "Idempotency-Key": "t"})
    answer = _statusline(client)
    assert answer["enabled"] is True and answer["error"] is None
    assert answer["lines"][0] == "model=m" and answer["lines"][1].startswith("dir=")


def test_no_status_line_without_a_setting_or_with_hooks_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/v1/conversations/c1/workspace-trust", headers={**AUTH, "Idempotency-Key": "t"})
    assert _statusline(client)["error"] == "not_configured"
    _write(orin_user_dir() / "settings.json", {"statusLine": {"type": "command", "command": "echo hi"}, "disableAllHooks": True})
    assert _statusline(client)["error"] == "hooks_disabled"


def test_a_failing_command_reports_it(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"statusLine": {"type": "command", "command": f'"{sys.executable}" -c "import sys; sys.exit(3)"'}})
    client = _client(tmp_path)
    client.post("/v1/conversations/c1/workspace-trust", headers={**AUTH, "Idempotency-Key": "t"})
    assert _statusline(client) == {"enabled": True, "lines": [], "error": "command_failed"}


def test_a_shared_server_runs_no_status_line(tmp_path: Path) -> None:
    client = _client(tmp_path, RuntimeMode.SERVER)
    assert _statusline(client)["error"] == "shell_unavailable"


def test_the_catalog_lists_the_skills_a_person_can_run(tmp_path: Path) -> None:
    for name, extra in (("review-pr", ""), ("internal", "user-invocable: false\n")):
        folder = orin_user_dir() / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\ndescription: Does {name}\nargument-hint: <pr>\n{extra}---\nBody", encoding="utf-8")
    body = _client(tmp_path).get("/v1/conversations/c1/commands", headers=AUTH).json()
    names = [item["name"] for item in body["commands"]]
    assert "review-pr" in names and "internal" not in names
    item = next(entry for entry in body["commands"] if entry["name"] == "review-pr")
    assert item["description"] == "Does review-pr" and item["argument_hint"] == "<pr>" and item["origin"] == "user"
    assert json.dumps(body)  # plain JSON

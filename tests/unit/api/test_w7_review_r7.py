"""W7-R7: a repository's `.orin` folder that is a link elsewhere cannot send a project write out of the repository."""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.api.test_w7_settings_api import AUTH, _client


def _link(link: Path, target: Path) -> None:
    """A directory link the way a cloned repository can contain one (a junction on Windows, a symlink elsewhere)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.exists():
            return
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        link.symlink_to(target, target_is_directory=True)


def _repository(client) -> Path:
    return Path(client.get("/v1/conversations/c1/workspace-trust", headers=AUTH).json()["path"])


def test_a_project_rule_through_a_linked_orin_folder_is_refused_and_writes_nothing_outside(tmp_path) -> None:
    client = _client(tmp_path)
    root = _repository(client)
    outside = tmp_path / "outside-the-repo"
    outside.mkdir()
    _link(root / ".orin", outside)

    response = client.put("/v1/settings/permissions", headers=AUTH,
                          json={"conversation_id": "c1", "layer": "project", "kind": "allow", "rule": "Bash(rm:*)", "op": "add"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "outside_folder"
    assert list(outside.iterdir()) == []


def test_a_project_agent_through_a_linked_orin_folder_is_refused_and_written_nowhere(tmp_path) -> None:
    client = _client(tmp_path)
    root = _repository(client)
    outside = tmp_path / "outside-the-repo"
    outside.mkdir()
    _link(root / ".orin", outside)

    response = client.put("/v1/settings/agents/reviewer", headers=AUTH,
                          json={"conversation_id": "c1", "scope": "project", "description": "d", "prompt": "p"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "outside_folder"
    assert list(outside.rglob("*.md")) == []


def test_deleting_a_project_agent_through_a_linked_folder_leaves_the_outside_file(tmp_path) -> None:
    client = _client(tmp_path)
    root = _repository(client)
    outside = tmp_path / "outside-the-repo"
    (outside / "agents").mkdir(parents=True)
    (outside / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: personal\n---\n\np\n", encoding="utf-8")
    _link(root / ".orin", outside)

    response = client.delete("/v1/settings/agents/reviewer?scope=project&conversation_id=c1", headers=AUTH)

    assert response.status_code == 422
    assert (outside / "agents" / "reviewer.md").is_file()


def test_a_project_agent_in_the_repository_is_still_written(tmp_path) -> None:
    client = _client(tmp_path)
    root = _repository(client)

    response = client.put("/v1/settings/agents/reviewer", headers=AUTH,
                          json={"conversation_id": "c1", "scope": "project", "description": "d", "prompt": "p"})

    assert response.status_code == 200
    assert (root / ".orin" / "agents" / "reviewer.md").is_file()


def test_an_agent_write_does_not_touch_a_file_that_has_the_old_predictable_temporary_name(tmp_path) -> None:
    from agentos.settings.paths import orin_user_dir

    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".helper.md.tmp").write_text("KEEP ME", encoding="utf-8")
    client = _client(tmp_path)

    response = client.put("/v1/settings/agents/helper", headers=AUTH, json={"scope": "user", "description": "d", "prompt": "p"})

    assert response.status_code == 200
    assert (folder / ".helper.md.tmp").read_text(encoding="utf-8") == "KEEP ME"
    assert response.json()["path"].endswith("helper.md")

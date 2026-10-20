"""W7-R6: editing an agent keeps what the form does not show; an agent is editable only where its file says it is."""
from __future__ import annotations

import json

from agentos.settings.paths import orin_user_dir
from tests.unit.api.test_w7_settings_api import AUTH, _client


def _agent(client, name: str) -> dict:
    agents = client.get("/v1/settings/agents", headers=AUTH).json()["agents"]
    return next(item for item in agents if item["name"] == name)


def test_editing_an_agent_keeps_the_frontmatter_the_form_does_not_show(tmp_path) -> None:
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "helper.md").write_text(
        "---\nname: helper\ndescription: d\nskills: pdf\ncolor: blue\nmemory: user\nhooks:\n  Stop:\n    - command: echo\n---\n\nprompt\n",
        encoding="utf-8",
    )
    client = _client(tmp_path)

    response = client.put("/v1/settings/agents/helper", headers=AUTH, json={"scope": "user", "description": "d2", "prompt": "prompt"})

    text = (folder / "helper.md").read_text(encoding="utf-8")
    assert response.status_code == 200
    assert 'description: "d2"' in text
    assert "skills: pdf" in text and "color: blue" in text and "memory: user" in text
    assert "hooks:\n  Stop:\n    - command: echo" in text


def test_an_agent_whose_file_name_differs_from_its_name_is_not_editable(tmp_path) -> None:
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reviewer.md").write_text("---\nname: code-reviewer\ndescription: d\n---\n\nprompt\n", encoding="utf-8")
    client = _client(tmp_path)

    assert _agent(client, "code-reviewer")["editable"] is False


def test_a_new_agent_does_not_overwrite_an_existing_file(tmp_path) -> None:
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "helper.md").write_text("---\nname: helper\ndescription: keep me\n---\n\nprompt\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.put("/v1/settings/agents/helper", headers=AUTH,
                          json={"scope": "user", "description": "new", "prompt": "new prompt", "create": True})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_exists"
    assert "keep me" in (folder / "helper.md").read_text(encoding="utf-8")


def test_a_new_agent_is_created_when_its_name_is_free(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.put("/v1/settings/agents/fresh", headers=AUTH,
                          json={"scope": "user", "description": "new", "prompt": "new prompt", "create": True})

    assert response.status_code == 200
    assert (orin_user_dir() / "agents" / "fresh.md").is_file()

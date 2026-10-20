"""W7-R5: removing a rule that is not in the file being written is refused, not a silent 200."""
from __future__ import annotations

import json

from agentos.settings.paths import claude_user_dir, orin_user_dir
from tests.unit.api.test_w7_settings_api import AUTH, _client


def test_removing_a_rule_that_is_not_in_the_orin_file_is_refused_and_writes_nothing(tmp_path) -> None:
    claude_file = claude_user_dir() / "settings.json"
    claude_file.parent.mkdir(parents=True, exist_ok=True)
    claude_file.write_text(json.dumps({"permissions": {"allow": ["Bash(curl:*)"]}}), encoding="utf-8")
    orin_file = orin_user_dir() / "settings.json"
    client = _client(tmp_path)

    response = client.put("/v1/settings/permissions", headers=AUTH,
                          json={"layer": "user", "kind": "allow", "rule": "Bash(curl:*)", "op": "remove"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_not_in_file"
    assert not orin_file.exists()
    assert json.loads(claude_file.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Bash(curl:*)"]}}


def test_removing_a_rule_that_is_in_the_orin_file_still_works(tmp_path) -> None:
    orin_file = orin_user_dir() / "settings.json"
    orin_file.parent.mkdir(parents=True, exist_ok=True)
    orin_file.write_text(json.dumps({"permissions": {"allow": ["Bash(git status)", "Read"]}}), encoding="utf-8")
    client = _client(tmp_path)

    response = client.put("/v1/settings/permissions", headers=AUTH,
                          json={"layer": "user", "kind": "allow", "rule": "Bash(git status)", "op": "remove"})

    assert response.status_code == 200
    assert json.loads(orin_file.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Read"]}}

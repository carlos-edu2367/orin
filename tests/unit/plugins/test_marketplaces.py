"""W5: plugin marketplaces are fetched and read, as Claude Code lays them out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import metadata
from agentos.plugins.marketplace import marketplace_file, parse_marketplace
from agentos.plugins.service import PluginService, PluginServiceError
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def test_the_index_lives_in_orins_or_claude_codes_folder(tmp_path: Path) -> None:
    assert marketplace_file(tmp_path) is None
    _write(tmp_path / "marketplace.json", {})
    _write(tmp_path / ".claude-plugin" / "marketplace.json", {})
    assert marketplace_file(tmp_path) == tmp_path / ".claude-plugin" / "marketplace.json"
    _write(tmp_path / ".orin-plugin" / "marketplace.json", {})
    assert marketplace_file(tmp_path) == tmp_path / ".orin-plugin" / "marketplace.json"


def test_every_kind_of_source_becomes_a_reference(tmp_path: Path) -> None:
    (tmp_path / "plugins" / "fmt").mkdir(parents=True)
    marketplace = parse_marketplace({"name": "Team Tools", "plugins": [
        {"name": "fmt", "source": "./plugins/fmt"},
        {"name": "escape", "source": "../outside"},
        {"name": "gh", "source": {"source": "github", "repo": "acme/tools"}},
        {"name": "gh-sub", "source": {"source": "github", "repo": "acme/mono", "path": "plugins/lint", "ref": "v2"}},
        {"name": "url", "source": {"source": "url", "url": "https://github.com/acme/url-plugin.git"}},
        {"name": "subdir", "source": {"source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": "plugins/test"}},
    ]}, root=tmp_path)
    references = {item.name: item.reference for item in marketplace.plugins}
    assert Path(references["fmt"]) == (tmp_path / "plugins" / "fmt").resolve()
    assert "escape" not in references
    assert references["gh"] == "acme/tools"
    assert references["gh-sub"] == "https://github.com/acme/mono/tree/v2/plugins/lint"
    assert references["url"] == "https://github.com/acme/url-plugin.git"
    assert references["subdir"] == "https://github.com/acme/mono/tree/HEAD/plugins/test"


@pytest.fixture()
def service(tmp_path: Path) -> PluginService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PluginService(engine, plugin_root=tmp_path / "plugins", skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService())


def _marketplace(root: Path) -> Path:
    _write(root / ".claude-plugin" / "marketplace.json", {"name": "team-tools", "owner": {"name": "Acme"}, "plugins": [
        {"name": "fmt", "description": "Formats code", "source": "./plugins/fmt"},
    ]})
    _write(root / "plugins" / "fmt" / ".claude-plugin" / "plugin.json", {"name": "fmt", "version": "1.0.0"})
    _write(root / "plugins" / "fmt" / "skills" / "format" / "SKILL.md", "---\nname: format\ndescription: Formats\n---\nFormat it.")
    return root


def test_a_marketplace_is_fetched_searched_per_person_and_installs_by_name(service: PluginService, tmp_path: Path) -> None:
    added = service.add_marketplace(user_id="u1", reference=str(_marketplace(tmp_path / "market")))
    assert added["name"] == "team-tools" and Path(added["clone_path"]).is_dir()
    found = service.search("fmt", user_id="u1")
    assert [(item["name"], item["marketplace"]) for item in found if item["marketplace"] == "team-tools"] == [("fmt", "team-tools")]
    assert not [item for item in service.search("fmt", user_id="u2") if item["marketplace"] == "team-tools"]
    plugin = service.inspect(user_id="u1", reference="fmt@team-tools")
    assert plugin["plugin_id"] == "fmt" and plugin["state"] == "pending_approval"
    with pytest.raises(PluginServiceError):
        service.inspect(user_id="u1", reference="fmt@another-market")


def test_a_folder_without_an_index_is_not_a_marketplace(service: PluginService, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(PluginServiceError, match="marketplace.json"):
        service.add_marketplace(user_id="u1", reference=str(tmp_path / "empty"))


def test_a_marketplace_whose_name_gives_no_identifier_is_refused(service: PluginService, tmp_path: Path) -> None:
    _write(tmp_path / "bad" / ".claude-plugin" / "marketplace.json", {"name": "!!!", "plugins": [{"name": "fmt", "source": "owner/repo"}]})
    with pytest.raises(PluginServiceError, match="name"):
        service.add_marketplace(user_id="u1", reference=str(tmp_path / "bad"))

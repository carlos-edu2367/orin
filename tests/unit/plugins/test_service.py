import json
from sqlalchemy import create_engine
from agentos.persistence.postgres.schema import metadata
from agentos.plugins.service import PluginService, PluginServiceError
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary

def _package(root, version="1.0.0"):
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":version}), encoding="utf-8")
    (root / "skills" / "s").mkdir(parents=True)
    (root / "skills" / "s" / "SKILL.md").write_text("---\nname: s\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    return root

def _service(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PluginService(engine, plugin_root=tmp_path / "plugins", skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService())

def test_service_inspects_approves_and_removes(tmp_path):
    service = _service(tmp_path)
    result = service.inspect(user_id="u1", reference=str(_package(tmp_path / "src")))
    assert result["state"] == "pending_approval" and result["skills"][0]["skill_id"] == "demo:s"
    approved = service.approve(user_id="u1", plugin_id="demo")
    assert approved["state"] == "active" and approved["contribution_count"] == 1
    try: service.approve(user_id="u1", plugin_id="demo")
    except PluginServiceError: pass
    else: raise AssertionError("active plugin was approved twice")
    service.remove(user_id="u1", plugin_id="demo")
    assert service.list("u1") == []

def test_different_version_replaces_pending_record(tmp_path):
    service = _service(tmp_path)
    source = _package(tmp_path / "src")
    service.inspect(user_id="u1", reference=str(source))
    (source / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":"1.1.0"}), encoding="utf-8")
    assert service.inspect(user_id="u1", reference=str(source))["version"] == "1.1.0"
    assert len(service.list("u1")) == 1

def test_discover_library_returns_registry_entries(tmp_path):
    service = _service(tmp_path)
    library = service.discover_library()
    assert library["web_search_available"] is False
    assert library["entries"][0]["name"] == "superpowers"
    assert library["entries"][0]["origin"] == "registry"

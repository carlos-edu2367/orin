from pathlib import Path
import shutil

from sqlalchemy import create_engine

from agentos.mcp.service import McpServerService
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.postgres.skills import PostgresSkillLibraryService
from agentos.plugins.service import PluginService


def test_real_plugin_install_uses_existing_skill_and_mcp_services(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    library = PostgresSkillLibraryService(engine)
    mcp = McpServerService(engine)
    service = PluginService(engine, plugin_root=tmp_path / "plugins", skill_library=library, mcp_service=mcp)
    fixture = Path(__file__).parents[1] / "fixtures" / "plugin_sample"
    result = service.inspect(user_id="u1", reference=str(fixture))
    assert len(result["skills"]) == 2 and len(result["mcp_servers"]) == 1
    assert any("hook" in item.lower() for item in result["warnings"])
    approved = service.approve(user_id="u1", plugin_id="sample-plugin")
    assert approved["state"] == "active"
    registry = library.registry_for("u1")
    assert registry.resolve("sample-plugin:one").name == "one"
    assert registry.read_resource("sample-plugin:one", "references/guide.md").startswith("# Guide")
    assert mcp.list("u1")[0]["state"] == "pending_approval"
    install_path = Path(approved["install_path"])
    service.remove(user_id="u1", plugin_id="sample-plugin")
    assert not install_path.exists()

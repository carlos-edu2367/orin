from agentos.plugins.activator import ActivationFailed, PluginActivator
from agentos.plugins.models import McpServerContribution, PluginInspection, PluginRef, SkillContribution
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary

def _inspection():
    return PluginInspection(PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc", skills=(SkillContribution("demo:s", "s", "d", "skills/s/SKILL.md"),), mcp_servers=(McpServerContribution("demo-mcp", "MCP", "stdio", "npx", ("-y", "x"), None, ("TOKEN",)),))

def test_activation_registers_and_deactivation_rolls_back(tmp_path):
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    library, mcp = FakeSkillLibrary(), FakeMcpService()
    activator = PluginActivator(skill_library=library, mcp_service=mcp)
    result = activator.activate(user_id="u1", install_path=tmp_path, inspection=_inspection())
    assert mcp.proposed[0]["user_id"] == "u1" and mcp.proposed[0]["secret_names"] == ["TOKEN"]
    activator.deactivate(user_id="u1", plugin_id="demo", contributions=result.contributions)
    assert library.installed == {} and mcp.removed == ["srv-1"]

def test_activation_failure_removes_skills(tmp_path):
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    library, mcp = FakeSkillLibrary(), FakeMcpService(failing=True)
    try: PluginActivator(skill_library=library, mcp_service=mcp).activate(user_id="u1", install_path=tmp_path, inspection=_inspection())
    except ActivationFailed: pass
    else: raise AssertionError("activation should fail")
    assert library.installed == {}

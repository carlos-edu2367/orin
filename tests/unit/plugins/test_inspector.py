import json
from agentos.plugins.inspector import inspect_plugin_package

def test_inspector_reports_declarative_contributions_and_warnings(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":"1.0.0"}), encoding="utf-8")
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    (tmp_path / "hooks").mkdir()
    result = inspect_plugin_package(tmp_path, package_digest="abc")
    assert result.skills[0].skill_id == "demo:s"
    assert any("hook" in warning.lower() for warning in result.warnings)

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


# Real SKILL.md content from github.com/obra/superpowers, skills/brainstorming/SKILL.md
# at tag 5.1.0. No `version` field: real-world plugin skills declare only name/description,
# with version tracked once at the plugin level (plugin.json), not per skill.
REAL_SUPERPOWERS_BRAINSTORMING_SKILL = """---
name: brainstorming
description: "You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation."
---

# Brainstorming Ideas Into Designs

Help turn ideas into fully formed designs and specs through natural collaborative dialogue.
"""


def test_inspector_accepts_real_world_plugin_skill_without_version_field(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "superpowers", "version": "5.1.0"}), encoding="utf-8"
    )
    (tmp_path / "skills" / "brainstorming").mkdir(parents=True)
    (tmp_path / "skills" / "brainstorming" / "SKILL.md").write_text(
        REAL_SUPERPOWERS_BRAINSTORMING_SKILL, encoding="utf-8"
    )
    result = inspect_plugin_package(tmp_path, package_digest="abc")
    assert not any("quebrada" in warning.lower() for warning in result.warnings)
    assert result.skills[0].skill_id == "superpowers:brainstorming"
    assert result.skills[0].name == "brainstorming"

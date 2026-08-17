from pathlib import Path

from agentos.plugins.activator import ActivationFailed, PluginActivator
from agentos.plugins.models import CommandContribution, HookContribution, McpServerContribution, PluginInspection, PluginRef, SkillContribution
from tests.unit.plugins.fakes import FakeCommandLibrary, FakeHookEngine, FakeMcpService, FakeSkillLibrary

def _inspection():
    return PluginInspection(PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc", skills=(SkillContribution("demo:s", "s", "d", "skills/s/SKILL.md"),), mcp_servers=(McpServerContribution("demo-mcp", "MCP", "stdio", "npx", ("-y", "x"), None, ("TOKEN",)),))

def test_activation_resolves_claude_plugin_root_in_mcp_command_and_args(tmp_path):
    """Real Claude Code plugins declare their MCP launch command with
    ${CLAUDE_PLUGIN_ROOT}, exactly like hooks do (see hook_executor.py's own
    substitution). Reproduces a live bug: obsidian-second-brain's vault
    server was rejected by StdioTransport's shell-metacharacter check
    because the literal "${CLAUDE_PLUGIN_ROOT}/..." string, containing an
    unresolved "$", was sent to mcp_service.propose() unchanged."""
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        mcp_servers=(McpServerContribution(
            "demo-vault", "vault", "stdio", "uv",
            ("run", "--with", "mcp<2", "python", "${CLAUDE_PLUGIN_ROOT}/integrations/obsidian-mcp-server/server.py"),
            None, (),
        ),),
    )
    mcp = FakeMcpService()
    activator = PluginActivator(skill_library=FakeSkillLibrary(), mcp_service=mcp)

    activator.activate(user_id="u1", install_path=tmp_path, inspection=inspection)

    # Path(...) renders with native (backslash) separators while the rest of
    # the placeholder string keeps the manifest's forward slashes; resolve()
    # normalizes both to the same file, which is what actually matters —
    # Windows accepts either separator, same as hook_executor.py's identical
    # substitution already relies on.
    expected = (tmp_path / "integrations" / "obsidian-mcp-server" / "server.py").resolve()
    assert Path(mcp.proposed[0]["args"][-1]).resolve() == expected
    assert "${CLAUDE_PLUGIN_ROOT}" not in mcp.proposed[0]["args"][-1]
    assert "$" not in mcp.proposed[0]["command"]


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

def test_activation_accepts_plugin_skill_without_per_skill_version(tmp_path):
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\nbody", encoding="utf-8")
    library, mcp = FakeSkillLibrary(), FakeMcpService()
    activator = PluginActivator(skill_library=library, mcp_service=mcp)
    inspection = _inspection()

    result = activator.activate(user_id="u1", install_path=tmp_path, inspection=inspection)

    assert result.contributions[0]["reference"] == "demo:s"
    assert library.installed["demo"][0].version == "1.0.0"


def test_activation_registers_commands_and_deactivation_removes_them(tmp_path):
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "daily.md").write_text("body", encoding="utf-8")
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        commands=(CommandContribution("demo:daily", "daily", "d", "", "daily.md"),),
    )
    commands = FakeCommandLibrary()
    activator = PluginActivator(
        skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService(), command_library=commands
    )

    result = activator.activate(user_id="u1", install_path=tmp_path, inspection=inspection)

    assert commands.installed["u1/demo"][0].command_id == "demo:daily"
    assert {item["kind"] for item in result.contributions} == {"command"}
    assert result.contributions[0]["reference"] == "demo:daily"

    activator.deactivate(user_id="u1", plugin_id="demo", contributions=result.contributions)

    assert commands.installed == {}


def test_activation_works_without_a_command_library(tmp_path):
    """The library is optional, exactly like agent_templates."""
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        commands=(CommandContribution("demo:daily", "daily", "d", "", "daily.md"),),
    )

    result = PluginActivator(skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService()).activate(
        user_id="u1", install_path=tmp_path, inspection=inspection
    )

    assert result.contributions[0]["kind"] == "command"


def test_hooks_are_installed_without_consent_to_execute(tmp_path):
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        hooks=(HookContribution("demo:SessionStart:0", "SessionStart", "", "cmd", 10),),
    )

    result = PluginActivator(skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService()).activate(
        user_id="u1", install_path=tmp_path, inspection=inspection
    )

    hook = next(item for item in result.contributions if item["kind"] == "hook")
    assert hook["reference"] == "demo:SessionStart:0"
    assert hook["enabled"] is False
    assert hook["display_name"] == "SessionStart"


def test_activation_registers_hooks_disabled_and_deactivation_unregisters_them(tmp_path):
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        hooks=(HookContribution("demo:SessionStart:0", "SessionStart", "", "cmd", 10),),
    )
    hook_engine = FakeHookEngine()
    activator = PluginActivator(skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService(), hook_engine=hook_engine)

    result = activator.activate(user_id="u1", install_path=tmp_path, inspection=inspection)

    assert hook_engine.registered["u1/demo"][0].hook_id == "demo:SessionStart:0"
    assert hook_engine.enabled["u1/demo"] is False

    activator.deactivate(user_id="u1", plugin_id="demo", contributions=result.contributions)

    assert "u1/demo" not in hook_engine.registered

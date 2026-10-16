from __future__ import annotations

from pathlib import Path

from agentos.agentic.phases import PHASE_TOOLS, TOOLKIT_TOOLS, Phase, tools_for
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession, build_system_prompt
from agentos.code_mode.prompt import build_code_mode_system_prompt
from agentos.skills.builtins import load_builtin_skills
from agentos.skills.registry import SkillRegistry


SKILL_TOOLS = {
    "search_skills",
    "list_skills",
    "read_skill_resource",
    "use_skill",
    "create_skill",
    "edit_skill",
}

MCP_TOOLS = {
    "list_mcp_catalog",
    "list_mcp_servers",
    "configure_mcp",
    "test_mcp_server",
}

PLUGIN_TOOLS = {
    "search_plugin",
    "inspect_plugin",
    "install_plugin",
    "list_plugins",
    "uninstall_plugin",
}


class _FakeMcpService:
    def list(self, user_id: str) -> list[dict[str, object]]:
        return [{"server_id": "srv_1", "slug": "github", "display_name": "GitHub", "state": "active"}]

    def propose(self, command: dict[str, object]) -> dict[str, object]:
        return {
            "server_id": "srv_2",
            "slug": "github",
            "display_name": command.get("display_name", "GitHub"),
            "state": "pending_approval",
            "secret_names": list(command.get("secret_names") or []),
        }


class _FakePluginService:
    def search(self, query: str) -> list[dict[str, object]]:
        return [{"name": "superpowers"}]

    def list(self, user_id: str) -> list[dict[str, object]]:
        return []


class _FakeSkillLibrary:
    def registry_for(self, user_id: str, *, agent_id: str | None = None):
        return SkillRegistry(load_builtin_skills())


class _FakeStore:
    def main_agent_id(self, turn: dict[str, object]) -> str:
        return "agent:main"

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "instale o MCP do GitHub"}]


class _FakeAgentsStore:
    def list(self) -> list[dict[str, object]]:
        return []


def test_turn_session_wires_mcp_service_and_registers_mcp_plugin_skill_tools(tmp_path: Path) -> None:
    turn = {
        "turn_id": "turn-ext-1",
        "conversation_id": "conv_ext_1",
        "user_id": "user-ext-1",
        "execution_id": "exec-ext-1",
        "provider": "openrouter",
        "model_id": "test-model",
    }
    mcp_service = _FakeMcpService()
    plugin_service = _FakePluginService()
    skill_lib = _FakeSkillLibrary()

    session = TurnSession(
        turn=turn,
        store=_FakeStore(),
        agents_store=_FakeAgentsStore(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        skills=skill_lib.registry_for("user-ext-1"),
        skill_library=skill_lib,
        mcp_service=mcp_service,
        plugin_service=plugin_service,
    )

    assert session.mcp_service is mcp_service
    toolset = session._toolset(subagents=False)
    assert toolset._mcp_service is mcp_service
    assert toolset._mcp_user_id == "user-ext-1"

    defined_names = {item.name for item in toolset.definitions()}
    assert SKILL_TOOLS <= defined_names
    assert MCP_TOOLS <= defined_names
    assert PLUGIN_TOOLS <= defined_names

    # Invoke configure_mcp through the wired session toolset
    outcome = toolset.invoke("configure_mcp", {"catalog_id": "github", "display_name": "GitHub"})
    assert outcome.status == "succeeded"
    assert outcome.payload["mcp_approval"] is True
    assert outcome.payload["wait_for_user"] is True


def test_phase_controller_publishes_skill_mcp_and_plugin_tools_in_orient_and_execute() -> None:
    orient_tools = tools_for(Phase.ORIENT)
    execute_tools = tools_for(Phase.EXECUTE)
    plan_tools = tools_for(Phase.PLAN)

    assert SKILL_TOOLS <= orient_tools
    assert MCP_TOOLS <= orient_tools
    assert PLUGIN_TOOLS <= orient_tools

    assert SKILL_TOOLS <= execute_tools
    assert MCP_TOOLS <= execute_tools
    assert PLUGIN_TOOLS <= execute_tools

    assert SKILL_TOOLS <= plan_tools
    assert set(TOOLKIT_TOOLS.get("skills", ())) == SKILL_TOOLS
    assert SKILL_TOOLS <= set(PHASE_TOOLS[Phase.ORIENT])
    assert MCP_TOOLS <= set(PHASE_TOOLS[Phase.ORIENT])
    assert PLUGIN_TOOLS <= set(PHASE_TOOLS[Phase.ORIENT])


def test_code_mode_core_tools_includes_all_skill_mcp_and_plugin_tools() -> None:
    assert SKILL_TOOLS <= CODE_MODE_CORE_TOOLS
    assert MCP_TOOLS <= CODE_MODE_CORE_TOOLS
    assert PLUGIN_TOOLS <= CODE_MODE_CORE_TOOLS


def test_prompts_document_extension_tools_in_chat_and_code() -> None:
    code_prompt = build_code_mode_system_prompt()
    assert "configure_mcp" in code_prompt
    assert "install_plugin" in code_prompt
    assert "create_skill" in code_prompt

    chat_stable, _ = build_system_prompt(
        tool_names=tuple(sorted(SKILL_TOOLS | MCP_TOOLS | PLUGIN_TOOLS)),
        memories=[],
        agents=[],
        workspace_hint="Hint",
        subagents_enabled=False,
    )
    assert "configure_mcp" in chat_stable
    assert "install_plugin" in chat_stable
    assert "create_skill" in chat_stable


def test_turn_session_handles_missing_mcp_service_or_blank_user_id_gracefully(tmp_path: Path) -> None:
    turn_no_user = {
        "turn_id": "turn-ext-2",
        "conversation_id": "conv_ext_2",
        "user_id": "   ",
        "execution_id": "exec-ext-2",
        "provider": "openrouter",
        "model_id": "test-model",
    }
    session = TurnSession(
        turn=turn_no_user,
        store=_FakeStore(),
        agents_store=_FakeAgentsStore(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        mcp_service=_FakeMcpService(),
    )
    toolset = session._toolset(subagents=False)
    assert toolset._mcp_user_id is None
    defined_names = {item.name for item in toolset.definitions()}
    assert not (MCP_TOOLS & defined_names)


def test_turn_session_code_mode_runtime_publishes_extension_schemas_and_rejects_secret_values(tmp_path: Path) -> None:
    turn_code = {
        "turn_id": "turn-ext-code",
        "conversation_id": "conv_ext_code",
        "user_id": "user-ext-1",
        "execution_id": "exec-ext-code",
        "provider": "openrouter",
        "model_id": "test-model",
        "code_mode": "code",
    }
    skill_lib = _FakeSkillLibrary()
    session = TurnSession(
        turn=turn_code,
        store=_FakeStore(),
        agents_store=_FakeAgentsStore(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        skills=skill_lib.registry_for("user-ext-1"),
        skill_library=skill_lib,
        mcp_service=_FakeMcpService(),
        plugin_service=_FakePluginService(),
    )
    runtime = session.build_runtime()
    published = {
        str(item["function"]["name"])
        for item in runtime._tool_schemas(turn_code)
    }
    assert SKILL_TOOLS <= published
    assert MCP_TOOLS <= published
    assert PLUGIN_TOOLS <= published

    # Adversarial: model tries to sneak secret value into configure_mcp
    bad_outcome = runtime.toolset.invoke(
        "configure_mcp",
        {"catalog_id": "github", "display_name": "GitHub", "api_key": "ghp_leaked_secret"},
    )
    assert bad_outcome.status == "failed"
    assert "does not accept credential values" in bad_outcome.content


from __future__ import annotations

from pathlib import Path
import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.phases import PHASE_TOOLS, Phase, tools_for
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession, build_system_prompt
from agentos.agentic.workspace import ConversationWorkspace


class _FakeStore:
    def main_agent_id(self, turn: dict[str, object]) -> str:
        return "agent:main"

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "refatore o módulo de autenticação no Orin Code"}]


class _FakeAgentsStore:
    def list(self) -> list[dict[str, object]]:
        return []


def test_propose_code_session_registered_only_in_chat_mode(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-handoff")

    chat_toolset = AgentToolset(workspace, code_mode_active=False)
    chat_names = {item.name for item in chat_toolset.definitions()}
    assert "propose_code_session" in chat_names

    code_toolset = AgentToolset(workspace, code_mode_active=True)
    code_names = {item.name for item in code_toolset.definitions()}
    assert "propose_code_session" not in code_names
    assert "propose_code_session" not in CODE_MODE_CORE_TOOLS


def test_propose_code_session_populates_defaults_and_returns_wait_for_user(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-handoff-defaults")
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
        default_workspace_path="C:/Users/User/project",
        project_id="project_abc123",
    )

    outcome = toolset.invoke(
        "propose_code_session",
        {
            "title": "  Refatorar autenticação JWT  ",
            "prompt": "  Implementar rotação de refresh token em auth.py com testes unitários.  ",
        },
    )
    assert outcome.status == "succeeded"
    assert outcome.summary == "Sugestão para iniciar no Orin Code: Refatorar autenticação JWT"
    assert outcome.payload["wait_for_user"] is True
    assert outcome.payload["tool_kind"] == "code_handoff"

    proposal = outcome.payload["code_session_proposal"]
    assert proposal["proposal_id"].startswith("codeprop_")
    assert proposal["title"] == "Refatorar autenticação JWT"
    assert proposal["prompt"] == "Implementar rotação de refresh token em auth.py com testes unitários."
    assert proposal["workspace_path"] == "C:/Users/User/project"
    assert proposal["provider"] == "anthropic"
    assert proposal["model_id"] == "claude-sonnet-4-5"
    assert proposal["permission_mode"] == "accept_edits"
    assert proposal["project_id"] == "project_abc123"


def test_propose_code_session_allows_explicit_overrides(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-handoff-overrides")
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        default_provider="openrouter",
        default_model_id="default-model",
        default_workspace_path="C:/default/path",
        project_id="project_xyz",
    )

    outcome = toolset.propose_code_session(
        title="Planejar migração",
        prompt="Analisar schema e criar plano de migração.",
        workspace_path="C:/custom/repo",
        provider="openai",
        model_id="gpt-5",
        permission_mode="plan_mode",
    )
    proposal = outcome.payload["code_session_proposal"]
    assert proposal["workspace_path"] == "C:/custom/repo"
    assert proposal["provider"] == "openai"
    assert proposal["model_id"] == "gpt-5"
    assert proposal["permission_mode"] == "plan_mode"


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"title": "   ", "prompt": "Valid prompt"},
        {"title": "x" * 201, "prompt": "Valid prompt"},
        {"title": "Valid title", "prompt": "   "},
        {"title": "Valid title", "prompt": "x" * 16001},
        {"title": "Valid title", "prompt": "Valid prompt", "permission_mode": "full_autonomy"},
        {"title": "Valid title", "prompt": "Valid prompt", "permission_mode": "sudo_root"},
    ],
)
def test_propose_code_session_rejects_invalid_or_unsafe_inputs(
    tmp_path: Path, bad_kwargs: dict[str, str]
) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-handoff-validation")
    toolset = AgentToolset(workspace, code_mode_active=False)

    with pytest.raises(AgentToolError):
        toolset.propose_code_session(**bad_kwargs)

    invoked = toolset.invoke("propose_code_session", bad_kwargs)
    assert invoked.status == "failed"


def test_turn_session_wires_defaults_and_phase_controller_exposes_propose_code_session(tmp_path: Path) -> None:
    local_dir = tmp_path / "repo"
    local_dir.mkdir()
    turn = {
        "turn_id": "turn-handoff-1",
        "conversation_id": "conv_handoff_1",
        "user_id": "user-1",
        "execution_id": "exec-handoff-1",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "workspace_root_path": str(local_dir),
        "project_id": "project_1",
        "project_workspace_id": "workspace:project_1",
    }
    session = TurnSession(
        turn=turn,
        store=_FakeStore(),
        agents_store=_FakeAgentsStore(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    toolset = session._toolset(subagents=False, code_mode_active=False)
    outcome = toolset.invoke(
        "propose_code_session",
        {"title": "Abrir no Code", "prompt": "Executar suíte pytest e corrigir falhas."},
    )
    assert outcome.status == "succeeded"
    proposal = outcome.payload["code_session_proposal"]
    assert proposal["provider"] == "anthropic"
    assert proposal["model_id"] == "claude-sonnet-4-5"
    assert proposal["workspace_path"] == str(local_dir)
    assert proposal["project_id"] == "project_1"

    assert "propose_code_session" in tools_for(Phase.ORIENT)
    assert "propose_code_session" in tools_for(Phase.PLAN)
    assert "propose_code_session" in tools_for(Phase.EXECUTE)
    assert "propose_code_session" in PHASE_TOOLS[Phase.ORIENT]
    assert "propose_code_session" in PHASE_TOOLS[Phase.PLAN]
    assert "propose_code_session" in PHASE_TOOLS[Phase.EXECUTE]

    stable_prompt, _ = build_system_prompt(
        tool_names=("propose_code_session",),
        memories=[],
        agents=[],
        workspace_hint="Hint",
        subagents_enabled=False,
    )
    assert "propose_code_session" in stable_prompt


def test_propose_code_session_prompt_is_not_redacted_in_activity_event_and_ignores_default_model_placeholder(
    tmp_path: Path,
) -> None:
    from agentos.agentic.models import sanitize_public_mapping

    workspace = ConversationWorkspace(tmp_path, "conv-handoff-redaction")
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        default_provider="openrouter",
        default_model_id="qwen/qwen-3.7-flash",
    )
    long_prompt = "Traduzir aulas 7 e 8 do módulo 5 mantendo formatação markdown.\n" * 40
    outcome = toolset.propose_code_session(
        title="Tradução Aulas 7 e 8",
        prompt=long_prompt,
        provider="openai",
        model_id="default",
    )
    proposal = outcome.payload["code_session_proposal"]
    assert proposal["provider"] == "openrouter"
    assert proposal["model_id"] == "qwen/qwen-3.7-flash"

    sanitized = sanitize_public_mapping(outcome.payload)
    assert sanitized["code_session_proposal"]["prompt"] == long_prompt.strip()
    assert sanitized["code_session_proposal"]["prompt"] != "[REDACTED]"
    # Adversarial check: top-level or nested 'prompt' outside code_session_proposal is STILL redacted!
    adversarial = sanitize_public_mapping({"prompt": "secret system prompt", "nested": {"prompt": "secret"}})
    assert adversarial["prompt"] == "[REDACTED]"
    assert adversarial["nested"]["prompt"] == "[REDACTED]"



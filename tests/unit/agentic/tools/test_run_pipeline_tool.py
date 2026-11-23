"""WS-C10: the ``RunPipeline`` tool: off by default, approved pipelines only, never inline, no command chosen by the agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.tools.public_names import internal_name, public_name
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.permissions.engine import PermissionEngine
from agentos.pipelines import service as service_module
from agentos.pipelines.service import PipelineService
from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings, load_effective_settings

USER = "user-1"
WHOLE_COMMAND = (
    "version: 1\nid: so-cmd\nname: Cmd\nbudget: {max_tokens: 1000}\ninputs:\n  comando: {type: string, default: pytest -q}\n"
    "  nota: {type: string}\nsteps:\n  - id: g\n    gate:\n      all:\n        - command: ${{ inputs.comando }}\n"
)


def _on() -> EffectiveSettings:
    return EffectiveSettings(data={}, provenance={}, layers=(SettingsLayer("user", None, {"pipelines": {"agentMayStart": True}}),), errors=(), trusted=True)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "orin-home"))
    monkeypatch.setenv("ORIN_CLAUDE_HOME", str(tmp_path / "claude-home"))
    (tmp_path / "orin-home").mkdir()
    (tmp_path / "orin-home" / "settings.json").write_text('{"pipelines": {"agentMayStart": true}}', encoding="utf-8")
    engine = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    from agentos.conversations.chat import ChatApplication

    conversation_id = ChatApplication(store).create(
        user_id=USER, message="oi", provider="openai", model_id="gpt-4o", idempotency_key="k-tool"
    ).conversation_id

    def toolset(settings: EffectiveSettings | None = None, **kwargs) -> AgentToolset:  # noqa: ANN003
        built = AgentToolset(
            ConversationWorkspace(workspace, "chat_pipeline_tool"), chat_store=store, project_user_id=USER, turn_id="turn-1",
            conversation_id=conversation_id, **kwargs,
        )
        built._effective_settings = settings if settings is not None else _on()
        built._team_sender = "lead"
        return built

    class World:
        pass

    w = World()
    w.toolset, w.root, w.tmp = toolset, workspace, tmp_path
    return w


def names(toolset: AgentToolset) -> set[str]:
    return {item.name for item in toolset.definitions()}


def test_the_tool_is_not_published_while_the_key_is_off_and_a_direct_call_is_refused(world) -> None:  # noqa: ANN001
    off = world.toolset(empty_settings())
    assert "run_pipeline" not in names(off)
    with pytest.raises(AgentToolError) as caught:
        off.run_pipeline("pesquisa", {"pergunta": "x"})
    assert caught.value.error_code == "PIPELINES_AGENT_START_DISABLED"


def test_the_tool_is_published_to_the_lead_agent_only_when_the_key_is_on(world) -> None:  # noqa: ANN001
    assert "run_pipeline" in names(world.toolset())
    child = world.toolset()
    child._team_sender = None
    assert "run_pipeline" not in names(child)


def test_the_public_name_is_runpipeline_and_it_is_a_mutating_tool_the_broker_asks_about(world) -> None:  # noqa: ANN001
    assert public_name("run_pipeline") == "RunPipeline" and internal_name("RunPipeline") == "run_pipeline"
    definition = next(item for item in world.toolset().definitions() if item.name == "run_pipeline")
    assert definition.read_only is False and "mutates" in definition.policy_tags and definition.concurrency_safe is False
    engine = PermissionEngine(world.root, load_effective_settings(world.root, trusted=True), shell="posix", home=world.tmp / "h")
    assert engine.decide("run_pipeline", {"pipeline_id": "pesquisa"}, "ask_permissions").behavior == "ask"
    assert engine.decide("run_pipeline", {"pipeline_id": "pesquisa"}, "accept_edits").behavior == "ask"


def test_without_an_id_it_lists_the_pipelines_the_agent_may_start(world) -> None:  # noqa: ANN001
    user_dir = world.tmp / "orin-home" / "pipelines"
    user_dir.mkdir()
    (user_dir / "so-cmd.yaml").write_text(WHOLE_COMMAND, encoding="utf-8")
    result = world.toolset().run_pipeline()
    listed = {item["id"] for item in result["payload"]["pipelines"]}
    assert "pesquisa" in listed and "so-cmd" not in listed  # a user file is not approved for the agent until a person approves it


def test_starting_registers_a_pending_start_and_runs_nothing_now(world) -> None:  # noqa: ANN001
    result = world.toolset().run_pipeline("pesquisa", {"pergunta": "o que é X?"})
    assert "começa quando este turno terminar" in result["content"]
    pending = service_module.pending_for("turn-1")
    assert [p.pipeline_id for p in pending] == ["pesquisa"] and pending[0].inputs["pergunta"] == "o que é X?"
    assert result["payload"]["budget"]["max_tokens"] > 0
    service_module._take_pending("turn-1")


def test_an_unapproved_or_unknown_pipeline_is_refused_with_a_code(world) -> None:  # noqa: ANN001
    user_dir = world.tmp / "orin-home" / "pipelines"
    user_dir.mkdir()
    (user_dir / "so-cmd.yaml").write_text(WHOLE_COMMAND, encoding="utf-8")
    for pipeline_id, code in (("so-cmd", "PIPELINE_NOT_APPROVED"), ("nao-existe", "PIPELINE_NOT_FOUND")):
        with pytest.raises(AgentToolError) as caught:
            world.toolset().run_pipeline(pipeline_id, {})
        assert caught.value.error_code == code


def test_the_agent_cannot_choose_the_command_of_a_gate(world) -> None:  # noqa: ANN001
    user_dir = world.tmp / "orin-home" / "pipelines"
    user_dir.mkdir()
    path = user_dir / "so-cmd.yaml"
    path.write_text(WHOLE_COMMAND, encoding="utf-8")
    service = PipelineService.for_store(world.toolset()._chat_store)
    service.approve(USER, world.root, "so-cmd", service.get_pipeline(USER, world.root, "so-cmd")["definition_hash"])
    with pytest.raises(AgentToolError) as caught:
        world.toolset().run_pipeline("so-cmd", {"comando": "rm -rf x"})
    assert caught.value.error_code == "PIPELINE_INPUT_NOT_ALLOWED"
    result = world.toolset().run_pipeline("so-cmd", {"nota": "ok"})  # the other input is fine; the default command is used
    assert result["payload"]["inputs"] == {"comando": "pytest -q", "nota": "ok"}
    service_module._take_pending("turn-1")


def test_a_user_pipeline_edited_after_the_approval_is_refused(world) -> None:  # noqa: ANN001
    user_dir = world.tmp / "orin-home" / "pipelines"
    user_dir.mkdir()
    path = user_dir / "so-cmd.yaml"
    path.write_text(WHOLE_COMMAND, encoding="utf-8")
    service = PipelineService.for_store(world.toolset()._chat_store)
    service.approve(USER, world.root, "so-cmd", service.get_pipeline(USER, world.root, "so-cmd")["definition_hash"])
    path.write_text(WHOLE_COMMAND.replace("Cmd", "Cmd2"), encoding="utf-8")
    with pytest.raises(AgentToolError) as caught:
        world.toolset().run_pipeline("so-cmd", {})
    assert caught.value.error_code == "PIPELINE_NOT_APPROVED"


def test_inputs_must_be_an_object(world) -> None:  # noqa: ANN001
    with pytest.raises(AgentToolError):
        world.toolset().run_pipeline("pesquisa", ["x"])  # type: ignore[arg-type]


def test_the_agent_tools_file_stays_below_its_limit_and_json_serialisable_results(world) -> None:  # noqa: ANN001
    lines = Path(AgentToolset.__module__.replace(".", "/") + ".py")
    del lines
    result = world.toolset().run_pipeline()
    json.dumps(result)

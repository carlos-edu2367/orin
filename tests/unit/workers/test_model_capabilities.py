from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.schema import metadata, provider_model_catalog
from agentos.workers.chat import ChatWorker


def test_toolset_reports_whether_the_turn_model_sees(tmp_path):
    workspace = ConversationWorkspace(tmp_path, "chat_1")
    assert AgentToolset(workspace, model_sees_images=True).model_sees_images is True
    assert AgentToolset(workspace).model_sees_images is False


def test_view_file_returns_the_image_when_the_model_sees(tmp_path):
    workspace = ConversationWorkspace(tmp_path, "chat_1")
    toolset = AgentToolset(workspace, model_sees_images=True, enable_terminal=False)
    (workspace.root / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    result = toolset.view_file("foto.png")
    assert result["images"][0]["type"] == "image"
    assert result["images"][0]["media_type"] == "image/png"


TURN = {
    "turn_id": "turn-1",
    "conversation_id": "conversation-1",
    "user_id": "user-1",
    "execution_id": "execution-1",
    "assistant_message_id": "message-1",
}


class Store:
    _engine = object()

    def heartbeat(self, worker):
        return None

    def claim(self, turn_id):
        return dict(TURN) if turn_id == TURN["turn_id"] else None

    def finish(self, turn, *, failed=False, code=None):
        return None

    def cancel_requested(self, turn_id):
        return False

    def main_agent_id(self, turn):
        return "agent-main"


def _catalog_engine(*rows: dict[str, object]):
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    if rows:
        now = datetime.now(UTC)
        with engine.begin() as c:
            c.execute(insert(provider_model_catalog), [
                {
                    "capabilities": [], "input_modalities": ["text"], "output_modalities": ["text"],
                    "input_per_million": None, "output_per_million": None, "route_kind": "model",
                    "refreshed_at": now, "created_at": now, "updated_at": now,
                    **row,
                }
                for row in rows
            ])
    return engine


def test_model_sees_images_is_true_when_the_catalog_lists_the_image_modality():
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5",
        "display_name": "Opus", "input_modalities": ["text", "image"],
    })

    class VisionStore(Store):
        _engine = engine

    worker = ChatWorker(VisionStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_sees_images(turn) is True


def test_model_sees_images_is_false_when_the_catalog_has_no_image_modality():
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5",
        "display_name": "Opus", "input_modalities": ["text"],
    })

    class TextOnlyStore(Store):
        _engine = engine

    worker = ChatWorker(TextOnlyStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_sees_images(turn) is False


def test_model_sees_images_is_false_when_the_catalog_has_no_row():
    engine = _catalog_engine()

    class EmptyStore(Store):
        _engine = engine

    worker = ChatWorker(EmptyStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_sees_images(turn) is False


def test_model_calls_tools_stays_true_when_the_catalog_is_unrefreshed_or_empty():
    """An unrefreshed or empty catalog must never silently disable tool calling."""
    engine = _catalog_engine()

    class EmptyStore(Store):
        _engine = engine

    worker = ChatWorker(EmptyStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_calls_tools(turn) is True


def test_model_calls_tools_is_false_only_when_capabilities_explicitly_omit_tools():
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5",
        "display_name": "Opus", "capabilities": ["chat"],
    })

    class NoToolsStore(Store):
        _engine = engine

    worker = ChatWorker(NoToolsStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_calls_tools(turn) is False


def test_model_calls_tools_is_true_when_capabilities_list_tools():
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5",
        "display_name": "Opus", "capabilities": ["chat", "tools"],
    })

    class ToolsStore(Store):
        _engine = engine

    worker = ChatWorker(ToolsStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._model_calls_tools(turn) is True

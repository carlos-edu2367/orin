from __future__ import annotations

from agentos.conversations.service import ConversationService
from agentos.provider_catalog.models import ProviderCatalogContext


class Catalog:
    def list(self, context, provider, favorites_only=False):
        return [type("Model", (), {"model_id": "anthropic/model-a"})()] if provider == "openrouter" else []


class Prompts:
    def save(self, user_id, message):
        assert message == "Organize os dados"
        return "prompt:opaque"


class Agents:
    def configure(self, context, agent_id, config_version, provider, model_id, selection=None):
        assert (provider, model_id) == ("openrouter", "anthropic/model-a")
        return type("Revision", (), {"model_profile_ref": "model-profile:selection-a"})()


class Executions:
    def create(self, command):
        assert command["task_ref"] == "prompt:opaque"
        return {"execution_id": command["context"]["execution_id"], "state_version": 1}


def test_conversation_creates_server_side_prompt_and_never_returns_its_task_reference() -> None:
    service = ConversationService(Catalog(), Prompts(), Agents(), Executions())
    receipt = service.create(
        ProviderCatalogContext("user-a", "conversation.create"), message="Organize os dados",
        provider="openrouter", model_id="anthropic/model-a", workspace_id=None, idempotency_key="conversation-1",
    )
    assert receipt.agent_id
    assert receipt.execution_id
    assert not hasattr(receipt, "task_ref")

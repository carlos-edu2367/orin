"""The worker builds a visual-reading model independently of the turn's own model.

Selection comes from the user's catalog (Task 15's ``input_modalities`` field),
an optional override in ``vision_model_selections``, and ``choose_vision_model``
(Task 16). The transport for that model must come from *its own* provider's
credential -- never the turn's -- because the chosen vision model routinely
belongs to a different provider than the one running the conversation.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, insert

from agentos.persistence.postgres.schema import (
    metadata,
    provider_api_keys,
    provider_configurations,
    provider_model_catalog,
    vision_model_selections,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.reading.vision import VisionReader
from agentos.workers.chat import ChatWorker

ENCRYPTION_KEY = "unit-test-vision-reader-key"

TURN = {
    "turn_id": "turn-1",
    "conversation_id": "conversation-1",
    "user_id": "user-1",
    "execution_id": "execution-1",
    "assistant_message_id": "message-1",
    "provider": "anthropic",
    "model_id": "claude-opus-5",
}


class Store:
    def __init__(self, engine) -> None:
        self._engine = engine

    def heartbeat(self, worker):
        return None

    def claim(self, turn_id):
        return None

    def finish(self, turn, *, failed=False, code=None):
        return None

    def cancel_requested(self, turn_id):
        return False

    def main_agent_id(self, turn):
        return "agent-main"


def _engine():
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return engine


def _catalog_rows(engine, *rows: dict[str, object]) -> None:
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


def _credential(engine, *, user_id: str, provider: str, plaintext_api_key: str, base_url: str | None = None) -> None:
    cipher = ProviderSecretCipher(ENCRYPTION_KEY)
    now = datetime.now(UTC)
    with engine.begin() as c:
        c.execute(insert(provider_configurations), [{
            "user_id": user_id, "provider": provider, "enabled": True,
            "base_url": base_url, "secret_ref": f"{provider}:{user_id}", "key_cooldown_seconds": 60,
            "catalog_refreshed_at": None, "created_at": now, "updated_at": now,
        }])
        if plaintext_api_key:
            c.execute(insert(provider_api_keys), [{
                "user_id": user_id, "provider": provider, "label": None,
                "api_key_ciphertext": cipher.encrypt(plaintext_api_key), "secret_ref": f"{provider}:{user_id}:key",
                "position": 0, "status": "active", "cooldown_until": None, "created_at": now, "updated_at": now,
            }])


def _override(engine, *, user_id: str, provider: str, model_id: str) -> None:
    with engine.begin() as c:
        c.execute(insert(vision_model_selections), [{
            "user_id": user_id, "provider": provider, "model_id": model_id, "updated_at": datetime.now(UTC),
        }])


def test_a_user_with_no_vision_capable_model_gets_no_reader(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", ENCRYPTION_KEY)
    engine = _engine()
    _catalog_rows(engine, {
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5",
        "display_name": "Opus", "input_modalities": ["text"],
    })
    worker = ChatWorker(Store(engine))

    reader = worker._vision_reader_factory(dict(TURN))()

    assert reader is None


def test_the_turn_provider_is_preferred_when_it_can_see(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", ENCRYPTION_KEY)
    engine = _engine()
    _catalog_rows(
        engine,
        {"user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5-vision",
         "display_name": "Opus Vision", "input_modalities": ["text", "image"]},
        {"user_id": "user-1", "provider": "ollama", "model_id": "qwen2.5-vl",
         "display_name": "qwen2.5-vl", "input_modalities": ["text", "image"]},
    )
    _credential(engine, user_id="user-1", provider="anthropic", plaintext_api_key="anthropic-secret")
    worker = ChatWorker(Store(engine))

    reader = worker._vision_reader_factory(dict(TURN))()

    assert isinstance(reader, VisionReader)
    assert reader.model.provider == "anthropic"
    assert reader.model.model_id == "claude-opus-5-vision"


def test_the_explicit_override_wins_and_its_own_credential_is_used(monkeypatch) -> None:
    """The turn runs on anthropic; the person picked a local ollama model to read
    images. The transport built for that choice must be built from ollama's own
    credential, never anthropic's -- there is no anthropic credential at all
    here, so reusing it would fail loudly instead of silently.
    """
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", ENCRYPTION_KEY)
    engine = _engine()
    _catalog_rows(
        engine,
        {"user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5-vision",
         "display_name": "Opus Vision", "input_modalities": ["text", "image"]},
        {"user_id": "user-1", "provider": "ollama", "model_id": "qwen2.5-vl",
         "display_name": "qwen2.5-vl", "input_modalities": ["text", "image"]},
    )
    _override(engine, user_id="user-1", provider="ollama", model_id="qwen2.5-vl")
    _credential(engine, user_id="user-1", provider="ollama", plaintext_api_key="", base_url="http://localhost:11434")
    worker = ChatWorker(Store(engine))

    reader = worker._vision_reader_factory(dict(TURN))()

    assert isinstance(reader, VisionReader)
    assert reader.model == worker._vision_override("user-1")
    transport = reader._transport_factory(reader.model)
    assert transport is not None
    assert transport.provider == "ollama"
    assert transport.model == "qwen2.5-vl"


def test_a_vision_model_transport_fails_closed_without_its_own_credential(monkeypatch) -> None:
    """anthropic runs the turn and has a credential; the only vision-capable
    model is on openrouter, which has none configured. Building that model's
    transport must fail rather than silently borrowing anthropic's key.
    """
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", ENCRYPTION_KEY)
    engine = _engine()
    _catalog_rows(engine, {
        "user_id": "user-1", "provider": "openrouter", "model_id": "some/vision-model",
        "display_name": "Vision Model", "input_modalities": ["text", "image"],
    })
    _credential(engine, user_id="user-1", provider="anthropic", plaintext_api_key="anthropic-secret")
    worker = ChatWorker(Store(engine))

    reader = worker._vision_reader_factory(dict(TURN))()

    assert isinstance(reader, VisionReader)
    assert reader._transport_factory(reader.model) is None

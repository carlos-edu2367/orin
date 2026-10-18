from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.permissions.trust import WorkspaceTrustStore

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_session", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant",
}


class Store:
    def __init__(self) -> None:
        self.activity = []

    def load(self, turn_id): return TURN
    def history_for_turn(self, turn): return [{"role": "user", "content": "x"}]
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass
    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None):
        self.activity.append((event_type, summary, dict(payload or {})))
    def main_agent_id(self, turn): return "agent:main"


class Agents:
    def list(self): return []


def _session(tmp_path: Path) -> tuple[TurnSession, Store]:
    store = Store()
    session = TurnSession(
        turn=dict(TURN), store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )
    return session, store


def test_untrusted_workspace_with_hooks_emits_trust_required(tmp_path: Path) -> None:
    session, store = _session(tmp_path)
    (session.workspace.root / ".claude").mkdir()
    (session.workspace.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
    session.build_runtime()
    events = [item for item in store.activity if item[0] is AgentActivityEventType.WORKSPACE_TRUST_REQUIRED]
    assert len(events) == 1
    payload = events[0][2]["workspace_trust"]
    assert payload["items"] == ["hooks"] and payload["path"] == str(session.workspace.root)


def test_trusted_workspace_does_not_emit(tmp_path: Path) -> None:
    session, store = _session(tmp_path)
    (session.workspace.root / ".claude").mkdir()
    (session.workspace.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
    WorkspaceTrustStore().trust("local-user", session.workspace.root)
    session.build_runtime()
    assert session.effective_settings.trusted is True
    assert not [item for item in store.activity if item[0] is AgentActivityEventType.WORKSPACE_TRUST_REQUIRED]


def test_nothing_to_trust_emits_nothing(tmp_path: Path) -> None:
    session, store = _session(tmp_path)
    session.build_runtime()
    assert not [item for item in store.activity if item[0] is AgentActivityEventType.WORKSPACE_TRUST_REQUIRED]

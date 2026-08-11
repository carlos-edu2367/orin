from __future__ import annotations

import pytest

from agentos.agentic.runtime import AgenticRunResult
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker


TURN = {
    "turn_id": "turn-1",
    "conversation_id": "conversation-1",
    "user_id": "user-1",
    "execution_id": "execution-1",
    "assistant_message_id": "message-1",
}


@pytest.fixture(autouse=True)
def stub_execution_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())


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


class ClosableRuntime:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.closed = 0

    def run(self, turn_id):
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("state", ("completed", "failed", "cancelled"))
def test_chat_worker_closes_runtime_for_every_terminal_result(state: str) -> None:
    runtime = ClosableRuntime(AgenticRunResult(state, error_code="FAILED" if state == "failed" else None))
    worker = ChatWorker(Store(), runtime_factory=lambda turn: runtime)
    worker._project = lambda *args, **kwargs: None

    worker.run("turn-1")

    assert runtime.closed == 1


def test_chat_worker_closes_runtime_when_execution_raises() -> None:
    runtime = ClosableRuntime(error=RuntimeError("provider failed"))
    worker = ChatWorker(Store(), runtime_factory=lambda turn: runtime)
    worker._project = lambda *args, **kwargs: None

    worker.run("turn-1")

    assert runtime.closed == 1


def test_runtime_construction_failure_closes_the_optional_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    class Browser:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    class Settings:
        def get(self, user_id):
            return {"max_iterations": 8}

    class SkillLibrary:
        def registry_for(self, user_id, *, agent_id):
            return None

    browser = Browser()
    monkeypatch.setattr(chat_module, "conversation_browser_for", lambda turn: browser)
    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)
    monkeypatch.setattr(chat_module, "TurnSession", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("construction failed")))
    worker = ChatWorker(Store(), runtime_settings=Settings())

    with pytest.raises(RuntimeError, match="construction failed"):
        worker._runtime_for(dict(TURN))

    assert browser.closed == 1

from __future__ import annotations

from pathlib import Path
from tempfile import mkdtemp

from agentos.agentic.session import TurnSession, build_system_prompt


# ``build_system_prompt`` returns (stable, volatile). These assertions are
# about what the model is told, which is both layers, so they join them.
def _joined_prompt(**values):
    stable, volatile = build_system_prompt(**values)
    return stable + chr(10) + volatile


def test_build_system_prompt_includes_hook_context_as_subordinate_info():
    prompt = _joined_prompt(
        tool_names=(), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False,
        hook_context="VAULT CONTEXT",
    )

    assert "VAULT CONTEXT" in prompt
    assert "subordinado" in prompt


def test_build_system_prompt_omits_the_section_when_there_is_no_hook_context():
    prompt = _joined_prompt(
        tool_names=(), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False,
    )

    assert "hooks de plugin" not in prompt


class AgentsStore:
    def create(self, *args, **kwargs):
        return {"agent_id": "agent-sub", "created": True}

    def find(self, name):
        return None

    def list(self):
        return []

    def set_state(self, *args, **kwargs):
        return None

    def record_usage(self, *args, **kwargs):
        return None


class RecordingEngine:
    def __init__(self, body="VAULT CONTEXT"):
        self.calls = 0
        self.body = body

    def dispatch(self, *, user_id, event, payload):
        self.calls += 1
        from agentos.plugins.hook_executor import HookOutcome
        return (HookOutcome("demo:SessionStart:0", "ok", self.body, "", 0),)


class Store:
    def __init__(self):
        self._context: dict[str, str] = {}

    def main_agent_id(self, turn):
        return "agent-main"

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "faça algo"}]

    def record(self, *args, **kwargs):
        return None

    def hook_context(self, conversation_id):
        return self._context.get(conversation_id)

    def record_hook_context(self, conversation_id, body, **kwargs):
        self._context[conversation_id] = body


def _turn(conversation_id="conversation-1"):
    return {"turn_id": "turn-1", "conversation_id": conversation_id, "user_id": "user-1", "provider": "openrouter", "model_id": "m", "execution_id": "execution-1"}


def _session(turn, store, *, hook_engine=None):
    return TurnSession(
        turn=turn, store=store, agents_store=AgentsStore(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=Path(mkdtemp()),
        hook_engine=hook_engine,
    )


def test_session_start_output_is_injected_into_the_prompt():
    store = Store()
    engine = RecordingEngine()
    session = _session(_turn(), store, hook_engine=engine)

    runtime = session.build_runtime()

    # Hook context is conversation state, so it rides in the volatile layer.
    assert "VAULT CONTEXT" in runtime.volatile_prompt
    assert engine.calls == 1


def test_session_start_runs_once_per_conversation():
    store = Store()
    engine = RecordingEngine()
    turn = _turn()

    _session(turn, store, hook_engine=engine).build_runtime()
    second_runtime = _session(turn, store, hook_engine=engine).build_runtime()

    assert engine.calls == 1
    assert "VAULT CONTEXT" in second_runtime.volatile_prompt


def test_a_failing_session_start_hook_does_not_break_the_prompt():
    class Exploding:
        def dispatch(self, **_kwargs):
            raise RuntimeError("boom")

    session = _session(_turn(), Store(), hook_engine=Exploding())

    runtime = session.build_runtime()

    assert isinstance(runtime.system_prompt, str) and runtime.system_prompt


def test_a_failing_hook_context_read_does_not_break_the_prompt():
    class FailingContextRead(Store):
        def hook_context(self, conversation_id):
            raise RuntimeError("db unavailable")

    session = _session(_turn(), FailingContextRead(), hook_engine=RecordingEngine())

    runtime = session.build_runtime()

    assert isinstance(runtime.system_prompt, str) and runtime.system_prompt


def test_a_failing_hook_context_write_does_not_break_the_prompt():
    class FailingContextWrite(Store):
        def record_hook_context(self, conversation_id, body, **kwargs):
            raise RuntimeError("uq_conversation_hook_context violated")

    session = _session(_turn(), FailingContextWrite(), hook_engine=RecordingEngine())

    runtime = session.build_runtime()

    assert isinstance(runtime.system_prompt, str) and runtime.system_prompt


def test_no_hook_engine_means_no_context_and_no_error():
    session = _session(_turn(), Store(), hook_engine=None)

    runtime = session.build_runtime()

    assert "hooks de plugin" not in runtime.system_prompt


def test_build_runtime_threads_the_hook_engine_through_for_post_tool_use_and_post_compact():
    engine = RecordingEngine(body="")
    session = _session(_turn(), Store(), hook_engine=engine)

    runtime = session.build_runtime()

    assert runtime.hook_engine is engine

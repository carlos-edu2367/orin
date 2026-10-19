"""W5 (W3-R26): a subagent has its own reminders and instruction-file tracker; its monitor still reaches the turn."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings import load_effective_settings
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN, _RecordingStore, _parallel_tool_calls_batch, _text_batch,
)
from tests.unit.agentic.tools.test_monitor_tool import conversation  # noqa: F401 - the Monitor fixture


class _ReadingChild:
    """First request: read a file in a subdirectory. Then answer. Every request is kept."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def stream(self, request):
        self.requests.append(repr(request))
        if len(self.requests) == 1:
            return iter(_parallel_tool_calls_batch([("call-1", "read_file", {"path": "pkg/mod.py"})]))
        return iter(_text_batch("pkg/mod.py defines VALUE."))


def test_a_subagents_reads_load_instructions_for_the_subagent_not_the_parent(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'mem.db').as_posix()}")
    metadata.create_all(engine)
    child = _ReadingChild()
    session = TurnSession(
        turn=dict(TURN), store=_RecordingStore(),
        agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
        memory_store=None, provider_factory=lambda: child, workspace_root=tmp_path, child_provider_factory=lambda _model: child,
    )
    root = session.workspace.root  # the conversation's own folder below tmp_path
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_bytes(b"VALUE = 1\n")
    (root / "pkg" / "CLAUDE.md").write_bytes(b"PKG-RULES: keep VALUE positive.\n")
    session.effective_settings = load_effective_settings(root, trusted=False)
    session._memory = session._memory_files(False)
    try:
        outcome = session._run_subagent(subagent_type="explore", task="Read pkg/mod.py and report VALUE")
    finally:
        engine.dispose()
    assert outcome.status == "succeeded", outcome.content
    assert len(child.requests) >= 2
    assert "PKG-RULES" in child.requests[1]  # the subagent saw its directory's instructions
    assert not [item for item in session._reminders.drain() if "PKG-RULES" in item.text]  # the parent did not


def test_a_subagents_monitor_sinks_into_the_turn_even_when_the_turn_queue_is_empty(conversation, monkeypatch) -> None:
    # An empty ReminderQueue is falsy (it has __len__), so the monitor sink must test for None, not truthiness (W4-R9).
    from agentos.agentic.reminders import ReminderQueue
    from agentos.agentic.tools.scheduling import monitor as monitor_module
    from tests.unit.agentic.tools.test_monitor_tool import _python

    make, _scheduler, _conversation_id = conversation
    started: list = []
    monkeypatch.setattr(monitor_module.MONITORS, "add", lambda task: started.append(task))
    turn_queue = ReminderQueue()  # empty, as it usually is when a subagent starts
    own_queue = ReminderQueue()
    toolset = make(reminders=own_queue)
    toolset._monitor_sink = turn_queue
    outcome = toolset.invoke("Monitor", {"command": _python("pass"), "description": "quick"})
    assert outcome.status == "succeeded", outcome.content
    assert started and started[0].sink is turn_queue

"""WS-B4 tests: Criterion 6 (interrupted state on worker restart, resume from saved history, autoResume & quota)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import threading
import time
from typing import Any

from agentos.agentic.agent_run_persistence import AgentRunRecorder
from agentos.agentic.background_agents import BACKGROUND_AGENTS, fail_orphaned_running_agents
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.quota.models import Confidence, QuotaStatus
from agentos.quota.policy import QuotaPolicy
from agentos.workers.publisher import auto_resume_interrupted_agents, recover_once
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


class _StubQuotaService:
    def __init__(self, *, max_percent: float | None = 10.0, rejected: bool = False) -> None:
        self._status = QuotaStatus(
            max_percent=max_percent,
            rejected=rejected,
            confidence=Confidence.MEASURED,
            observed_at=datetime.now(UTC),
        )

    def status_for(self, user_id: str, provider: str, model_id: str | None = None) -> QuotaStatus:
        return self._status


def test_criterion_6_worker_restart_marks_interrupted_and_resume_restores_saved_history(tmp_path: Path) -> None:
    """Critério 6: background subagent running -> worker restarts -> state becomes 'interrupted' (not 'failed')
    -> resume restores prior history + interruption notice into the fake provider."""
    first_call_done = threading.Event()
    second_call_gate = threading.Event()

    class _CrashingWorkerChild:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request):
            self.calls += 1
            if self.calls == 1:
                return iter(_parallel_tool_calls_batch([("call-1", "list_files", {"path": "."})]))
            first_call_done.set()
            # Block until test simulates worker crash/restart
            second_call_gate.wait(timeout=5.0)
            return iter(_text_batch("should not finish before crash"))

    class _ResumedFakeProvider:
        def __init__(self) -> None:
            self.received_messages: list[list[dict[str, Any]]] = []

        def stream(self, request):
            raw = request.get("messages", []) if isinstance(request, dict) else getattr(request, "messages", [])
            msgs = [dict(m) for m in raw]
            self.received_messages.append(msgs)
            return iter(_text_batch("Resumed cleanly from saved history."))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'crit6.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    BACKGROUND_AGENTS.clear_for_tests()
    try:
        (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
        child1 = _CrashingWorkerChild()
        session1 = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: child1,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: child1,
        )
        session1._agent_runs_dir = runs_dir
        started = session1._run_subagent(
            subagent_type="general-purpose",
            task="Inspect workspace and summarize files",
            run_in_background=True,
        )
        assert started.status == "succeeded"
        agent_id = str(started.payload["agent_id"])
        output_file = str(started.payload["output_file"])
        assert first_call_done.wait(timeout=10.0), "First model/tool step did not complete"

        # Wait briefly for incremental history file to be written after call #1.
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        history_file = recorder.history_path_for(agent_id)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not history_file.is_file():
            time.sleep(0.05)
        assert history_file.is_file(), "History file must be persisted after model call"

        # Simulate abrupt worker crash + restart: wipe in-memory registry & cancel old thread silently.
        # Ensure DB rows still look 'running'/'working' as they would after SIGKILL.
        BACKGROUND_AGENTS.clear_for_tests()
        session1.agents_store.set_state(agent_id, "working")
        AgentRunStore(engine).touch(agent_id, state="running")

        swept = fail_orphaned_running_agents(
            engine,
            workspace_root_resolver=lambda _cid: session1.workspace.root,
        )
        assert agent_id in swept

        # Both tables must mark 'interrupted' (NOT 'failed'!)
        agent_row = session1.agents_store.get(agent_id)
        assert agent_row is not None
        assert agent_row["state"] == "interrupted"

        run_row = AgentRunStore(engine).get(agent_id)
        assert run_row is not None
        assert run_row.state == "interrupted"

        # Output file must note interrupted & resumable, not definitive failure.
        report_path = session1.workspace.root / output_file
        assert report_path.is_file()
        report_text = report_path.read_text(encoding="utf-8").lower()
        assert "interromp" in report_text or "interrupted" in report_text
        assert "retom" in report_text or "resum" in report_text

        # Now resume in a fresh TurnSession after restart with a fake provider.
        resumed_provider = _ResumedFakeProvider()
        session2 = TurnSession(
            turn={**TURN, "turn_id": "turn-after-restart", "execution_id": "exec-after-restart"},
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: resumed_provider,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: resumed_provider,
        )
        session2._agent_runs_dir = runs_dir
        resumed_outcome = session2.resume_interrupted_subagent(agent_id, run_in_background=False)
        assert resumed_outcome.status == "succeeded", resumed_outcome.content
        assert "Resumed cleanly from saved history." in resumed_outcome.content

        assert len(resumed_provider.received_messages) >= 1
        sent = resumed_provider.received_messages[0]
        # Must include prior saved messages AND an interruption warning about partial prior work.
        full_text = "\n".join(str(m.get("content") or "") for m in sent)
        assert "Inspect workspace and summarize files" in full_text
        assert "list_files" in full_text
        assert ("interrupted" in full_text.lower() or "interromp" in full_text.lower())
        assert ("partial" in full_text.lower() or "parcial" in full_text.lower())

        final_run = AgentRunStore(engine).get(agent_id)
        assert final_run is not None
        assert final_run.state == "completed"
    finally:
        second_call_gate.set()
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_resume_with_corrupted_history_falls_back_gracefully_with_warning(tmp_path: Path) -> None:
    class _CapturingProvider:
        def __init__(self) -> None:
            self.received: list[list[dict[str, Any]]] = []

        def stream(self, request):
            raw = request.get("messages", []) if isinstance(request, dict) else getattr(request, "messages", [])
            self.received.append([dict(m) for m in raw])
            return iter(_text_batch("Recovered despite corrupt history."))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'corrupt_resume.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    BACKGROUND_AGENTS.clear_for_tests()
    try:
        agents = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
        rec = agents.create("worker", "general", parent_agent_id=f"agent:{TURN['conversation_id']}:main")
        agent_id = str(rec["agent_id"])
        agents.set_state(agent_id, "interrupted")

        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        recorder.start(
            agent_id=agent_id,
            user_id=TURN["user_id"],
            conversation_id=TURN["conversation_id"],
            turn_id=TURN["turn_id"],
            parent_agent_id=f"agent:{TURN['conversation_id']}:main",
            subagent_type="general",
            provider=TURN["provider"],
            model_id=TURN["model_id"],
            task="Original task",
            state="interrupted",
        )
        # Corrupt the history file on disk
        recorder.history_path_for(agent_id).write_text("{truncated json", encoding="utf-8")

        provider = _CapturingProvider()
        session = TurnSession(
            turn={**TURN, "turn_id": "turn-corrupt-resume"},
            store=_RecordingStore(),
            agents_store=agents,
            memory_store=None,
            provider_factory=lambda: provider,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: provider,
        )
        session._agent_runs_dir = runs_dir
        outcome = session.resume_interrupted_subagent(agent_id, run_in_background=False)
        assert outcome.status == "succeeded", outcome.content
        assert len(provider.received) == 1
        sent_text = "\n".join(str(m.get("content") or "") for m in provider.received[0]).lower()
        assert "interrupted" in sent_text or "interromp" in sent_text
        assert "corrupt" in sent_text or "hist" in sent_text
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_auto_resume_off_by_default_and_on_resumes_at_most_once_and_respects_quota(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'autoresume.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    BACKGROUND_AGENTS.clear_for_tests()
    try:
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        agents = ConversationAgentStore(engine, conversation_id="conv-auto", user_id="user-1")
        rec = agents.create("bg-worker", "general", parent_agent_id="agent:conv-auto:main")
        agent_id = str(rec["agent_id"])
        agents.set_state(agent_id, "working")
        recorder.start(
            agent_id=agent_id,
            user_id="user-1",
            conversation_id="conv-auto",
            turn_id="turn-1",
            parent_agent_id="agent:conv-auto:main",
            subagent_type="general",
            provider="anthropic-cli",
            model_id="claude-sonnet-4-6",
            task="Background job",
            state="running",
        )

        # Simulate orphan sweep -> state becomes interrupted
        swept = fail_orphaned_running_agents(engine)
        assert agent_id in swept

        resumed_ids: list[str] = []

        # 1) Default autoResume = False -> does NOT resume
        resumed_default = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {},
            quota_policy=QuotaPolicy(_StubQuotaService(max_percent=10.0)),
            resume_handler=lambda run: resumed_ids.append(run.agent_id),
        )
        assert resumed_default == []
        assert resumed_ids == []

        # 2) autoResume = True, but QuotaPolicy blocks background (95% >= 90%) -> does NOT resume
        resumed_blocked = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {
                "orchestration": {"background": {"autoResume": True}},
                "quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}},
            },
            quota_policy=QuotaPolicy(_StubQuotaService(max_percent=95.0)),
            resume_handler=lambda run: resumed_ids.append(run.agent_id),
        )
        assert resumed_blocked == []
        assert resumed_ids == []
        assert not recorder.has_auto_resumed(agent_id)

        # 3) autoResume = True and quota allows -> resumes ONCE
        resumed_ok = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {
                "orchestration": {"background": {"autoResume": True}},
                "quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}},
            },
            quota_policy=QuotaPolicy(_StubQuotaService(max_percent=40.0)),
            resume_handler=lambda run: resumed_ids.append(run.agent_id),
        )
        assert resumed_ok == [agent_id]
        assert resumed_ids == [agent_id]
        assert recorder.has_auto_resumed(agent_id)

        # 4) Even if it crashes and becomes interrupted AGAIN, autoResume must NOT loop a second time!
        AgentRunStore(engine).touch(agent_id, state="interrupted")
        resumed_again = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {
                "orchestration": {"background": {"autoResume": True}},
                "quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}},
            },
            quota_policy=QuotaPolicy(_StubQuotaService(max_percent=40.0)),
            resume_handler=lambda run: resumed_ids.append(run.agent_id),
        )
        assert resumed_again == []
        assert resumed_ids == [agent_id]
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_recover_once_sweeps_orphans_to_interrupted(tmp_path: Path) -> None:
    from agentos.conversations.chat import PostgresChatStore
    from agentos.workers import publisher

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'recover.db').as_posix()}")
    metadata.create_all(engine)
    BACKGROUND_AGENTS.clear_for_tests()
    prev_swept = publisher._ORPHANS_SWEPT
    publisher._ORPHANS_SWEPT = False
    try:
        store = PostgresChatStore(engine)
        agents = ConversationAgentStore(engine, conversation_id="conv-r", user_id="user-1")
        rec = agents.create("worker-r", "general", parent_agent_id="agent:conv-r:main")
        agent_id = str(rec["agent_id"])
        agents.set_state(agent_id, "running")
        AgentRunStore(engine).upsert(
            agent_id=agent_id,
            user_id="user-1",
            conversation_id="conv-r",
            turn_id="turn-r",
            subagent_type="general",
            state="running",
        )

        recover_once(store, has_in_flight=False)

        assert agents.get(agent_id)["state"] == "interrupted"
        run = AgentRunStore(engine).get(agent_id)
        assert run is not None and run.state == "interrupted"
    finally:
        publisher._ORPHANS_SWEPT = prev_swept
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()

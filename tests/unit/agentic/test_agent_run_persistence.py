"""WS-B4 tests: AgentRunRecorder atomic persistence, redaction, corruption handling, retention, and K20."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agentos.agentic.agent_run_persistence import (
    AgentRunRecorder,
    PathContainmentError,
    resolve_contained,
    safe_agent_run_filename,
)
from agentos.agentic.session import TurnSession
from agentos.agentic.tools.agents.agent import SPECS as AGENT_TOOL_SPECS, AgentInput
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _text_batch,
)


class _FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now_dt = start or datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_dt

    def monotonic(self) -> float:
        return self.now_dt.timestamp()

    def advance(self, seconds: float) -> None:
        self.now_dt = self.now_dt + timedelta(seconds=seconds)


def test_safe_filename_and_resolve_contained_handle_windows_colons_and_reject_traversal(tmp_path: Path) -> None:
    runs_dir = tmp_path / "agent-runs"
    runs_dir.mkdir()

    agent_id = "agent:conv-123:deadbeef99"
    fname = safe_agent_run_filename(agent_id)
    assert ":" not in fname
    assert "/" not in fname
    assert "\\" not in fname
    assert fname.endswith(".json")

    resolved = resolve_contained(runs_dir, fname)
    assert resolved.parent == runs_dir.resolve()

    # Different agent IDs that collapse to the same slug must not collide thanks to short hash.
    assert safe_agent_run_filename("agent:conv:a") != safe_agent_run_filename("agent_conv_a")

    for bad in ("../escape.json", "..\\escape.json", "/etc/passwd", "C:/Windows/win.ini", "sub/file.json", "a\x00b.json"):
        with pytest.raises(PathContainmentError):
            resolve_contained(runs_dir, bad)


def test_atomic_history_write_redacts_secrets_and_leaves_no_temp_files(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    try:
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        agent_id = "agent:conv-1:secret-test"
        recorder.start(
            agent_id=agent_id,
            user_id="user-1",
            conversation_id="conv-1",
            turn_id="turn-1",
            parent_agent_id="agent:conv-1:main",
            subagent_type="general",
            provider="openai",
            model_id="gpt-4o",
            task="Check env config",
        )

        replace_calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def _spy_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            replace_calls.append((str(src), str(dst)))
            assert Path(src).parent == runs_dir.resolve()
            assert Path(dst).parent == runs_dir.resolve()
            real_replace(src, dst)

        secret_token = "sk-proj-1234567890abcdefghijklmnop"
        gh_token = "ghp_1234567890abcdefghijklmnopqrstuv"
        with patch("agentos.agentic.agent_run_persistence.os.replace", side_effect=_spy_replace):
            saved_path = recorder.save_history(
                agent_id,
                [
                    {"role": "user", "content": f"Use API_KEY={secret_token} and token {gh_token}"},
                    {"role": "assistant", "content": "Authorization: Bearer supersecrettokenvalue123"},
                ],
            )

        assert len(replace_calls) == 1
        assert saved_path.is_file()
        assert list(runs_dir.glob("*.tmp")) == []

        raw = saved_path.read_text(encoding="utf-8")
        assert secret_token not in raw
        assert gh_token not in raw
        assert "supersecrettokenvalue123" not in raw
        assert "[REDACTED]" in raw

        restored = recorder.restore_history(agent_id)
        assert not restored.corrupted
        assert not restored.missing
        assert len(restored.messages) == 2
        assert restored.messages[0]["role"] == "user"
        assert "[REDACTED]" in str(restored.messages[0]["content"])
    finally:
        engine.dispose()


def test_history_size_limit_and_retention_pruning(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'limit.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    try:
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir, max_history_bytes=1200, max_files=3)
        long_msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}-" + ("x" * 180)} for i in range(20)]
        path = recorder.save_history("agent:c:big", long_msgs)
        assert path.stat().st_size <= 1600
        restored = recorder.restore_history("agent:c:big")
        assert not restored.corrupted
        assert 1 <= len(restored.messages) < len(long_msgs)
        # Newest message is preserved when trimming oldest messages.
        assert "msg-19-" in str(restored.messages[-1]["content"])

        # Retention keeps at most max_files json files.
        for idx in range(5):
            recorder.save_history(f"agent:c:ret-{idx}", [{"role": "user", "content": f"r-{idx}"}])
        json_files = list(runs_dir.glob("*.json"))
        assert len(json_files) <= 3
    finally:
        engine.dispose()


def test_corrupted_or_truncated_history_file_falls_back_to_empty_with_warning(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'corrupt.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    try:
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        agent_id = "agent:conv-1:truncated"
        target = recorder.history_path_for(agent_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"version": 1, "messages": [{"role": "user", "content": "trunc', encoding="utf-8")

        restored = recorder.restore_history(agent_id)
        assert restored.corrupted is True
        assert restored.messages == []
        assert restored.warning is not None and len(restored.warning) > 0
        notice = restored.resume_notice()
        assert "interrupted" in notice.lower() or "interromp" in notice.lower()
        assert "corrupt" in notice.lower() or "hist" in notice.lower()
    finally:
        engine.dispose()


def test_touch_batches_db_writes_at_most_every_5s_and_flushes_on_finish(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'batch.db').as_posix()}")
    metadata.create_all(engine)
    clock = _FakeClock()
    try:
        store = AgentRunStore(engine, clock=clock.now)
        recorder = AgentRunRecorder(
            engine,
            runs_dir=tmp_path / "agent-runs",
            store=store,
            clock=clock.now,
            monotonic=clock.monotonic,
            min_touch_interval_seconds=5.0,
        )
        agent_id = "agent:conv-1:batched"
        recorder.start(
            agent_id=agent_id,
            user_id="user-1",
            conversation_id="conv-1",
            turn_id="turn-1",
            subagent_type="general",
            task="Long work",
        )

        touch_calls = 0
        real_touch = store.touch

        def _counting_touch(*args, **kwargs):
            nonlocal touch_calls
            touch_calls += 1
            return real_touch(*args, **kwargs)

        with patch.object(store, "touch", side_effect=_counting_touch):
            # Within 5s of start -> throttled
            clock.advance(1.0)
            recorder.touch(agent_id, tool_calls=1, input_tokens=100, output_tokens=20)
            clock.advance(2.0)
            recorder.touch(agent_id, tool_calls=2, input_tokens=200, output_tokens=40)
            assert touch_calls == 0

            # At +5.0s -> flushed once
            clock.advance(2.5)
            recorder.touch(agent_id, tool_calls=3, input_tokens=300, output_tokens=60)
            assert touch_calls == 1

            # Immediately again -> throttled
            clock.advance(1.0)
            recorder.touch(agent_id, tool_calls=4, input_tokens=400, output_tokens=80)
            assert touch_calls == 1

            # Finish ALWAYS flushes immediately even inside the 5s window
            clock.advance(0.5)
            recorder.finish(
                agent_id,
                state="completed",
                tool_calls=5,
                input_tokens=500,
                output_tokens=100,
                cost_usd=0.042,
                result_ref=".orin/agent-outputs/batched.md",
            )
            assert touch_calls == 2

        final = store.get(agent_id)
        assert final is not None
        assert final.state == "completed"
        assert final.tool_calls == 5
        assert final.input_tokens == 500
        assert final.output_tokens == 100
        assert final.finished_at is not None
    finally:
        engine.dispose()


def test_k20_pipeline_run_id_internal_parameter_persisted_and_not_in_public_schema(tmp_path: Path) -> None:
    # Public tool schema must NOT expose pipeline_run_id
    assert "pipeline_run_id" not in AgentInput.model_fields
    run_spec = next(spec for spec in AGENT_TOOL_SPECS if spec.key == "run_subagent")
    assert "pipeline_run_id" not in run_spec.legacy_schema["properties"]

    class _QuickChild:
        def stream(self, _request):
            return iter(_text_batch("Pipeline step done."))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'k20.db').as_posix()}")
    metadata.create_all(engine)
    try:
        session = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _QuickChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _QuickChild(),
        )
        session._agent_runs_dir = tmp_path / "agent-runs"
        outcome = session._run_subagent(
            subagent_type="general-purpose",
            task="Run step for pipeline",
            run_in_background=True,
            pipeline_run_id="pipe-run-42",
        )
        assert outcome.status == "succeeded"
        agent_id = str(outcome.payload["agent_id"])
        checked = session._check_subagent(agent_id, wait_seconds=10)
        assert checked.status == "succeeded"

        run_row = AgentRunStore(engine).get(agent_id)
        assert run_row is not None
        assert run_row.pipeline_run_id == "pipe-run-42"
        assert run_row.state == "completed"
        assert run_row.history_path is not None
        assert Path(run_row.history_path).is_file()
    finally:
        session.close_background_subagents()
        engine.dispose()

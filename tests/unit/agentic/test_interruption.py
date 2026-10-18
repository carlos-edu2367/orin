from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
import time

from sqlalchemy import create_engine, select

from agentos.agentic import agent_tools
from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.workspace import ConversationWorkspace
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import INTERRUPTED_TOOL_RESULT, AgenticLimits, AgenticTurnRuntime
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_messages, metadata


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e",
            "provider": "openrouter", "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.steps: list[tuple[str, dict]] = []
        self.finished: list[str | None] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        self.finished.append(code)

    def record_step(self, turn, *, kind, payload, **fields):
        self.steps.append((kind, dict(payload)))


class TwoWrites:
    def stream(self, request):
        return iter([
            NormalizedStreamItem(StreamKind.TEXT, 1, text="Writing both files."),
            NormalizedStreamItem(StreamKind.TOOL_CALL, 2, tool_call_id="c1", tool_name="write_file", arguments_delta='{"path": "a"}'),
            NormalizedStreamItem(StreamKind.TOOL_CALL, 3, tool_call_id="c2", tool_name="write_file", arguments_delta='{"path": "b"}'),
            NormalizedStreamItem(StreamKind.FINISH, 4, finish_reason="tool_calls"),
        ])


class CancellingToolset:
    def __init__(self, flag: dict) -> None:
        self.flag = flag
        self.invoked: list[str] = []

    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return False

    def argument_names(self, name):
        return frozenset({"path"})

    def invoke(self, name, arguments):
        self.invoked.append(str(arguments.get("path")))
        self.flag["cancelled"] = True  # the person presses stop while the first write runs
        return ToolOutcome("succeeded", "ok", "written")


def test_a_cancel_mid_batch_skips_the_rest_and_records_a_consistent_trajectory() -> None:
    flag = {"cancelled": False}
    store = Store()
    toolset = CancellingToolset(flag)
    runtime = AgenticTurnRuntime(
        store=store, provider=TwoWrites(), toolset=toolset, cancelled=lambda turn: flag["cancelled"],
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
    )
    result = runtime.run("t")
    assert result.state == "cancelled"
    assert toolset.invoked == ["a"]
    results = [payload for kind, payload in store.steps if kind == "tool_result"]
    assert [item["status"] for item in results] == ["succeeded", "failed"]
    assert results[1]["content"] == INTERRUPTED_TOOL_RESULT
    assert store.finished == ["TURN_CANCELLED"]


def test_a_late_delta_never_reopens_a_cancelled_message() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="u", message="hi", provider="openrouter", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.delta(turn, "partial ")
    store.request_cancel(receipt.conversation_id, "u")
    store.delta(turn, "late text")
    with engine.connect() as connection:
        row = connection.execute(select(conversation_messages.c.content, conversation_messages.c.status).where(
            conversation_messages.c.message_id == turn["assistant_message_id"],
        )).one()
    assert (row.content, row.status) == ("partial ", "cancelled")


def test_a_cancel_stops_the_running_command(tmp_path: Path) -> None:
    """Spec 6.1: a tool in execution gets "interrupted by user"; the command is stopped, not waited for."""
    pid_file = tmp_path / "pid.txt"
    script = f"import os, pathlib, time; pathlib.Path(r'{pid_file}').write_text(str(os.getpid())); time.sleep(30)"
    command = f'"{sys.executable}" -c "{script}"'
    checks: list[float] = []

    def cancel_check() -> bool:
        checks.append(time.monotonic())
        return pid_file.exists()  # the person stops the turn once the command is running

    toolset = AgentToolset(ConversationWorkspace(tmp_path, "chat_cancel"), cancel_check=cancel_check)
    started = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": command})
    elapsed = time.monotonic() - started

    assert outcome.error_code == "INTERRUPTED_BY_USER"
    assert elapsed < 3, f"the command ran {elapsed:.1f} s after the stop"
    assert not agent_tools._process_is_running(int(pid_file.read_text(encoding="utf-8")))
    # The check reads the database, so the command polls it at most every half second.
    assert all(later - earlier >= 0.45 for earlier, later in zip(checks, checks[1:]))

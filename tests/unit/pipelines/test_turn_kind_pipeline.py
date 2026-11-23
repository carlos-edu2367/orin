"""WS-C7: ``turn_kind="pipeline"`` from the conversation store to the runtime, and a whole ``/pipeline`` turn end to end."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns, metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.pipelines.driver import PipelineRequest
from agentos.pipelines.store import PipelineStore
from agentos.pipelines.validator import validate_structure
from tests.unit.agentic.test_nested_subagents import _Scripted
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _text_batch
from tests.unit.conversations.test_chat_application import _store
from tests.unit.conversations.test_w7_fork_rename_shell import _conversation


# ================================================================ conversation store
def _turn_row(store, turn_id: str):
    with store._engine.connect() as c:
        return c.execute(select(conversation_turns).where(conversation_turns.c.turn_id == turn_id)).mappings().one()


def test_a_pipeline_turn_is_accepted_and_carries_the_command_text() -> None:
    store = _store()
    application, conversation_id, _ids = _conversation(store)
    receipt = application.send_pipeline("user-1", conversation_id, "run-abc")
    assert _turn_row(store, receipt.turn_id)["turn_kind"] == "pipeline"
    with store._engine.connect() as c:
        texts = [row.content for row in c.execute(select(conversation_messages).where(conversation_messages.c.conversation_id == conversation_id))]
    assert "/pipeline run-abc" in texts


def test_resume_and_flags_ride_in_the_text_and_the_idempotency_key_dedupes_the_attempt() -> None:
    store = _store()
    application, conversation_id, _ = _conversation(store)
    first = application.send_pipeline("user-1", conversation_id, "run-abc", resume=True, rerun_gates=True, budget_override=True)
    with store._engine.connect() as c:
        text = c.execute(select(conversation_messages.c.content).where(conversation_messages.c.message_id == _turn_row(store, first.turn_id)["user_message_id"])).scalar()
    assert text == "/pipeline resume run-abc --rerun-gates --budget-override"
    again = application.send_pipeline("user-1", conversation_id, "run-abc", resume=True, rerun_gates=True, budget_override=True)
    assert again.turn_id == first.turn_id  # the same attempt is the same turn
    assert application.send_pipeline("user-1", conversation_id, "run-abc", resume=True, attempt=2).turn_id != first.turn_id


def test_other_turn_kinds_and_bad_run_ids_are_still_refused() -> None:
    store = _store()
    application, conversation_id, _ = _conversation(store)
    with pytest.raises(ValueError):
        application.send("user-1", conversation_id, "x", "k-bad", turn_kind="whatever")
    for bad in ("../x", "a b", "", "x" * 80):
        with pytest.raises(ValueError):
            application.send_pipeline("user-1", conversation_id, bad)


def test_a_plugin_command_never_hijacks_a_pipeline_turn() -> None:
    store = _store()
    application, conversation_id, _ = _conversation(store)
    receipt = application.send("user-1", conversation_id, "/pipeline run-abc", "k-plain", turn_kind="pipeline")
    assert _turn_row(store, receipt.turn_id)["turn_kind"] == "pipeline"


# =========================================================================== session
class _Store:
    """The minimum of a chat store a runtime run needs; records what the turn wrote."""

    def __init__(self, task: str) -> None:
        self.turn = dict(TURN, turn_kind="pipeline", permission_mode="full_autonomy")
        self.task = task
        self.deltas: list[str] = []
        self.finished: list[tuple[bool, str | None]] = []
        self.marked = 0
        self.activity: list[tuple[Any, str, dict[str, Any], str | None]] = []
        import threading

        self._lock = threading.Lock()

    def load(self, turn_id):  # noqa: ANN001, ANN201
        return self.turn

    def history_for_turn(self, turn):  # noqa: ANN001, ANN201
        return [{"role": "user", "content": self.task}]

    def lifecycle(self, turn, state, **payload):  # noqa: ANN001, ANN201
        pass

    def delta(self, turn, text):  # noqa: ANN001, ANN201
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):  # noqa: ANN001, ANN201
        self.finished.append((failed, code))

    def mark_command_turn(self, turn):  # noqa: ANN001, ANN201
        self.marked += 1

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None):  # noqa: ANN001, ANN201
        with self._lock:
            self.activity.append((event_type, summary, dict(payload or {}), agent_id))

    def main_agent_id(self, turn):  # noqa: ANN001, ANN201
        return f"agent:{turn['conversation_id']}:main"


@pytest.fixture()
def world(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'turn.db').as_posix()}")
    metadata.create_all(engine)
    agents = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    BACKGROUND_AGENTS.clear_for_tests()
    yield engine, agents
    BACKGROUND_AGENTS.clear_for_tests()
    engine.dispose()


def _session(tmp_path: Path, world, task: str, child: _Scripted | None = None, **turn: Any) -> tuple[TurnSession, _Store]:
    engine, agents = world
    store = _Store(task)
    store.turn.update(turn)
    session = TurnSession(
        turn=store.turn, store=store, agents_store=agents, memory_store=None, provider_factory=lambda: _Scripted([]),
        workspace_root=tmp_path, child_provider_factory=lambda _m: child or _Scripted([]),
    )
    session.pipeline_store = PipelineStore(engine)  # type: ignore[attr-defined]
    return session, store


def test_the_session_reads_the_request_only_from_a_pipeline_turn_with_a_matching_text(tmp_path: Path, world) -> None:
    session, _ = _session(tmp_path, world, "/pipeline resume run-9 --budget-override")
    assert session._pipeline_request("/pipeline resume run-9 --budget-override") == PipelineRequest("run-9", True, False, True)
    assert session._pipeline_request("/pipeline") is None and session._pipeline_request("faça algo") is None
    other, _ = _session(tmp_path, world, "/pipeline run-9", turn_kind=None)
    assert other._pipeline_request("/pipeline run-9") is None  # an ordinary turn that happens to start with the word
    shell, _ = _session(tmp_path, world, "/pipeline run-9", turn_kind="shell")
    assert shell._pipeline_request("/pipeline run-9") is None


def test_the_runtime_is_a_pipeline_runtime_that_never_uses_the_model_loop(tmp_path: Path, world) -> None:
    session, _ = _session(tmp_path, world, "/pipeline run-1")
    runtime = session.build_runtime()
    assert runtime.pipeline_request == PipelineRequest("run-1") and runtime.pipeline_host is session
    assert runtime._native_provider(session.turn) is None  # no CLI model loop for this turn
    assert runtime.shell_command is None and runtime.compact_request is None
    ordinary, _ = _session(tmp_path, world, "oi", turn_kind=None)
    built = ordinary.build_runtime()
    assert built.pipeline_request is None


@pytest.mark.parametrize("mode", ["ask_permissions", "accept_edits", "plan_mode", "full_autonomy"])
def test_the_pipeline_turn_keeps_the_permission_mode_of_the_conversation(tmp_path: Path, world, mode: str) -> None:
    """Unlike a ``!`` shell turn, nothing here raises the mode to ``full_autonomy``."""
    session, _ = _session(tmp_path, world, "/pipeline run-1", permission_mode=mode)
    session.build_runtime()
    assert session.permission_mode == mode


def test_the_per_turn_cap_on_subagents_is_lifted_for_a_pipeline_child(tmp_path: Path, world) -> None:
    session, _ = _session(tmp_path, world, "/pipeline run-1", _Scripted([_text_batch("a"), _text_batch("b")]))
    session._max_subagents_per_turn = 1
    for _ in range(2):
        outcome = session.run_pipeline_agent("general", "tarefa")
        assert outcome.status == "succeeded", outcome
    assert session._max_subagents_per_turn is None


# =========================================================================== end to end
def _write_counter(root: Path) -> None:
    (root / "gate_counter.py").write_text(
        textwrap.dedent(
            """
            import pathlib, sys
            file = pathlib.Path("gate_count.txt")
            count = int(file.read_text()) + 1 if file.exists() else 1
            file.write_text(str(count))
            print(f"tentativa {count}")
            sys.exit(0 if count >= 2 else 1)
            """
        ),
        encoding="utf-8",
    )


PIPELINE = textwrap.dedent(
    """\
    version: 1
    id: e2e
    name: Ponta a ponta
    budget: {max_tokens: 1000000}
    inputs:
      testes: {type: string, required: true}
    steps:
      - id: impl
        agent: general
        prompt: faça
      - id: verificar
        gate:
          all:
            - command: ${{ inputs.testes }}
              timeout_seconds: 60
        on_fail: {goto: impl, max_loops: 2}
    """
)


def _create_run(engine, workspace: Path, run_id: str, inputs: dict[str, Any], text: str = PIPELINE, **extra: Any) -> PipelineStore:
    definition, report = validate_structure(text, "user")
    assert definition is not None, report.errors
    store = PipelineStore(engine)
    store.create_run(
        run_id=run_id, user_id=TURN["user_id"], conversation_id=TURN["conversation_id"], workspace_root=workspace.as_posix(),
        pipeline_id=definition.id, definition_hash=definition.definition_hash, source="user", inputs=inputs, definition_snapshot=text, **extra,
    )
    return store


def _run_turn(session: TurnSession, store: _Store):
    runtime = session.build_runtime()
    return runtime.run(str(store.turn["turn_id"]), turn=store.turn)


def test_a_pipeline_turn_runs_the_gate_through_bash_loops_and_reports_a_summary(tmp_path: Path, world) -> None:
    """The gate is the ordinary Bash tool (real exit code); the same command runs again after a correction loop."""
    session, store = _session(tmp_path, world, "/pipeline run-1", _Scripted([_text_batch("feito 1"), _text_batch("feito 2")]))
    _write_counter(session.workspace.root)
    command = f'"{Path(sys.executable).as_posix()}" gate_counter.py'
    journal = _create_run(world[0], session.workspace.root, "run-1", {"testes": command})
    result = _run_turn(session, store)
    assert result.state == "completed" and result.error_code is None, store.deltas
    run = journal.get_run("run-1")
    assert run.state == "succeeded" and run.turn_id == TURN["turn_id"]
    gates = [row for row in journal.steps_of("run-1") if row.step_id == "verificar"]
    assert [row.state for row in gates] == ["gate_failed", "succeeded"]  # the same command twice, both really executed
    summary = "".join(store.deltas)
    assert "| Etapa | Estado | Tokens | Custo (US$) |" in summary and "| impl |" in summary and "| verificar |" in summary
    assert store.marked == 0  # the summary stays in the history: the turn is not a "command turn" like /compact
    assert store.finished == [(False, None)]


def test_a_failed_pipeline_fails_the_turn_with_the_stop_reason_as_the_code(tmp_path: Path, world) -> None:
    session, store = _session(tmp_path, world, "/pipeline run-2", _Scripted([_text_batch("a"), _text_batch("b"), _text_batch("c")]))
    command = f'"{Path(sys.executable).as_posix()}" -c "import sys; print(\'quebrou\'); sys.exit(2)"'
    journal = _create_run(world[0], session.workspace.root, "run-2", {"testes": command})
    result = _run_turn(session, store)
    assert result.state == "failed" and result.error_code == "PIPELINE_NO_PROGRESS"  # same failure twice: the loop stops early
    assert store.finished[-1] == (True, "PIPELINE_NO_PROGRESS")
    assert journal.get_run("run-2").state == "failed"
    assert "quebrou" not in "".join(store.deltas)  # step output stays in the journal, not in the conversation


def test_a_pipeline_turn_in_plan_mode_runs_nothing(tmp_path: Path, world) -> None:
    session, store = _session(tmp_path, world, "/pipeline run-3", _Scripted([]), permission_mode="plan_mode")
    journal = _create_run(world[0], session.workspace.root, "run-3", {"testes": "true"})
    result = _run_turn(session, store)
    assert result.state == "failed" and result.error_code == "PIPELINE_PLAN_MODE"
    assert journal.steps_of("run-3") == [] and journal.get_run("run-3").state == "interrupted"


def test_a_run_of_another_conversation_cannot_be_run_from_this_turn(tmp_path: Path, world) -> None:
    session, store = _session(tmp_path, world, "/pipeline run-4", _Scripted([]))
    journal = PipelineStore(world[0])
    definition, _ = validate_structure(PIPELINE, "user")
    journal.create_run(
        run_id="run-4", user_id=TURN["user_id"], conversation_id="another-conversation", workspace_root=tmp_path.as_posix(),
        pipeline_id="e2e", definition_hash=definition.definition_hash, source="user", inputs={"testes": "true"}, definition_snapshot=PIPELINE,
    )
    result = _run_turn(session, store)
    assert result.error_code == "PIPELINE_RUN_FORBIDDEN" and journal.steps_of("run-4") == []


def test_cancelling_the_turn_cancels_the_run_and_its_children(tmp_path: Path, world) -> None:
    import threading

    release = threading.Event()
    started = threading.Event()

    class Hanging:
        def stream(self, request):  # noqa: ANN001, ANN201
            from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind

            started.set()
            for _ in range(3000):
                if release.wait(0.01):
                    break
                yield NormalizedStreamItem(StreamKind.TEXT, 1, text=".")
            yield NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")

    session, store = _session(tmp_path, world, "/pipeline run-5", Hanging())  # type: ignore[arg-type]
    journal = _create_run(world[0], session.workspace.root, "run-5", {"testes": "true"})
    flag = threading.Event()
    session.cancelled = lambda _turn: flag.is_set()  # type: ignore[method-assign]

    def cancel_when_running() -> None:
        assert started.wait(20)
        flag.set()

    threading.Thread(target=cancel_when_running, daemon=True).start()
    runtime = session.build_runtime()
    runtime.cancelled = lambda _turn: flag.is_set()
    result = runtime.run(str(store.turn["turn_id"]), turn=store.turn)
    release.set()
    assert result.state == "cancelled"
    assert journal.get_run("run-5").state == "cancelled"
    assert store.finished[-1] == (True, "TURN_CANCELLED")
    assert not BACKGROUND_AGENTS.running(TURN["conversation_id"])  # no child left behind


def test_a_gate_that_fails_as_a_tool_twice_really_runs_twice(tmp_path: Path, world) -> None:
    """A timeout is a *failed tool call*: the runtime short-circuits an identical failed call, but a gate that
    comes back in a correction loop must run again, so the turn clears that memory before each gate command."""
    pipeline = PIPELINE.replace("timeout_seconds: 60", "timeout_seconds: 1")
    session, store = _session(tmp_path, world, "/pipeline run-6", _Scripted([_text_batch("a"), _text_batch("b")]))
    command = f'"{Path(sys.executable).as_posix()}" -c "import time; time.sleep(30)"'
    journal = _create_run(world[0], session.workspace.root, "run-6", {"testes": command}, text=pipeline)
    _run_turn(session, store)
    gates = [row for row in journal.steps_of("run-6") if row.step_id == "verificar"]
    assert len(gates) == 2
    for row in gates:
        findings = journal.load_output("run-6", row.output)["value"]["findings"]
        assert findings[0]["timed_out"] is True and findings[0]["exit_code"] is None, findings
        assert "already failed" not in findings[0]["output_excerpt"], findings  # not the runtime's DUPLICATE_TOOL_CALL stub


# ---- WS-C7 review F9: a pipeline turn in plan mode must not scaffold the Code-mode plan file
def test_a_pipeline_turn_in_plan_mode_creates_no_code_mode_plan(tmp_path: Path, world) -> None:
    session, store = _session(tmp_path, world, "/pipeline run-1", permission_mode="plan_mode", code_mode="code")
    session.build_runtime()
    assert not (tmp_path / ".orin" / "plans").exists()
    assert not [item for item in store.activity if item[0] is AgentActivityEventType.CODE_MODE_PLAN_READY]
    ordinary, other_store = _session(tmp_path, world, "faça algo", turn_kind=None, permission_mode="plan_mode", code_mode="code")
    ordinary.build_runtime()
    assert [item for item in other_store.activity if item[0] is AgentActivityEventType.CODE_MODE_PLAN_READY]  # an ordinary plan-mode Code turn still gets its plan

"""WS-C9: the /goal controller (journal rows, stop rule, idempotent continuation). No test calls a real model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.goal import (
    GOAL_MAX_TURNS,
    GoalController,
    GoalDeps,
    GoalError,
    TurnEvidence,
)
from agentos.pipelines.goal_evaluator import ModelReply, ToolUse
from agentos.pipelines.store import PipelineStore

CONV = "conv-1"
USER = "user-1"


@dataclass
class Receipt:
    turn_id: str


class FakeChat:
    """Idempotent on the key, like ``ChatApplication.send``."""

    def __init__(self) -> None:
        self.by_key: dict[str, Receipt] = {}
        self.sent: list[tuple[str, str, dict]] = []

    def send(self, user_id, conversation_id, message, key, **kwargs):  # noqa: ANN001, ANN201
        if key not in self.by_key:
            self.by_key[key] = Receipt(f"turn-{len(self.by_key) + 1}")
            self.sent.append((key, message, kwargs))
        return self.by_key[key]


class FakeEvidence:
    def __init__(self) -> None:
        self.usage = (0, 0)
        self.pending = False
        self.live = False
        self.evidence = TurnEvidence("I did the work", (ToolUse("Bash", "succeeded", "ok"),), 1, 0)

    def turn_evidence(self, turn):  # noqa: ANN001, ANN201
        return self.evidence

    def usage_total(self, conversation_id):  # noqa: ANN001, ANN201
        return self.usage

    def has_pending_work(self, turn):  # noqa: ANN001, ANN201
        return self.pending


class FakeJudge:
    def __init__(self, *verdicts: dict | Exception) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.choices: list = []

    def complete(self, choice, messages):  # noqa: ANN001, ANN201
        self.calls += 1
        self.choices.append(choice)
        item = self.verdicts.pop(0) if self.verdicts else {"done": False, "reason": "still missing"}
        if isinstance(item, Exception):
            raise item
        return ModelReply("```json\n" + json.dumps(item) + "\n```", 50, 10)


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(eng)
    yield eng
    eng.dispose()


class Rig:
    def __init__(self, engine, judge: FakeJudge | None = None, candidates=("claude-haiku-5-5", "claude-sonnet-5-5")) -> None:  # noqa: ANN001
        self.runs = PipelineStore(engine)
        self.chat = FakeChat()
        self.evidence = FakeEvidence()
        self.judge = judge if judge is not None else FakeJudge()
        self.events: list = []
        self.controller = GoalController(GoalDeps(
            runs=self.runs, evidence=self.evidence, send=self.chat.send,
            emit=lambda turn, event: self.events.append((turn["turn_id"], event.type.value, event.payload)),
            evaluator=self.judge, candidates=lambda turn: candidates,
            live_turn=lambda conversation_id: self.evidence.live,
            load_turn=lambda turn_id: {"turn_id": turn_id},
        ))
        self.counter = 0

    def start(self, **kwargs):  # noqa: ANN003, ANN201
        values = dict(user_id=USER, conversation_id=CONV, objective="create x.txt with hello", max_tokens=100_000)
        values.update(kwargs)
        return self.controller.start(**values)

    def end_turn(self, state: str = "completed", *, tokens: int = 1000, kind: str | None = None, **evidence):  # noqa: ANN003, ANN201
        """A turn of the conversation ends having spent ``tokens`` more; returns what the controller did."""
        self.counter += 1
        self.evidence.usage = (self.evidence.usage[0] + tokens, self.evidence.usage[1])
        if evidence:
            self.evidence.evidence = TurnEvidence("answer", (), evidence.get("tool_calls", 0), evidence.get("files_changed", 0))
        turn = {
            "turn_id": f"t{self.counter}", "conversation_id": CONV, "user_id": USER, "provider": "anthropic",
            "model_id": "claude-sonnet-5-5", "turn_kind": kind, "effort": "high",
        }
        return self.controller.after_turn(turn, state)

    def run(self):  # noqa: ANN201
        return self.runs.list_runs(CONV)[0]

    def steps(self, step_id: str):  # noqa: ANN201
        return [s for s in self.runs.steps_of(self.run().run_id) if s.step_id == step_id]


# --------------------------------------------------------------------------------------------- start
def test_start_creates_the_goal_run_and_sends_the_objective_as_message_zero(engine) -> None:
    rig = Rig(engine)
    started = rig.start(workspace_root="/ws")
    run = rig.run()
    assert (run.pipeline_id, run.source, run.state, run.workspace_root) == ("goal", "builtin", "running", "/ws")
    assert run.inputs["objetivo"] == "create x.txt with hello" and run.inputs["max_tokens"] == 100_000
    assert run.inputs["usage_baseline"] == {"input": 0, "output": 0}
    assert rig.chat.sent[0][0] == f"goal:{run.run_id}:0"
    assert rig.chat.sent[0][1] == "create x.txt with hello"  # an ordinary user message, not "/pipeline"
    assert (started.run_id, started.turn_id, run.turn_id) == (run.run_id, "turn-1", "turn-1")
    assert [e[1] for e in rig.events] == ["goal.started"]


def test_a_second_goal_in_the_same_conversation_is_refused_with_goal_active(engine) -> None:
    rig = Rig(engine)
    rig.start()
    with pytest.raises(GoalError) as refused:
        rig.start(objective="another")
    assert (refused.value.code, refused.value.status) == ("GOAL_ACTIVE", 409)
    assert len(rig.chat.sent) == 1


@pytest.mark.parametrize("kwargs", [{"objective": "   "}, {"objective": "x" * 4001}, {"max_tokens": 999}, {"max_tokens": 5_000_001}])
def test_start_validates_the_objective_and_the_token_cap(engine, kwargs) -> None:  # noqa: ANN001
    with pytest.raises(GoalError) as refused:
        Rig(engine).start(**kwargs)
    assert refused.value.status == 422


def test_the_cap_defaults_to_the_configured_value(engine) -> None:
    rig = Rig(engine)
    rig.controller.deps.default_max_tokens = 250_000
    rig.controller.start(user_id=USER, conversation_id=CONV, objective="g")
    assert rig.run().inputs["max_tokens"] == 250_000


def test_a_failed_send_does_not_leave_an_active_goal(engine) -> None:
    rig = Rig(engine)
    rig.controller.deps.send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(RuntimeError):
        rig.start()
    assert rig.run().state == "cancelled"
    rig.controller.deps.send = rig.chat.send
    rig.start()  # not blocked by the failed attempt


def test_the_baseline_keeps_earlier_conversation_usage_out_of_the_goal(engine) -> None:
    rig = Rig(engine)
    rig.evidence.usage = (5000, 0)
    rig.start()
    rig.end_turn(tokens=800)
    assert rig.run().used_tokens == 800 + 60  # the turn's own spend plus the evaluator call


# ---------------------------------------------------------------------------------- continue and stop
def test_not_done_sends_the_continuation_with_the_next_idempotency_key(engine) -> None:
    rig = Rig(engine, FakeJudge({"done": False, "reason": "x.txt does not exist yet"}))
    run_id = rig.start().run_id
    assert rig.end_turn() == "continued"
    key, message, kwargs = rig.chat.sent[1]
    assert key == f"goal:{run_id}:1"
    assert message == "Continue working toward the goal: create x.txt with hello. Last evaluation: x.txt does not exist yet."
    assert kwargs == {"effort": "high"}
    assert rig.run().turn_id == "turn-2"
    assert [e[1] for e in rig.events] == ["goal.started", "goal.evaluated", "goal.continued"]
    evaluated = rig.events[1][2]
    assert evaluated["met"] is False and evaluated["evaluator_model"] == "anthropic/claude-haiku-5-5"


def test_the_same_hook_delivered_twice_neither_duplicates_the_turn_nor_pays_for_a_second_evaluation(engine) -> None:
    rig = Rig(engine)
    rig.start()
    turn = {"turn_id": "t1", "conversation_id": CONV, "user_id": USER, "provider": "anthropic", "model_id": "claude-sonnet-5-5"}
    rig.evidence.usage = (1000, 0)
    rig.controller.after_turn(turn, "completed")
    rig.controller.after_turn(turn, "completed")
    assert len(rig.chat.sent) == 2 and rig.judge.calls == 1
    assert len(rig.steps("turno")) == 1 and len(rig.steps("avaliar")) == 1


def test_done_ends_the_goal_as_succeeded_and_sends_nothing(engine) -> None:
    rig = Rig(engine, FakeJudge({"done": True, "reason": "x.txt read back with hello"}))
    rig.start()
    assert rig.end_turn() == "done"
    run = rig.run()
    assert (run.state, run.stop_reason) == ("succeeded", "done")
    assert len(rig.chat.sent) == 1
    assert [e[1] for e in rig.events][-2:] == ["goal.evaluated", "goal.finished"]
    assert rig.events[-1][2]["state"] == "succeeded"
    assert rig.end_turn() == "no_goal"  # a finished goal is not evaluated again


def test_the_token_cap_stops_without_calling_the_evaluator_or_sending_a_turn(engine) -> None:
    rig = Rig(engine)
    rig.start(max_tokens=1000)
    assert rig.end_turn(tokens=1500) == "budget"
    run = rig.run()
    assert (run.state, run.stop_reason) == ("budget_exceeded", "budget")
    assert rig.judge.calls == 0 and len(rig.chat.sent) == 1


def test_evaluator_tokens_count_toward_the_cap_and_stop_before_another_turn(engine) -> None:
    rig = Rig(engine)
    rig.start(max_tokens=1050)
    assert rig.end_turn(tokens=1000) == "budget"  # 1000 + 60 of the evaluator reaches the cap: no new turn
    assert rig.judge.calls == 1 and len(rig.chat.sent) == 1


def test_three_turns_without_a_tool_stop_with_no_progress(engine) -> None:
    rig = Rig(engine)
    rig.start()
    assert rig.end_turn(tool_calls=0) == "continued"
    assert rig.end_turn(tool_calls=0) == "continued"
    assert rig.end_turn(tool_calls=0) == "no_progress"
    run = rig.run()
    assert (run.state, run.stop_reason) == ("failed", "no_progress")
    assert len(rig.chat.sent) == 3


def test_a_turn_that_changes_a_file_counts_as_progress(engine) -> None:
    rig = Rig(engine)
    rig.start()
    for _ in range(4):
        assert rig.end_turn(tool_calls=0, files_changed=1) == "continued"


def test_the_turn_ceiling_stops_even_when_the_evaluator_keeps_saying_not_done(engine) -> None:
    rig = Rig(engine)
    rig.start(max_tokens=5_000_000)
    outcomes = [rig.end_turn(tokens=10) for _ in range(GOAL_MAX_TURNS)]
    assert outcomes[:-1] == ["continued"] * (GOAL_MAX_TURNS - 1) and outcomes[-1] == "max_turns"
    assert (rig.run().state, rig.run().stop_reason) == ("failed", "max_turns")


def test_one_evaluator_failure_continues_and_two_in_a_row_stop_without_pretending_done(engine) -> None:
    rig = Rig(engine, FakeJudge(ConnectionError("down"), ConnectionError("down")))
    rig.start()
    assert rig.end_turn() == "continued"
    assert "could not be completed" in rig.chat.sent[1][1]
    assert rig.end_turn() == "evaluator_failed"
    run = rig.run()
    assert (run.state, run.stop_reason) == ("failed", "evaluator_failed")
    assert [s.state for s in rig.steps("avaliar")] == ["failed", "failed"]


def test_a_success_between_failures_resets_the_count(engine) -> None:
    rig = Rig(engine, FakeJudge(ConnectionError("down"), {"done": False, "reason": "r"}, ConnectionError("down")))
    rig.start()
    assert [rig.end_turn() for _ in range(3)] == ["continued"] * 3


def test_without_an_evaluator_the_goal_stops_after_two_turns(engine) -> None:
    rig = Rig(engine)
    rig.controller.deps.evaluator = None
    rig.start()
    assert [rig.end_turn() for _ in range(2)] == ["continued", "evaluator_failed"]


def test_the_evaluator_uses_a_small_model_of_the_same_subscription(engine) -> None:
    rig = Rig(engine)
    rig.start()
    rig.end_turn()
    assert str(rig.judge.choices[0]) == "anthropic/claude-haiku-5-5" and rig.judge.choices[0].fallback is False
    rig2 = Rig(engine, candidates=("claude-sonnet-5-5",))  # no small model: the turn's model at low effort
    rig2.controller.start(user_id=USER, conversation_id="conv-2", objective="g", max_tokens=5000)
    turn = {"turn_id": "z1", "conversation_id": "conv-2", "user_id": USER, "provider": "anthropic", "model_id": "claude-sonnet-5-5"}
    rig2.controller.after_turn(turn, "completed")
    assert (rig2.judge.choices[0].model, rig2.judge.choices[0].effort, rig2.judge.choices[0].fallback) == ("claude-sonnet-5-5", "low", True)


def test_the_journal_total_matches_the_rows(engine) -> None:
    rig = Rig(engine)
    rig.start()
    rig.end_turn(tokens=700)
    rig.end_turn(tokens=300)
    rows = rig.runs.steps_of(rig.run().run_id)
    assert rig.run().used_tokens == sum(s.input_tokens + s.output_tokens for s in rows) == 1000 + 120


# ------------------------------------------------------------------------------- park, resume, clear
@pytest.mark.parametrize(
    ("state", "reason"),
    [("failed", "turn_failed"), ("cancelled", "turn_cancelled"), ("waiting_user", "waiting_user"), ("reconciliation_required", "reconciliation")],
)
def test_a_turn_that_did_not_finish_parks_the_goal_as_interrupted(engine, state: str, reason: str) -> None:
    rig = Rig(engine)
    rig.start()
    assert rig.end_turn(state) == "parked"
    run = rig.run()
    assert (run.state, run.stop_reason) == ("interrupted", reason)
    assert rig.judge.calls == 0 and len(rig.chat.sent) == 1
    assert rig.events[-1][1] == "goal.finished" and rig.events[-1][2]["state"] == "interrupted"


def test_resume_brings_a_parked_goal_back_to_running_and_sends_the_next_message(engine) -> None:
    rig = Rig(engine)
    run_id = rig.start().run_id
    rig.end_turn("failed")
    status = rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert (status.state, status.stop_reason) == ("running", None)
    assert rig.chat.sent[1][0] == f"goal:{run_id}:r1"
    assert "Continue working toward the goal: create x.txt with hello." in rig.chat.sent[1][1]
    assert rig.end_turn() == "continued"  # the goal works again


def test_resume_with_a_live_turn_only_reopens_the_goal(engine) -> None:
    rig = Rig(engine)
    rig.start()
    rig.end_turn("failed")
    rig.evidence.live = True
    rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert len(rig.chat.sent) == 1 and rig.run().state == "running"


def test_resume_after_the_cap_opens_a_fresh_window_with_the_new_cap(engine) -> None:
    rig = Rig(engine)
    rig.start(max_tokens=1000)
    assert rig.end_turn(tokens=1500) == "budget"
    status = rig.controller.resume(user_id=USER, conversation_id=CONV, max_tokens=50_000)
    assert (status.state, status.max_tokens, status.window_used_tokens, status.remaining_tokens) == ("running", 50_000, 0, 50_000)
    assert status.used_tokens == 1500  # the journal total keeps counting
    assert rig.end_turn(tokens=500) == "continued"


def test_resume_after_no_progress_resets_the_window_counters(engine) -> None:
    rig = Rig(engine)
    rig.start()
    for _ in range(3):
        rig.end_turn(tool_calls=0)
    rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert rig.end_turn(tool_calls=0) == "continued"  # old idle turns are outside the new window
    assert rig.controller.status(CONV).window_turns == 1


def test_resume_refuses_what_cannot_be_resumed(engine) -> None:
    rig = Rig(engine)
    with pytest.raises(GoalError) as none:
        rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert (none.value.code, none.value.status) == ("GOAL_NOT_FOUND", 404)
    rig.start()
    with pytest.raises(GoalError) as active:
        rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert active.value.code == "GOAL_ACTIVE"
    rig.controller.clear(CONV)
    with pytest.raises(GoalError) as cleared:
        rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert cleared.value.code == "GOAL_NOT_RESUMABLE"
    rig.start(objective="again")
    rig.judge.verdicts.append({"done": True, "reason": "evidence"})
    rig.end_turn()
    with pytest.raises(GoalError) as done:
        rig.controller.resume(user_id=USER, conversation_id=CONV)
    assert done.value.code == "GOAL_NOT_RESUMABLE"


def test_clear_cancels_the_goal_and_a_later_turn_end_does_nothing(engine) -> None:
    rig = Rig(engine)
    rig.start()
    status = rig.controller.clear(CONV)
    assert (status.state, status.stop_reason) == ("cancelled", "cleared")
    assert rig.end_turn() == "no_goal" and rig.judge.calls == 0
    assert rig.controller.clear(CONV) is None


def test_starting_a_goal_replaces_a_parked_one(engine) -> None:
    rig = Rig(engine)
    first = rig.start().run_id
    rig.end_turn("failed")
    second = rig.start(objective="new one").run_id
    assert rig.runs.get_run(first).state == "cancelled" and rig.runs.get_run(second).state == "running"


# ------------------------------------------------------------------------------------- what is ignored
def test_queued_text_or_a_live_turn_defers_the_evaluation_to_the_next_turn_end(engine) -> None:
    """The person's queued text (and the Friend's, same queue) has priority: nothing is judged or sent now."""
    rig = Rig(engine)
    rig.start()
    rig.evidence.pending = True
    assert rig.end_turn() == "deferred"
    assert rig.judge.calls == 0 and len(rig.chat.sent) == 1 and rig.steps("turno") == []
    rig.evidence.pending = False
    assert rig.end_turn() == "continued"


def test_a_conversation_without_a_goal_never_reaches_the_evaluator(engine) -> None:
    rig = Rig(engine)
    assert rig.end_turn() == "no_goal"
    assert rig.judge.calls == 0 and rig.chat.sent == []


@pytest.mark.parametrize("kind", ["compact", "shell", "pipeline"])
def test_the_turns_orin_runs_by_itself_are_not_goal_turns(engine, kind: str) -> None:
    rig = Rig(engine)
    rig.start()
    assert rig.end_turn(kind=kind) == "ignored"
    assert rig.end_turn("failed", kind=kind) == "ignored"
    assert rig.run().state == "running" and rig.judge.calls == 0


def test_a_goal_of_another_user_is_invisible(engine) -> None:
    rig = Rig(engine)
    rig.start()
    assert rig.controller.status(CONV, "someone-else") is None
    assert rig.controller.clear(CONV, "someone-else") is None


def test_a_failed_continuation_parks_the_goal_instead_of_losing_it(engine) -> None:
    rig = Rig(engine)
    rig.start()
    rig.controller.deps.send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked"))
    assert rig.end_turn() == "send_failed"
    assert (rig.run().state, rig.run().stop_reason) == ("interrupted", "send_failed")


# ------------------------------------------------------------------------------------------- status
def test_status_reports_the_window_the_last_verdict_and_the_evaluator(engine) -> None:
    rig = Rig(engine, FakeJudge({"done": False, "reason": "file missing"}))
    rig.start(max_tokens=10_000)
    assert rig.controller.status(CONV).last_evaluation is None
    rig.end_turn(tokens=900)
    data = rig.controller.status(CONV).to_dict()
    assert data["state"] == "running" and data["objective"] == "create x.txt with hello"
    assert (data["turns"], data["max_turns"], data["max_tokens"]) == (1, GOAL_MAX_TURNS, 10_000)
    assert data["used_tokens"] == 960 and data["remaining_tokens"] == 10_000 - 960
    assert data["last_evaluation"]["reason"] == "file missing" and data["last_evaluation"]["done"] is False
    assert data["evaluator_model"] == "anthropic/claude-haiku-5-5"
    assert data["resumable"] is False


# ----------------------------------------------------------------------------------- no model entry point
def test_no_tool_can_start_or_touch_a_goal() -> None:
    """P4: a goal exists only because the person typed ``/goal``. The tool registry has nothing that names it."""
    from agentos.agentic.tools.registry import default_registry

    for spec in default_registry().specs():
        assert "goal" not in " ".join(spec.names()).lower(), spec.key


# ------------------------------------------------------------- through the real store and the worker
def _real_world():  # noqa: ANN202
    from sqlalchemy import create_engine

    from agentos.conversations.chat import PostgresChatStore

    eng = create_engine("sqlite://", future=True)
    metadata.create_all(eng)
    from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

    store = PostgresChatStore(eng, activity_store=PostgresAgenticActivityStore(eng, cursor_secret="test-secret"))
    first = store.create(user_id=USER, message="oi", provider="anthropic", model_id="claude-sonnet-5-5", idempotency_key="k0")
    return eng, store, first


def _worker(store, result_state: str = "completed"):  # noqa: ANN001, ANN202
    from agentos.agentic.runtime import AgenticRunResult
    from agentos.workers.chat import ChatWorker

    class Runtime:
        def run(self, turn_id):  # noqa: ANN001, ANN201
            return AgenticRunResult(result_state)

        def close(self):  # noqa: ANN201
            return None

    worker = ChatWorker(store)
    worker._runtime_for = lambda turn: Runtime()  # type: ignore[method-assign]
    return worker


def test_the_real_worker_continues_a_goal_and_stops_it_when_the_evaluator_says_done(monkeypatch) -> None:  # noqa: ANN001
    from agentos.pipelines import after_turn, goal

    after_turn.clear_handlers()
    after_turn.install_default_handlers()
    judge = FakeJudge({"done": False, "reason": "no file yet"}, {"done": True, "reason": "file read back"})
    monkeypatch.setattr(goal, "TransportEvaluatorModel", lambda transport, *, user_id: judge)
    eng, store, first = _real_world()
    try:
        worker = _worker(store)
        worker.run(first.turn_id)
        controller = GoalController.from_store(store)
        run_id = controller.start(user_id=USER, conversation_id=first.conversation_id, objective="make the file", max_tokens=50_000).run_id
        rows = _turns(eng)
        assert [row["idempotency_key"] for row in rows] == ["k0", f"goal:{run_id}:0"]
        assert _event_types(eng, rows[1]["turn_id"]) == ["goal.started"]
        worker.run(rows[1]["turn_id"])
        rows = _turns(eng)
        assert [row["idempotency_key"] for row in rows][-1] == f"goal:{run_id}:1"
        assert "Last evaluation: no file yet." in _first_message(eng, rows[-1]["user_message_id"])
        worker.run(rows[-1]["turn_id"])
        status = GoalController.from_store(store).status(first.conversation_id)
        assert (status.state, status.stop_reason, status.turns) == ("succeeded", "done", 2)
        assert judge.calls == 2
        assert len(_turns(eng)) == 3  # no turn after done
    finally:
        after_turn.clear_handlers()


def test_a_failed_turn_parks_the_goal_through_the_real_worker(monkeypatch) -> None:  # noqa: ANN001
    from agentos.pipelines import after_turn, goal

    after_turn.clear_handlers()
    after_turn.install_default_handlers()
    monkeypatch.setattr(goal, "TransportEvaluatorModel", lambda transport, *, user_id: FakeJudge())
    eng, store, first = _real_world()
    try:
        _worker(store).run(first.turn_id)
        controller = GoalController.from_store(store)
        controller.start(user_id=USER, conversation_id=first.conversation_id, objective="make the file", max_tokens=50_000)
        goal_turn = _latest_turn_id(eng)
        _worker(store, "failed").run(goal_turn)
        status = GoalController.from_store(store).status(first.conversation_id)
        assert (status.state, status.stop_reason, status.resumable) == ("interrupted", "turn_failed", True)
    finally:
        after_turn.clear_handlers()


def _turns(eng):  # noqa: ANN001, ANN202
    from agentos.persistence.postgres.schema import conversation_turns

    with eng.connect() as connection:
        return connection.execute(conversation_turns.select().order_by(conversation_turns.c.id)).mappings().all()


def _event_types(eng, turn_id: str) -> list[str]:  # noqa: ANN001
    from agentos.persistence.postgres.schema import conversation_activity_events as events

    with eng.connect() as connection:
        rows = connection.execute(events.select().where(events.c.turn_id == turn_id).order_by(events.c.sequence)).mappings().all()
    return [row["event_type"] for row in rows if row["event_type"].startswith("goal.")]


def _first_message(eng, message_id: str) -> str:  # noqa: ANN001
    from agentos.persistence.postgres.schema import conversation_messages

    with eng.connect() as connection:
        return str(connection.execute(
            conversation_messages.select().where(conversation_messages.c.message_id == message_id)
        ).mappings().first()["content"])


def _latest_turn_id(eng) -> str:  # noqa: ANN001
    from agentos.persistence.postgres.schema import conversation_turns

    with eng.connect() as connection:
        return str(connection.execute(conversation_turns.select().order_by(conversation_turns.c.id.desc())).mappings().first()["turn_id"])


def test_the_store_evidence_reads_the_answer_the_tools_the_usage_and_the_pending_work() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import insert

    from agentos.persistence.postgres.schema import (
        conversation_activity_events,
        conversation_agent_usage,
        conversation_messages,
        conversation_turns,
    )
    from agentos.pipelines.goal import StoreGoalEvidence

    eng, store, first = _real_world()
    now = datetime.now(UTC)
    with eng.begin() as connection:
        row = connection.execute(conversation_turns.select().where(conversation_turns.c.turn_id == first.turn_id)).mappings().one()
        connection.execute(
            conversation_messages.update().where(conversation_messages.c.message_id == row["assistant_message_id"]).values(content="all done")
        )
        for sequence, (event_type, payload) in enumerate([
            ("tool.finished", {"tool_name": "Bash", "status": "succeeded"}),
            ("filesystem.write", {"path": "x.txt"}),
            ("tool.requested", {"tool_name": "Bash"}),
        ], start=100):
            connection.execute(insert(conversation_activity_events).values(
                event_id=f"e{sequence}", conversation_id=first.conversation_id, user_id=USER, turn_id=first.turn_id,
                execution_id=row["execution_id"], agent_id="main", event_type=event_type, sequence=sequence,
                summary=f"{event_type} summary", payload=payload, visibility="user", created_at=now,
            ))
        for agent, tokens in (("main", (100, 40)), ("child", (10, 5))):
            connection.execute(insert(conversation_agent_usage).values(
                conversation_id=first.conversation_id, agent_id=agent, user_id=USER, provider="anthropic",
                model_id="m", input_tokens=tokens[0], output_tokens=tokens[1], total_tokens=sum(tokens),
                usage_reported=True, updated_at=now,
            ))
    evidence = StoreGoalEvidence(store)
    turn = dict(row)
    seen = evidence.turn_evidence(turn)
    assert seen.final_text == "all done" and (seen.tool_calls, seen.files_changed) == (1, 1)
    assert seen.tools[0].name == "Bash" and seen.tools[0].status == "succeeded"
    assert evidence.usage_total(first.conversation_id) == (110, 45)
    assert evidence.has_pending_work(turn) is False
    store.create(user_id=USER, message="more", provider="", model_id="", idempotency_key="k1", conversation_id=first.conversation_id)
    assert evidence.has_pending_work(turn) is True  # another turn of the conversation is live
    assert evidence.live_turn(first.conversation_id) is True

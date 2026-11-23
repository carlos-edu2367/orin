"""WS-C7: the real ports of the engine over a ``TurnSession`` (children, journal id, live budget, progress, gate, worktree)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, ProviderUsage, StreamKind
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import agent_runs, metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.pipelines.engine import PipelineEngine
from agentos.pipelines.events import TurnSessionEventSink
from agentos.pipelines.ports import AgentCall, AgentHandle, CommandResult, EngineOptions
from agentos.pipelines.session_adapter import (
    SessionAgentLauncher,
    SessionGateRunner,
    classify_gate_result,
    make_orphan_finder,
    pipeline_store_for,
)
from agentos.pipelines.store import PipelineStore
from agentos.pipelines.validator import validate_structure
from tests.unit.agentic.test_nested_subagents import _Scripted, _TreeStore
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _parallel_tool_calls_batch, _text_batch

HEADER = "version: 1\nid: teste\nname: Teste\nbudget: {max_tokens: 1000000}\n"


def _usage(batch: list[NormalizedStreamItem], tokens: int) -> list[NormalizedStreamItem]:
    usage = NormalizedStreamItem(StreamKind.USAGE, 1, usage=ProviderUsage(tokens // 2, tokens - tokens // 2, tokens, None, measurement="CONFIRMED"))
    return [usage, *batch]


@pytest.fixture()
def world(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'adapter.db').as_posix()}")
    metadata.create_all(engine)
    agents = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    BACKGROUND_AGENTS.clear_for_tests()
    yield engine, agents
    BACKGROUND_AGENTS.clear_for_tests()
    engine.dispose()


def _session(tmp_path: Path, world, child: _Scripted, **kwargs: Any) -> TurnSession:
    _engine, agents = world
    return TurnSession(
        turn=dict(TURN), store=_TreeStore(), agents_store=agents, memory_store=None, provider_factory=lambda: _Scripted([]),
        workspace_root=tmp_path, child_provider_factory=lambda _m: child, **kwargs,
    )


def _run(engine, workspace: Path, run_id: str = "run-1", text: str | None = None):
    store = PipelineStore(engine)
    store.create_run(
        run_id=run_id, user_id=TURN["user_id"], conversation_id=TURN["conversation_id"], workspace_root=workspace.as_posix(),
        pipeline_id="teste", definition_hash="h" * 64, source="user", inputs={}, definition_snapshot=text or "",
    )
    return store


def _call(step: str = "a", *, run_id: str = "run-1", attempt: int = 1, **overrides: Any) -> AgentCall:
    values: dict[str, Any] = dict(
        step_id=step, item_key="", attempt=attempt, agent="general", prompt="Faça a tarefa", provider=None, model=None, effort=None,
        isolation=None, output_schema=None, budget=None, pipeline_run_id=run_id,
    )
    values.update(overrides)
    return AgentCall(**values)


def _launcher(session, store, **kwargs: Any) -> SessionAgentLauncher:
    return SessionAgentLauncher(session, store, run_id="run-1", **kwargs)


def _wait(launcher, handle, timeout: float = 20.0):
    result = launcher.wait(handle, timeout)
    assert result is not None, "the child did not finish"
    return result


# =============================================================================== launcher
def test_a_step_child_runs_in_the_background_and_the_result_comes_back_typed(tmp_path: Path, world) -> None:
    child = _Scripted([_usage(_text_batch("pronto"), 300)])
    session = _session(tmp_path, world, child)
    store = _run(world[0], tmp_path)
    store.start_step(run_id="run-1", step_id="a", input_hash="1" * 64)
    launcher = _launcher(session, store)
    assert launcher.live_metering is True
    handle = launcher.launch(_call("a"))
    assert handle.agent_id and handle.step_id == "a"
    assert store.steps_of("run-1")[0].agent_id == handle.agent_id  # journaled at launch, before the step finishes
    entry = BACKGROUND_AGENTS.get(TURN["conversation_id"], handle.agent_id)
    assert entry is not None and entry.notified is True  # the end-of-turn barrier will not re-inject this child's report
    result = _wait(launcher, handle)
    assert result.status == "succeeded" and "pronto" in result.text and result.error_code is None
    assert result.tokens == 300 and result.cost_known in (True, False)
    assert launcher.wait(handle, 0.0) is result  # a second look returns the very same result


def test_the_turns_own_model_needs_no_routing_and_other_options_are_forwarded(tmp_path: Path, world) -> None:
    seen: dict[str, Any] = {}

    class Spy:
        turn = {**TURN, "provider": "anthropic", "model_id": "claude-sonnet-5-5"}

        def run_pipeline_agent(self, agent, prompt, **options):  # noqa: ANN001, ANN201
            seen.update(agent=agent, prompt=prompt, **options)
            return ToolOutcome("failed", "x", "recusado", {}, "CHILD_PROVIDER_PINNED")

    launcher = SessionAgentLauncher(Spy(), PipelineStore(world[0]), run_id="run-1")
    schema = {"type": "object"}
    launcher.launch(_call("a", provider="anthropic", model="claude-sonnet-5-5", effort="high", isolation="worktree",
                          output_schema=schema, budget={"max_tokens": 1000}, replan_note="mude de abordagem", item_key="000-ab"))
    assert "provider" not in seen and "model_id" not in seen  # same provider and model as the turn
    assert seen["effort"] == "high" and seen["isolation"] == "worktree" and seen["output_schema"] == schema
    assert seen["budget"] == {"max_tokens": 1000} and seen["pipeline_run_id"] == "run-1"
    assert seen["description"] == "Etapa a (000-ab)" and "mude de abordagem" in seen["prompt"]
    launcher.launch(_call("b", provider="openai-codex", model="gpt-6-luna"))
    assert (seen["provider"], seen["model_id"]) == ("openai-codex", "gpt-6-luna")


def test_a_refused_start_becomes_a_failed_result_with_the_code_and_never_a_retry_candidate(tmp_path: Path, world) -> None:
    class Refuser:
        turn = dict(TURN)

        def run_pipeline_agent(self, agent, prompt, **options):  # noqa: ANN001, ANN201
            return ToolOutcome("failed", "Provedor fixado", "agent is pinned", {}, "CHILD_PROVIDER_PINNED")

        def child_progress(self, agent_id):  # noqa: ANN001, ANN201
            raise AssertionError("a child that never started has no progress to read")

    store = _run(world[0], tmp_path)
    store.start_step(run_id="run-1", step_id="a", input_hash="1" * 64)
    launcher = SessionAgentLauncher(Refuser(), store, run_id="run-1")
    handle = launcher.launch(_call("a"))
    result = launcher.wait(handle, 0.0)
    assert result.status == "failed" and result.error_code == "CHILD_PROVIDER_PINNED"
    assert store.steps_of("run-1")[0].agent_id is None  # nothing to journal
    assert launcher.progress(handle).tool_calls == 0
    launcher.cancel(handle, "x")  # harmless
    launcher.discard_worktree(handle)  # harmless without a discarder


def test_a_start_that_raises_is_a_failed_step_not_a_dead_run(tmp_path: Path, world) -> None:
    class Boom:
        turn = dict(TURN)

        def run_pipeline_agent(self, agent, prompt, **options):  # noqa: ANN001, ANN201
            raise RuntimeError("pool is shut down")

    launcher = SessionAgentLauncher(Boom(), PipelineStore(world[0]), run_id="run-1")
    result = launcher.wait(launcher.launch(_call("a")), 0.0)
    assert result.status == "failed" and result.error_code == "AGENT_START_FAILED" and "pool" in (result.error_message or "")


def test_an_unknown_agent_type_is_refused_by_the_session_and_reported(tmp_path: Path, world) -> None:
    session = _session(tmp_path, world, _Scripted([]))
    launcher = _launcher(session, _run(world[0], tmp_path))
    result = launcher.wait(launcher.launch(_call("a", agent="no-such-agent")), 0.0)
    assert result.status == "failed" and result.error_code == "INVALID_ARGUMENTS"


def test_cancel_stops_the_child_and_the_result_says_cancelled(tmp_path: Path, world) -> None:
    release = threading.Event()
    started = threading.Event()

    class Hanging:
        def stream(self, request):  # noqa: ANN001, ANN201
            started.set()
            for _ in range(2000):  # a child that keeps working until it is cancelled
                if release.wait(0.01):
                    break
                yield NormalizedStreamItem(StreamKind.TEXT, 1, text=".")
            yield NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")

    session = _session(tmp_path, world, Hanging())  # type: ignore[arg-type]
    launcher = _launcher(session, _run(world[0], tmp_path))
    handle = launcher.launch(_call("a"))
    assert started.wait(10)
    assert launcher.wait(handle, 0.05) is None  # still running
    launcher.cancel(handle, "test")
    result = _wait(launcher, handle)
    release.set()
    assert result.status in {"cancelled", "failed"} and result.status != "succeeded"
    assert BACKGROUND_AGENTS.get(TURN["conversation_id"], handle.agent_id).cancel.is_set()


# ======================================================================== live budget (risk 3)
def _engine_for(session, store, launcher, text: str, **options: Any) -> PipelineEngine:
    definition, report = validate_structure(text, "user")
    assert definition is not None, report.errors
    store.create_run(
        run_id="run-e", user_id=TURN["user_id"], conversation_id=TURN["conversation_id"], workspace_root=session.workspace.root.as_posix(),
        pipeline_id=definition.id, definition_hash=definition.definition_hash, source="user", inputs={}, definition_snapshot=text,
    )
    return PipelineEngine(
        definition, "run-e", store=store, launcher=launcher, gate_runner=SessionGateRunner(lambda calls: []), sink=TurnSessionEventSink(session),
        inputs={}, options=EngineOptions(poll_seconds=0.05, monitor_seconds=0.02, **options),
    )


TWO_STEPS = HEADER + "steps:\n  - id: a\n    agent: general\n    prompt: um\n  - id: b\n    agent: general\n    prompt: dois\n"


def test_children_spend_against_the_run_budget_live_and_exactly_once(tmp_path: Path, world) -> None:
    child = _Scripted([_usage(_text_batch("um"), 400), _usage(_text_batch("dois"), 600)])
    session = _session(tmp_path, world, child)
    store = PipelineStore(world[0])
    engine = _engine_for(session, store, _launcher(session, store), TWO_STEPS)
    detach = session.attach_pipeline_budget(engine.usage_budget)
    try:
        outcome = engine.execute()
    finally:
        detach()
    assert outcome.state == "succeeded"
    assert engine.usage_budget.tokens == 1000  # 400 + 600 once, not 2000: the engine did not charge what the session charged
    run = store.get_run("run-e")
    assert run.used_tokens == 1000 == sum(row.input_tokens + row.output_tokens for row in store.steps_of("run-e"))
    assert session._usage_budgets.get(session.main_agent_id) is None  # detached when the turn is done


def test_the_run_budget_is_the_hard_ceiling_for_what_the_children_spend(tmp_path: Path, world) -> None:
    def step(call_id: str) -> list[NormalizedStreamItem]:
        return _usage(_parallel_tool_calls_batch([(call_id, "list_files", {"path": "."})]), 600)

    child = _Scripted([step("c1"), step("c2"), step("c3"), _text_batch("nunca chega aqui")])
    session = _session(tmp_path, world, child)
    store = PipelineStore(world[0])
    text = "version: 1\nid: teste\nname: Teste\nbudget: {max_tokens: 1000}\nsteps:\n  - id: a\n    agent: general\n    prompt: um\n"
    engine = _engine_for(session, store, _launcher(session, store), text)
    detach = session.attach_pipeline_budget(engine.usage_budget)
    try:
        outcome = engine.execute()
    finally:
        detach()
    assert outcome.state == "budget_exceeded" and outcome.stop_reason == "budget"
    assert len(child.requests) == 2  # stopped right after the call that went over, the third never happened
    assert store.get_run("run-e").state == "budget_exceeded"


# ============================================================================ progress (K11)
def test_progress_reads_the_trackers_real_activity_not_the_time_since_start(tmp_path: Path, world) -> None:
    release = threading.Event()
    started = threading.Event()

    class Slow:
        def stream(self, request):  # noqa: ANN001, ANN201
            started.set()
            release.wait(20)
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="fim")
            yield NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")

    session = _session(tmp_path, world, Slow())  # type: ignore[arg-type]
    launcher = _launcher(session, _run(world[0], tmp_path))
    handle = launcher.launch(_call("a"))
    assert started.wait(10)
    tracker = session._progress_tracker()
    tracker.on_tool_start(handle.agent_id, "Bash")
    tracker.on_usage(handle.agent_id, input_tokens=70, output_tokens=30, cost_usd=0.5)
    progress = launcher.progress(handle)
    assert progress.current_tool == "Bash" and progress.tool_calls == 1
    assert progress.input_tokens == 70 and progress.output_tokens == 30 and progress.cost_usd == 0.5
    assert progress.last_activity_at is not None and progress.last_activity_at.tzinfo is not None
    before = progress.last_activity_at
    tracker.on_tool_finish(handle.agent_id, "Bash")
    assert launcher.progress(handle).last_activity_at >= before and launcher.progress(handle).current_tool is None
    release.set()
    _wait(launcher, handle)


# ================================================================== worktree (risks 1 and 7)
def test_discard_uses_the_journaled_id_and_an_empty_id_finds_the_agents_the_journal_never_learned(tmp_path: Path, world) -> None:
    engine, _agents = world
    store = _run(engine, tmp_path)
    known = store.start_step(run_id="run-1", step_id="a", input_hash="1" * 64)
    store.set_step_agent("run-1", "a", "", known.attempt, "agent:known")
    from sqlalchemy import insert

    with engine.begin() as connection:
        for agent_id, state in (("agent:known", "interrupted"), ("agent:lost", "interrupted"), ("agent:done", "completed")):
            connection.execute(
                insert(agent_runs).values(
                    agent_id=agent_id, user_id="u", conversation_id="c", turn_id="t", subagent_type="general", state=state,
                    pipeline_run_id="run-1", created_at=_now(), last_activity_at=_now(),
                    tool_calls=0, input_tokens=0, output_tokens=0,
                )
            )
    discarded: list[str] = []
    session = _session(tmp_path, world, _Scripted([]))
    launcher = SessionAgentLauncher(
        session, store, run_id="run-1", discard=discarded.append, orphan_agents=make_orphan_finder(session, store),
    )
    launcher.discard_worktree(AgentHandle("agent:known", "a", ""))
    assert discarded == ["agent:known"]
    discarded.clear()
    launcher.discard_worktree(AgentHandle("", "a", ""))  # the id never reached the journal
    assert discarded == ["agent:lost"]  # not the journaled one, not the finished one


def _now():  # noqa: ANN202
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _settings(worktree_enabled: bool):  # noqa: ANN202
    from agentos.settings.loader import EffectiveSettings, SettingsLayer

    data = {"orchestration": {"worktree": {"enabled": worktree_enabled}}}
    return EffectiveSettings(data=data, provenance={}, layers=(SettingsLayer("user", None, data),), errors=(), trusted=True)


def test_a_worktree_step_outside_git_degrades_to_the_project_folder_with_the_event(tmp_path: Path, world) -> None:
    """Risk 7: ``isolation: worktree`` with ``max_parallel == 1`` outside git still runs, and says so."""
    child = _Scripted([_parallel_tool_calls_batch([("w1", "write_file", {"path": "out.txt", "content": "ok"})]), _text_batch("feito")])
    session = _session(tmp_path, world, child)
    session.effective_settings = _settings(True)
    launcher = _launcher(session, _run(world[0], tmp_path))
    result = _wait(launcher, launcher.launch(_call("a", isolation="worktree")))
    assert result.status == "succeeded" and result.worktree is None
    assert (session.workspace.root / "out.txt").read_text(encoding="utf-8") == "ok"
    store = session.store
    unavailable = [e for e in store.tree_events if e[0] == AgentActivityEventType.ISOLATION_UNAVAILABLE]  # type: ignore[attr-defined]
    assert len(unavailable) == 1 and unavailable[0][3].get("reason") == "not_git_repo"


def test_the_store_comes_from_the_sessions_database_or_from_the_attribute_tests_set(tmp_path: Path, world) -> None:
    session = _session(tmp_path, world, _Scripted([]))
    derived = pipeline_store_for(session)
    assert isinstance(derived, PipelineStore)  # from the engine of the session's stores
    store = PipelineStore(world[0])
    session.pipeline_store = store  # type: ignore[attr-defined]
    assert pipeline_store_for(session) is store

    class NoDatabase:
        turn = dict(TURN)

    assert pipeline_store_for(NoDatabase()) is None


# ============================================================================== gate (S4)
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "succeeded", "content": "exit=0\n3 passed", "error_code": None}, CommandResult(0, "exit=0\n3 passed")),
        ({"status": "failed", "content": "exit=3\nFAILED a", "error_code": None}, CommandResult(3, "exit=3\nFAILED a")),
        ({"status": "failed", "content": "[cwd: sub]\nexit=1\nboom", "error_code": None}, CommandResult(1, "[cwd: sub]\nexit=1\nboom")),
        ({"status": "failed", "content": "exit=-1\nkilled", "error_code": None}, CommandResult(-1, "exit=-1\nkilled")),
        # the first exit line is the tool's; a command printing its own later does not fool the parser
        ({"status": "failed", "content": "exit=2\nexit=0\n", "error_code": None}, CommandResult(2, "exit=2\nexit=0\n")),
    ],
)
def test_the_exit_code_is_read_from_the_tool_result(result, expected) -> None:
    assert classify_gate_result(result) == expected


@pytest.mark.parametrize(
    ("result", "flags"),
    [
        ({"status": "failed", "content": "Command timed out after 5s.", "error_code": "TOOL_REFUSED"}, {"timed_out": True}),
        ({"status": "failed", "content": "Interrupted by user: the command was stopped.", "error_code": "INTERRUPTED_BY_USER"}, {"cancelled": True}),
        ({"status": "failed", "content": "stopped", "error_code": "STOPPED_BY_USER"}, {"cancelled": True}),
        ({"status": "failed", "content": "negado", "error_code": "CODE_PERMISSION_DENIED"}, {"denied": True}),
        ({"status": "failed", "content": "hook", "error_code": "HOOK_DENIED"}, {"denied": True}),
        ({"status": "failed", "content": "plan", "error_code": "CODE_PLAN_MODE_READONLY"}, {"denied": True}),
        ({"status": "failed", "content": "This command is blocked because it would affect the host system.", "error_code": "TOOL_REFUSED"}, {"denied": True}),
        ({"status": "failed", "content": "Unknown tool", "error_code": "UNKNOWN_TOOL"}, {}),
        ({}, {}),
    ],
)
def test_without_an_exit_line_the_result_says_how_the_command_ended_and_the_code_stays_none(result, flags) -> None:
    command = classify_gate_result(result)
    assert command.exit_code is None  # never "the tests failed"
    assert (command.timed_out, command.cancelled, command.denied) == (
        flags.get("timed_out", False), flags.get("cancelled", False), flags.get("denied", False),
    )


def test_a_gate_command_shaped_like_a_timeout_notes_when_the_tool_ceiling_cut_it_short() -> None:
    cut = classify_gate_result({"status": "failed", "content": "Command timed out after 600s.", "error_code": "TOOL_REFUSED"}, clamped=True)
    assert cut.timed_out and "600 s" in cut.output_tail


def test_the_gate_runner_calls_the_ordinary_tool_with_a_bounded_foreground_timeout() -> None:
    calls: list[list[dict[str, str]]] = []

    def run_tool(batch):  # noqa: ANN001, ANN201
        calls.append(batch)
        return [{"status": "succeeded", "content": "exit=0\nok", "error_code": None}]

    runner = SessionGateRunner(run_tool, "Bash")
    assert runner.run_command("pytest -q", 900, threading.Event()).exit_code == 0
    import json

    sent = json.loads(calls[0][0]["arguments"])
    assert calls[0][0]["name"] == "Bash" and sent["command"] == "pytest -q"
    assert sent["timeout_seconds"] == 600 and sent["auto_background"] is False  # the Bash ceiling; no auto-background
    cancelled = threading.Event()
    cancelled.set()
    assert runner.run_command("x", 5, cancelled).cancelled is True and len(calls) == 1  # never started
    assert SessionGateRunner(lambda batch: (_ for _ in ()).throw(RuntimeError("x")), "Bash").run_command("x", 5, threading.Event()).error_code == "GATE_TOOL_FAILED"


# ============================================================================ threads (risk 5)
MAP_PIPELINE = (
    HEADER
    + "steps:\n  - id: itens\n    agent: general\n    map: {over: [a, b, c, d, e, f], as: x, max_parallel: 4}\n    prompt: faz ${{ x }}\n"
)


class _Counting:
    """A child provider that reports how many children are inside it at once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = 0

    def stream(self, request):  # noqa: ANN001, ANN201
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        threading.Event().wait(0.05)  # hold the slot long enough for the others to pile up
        with self.lock:
            self.active -= 1
        return iter(_text_batch("ok"))


@pytest.mark.parametrize(("session_slots", "expected_peak"), [(16, 4), (2, 2)])
def test_a_map_never_has_more_children_running_than_its_cap_or_the_sessions_semaphore_allows(
    tmp_path: Path, world, session_slots: int, expected_peak: int,
) -> None:
    child = _Counting()
    session = _session(tmp_path, world, child, max_concurrent_subagents=session_slots)  # type: ignore[arg-type]
    store = PipelineStore(world[0])
    engine = _engine_for(session, store, _launcher(session, store), MAP_PIPELINE)
    detach = session.attach_pipeline_budget(engine.usage_budget)
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.setdefault("o", engine.execute()), name="engine-under-test")
    thread.start()
    thread.join(timeout=60)
    detach()
    assert not thread.is_alive(), "deadlock between the map pool and the session's child semaphore"
    assert result["o"].state == "succeeded" and child.calls == 6
    assert child.peak <= expected_peak

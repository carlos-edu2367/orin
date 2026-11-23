"""Pipeline and goal activity events: contract, redaction safety and bounds (WS-C5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.pipelines import events as pe

NEW_TYPES = {
    "PIPELINE_STARTED": "pipeline.started",
    "PIPELINE_STEP_STARTED": "pipeline.step_started",
    "PIPELINE_STEP_FINISHED": "pipeline.step_finished",
    "PIPELINE_STEP_REUSED": "pipeline.step_reused",
    "PIPELINE_STEP_RETRYING": "pipeline.step_retrying",
    "PIPELINE_GATE_EVALUATED": "pipeline.gate_evaluated",
    "PIPELINE_LOOP": "pipeline.loop",
    "PIPELINE_STALLED": "pipeline.stalled",
    "PIPELINE_DEGRADED": "pipeline.degraded",
    "PIPELINE_BUDGET_EXCEEDED": "pipeline.budget_exceeded",
    "PIPELINE_FINISHED": "pipeline.finished",
    "PIPELINE_RESUMED": "pipeline.resumed",
    "GOAL_STARTED": "goal.started",
    "GOAL_EVALUATED": "goal.evaluated",
    "GOAL_CONTINUED": "goal.continued",
    "GOAL_FINISHED": "goal.finished",
    "SUBAGENT_BUDGET_EXCEEDED": "subagent.budget_exceeded",
}
REDACTED_KEYS = {"prompt", "output", "raw_args"}


def _wrap(event: pe.PipelineEvent) -> AgentActivityEvent:
    return AgentActivityEvent(
        event_id="activity:1",
        conversation_id="conversation:1",
        turn_id="turn:1",
        execution_id="execution:1",
        user_id="user:1",
        agent_id="agent:1",
        event_type=event.type,
        sequence=1,
        summary=event.summary,
        payload=event.payload,
        created_at=datetime.now(UTC),
    )


def _every_event() -> list[pe.PipelineEvent]:
    return [
        pe.pipeline_started(
            run_id="run-1", pipeline_id="implementar-verificar", source="builtin", definition_hash="h" * 64,
            steps_total=4, input_names=["task", "files"],
        ),
        pe.step_started(
            run_id="run-1", step_id="implementar", item_key="a", attempt=1, agent="builder",
            provider="anthropic-cli", model="claude-sonnet-5-5", effort="medium", loop_count=0,
        ),
        pe.step_finished(
            run_id="run-1", step_id="implementar", item_key="a", state="succeeded", input_tokens=10,
            output_tokens=5, cost_usd=Decimal("0.0123"), cost_known=True, duration_ms=1200,
            output_ref=".orin/pipeline-runs/run-1/x.json", error_code=None,
        ),
        pe.step_reused(run_id="run-1", step_id="implementar", item_key="a", loop_count=0, from_attempt=1, input_hash="a" * 64),
        pe.step_retrying(
            run_id="run-1", step_id="implementar", item_key="a", attempt=1, next_attempt=2,
            reason="rate_limited", delay_ms=5000, error_code="CHILD_QUOTA_BLOCKED",
        ),
        pe.gate_evaluated(
            run_id="run-1", step_id="verificar", item_key="", gate_kind="command", passed=False, exit_code=1,
            verdict=None, findings_count=0,
        ),
        pe.loop_iteration(run_id="run-1", step_id="verificar", loop_count=1, max_loops=3, reason="gate_failed"),
        pe.step_stalled(run_id="run-1", step_id="implementar", item_key="a", agent_id="agent-7", idle_seconds=620, action="cancelled"),
        pe.degraded(run_id="run-1", step_id=None, reason="ask_permissions", detail="gate pede permissao", effect="continua"),
        pe.budget_exceeded(run_id="run-1", scope="pipeline", limit_kind="max_tokens", limit=1000, used=1200),
        pe.pipeline_finished(
            run_id="run-1", state="succeeded", stop_reason=None, used_tokens=15, used_cost_usd=Decimal("0.0123"),
            cost_known=True, duration_ms=5000, steps_succeeded=4, steps_failed=0,
        ),
        pe.pipeline_resumed(run_id="run-1", reused_steps=3, pending_steps=2, definition_changed=False),
        pe.goal_started(run_id="run-2", condition="todos os testes passam", max_tokens=100000, max_turns=10),
        pe.goal_evaluated(run_id="run-2", turn_index=1, met=False, reason="faltam testes", evaluator_model="claude-sonnet-5-5"),
        pe.goal_continued(run_id="run-2", turn_index=2, used_tokens=5000),
        pe.goal_finished(run_id="run-2", state="succeeded", stop_reason=None, turns=3, used_tokens=9000),
        pe.subagent_budget_exceeded(
            agent_id="agent-9", parent_agent_id="agent-1", run_id="run-1", limit_kind="max_seconds", limit=60, used=75,
        ),
    ]


def test_the_enum_carries_every_new_type_with_its_wire_name() -> None:
    for name, value in NEW_TYPES.items():
        assert AgentActivityEventType[name].value == value
        assert AgentActivityEventType(value) is AgentActivityEventType[name]


def test_existing_enum_values_are_untouched() -> None:
    assert AgentActivityEventType.TURN_STARTED.value == "turn.started"
    assert AgentActivityEventType.MERGE_CHECK_FAILED.value == "merge.check_failed"


def test_unknown_event_types_are_still_refused() -> None:
    with pytest.raises(ValueError, match="event_type"):
        _wrap(pe.PipelineEvent(type="pipeline.secret_dump", summary="x", payload={}))  # type: ignore[arg-type]


def test_every_constructor_produces_a_type_the_contract_accepts_and_uses_each_new_type() -> None:
    events = _every_event()
    seen = {event.type.value for event in events}
    assert seen == set(NEW_TYPES.values())
    for event in events:
        wrapped = _wrap(event)
        assert wrapped.event_type is event.type


def test_no_payload_uses_a_redacted_key_and_nothing_gets_redacted() -> None:
    for event in _every_event():
        assert not (set(event.payload) & REDACTED_KEYS), event.type
        wrapped = _wrap(event)
        for key, value in wrapped.payload.items():
            assert value != "[REDACTED]", (event.type.value, key)


def test_step_finished_has_the_minimum_fields_of_the_plan() -> None:
    event = pe.step_finished(
        run_id="r", step_id="s", item_key="", state="failed", input_tokens=1, output_tokens=2, cost_usd=None,
        cost_known=False, duration_ms=3, output_ref=None, error_code="E_X",
    )
    assert {
        "run_id", "step_id", "item_key", "state", "input_tokens", "output_tokens", "cost_usd", "cost_known",
        "duration_ms", "output_ref", "error_code",
    } <= set(event.payload)
    assert event.payload["cost_usd"] is None and event.payload["cost_known"] is False


def test_cost_is_json_friendly_and_unknown_cost_is_never_zero() -> None:
    known = pe.step_finished(
        run_id="r", step_id="s", item_key="", state="succeeded", input_tokens=0, output_tokens=0,
        cost_usd=Decimal("0.5"), cost_known=True, duration_ms=1, output_ref=None, error_code=None,
    )
    json.dumps(known.payload)
    assert known.payload["cost_usd"] == 0.5
    unknown = pe.step_finished(
        run_id="r", step_id="s", item_key="", state="succeeded", input_tokens=0, output_tokens=0,
        cost_usd=None, cost_known=False, duration_ms=1, output_ref=None, error_code=None,
    )
    assert unknown.payload["cost_usd"] is None


def test_step_reused_and_degraded_exist_so_nothing_is_dropped_silently() -> None:
    assert pe.step_reused(run_id="r", step_id="s", item_key="", loop_count=0, from_attempt=2, input_hash="x").type is (
        AgentActivityEventType.PIPELINE_STEP_REUSED
    )
    degraded = pe.degraded(run_id="r", step_id="s", reason="r", detail="d", effect="e")
    assert degraded.type is AgentActivityEventType.PIPELINE_DEGRADED
    assert degraded.payload["reason"] == "r"


def test_payloads_are_capped_at_eight_kilobytes_and_lists_report_their_total() -> None:
    findings = [{"id": f"f{i}", "severity": "major", "claim": "c" * 300} for i in range(200)]
    event = pe.gate_evaluated(
        run_id="r", step_id="s", item_key="", gate_kind="verifier", passed=False, exit_code=None,
        verdict="fail", findings_count=len(findings), findings=findings,
    )
    size = len(json.dumps(event.payload, ensure_ascii=False).encode("utf-8"))
    assert size <= pe.MAX_PAYLOAD_BYTES == 8 * 1024
    assert event.payload["findings_count"] == 200
    assert event.payload["findings_truncated"] is True
    assert 0 < len(event.payload["findings"]) < 200
    _wrap(event)  # the closed contract still accepts it


def test_long_free_text_is_cut_to_what_the_activity_log_accepts() -> None:
    event = pe.degraded(run_id="r", step_id=None, reason="x" * 5000, detail="y" * 5000, effect="z" * 5000)
    assert all(len(value) <= 500 for value in event.payload.values() if isinstance(value, str))
    wrapped = _wrap(event)
    assert wrapped.payload["reason"].startswith("xxx")


def test_summaries_are_portuguese_non_blank_and_bounded() -> None:
    for event in _every_event():
        assert event.summary.strip()
        assert len(event.summary) <= 512
    assert "Pipeline" in pe.pipeline_started(
        run_id="r", pipeline_id="p", source="user", definition_hash="h", steps_total=1, input_names=[]
    ).summary


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _record(self, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        self.calls.append((event_type, summary, dict(payload or {}), agent_id, parent_agent_id))


def test_the_session_sink_records_through_turn_session_with_the_child_agent_id() -> None:
    session = _FakeSession()
    sink = pe.TurnSessionEventSink(session)
    event = pe.step_started(
        run_id="r", step_id="s", item_key="", attempt=1, agent=None, provider=None, model=None, effort=None, loop_count=0
    )
    sink.emit(event)
    sink.emit(event, agent_id="child-1", parent_agent_id="main")
    assert session.calls[0][0] is AgentActivityEventType.PIPELINE_STEP_STARTED
    assert session.calls[0][3] is None
    assert session.calls[1][3:] == ("child-1", "main")
    assert session.calls[1][2]["step_id"] == "s"


def test_the_sink_never_lets_a_recording_failure_break_the_engine() -> None:
    class Broken:
        def _record(self, *_args, **_kwargs) -> None:
            raise RuntimeError("store gone")

    pe.TurnSessionEventSink(Broken()).emit(
        pe.pipeline_resumed(run_id="r", reused_steps=0, pending_steps=0, definition_changed=False)
    )

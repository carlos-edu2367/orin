"""Retries: causes, exponential backoff on the injected clock, cancel during the wait, never-retried codes (WS-C6)."""

from __future__ import annotations

import pytest

from agentos.pipelines.fakes import Reply
from agentos.pipelines.retries import backoff_delay

from .engine_support import make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  objetivo: {type: string, default: "x"}
"""


def _step(retries: str, extra: str = "") -> str:
    return pipeline(
        f"""
        - id: a
          agent: general
          prompt: faz
          retries: {retries}
          {extra}
        """,
        HEADER,
    )


def _fail(code: str = "BOOM") -> Reply:
    return Reply(status="failed", error_code=code, text="")


def test_backoff_sequence_is_5_10_20_40_60_60() -> None:
    assert [backoff_delay(n) for n in range(1, 7)] == [5, 10, 20, 40, 60, 60]


def test_three_retries_wait_5_10_20_without_sleeping(tmp_path) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [failed]}"))
    h.launcher.script("a", _fail())  # repeats forever
    outcome = h.run()
    assert outcome.state == "failed"
    assert h.launcher.launch_count_by_step["a"] == 4
    assert h.clock.waits == [5.0, 10.0, 20.0]
    attempts = [row.attempt for row in h.rows() if row.step_id == "a"]
    assert attempts == [1, 2, 3, 4]  # each attempt is a new journal row
    assert len(h.sink.of("pipeline.step_retrying")) == 3


def test_success_on_the_second_attempt_stops_retrying(tmp_path) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [failed]}"))
    h.launcher.script("a", _fail(), Reply(text="agora foi"))
    assert h.run().state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 2
    assert [row.state for row in h.rows()] == ["failed", "succeeded"]


def test_retries_on_filters_by_cause(tmp_path) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [stalled]}"))
    h.launcher.script("a", _fail())
    assert h.run().state == "failed"
    assert h.launcher.launch_count_by_step["a"] == 1
    assert h.clock.waits == []


def test_schema_invalid_cause_is_retried_when_declared(tmp_path) -> None:
    text = _step("{max: 1, on: [schema_invalid]}", "output_schema: {type: object, required: [x]}")
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="ruim", structured_output={"y": 1}), Reply(text="ok", structured_output={"x": 1}))
    assert h.run().state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 2
    first = h.rows()[0]
    assert first.state == "failed" and "esquema" in (first.error or "")


def test_schema_invalid_without_the_cause_declared_fails(tmp_path) -> None:
    text = _step("{max: 1, on: [failed]}", "output_schema: {type: object, required: [x]}")
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="ruim", structured_output={"y": 1}))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "SCHEMA_INVALID"
    assert h.launcher.launch_count_by_step["a"] == 1


def test_cancel_during_the_wait_wakes_it_at_once(tmp_path) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [failed]}"))
    h.launcher.script("a", _fail())
    engine = h.engine()
    h.clock.on_wait = lambda index, timeout: engine.cancel()
    outcome = engine.execute()
    assert outcome.state == "cancelled"
    assert h.clock.waits == [5.0]  # the sleep did not run its course: the event was already set
    assert h.launcher.launch_count_by_step["a"] == 1


@pytest.mark.parametrize(
    "code",
    [
        "CHILD_PROVIDER_PINNED",
        "CHILD_PROVIDER_UNAVAILABLE",
        "CHILD_MODEL_NOT_FAVORITE",
        "CHILD_QUOTA_BLOCKED",
        "GATE_PERMISSION_DENIED",
    ],
)
def test_configuration_errors_are_never_retried(tmp_path, code) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [failed, stalled, schema_invalid, gate_failed]}"))
    h.launcher.script("a", _fail(code))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == code
    assert h.launcher.launch_count_by_step["a"] == 1
    assert h.clock.waits == []


def test_child_budget_overrun_is_not_retried(tmp_path) -> None:
    h = make_harness(tmp_path, _step("{max: 3, on: [failed]}"))
    h.launcher.script("a", Reply(status="budget_exceeded", error_code="SUBAGENT_BUDGET_EXCEEDED", text=""))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert h.launcher.launch_count_by_step["a"] == 1


def test_default_retries_apply_to_steps_without_their_own(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        """,
        HEADER + "defaults: {retries: {max: 1, on: [failed]}}\n",
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", _fail(), Reply())
    assert h.run().state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 2

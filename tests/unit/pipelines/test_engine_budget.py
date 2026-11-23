"""Pipeline budget: tokens, cost and time as a hard ceiling, per-agent caps, default ceiling, quota (WS-C6, AC6)."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from agentos.pipelines.fakes import Reply
from agentos.pipelines.ports import QuotaAssessment
from agentos.pipelines.resume import ResumeRefused

from .engine_support import make_harness
from .helpers import pipeline


def _header(budget: str) -> str:
    return f"""\
version: 1
id: teste
name: Teste
{budget}
inputs:
  objetivo: {{type: string, default: "x"}}
"""


MAP4 = """
- id: itens
  agent: general
  map: {over: [a, b, c, d], as: x, max_parallel: 3}
  prompt: faz ${{ x }}
"""


def _hold_two_then_overspend(tokens: int, cost: float | None = 0.01):
    """Behaviors for items 0..2: items 1 and 2 hang; item 0 waits for both to be alive, then overspends."""
    alive = threading.Barrier(3, timeout=10)

    def spender(call, control):
        alive.wait()
        return Reply(text="gastou", input_tokens=tokens, output_tokens=0, cost_usd=cost)

    def hanger(call, control):
        alive.wait()
        return control.hang()

    return spender, hanger


def _assert_nothing_left_running(h) -> None:
    assert h.launcher.all_finished(), h.launcher.unfinished()


def test_token_budget_cancels_every_child_in_flight_and_launches_no_more(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP4, _header("budget: {max_tokens: 100}")))
    spender, hanger = _hold_two_then_overspend(200)
    h.launcher.script("itens", spender, index=0)
    h.launcher.script("itens", hanger, index=1)
    h.launcher.script("itens", hanger, index=2)
    outcome = h.run()
    assert outcome.state == "budget_exceeded" and outcome.stop_reason == "budget"
    assert h.launcher.launch_count_by_step["itens"] == 3  # the fourth item never started
    assert len({agent for agent, _reason in h.launcher.cancelled}) == 2  # both hanging children were cancelled
    _assert_nothing_left_running(h)
    interrupted = [row for row in h.rows() if row.state == "interrupted"]
    assert len(interrupted) == 2 and all(row.error == "budget_exceeded" for row in interrupted)
    assert h.state() == "budget_exceeded"
    events = h.sink.of("pipeline.budget_exceeded")
    assert len(events) == 1 and events[0].payload["limit_kind"] == "tokens"


def test_cost_budget_trips_with_known_cost(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP4, _header("budget: {max_cost_usd: 0.05}")))
    spender, hanger = _hold_two_then_overspend(10, cost=0.10)
    h.launcher.script("itens", spender, index=0)
    h.launcher.script("itens", hanger, index=1)
    h.launcher.script("itens", hanger, index=2)
    outcome = h.run()
    assert outcome.state == "budget_exceeded"
    assert h.sink.of("pipeline.budget_exceeded")[0].payload["limit_kind"] == "cost"
    _assert_nothing_left_running(h)


def test_time_budget_uses_the_injected_clock(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        """,
        _header("budget: {max_minutes: 1}"),
    )
    h = make_harness(tmp_path, text, advance_on_wait=True)
    h.launcher.script("a", lambda call, control: control.hang())
    outcome = h.run(poll_seconds=30.0)
    assert outcome.state == "budget_exceeded"
    assert h.sink.of("pipeline.budget_exceeded")[0].payload["limit_kind"] == "time"
    _assert_nothing_left_running(h)
    assert h.rows()[0].state == "interrupted" and h.rows()[0].error == "budget_exceeded"


def test_unknown_cost_does_not_trip_the_cost_limit_but_is_reported(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        """,
        _header("budget: {max_cost_usd: 0.0001}"),
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="ok", cost_usd=None))
    outcome = h.run()
    assert outcome.state == "succeeded"
    assert outcome.cost_known is False
    finished = h.sink.of("pipeline.step_finished")[0]
    assert finished.payload["cost_known"] is False
    assert h.sink.of("pipeline.finished")[0].payload["cost_known"] is False


def test_step_budget_reaches_the_call_and_is_capped_by_what_the_pipeline_has_left(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        - id: b
          agent: general
          prompt: faz de novo
          budget: {max_tokens: 400, max_minutes: 2}
        """,
        _header("budget: {max_tokens: 1000, max_minutes: 120}"),
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="ok", input_tokens=800, output_tokens=0))
    assert h.run().state == "succeeded"
    first, second = h.launcher.calls
    assert first.budget["max_tokens"] == 1000  # the whole pipeline is still available to the first step
    assert second.budget["max_tokens"] == 200  # min(its own 400, the 200 that are left)
    assert second.budget["max_seconds"] == 120  # G26: 2 minutes in the YAML are 120 seconds for ``Agent``


def test_step_budget_alone_is_passed_through(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
          budget: {max_tokens: 400}
        """,
        _header("budget: {max_tokens: 5000}"),
    )
    h = make_harness(tmp_path, text)
    h.run()
    assert h.launcher.calls[0].budget["max_tokens"] == 400


def test_pipeline_without_budget_gets_the_default_ceiling_and_an_event(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        """,
        _header(""),
    )
    h = make_harness(tmp_path, text)
    engine = h.engine()
    limits = engine.budget.limits
    assert (limits.max_tokens, limits.max_cost_usd, limits.max_seconds) == (2_000_000, 15.0, 7200.0)
    assert engine.execute().state == "succeeded"
    degraded = h.sink.of("pipeline.degraded")
    assert degraded and degraded[0].payload["reason"] == "budget_default"


def test_live_metering_launcher_feeds_the_budget_and_the_engine_does_not_double_count(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        - id: b
          agent: general
          prompt: nunca
        """,
        _header("budget: {max_tokens: 100}"),
    )
    h = make_harness(tmp_path, text)
    h.launcher.live_metering = True
    engine = h.engine()

    def session_like(call, control):
        engine.usage_budget.add(500, 0.0)  # what ``TurnSession.record_usage`` does for every call
        return Reply(text="gastou", input_tokens=500, output_tokens=0)

    h.launcher.script("a", session_like)
    outcome = engine.execute()
    assert outcome.state == "budget_exceeded"
    assert engine.usage_budget.tokens == 500  # not 1000: the reported tokens were not charged again
    assert h.launcher.launch_count_by_step["b"] == 0


def test_resume_of_a_budget_exceeded_run_needs_an_explicit_override(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP4, _header("budget: {max_tokens: 100}")))
    spender, hanger = _hold_two_then_overspend(200)
    h.launcher.script("itens", spender, index=0)
    h.launcher.script("itens", hanger, index=1)
    h.launcher.script("itens", hanger, index=2)
    assert h.run().state == "budget_exceeded"
    with pytest.raises(ResumeRefused) as caught:
        h.engine(resume=True).execute()
    assert caught.value.code == "BUDGET_OVERRIDE_REQUIRED"
    h.launcher.script("itens", Reply(text="barato", input_tokens=1, output_tokens=0), index=1)
    h.launcher.script("itens", Reply(text="barato", input_tokens=1, output_tokens=0), index=2)
    h.launcher.script("itens", Reply(text="barato", input_tokens=1, output_tokens=0), index=3)
    resumed = h.engine(resume=True, budget_override=True).execute()
    assert resumed.state == "succeeded"


def test_resume_counts_what_the_run_already_spent(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        - id: b
          agent: general
          prompt: depois
        """,
        _header("budget: {max_tokens: 300}"),
    )
    h = make_harness(tmp_path, text)
    h.store.add_usage("run-1", tokens=250)
    h.store.set_run_state("run-1", "interrupted", stop_reason="paused")
    h.launcher.script("a", Reply(text="ok", input_tokens=100, output_tokens=0))
    outcome = h.engine(resume=True).execute()
    assert outcome.state == "budget_exceeded"  # 250 already used + 100 > 300
    assert h.launcher.launch_count_by_step["b"] == 0


def test_overrun_on_the_last_step_keeps_the_result_with_a_caveat(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        """,
        _header("budget: {max_tokens: 100}"),
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="ok", input_tokens=500, output_tokens=0))
    outcome = h.run()
    assert outcome.state == "succeeded"  # R5: the child finished its work in the call that went over
    assert any("orçamento" in caveat for caveat in outcome.caveats)
    assert len(h.sink.of("pipeline.budget_exceeded")) == 1


def test_run_totals_equal_the_sum_of_the_journal(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP4, _header("budget: {max_tokens: 100000}")))
    h.launcher.script("itens", Reply(text="ok", input_tokens=7, output_tokens=3, cost_usd=0.5))
    assert h.run().state == "succeeded"
    run = h.store.get_run("run-1")
    assert run.used_tokens == 4 * 10
    assert run.used_cost_usd == Decimal("2.0")
    summed = sum(row.input_tokens + row.output_tokens for row in h.rows())
    assert summed == run.used_tokens


def test_quota_block_fails_the_step_without_retry_and_warn_is_only_a_notice(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
          retries: {max: 3, on: [failed]}
        """,
        _header("budget: {max_tokens: 100000}\ndefaults: {provider: anthropic}"),
    )
    blocked = make_harness(tmp_path / "blocked", text)
    blocked.quota.assessment = QuotaAssessment(blocked=("anthropic",))
    outcome = blocked.run()
    assert outcome.state == "failed" and outcome.error_code == "CHILD_QUOTA_BLOCKED"
    assert blocked.launcher.launches == 0
    warned = make_harness(tmp_path / "warned", text)
    warned.quota.assessment = QuotaAssessment(warn=("anthropic",))
    assert warned.run().state == "succeeded"
    assert any(e.payload["reason"] == "quota_warn" for e in warned.sink.of("pipeline.degraded"))

"""``map`` fan-out: parallel ceiling, ordering, range, fail_fast, partial resume and the agent cap (WS-C6, AC4)."""

from __future__ import annotations

import threading

import pytest

from agentos.pipelines.fakes import Reply
from agentos.pipelines.steps import item_key_for

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


def _map_pipeline(items: str, parallel: int = 2, fail_fast: str = "") -> str:
    return pipeline(
        f"""
        - id: itens
          agent: general
          map:
            over: {items}
            as: x
            max_parallel: {parallel}{fail_fast}
          prompt: faz ${{{{ x }}}}
        """,
        HEADER,
    )


LITERAL6 = "[a, b, c, d, e, f]"


def test_six_items_never_exceed_two_children_at_once(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline(LITERAL6, 2))
    pair = threading.Barrier(2, timeout=10)

    def behavior(call, control):
        pair.wait()  # two children must be alive at the same time for the barrier to open
        return Reply(text=f"feito {call.item}")

    h.launcher.script("itens", behavior)
    outcome = h.run()
    assert outcome.state == "succeeded", outcome
    assert h.launcher.max_running == 2
    assert h.launcher.launch_count_by_step["itens"] == 6


def test_output_is_in_item_order_not_completion_order(tmp_path) -> None:
    text = pipeline(
        """
        - id: itens
          agent: general
          map: {over: [a, b], as: x, max_parallel: 2}
          prompt: faz ${{ x }}
        - id: depois
          agent: general
          prompt: junta ${{ steps.itens.output }}
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    second_done = threading.Event()

    def first(call, control):
        assert second_done.wait(10)  # the first item finishes after the second one
        return Reply(text="R-primeiro")

    def second(call, control):
        second_done.set()
        return Reply(text="R-segundo")

    h.launcher.script("itens", first, index=0)
    h.launcher.script("itens", second, index=1)
    assert h.run().state == "succeeded"
    prompt = next(call.prompt for call in h.launcher.calls if call.step_id == "depois")
    assert prompt.index("R-primeiro") < prompt.index("R-segundo")
    assert '["R-primeiro","R-segundo"]' in prompt


def test_item_keys_follow_index_and_hash(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline("[a, b]", 1))
    h.run()
    keys = sorted(row.item_key for row in h.rows())
    assert keys == [item_key_for(0, "a"), item_key_for(1, "b")]
    assert keys[0].startswith("000-") and len(keys[0]) == 12


def _plan_then_map(count: object) -> str:
    return pipeline(
        """
        - id: plano
          agent: plan
          prompt: planeja
          output_schema: {type: object}
        - id: itens
          agent: general
          map: {over: "${{ steps.plano.output.itens }}", as: x, max_parallel: 2}
          prompt: faz ${{ x }}
        """,
        HEADER,
    )


def test_more_than_fifty_items_fails_at_run_time(tmp_path) -> None:
    h = make_harness(tmp_path, _plan_then_map(51))
    h.launcher.script("plano", Reply(text="plano", structured_output={"itens": list(range(51))}))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "E_MAP_RANGE"
    assert h.launcher.launch_count_by_step["itens"] == 0


def test_over_that_is_not_a_list_fails_with_clear_message(tmp_path) -> None:
    h = make_harness(tmp_path, _plan_then_map("x"))
    h.launcher.script("plano", Reply(text="plano", structured_output={"itens": "nao e lista"}))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "E_MAP_TYPE"
    assert "lista" in outcome.message


def test_fail_fast_does_not_start_new_items_but_lets_running_ones_finish(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline(LITERAL6, 2))
    slow_started = threading.Event()
    release = threading.Event()

    def failing(call, control):
        assert slow_started.wait(10)
        return Reply(status="failed", error_code="BOOM", text="")

    def slow(call, control):
        slow_started.set()
        assert release.wait(10)
        return Reply(text="terminou mesmo assim")

    h.launcher.script("itens", failing, index=0)
    h.launcher.script("itens", slow, index=1)
    result: dict = {}
    runner = threading.Thread(target=lambda: result.setdefault("o", h.run()))
    runner.start()
    assert h.sink.wait_for(
        lambda events: any(
            e.type.value == "pipeline.step_finished" and e.payload["item_key"].startswith("000-") for e in events
        )
    )
    release.set()
    runner.join(timeout=20)
    assert not runner.is_alive()
    outcome = result["o"]
    assert outcome.state == "failed" and outcome.error_code == "BOOM"
    assert h.launcher.launch_count_by_step["itens"] == 2  # the other four never started
    done = {row.item_key[:3]: row.state for row in h.rows()}
    assert done == {"000": "failed", "001": "succeeded"}


def test_fail_fast_false_runs_every_item(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline(LITERAL6, 2, "\n            fail_fast: false"))
    h.launcher.script("itens", Reply(status="failed", error_code="BOOM", text=""), index=2)
    outcome = h.run()
    assert outcome.state == "failed"
    assert h.launcher.launch_count_by_step["itens"] == 6
    assert "1 de 6" in outcome.message


def test_resume_redoes_only_failed_and_pending_items(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline(LITERAL6, 1))
    h.launcher.script("itens", Reply(status="failed", error_code="BOOM", text=""), index=2)
    first = h.run()
    assert first.state == "failed"
    assert h.launcher.launch_count_by_step["itens"] == 3  # items 0, 1 and the failed 2; 3 to 5 stay pending
    h.launcher.script("itens", Reply(text="agora vai"), index=2)
    second = h.resume()
    assert second.state == "succeeded"
    assert h.launcher.launch_count_by_step["itens"] == 3 + 4  # item 2 again, then 3, 4, 5
    reused = {e.payload["item_key"][:3] for e in h.sink.of("pipeline.step_reused")}
    assert reused == {"000", "001"}


# ------------------------------------------------------------------ review fixes (S1, C8 gaps)


def _finished_of(h, step_id: str):
    return [e for e in h.sink.of("pipeline.step_finished") if e.payload["step_id"] == step_id]


def test_a_map_that_fails_before_launching_is_journaled_and_announced(tmp_path) -> None:
    for value, code in ((list(range(51)), "E_MAP_RANGE"), ("nao e lista", "E_MAP_TYPE")):
        h = make_harness(tmp_path / code, _plan_then_map(0))
        h.launcher.script("plano", Reply(text="plano", structured_output={"itens": value}))
        outcome = h.run()
        assert outcome.error_code == code
        rows = [row for row in h.rows() if row.step_id == "itens"]
        assert len(rows) == 1 and rows[0].state == "failed" and rows[0].error
        started = [e for e in h.sink.of("pipeline.step_started") if e.payload["step_id"] == "itens"]
        finished = _finished_of(h, "itens")
        assert len(started) == 1 and len(finished) == 1
        assert finished[0].payload["state"] == "failed" and finished[0].payload["error_code"] == code


def test_a_quota_block_is_journaled_and_announced(tmp_path) -> None:
    from agentos.pipelines.ports import QuotaAssessment

    text = pipeline("- id: a\n  agent: general\n  prompt: faz\n", HEADER.replace("inputs:", "defaults: {provider: anthropic}\ninputs:"))
    h = make_harness(tmp_path, text)
    h.quota.assessment = QuotaAssessment(blocked=("anthropic",))
    outcome = h.run()
    assert outcome.error_code == "CHILD_QUOTA_BLOCKED" and h.launcher.launches == 0
    rows = [row for row in h.rows() if row.step_id == "a"]
    assert [row.state for row in rows] == ["failed"] and "cota" in (rows[0].error or "")
    assert _finished_of(h, "a")[0].payload["error_code"] == "CHILD_QUOTA_BLOCKED"


PROVIDERS = """
- id: itens
  agent: general
  map:
    over: [{provider: anthropic, model: m1}, {provider: openai-codex, model: m2}]
    as: r
    max_parallel: 1
    fail_fast: false
  provider: ${{ r.provider }}
  model: ${{ r.model }}
  prompt: faz ${{ r.model }}
"""


def test_quota_is_assessed_per_item_once_the_provider_of_the_item_is_known(tmp_path) -> None:
    from agentos.pipelines.ports import QuotaAssessment

    h = make_harness(tmp_path, pipeline(PROVIDERS, HEADER))
    h.quota.assessment = QuotaAssessment(blocked=("openai-codex",))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "CHILD_QUOTA_BLOCKED"
    assert [call.provider for call in h.launcher.calls] == ["anthropic"]  # the second item never launched
    assert ("openai-codex",) in h.quota.asked


def test_quota_warning_of_a_templated_provider_is_announced_once_per_provider(tmp_path) -> None:
    from agentos.pipelines.ports import QuotaAssessment

    h = make_harness(tmp_path, pipeline(PROVIDERS, HEADER))
    h.quota.assessment = QuotaAssessment(warn=("openai-codex",))
    assert h.run().state == "succeeded"
    warns = [e for e in h.sink.of("pipeline.degraded") if e.payload["reason"] == "quota_warn"]
    assert len(warns) == 1 and "openai-codex" in warns[0].payload["detail"]


@pytest.mark.parametrize("bad", ["null", "5", "{a: 1}", "[x]", "true"])
def test_an_item_whose_provider_is_not_text_fails_the_step_without_launching(tmp_path, bad) -> None:
    text = pipeline(
        f"""
        - id: itens
          agent: general
          map: {{over: [{{provider: {bad}, model: m1}}], as: r, max_parallel: 1}}
          provider: ${{{{ r.provider }}}}
          model: ${{{{ r.model }}}}
          prompt: faz
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "E_RENDER"
    assert "provider" in outcome.message
    assert h.launcher.launches == 0
    row = next(row for row in h.rows() if row.step_id == "itens")
    assert row.state == "failed"


def test_a_missing_model_field_of_an_item_fails_the_step_without_launching(tmp_path) -> None:
    text = pipeline(
        """
        - id: itens
          agent: general
          map: {over: [{provider: anthropic}], as: r, max_parallel: 1}
          provider: ${{ r.provider }}
          model: ${{ r.model }}
          prompt: faz
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    outcome = h.run()
    assert outcome.error_code == "E_RENDER" and "model" in outcome.message and h.launcher.launches == 0


def test_agent_cap_stops_the_run(tmp_path) -> None:
    h = make_harness(tmp_path, _map_pipeline(LITERAL6, 1))
    outcome = h.run(max_agents=3)
    assert outcome.state == "failed" and outcome.stop_reason == "agent_cap"
    assert h.launcher.launch_count_by_step["itens"] == 3

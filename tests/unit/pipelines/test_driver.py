"""WS-C7: ``PipelineDriver`` turns a journal run into a finished turn (summary, state, permission mode, cancel)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from agentos.pipelines import driver as drv
from agentos.pipelines.driver import DriverPorts, PipelineDriver, PipelineRequest, parse_pipeline_request
from agentos.pipelines.fakes import Reply, SimulatedCrash
from agentos.pipelines.ports import CommandResult
from agentos.pipelines.validator import ValidationContext

from .engine_support import PLAN_OUTPUT, make_harness
from .helpers import fixture_text, pipeline

TURN: dict[str, Any] = {
    "turn_id": "turn-1", "conversation_id": "c1", "user_id": "u1", "provider": "anthropic", "model_id": "claude-sonnet-5-5",
}
HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  testes: {type: string, default: "pytest -q"}
"""
TWO = """
- id: a
  agent: general
  prompt: um
- id: b
  agent: general
  prompt: dois
"""


def _harness(tmp_path, text: str | None = None, **kw):
    body = text if text is not None else fixture_text("implementar-verificar.yaml")
    h = make_harness(tmp_path, body, **kw)
    if text is None:
        h.launcher.script("plano", Reply(text="plano", structured_output=PLAN_OUTPUT))
    return h


def _ports(h, **overrides: Any) -> DriverPorts:
    values: dict[str, Any] = dict(
        launcher=h.launcher, gate_runner=h.gates, sink=h.sink, verifier=h.verifier, change_scope=h.scope, quota=h.quota,
        clock=h.clock, permission_mode="accept_edits", cancelled=lambda: False,
        default_provider="anthropic", default_model="claude-sonnet-5-5",
    )
    values.update(overrides)
    return DriverPorts(**values)


def _drive(h, *, request: PipelineRequest | None = None, turn: dict[str, Any] | None = None, **overrides: Any):
    driver = PipelineDriver(
        h.store, request or PipelineRequest(h.run_id), turn or TURN, _ports(h, **overrides),
        poll_seconds=0.02, engine_poll_seconds=0.05, engine_monitor_seconds=0.02,
    )
    return driver.run()


# ------------------------------------------------------------------------- request
def test_the_turn_text_names_the_run_and_the_flags() -> None:
    assert parse_pipeline_request("/pipeline run-1") == PipelineRequest("run-1")
    assert parse_pipeline_request("  /pipeline resume run-1 ") == PipelineRequest("run-1", resume=True)
    assert parse_pipeline_request("/pipeline resume run-1 --rerun-gates --budget-override") == PipelineRequest(
        "run-1", resume=True, rerun_gates=True, budget_override=True
    )
    for text in ("", "/pipeline", "/pipeline resume", "pipeline run-1", "/pipelines run-1", "/pipeline a b", "/pipeline run-1 --x",
                 "/pipeline ../x", "/pipeline run-1 --rerun-gates"):
        assert parse_pipeline_request(text) is None, text


# -------------------------------------------------------------------------- outcome
def test_a_run_that_succeeds_ends_completed_with_the_summary_table(tmp_path) -> None:
    h = _harness(tmp_path)
    result = _drive(h)
    assert result.state == "completed" and result.error_code is None
    assert h.store.get_run("run-1").state == "succeeded"
    assert h.store.get_run("run-1").turn_id == "turn-1"
    text = result.markdown
    assert "| Etapa | Estado | Tokens | Custo (US$) |" in text
    for step in ("plano", "implementar", "verificar"):
        assert f"| {step} |" in text
    assert "execução `run-1`" in text and "implementar-verificar" in text
    assert "Total:" in text
    assert h.launcher.launches == 3  # plano + two implementar items


def test_ignored_failures_end_completed_with_caveats(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: um
          on_fail: continue
        - id: b
          agent: general
          prompt: dois
        """,
        HEADER,
    )
    h = _harness(tmp_path, text)
    h.launcher.script("a", Reply(status="failed", error_code="AGENT_FAILED"))
    result = _drive(h)
    assert result.state == "completed_with_caveats"
    assert "Ressalvas" in result.markdown and "a etapa a falhou" in result.markdown


@pytest.mark.parametrize(
    ("gates", "code"),
    [([CommandResult(1, "FAILED a")], "PIPELINE_STEP_FAILED")],
)
def test_a_failed_run_fails_the_turn_with_the_stop_reason_as_code(tmp_path, gates, code) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: um
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
        """,
        HEADER,
    )
    h = _harness(tmp_path, text)
    h.gates.script(*gates)
    result = _drive(h)
    assert result.state == "failed" and result.error_code == code
    assert "falhou" in result.markdown and "`g`" in result.markdown
    assert h.store.get_run("run-1").state == "failed"


def test_a_run_that_stopped_on_its_budget_fails_with_a_budget_code(tmp_path) -> None:
    text = pipeline(TWO, "version: 1\nid: teste\nname: T\nbudget: {max_tokens: 20}\n")
    h = _harness(tmp_path, text)
    h.launcher.script("a", Reply(input_tokens=30, output_tokens=0), index=None)
    result = _drive(h)
    assert result.state == "failed" and result.error_code == "PIPELINE_BUDGET"
    assert h.store.get_run("run-1").state == "budget_exceeded"
    assert "orçamento" in result.markdown


def test_a_turn_cancelled_while_a_child_runs_cancels_the_engine(tmp_path) -> None:
    text = pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER)
    h = _harness(tmp_path, text)
    h.launcher.script("a", lambda call, control: control.hang())
    flag = threading.Event()
    ready = threading.Event()
    h.launcher.on_launch = lambda call: ready.set()

    def flip() -> None:
        assert ready.wait(10)
        flag.set()

    threading.Thread(target=flip, daemon=True).start()
    result = _drive(h, cancelled=flag.is_set)
    assert result.state == "cancelled"
    assert h.store.get_run("run-1").state == "cancelled"
    assert h.launcher.all_finished()


def test_pausing_through_the_registry_drains_and_ends_the_turn_without_failure(tmp_path) -> None:
    h = _harness(tmp_path, pipeline(TWO, HEADER))
    entered = threading.Event()
    release = threading.Event()

    def slow(call, control):
        entered.set()
        assert release.wait(10)
        return Reply()

    h.launcher.script("a", slow)
    box: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", _drive(h)), name="driver-under-test")
    thread.start()
    assert entered.wait(10)
    assert drv.pause_run("run-1") is True
    release.set()
    thread.join(timeout=20)
    assert not thread.is_alive()
    result = box["r"]
    assert result.state == "completed_with_caveats" and result.error_code == "PIPELINE_PAUSED"
    assert "/pipeline resume run-1" in result.markdown
    run = h.store.get_run("run-1")
    assert run.state == "interrupted" and run.stop_reason == "paused"
    assert drv.pause_run("run-1") is False and drv.cancel_run("run-1") is False  # nothing live any more


# --------------------------------------------------------------------- guards
def test_plan_mode_refuses_and_runs_nothing(tmp_path) -> None:
    h = _harness(tmp_path)
    result = _drive(h, permission_mode="plan_mode")
    assert result.state == "failed" and result.error_code == "PIPELINE_PLAN_MODE"
    assert "modo de planejamento" in result.markdown.lower()
    assert h.launcher.launches == 0 and h.gates.commands == []
    run = h.store.get_run("run-1")
    assert run.state == "interrupted" and run.stop_reason == "plan_mode"  # resumable once the person leaves plan mode


def test_the_driver_never_raises_the_permission_mode(tmp_path) -> None:
    """``ask_permissions`` stays as it is: the engine only warns (``pipeline.degraded``)."""
    h = _harness(tmp_path, pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER))
    ports = _ports(h, permission_mode="ask_permissions")
    PipelineDriver(h.store, PipelineRequest("run-1"), TURN, ports, poll_seconds=0.02, engine_poll_seconds=0.05,
                   engine_monitor_seconds=0.02).run()
    degraded = [e for e in h.sink.of("pipeline.degraded") if e.payload.get("reason") == "ask_permissions"]
    assert degraded and ports.permission_mode == "ask_permissions"


def test_a_run_of_another_user_or_conversation_is_refused(tmp_path) -> None:
    h = _harness(tmp_path)
    other_user = _drive(h, turn={**TURN, "user_id": "intruder"})
    assert other_user.error_code == "PIPELINE_RUN_FORBIDDEN" and h.launcher.launches == 0
    other_conversation = _drive(h, turn={**TURN, "conversation_id": "c2"})
    assert other_conversation.error_code == "PIPELINE_RUN_FORBIDDEN"
    assert h.store.get_run("run-1").state == "running"  # untouched


def test_an_unknown_run_fails_the_turn_with_a_clear_message(tmp_path) -> None:
    h = _harness(tmp_path)
    result = _drive(h, request=PipelineRequest("no-such-run"))
    assert result.state == "failed" and result.error_code == "PIPELINE_RUN_NOT_FOUND" and "no-such-run" in result.markdown


def test_a_finished_run_is_not_started_again(tmp_path) -> None:
    h = _harness(tmp_path, pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER))
    assert _drive(h).state == "completed"
    launches = h.launcher.launches
    again = _drive(h, request=PipelineRequest("run-1", resume=True), turn={**TURN, "turn_id": "turn-2"})  # another turn: a requeue of the same one is F8
    assert again.state == "failed" and again.error_code == "PIPELINE_ALREADY_DONE" and h.launcher.launches == launches
    fresh = _drive(h, turn={**TURN, "turn_id": "turn-3"})
    assert fresh.error_code == "PIPELINE_ALREADY_DONE"


def test_a_failed_run_needs_resume_to_run_again(tmp_path) -> None:
    h = _harness(tmp_path, pipeline(TWO, HEADER))
    h.launcher.script("b", Reply(status="failed", error_code="AGENT_FAILED"), Reply())
    assert _drive(h).state == "failed"
    refused = _drive(h)
    assert refused.error_code == "PIPELINE_NOT_RUNNING" and "/pipeline resume run-1" in refused.markdown
    resumed = _drive(h, request=PipelineRequest("run-1", resume=True))
    assert resumed.state == "completed"
    assert h.launcher.launch_count_by_step["a"] == 1  # reused


def test_a_run_already_executing_in_this_process_is_refused(tmp_path) -> None:
    h = _harness(tmp_path, pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER))
    entered, release = threading.Event(), threading.Event()

    def slow(call, control):
        entered.set()
        assert release.wait(10)
        return Reply()

    h.launcher.script("a", slow)
    box: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", _drive(h)))
    thread.start()
    assert entered.wait(10)
    second = _drive(h)
    assert second.error_code == "PIPELINE_ALREADY_ACTIVE"
    release.set()
    thread.join(timeout=20)
    assert box["r"].state == "completed"


# ----------------------------------------------------------------- recovery/resume
def test_a_requeued_turn_resumes_the_same_run_without_redoing_finished_steps(tmp_path) -> None:
    """AC3 with no button: the worker died inside the gate, ``recover_stale`` requeued the turn, the driver resumes."""
    h = _harness(tmp_path)

    def crash(command, timeout, cancel):
        raise SimulatedCrash()

    h.gates.script(crash)
    with pytest.raises(SimulatedCrash):
        h.run()
    assert h.state() == "running"
    before = dict(h.launcher.launch_count_by_step)
    result = _drive(h)  # the requeued turn: no ``resume`` word in the request
    assert result.state == "completed"
    assert dict(h.launcher.launch_count_by_step) == before
    assert h.sink.of("pipeline.resumed") and h.sink.of("pipeline.step_reused")


def test_a_budget_exceeded_run_needs_the_explicit_override(tmp_path) -> None:
    text = pipeline(TWO, "version: 1\nid: teste\nname: T\nbudget: {max_tokens: 20}\n")
    h = _harness(tmp_path, text)
    h.launcher.script("a", Reply(input_tokens=30, output_tokens=0))
    assert _drive(h).error_code == "PIPELINE_BUDGET"
    refused = _drive(h, request=PipelineRequest("run-1", resume=True))
    assert refused.state == "failed" and refused.error_code == "PIPELINE_BUDGET_OVERRIDE_REQUIRED"
    assert "--budget-override" in refused.markdown
    assert h.store.get_run("run-1").state == "budget_exceeded"
    h.launcher.script("a", Reply(input_tokens=3, output_tokens=0))
    h.launcher.script("b", Reply(input_tokens=3, output_tokens=0))
    ok = _drive(h, request=PipelineRequest("run-1", resume=True, budget_override=True))
    assert ok.state == "completed"


def test_the_run_is_executed_from_its_snapshot_not_from_the_file_on_disk(tmp_path) -> None:
    h = _harness(tmp_path)
    assert h.store.get_run("run-1").definition_snapshot
    result = _drive(h)
    assert result.state == "completed"


def test_a_run_without_a_snapshot_cannot_run(tmp_path) -> None:
    h = make_harness(tmp_path, fixture_text("implementar-verificar.yaml"))
    h.store.create_run(
        run_id="run-2", user_id="u1", conversation_id="c1", workspace_root=h.workspace.as_posix(), pipeline_id="x",
        definition_hash="h" * 64, source="user", inputs={}, definition_snapshot="",
    )
    result = _drive(h, request=PipelineRequest("run-2"))
    assert result.error_code == "PIPELINE_SNAPSHOT_MISSING"


def test_a_definition_whose_hash_no_longer_matches_is_reported_not_run(tmp_path) -> None:
    h = _harness(tmp_path, pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER))
    h.store.create_run(
        run_id="run-3", user_id="u1", conversation_id="c1", workspace_root=h.workspace.as_posix(), pipeline_id="teste",
        definition_hash="0" * 64, source="user", inputs={}, definition_snapshot=pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER),
    )
    h.store.start_step(run_id="run-3", step_id="a", input_hash="1" * 64)
    result = _drive(h, request=PipelineRequest("run-3", resume=True))
    assert result.state == "failed" and result.error_code == "PIPELINE_DEFINITION_CHANGED"
    assert h.launcher.launches == 0


# ---------------------------------------------------------------- validation context
def test_the_definition_is_validated_again_against_the_live_system(tmp_path) -> None:
    h = _harness(tmp_path)
    ctx = ValidationContext(known_agents=frozenset({"plan"}), worktree_enabled=False)
    result = _drive(h, validation_context=lambda: ctx)
    assert result.state == "failed" and result.error_code == "PIPELINE_INVALID"
    assert "E_AGENT_UNKNOWN" in result.markdown and "E_ISOLATION_DISABLED" in result.markdown and "linha" in result.markdown
    assert h.launcher.launches == 0
    assert h.store.get_run("run-1").state == "failed" and h.store.get_run("run-1").stop_reason == "invalid"


def test_pipelines_turned_off_after_the_run_was_created_stops_it(tmp_path) -> None:
    h = _harness(tmp_path)
    result = _drive(h, validation_context=lambda: ValidationContext(pipelines_enabled=False))
    assert result.error_code == "PIPELINE_INVALID" and "E_DISABLED" in result.markdown


# ------------------------------------------------------------------- options wiring
def test_the_agent_digest_and_defaults_reach_the_engine(tmp_path) -> None:
    h = _harness(tmp_path, pipeline("- id: a\n  agent: general\n  prompt: um\n", HEADER))
    seen: dict[str, Any] = {}

    class SpyEngine:
        def __init__(self, definition, run_id, **kwargs):  # noqa: ANN001
            seen.update({name: getattr(kwargs["options"], name) for name in kwargs["options"].__slots__})
            raise RuntimeError("stop here")

    ports = _ports(h, agent_digest=lambda name: f"digest-{name}", shell_dialect="posix", default_effort="medium")
    driver = PipelineDriver(h.store, PipelineRequest("run-1"), TURN, ports, engine_factory=SpyEngine, poll_seconds=0.02)
    result = driver.run()
    assert result.error_code == "PIPELINE_ENGINE_ERROR" and result.state == "failed"
    assert seen["agent_digests"] == {"general": "digest-general"}
    assert (seen["default_provider"], seen["default_model"], seen["default_effort"]) == ("anthropic", "claude-sonnet-5-5", "medium")
    assert seen["permission_mode"] == "accept_edits" and seen["shell_dialect"] == "posix"
    assert drv.active_run_ids() == ()  # the registry is always cleaned


# ---- WS-C7 review F8: a turn requeued after its run already succeeded answers with the summary, not an error
def test_a_requeued_turn_whose_run_already_succeeded_returns_the_completion_summary(tmp_path) -> None:  # noqa: ANN001
    h = _harness(tmp_path)
    first = _drive(h)
    assert first.state == "completed" and h.store.get_run(h.run_id).state == "succeeded"
    again = _drive(h)  # the same turn id: the worker died after the run finished and before the turn did
    assert again.state == "completed" and again.error_code is None
    assert "| Etapa |" in again.markdown and h.run_id in again.markdown


def test_another_turn_still_cannot_rerun_a_succeeded_run(tmp_path) -> None:  # noqa: ANN001
    h = _harness(tmp_path)
    assert _drive(h).state == "completed"
    other = _drive(h, turn={**TURN, "turn_id": "turn-2"})
    assert other.state == "failed" and other.error_code == "PIPELINE_ALREADY_DONE"

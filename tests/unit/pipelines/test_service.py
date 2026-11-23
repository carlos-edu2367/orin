"""WS-C10: ``PipelineService``: start, approval, inputs, pause, cancel, resume, RunPipeline's pending starts, /goal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.permissions.trust import WorkspaceTrustStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines import service as service_module
from agentos.pipelines.approvals import ApprovalStore
from agentos.pipelines.goal import GoalError
from agentos.pipelines.service import PipelineService, ServiceError, start_pending_handler
from agentos.settings.loader import EffectiveSettings, SettingsLayer

USER, CONV = "user-1", "conv-1"
FAVORITES = {
    "anthropic": ("claude-sonnet-5-5",),
    "openai-codex": ("gpt-6-luna",),
    "google-antigravity": ("gemini-3.8-flash-high",),
}


def doc(pipeline_id: str, command: str = "pytest -q", extra_inputs: str = "") -> str:
    return (
        f"version: 1\nid: {pipeline_id}\nname: Nome\nbudget: {{max_tokens: 1000000}}\n"
        f"inputs:\n  testes: {{type: string, default: pytest -q}}\n  arquivo: {{type: string}}\n{extra_inputs}"
        f"steps:\n  - id: verificar\n    gate:\n      all:\n        - command: {command}\n"
    )


@dataclass
class Receipt:
    turn_id: str


class FakeStore:
    def __init__(self) -> None:
        self.header: dict = {"provider": "anthropic", "model_id": "claude-sonnet-5-5", "permission_mode": "accept_edits"}
        self.activity: list = []
        self._engine = None

    def get_header(self, conversation_id, user_id):  # noqa: ANN001, ANN201
        return self.header

    def _activity(self, turn, kind, summary, payload):  # noqa: ANN001, ANN201
        self.activity.append((kind, payload))


class FakeChat:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.sent: list[dict] = []
        self.cancelled: list[tuple] = []
        self.fail = False

    def send_pipeline(self, user_id, conversation_id, run_id, **kwargs):  # noqa: ANN001, ANN201
        if self.fail:
            raise RuntimeError("no worker")
        self.sent.append({"user": user_id, "conversation": conversation_id, "run_id": run_id, **kwargs})
        return Receipt(f"turn-{len(self.sent)}")

    def cancel(self, conversation_id, user_id, **kwargs):  # noqa: ANN001, ANN201
        self.cancelled.append((conversation_id, user_id))


def _effective(**user) -> EffectiveSettings:  # noqa: ANN003
    return EffectiveSettings(data={}, provenance={}, layers=(SettingsLayer("user", None, user),), errors=(), trusted=True)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.engine = create_local_engine(sqlite_url(tmp_path / "orin.db"))
        metadata.create_all(self.engine)
        self.root = tmp_path / "repo"
        self.project = self.root / ".orin" / "pipelines"
        self.user_dir = tmp_path / "home" / "pipelines"
        self.project.mkdir(parents=True)
        self.user_dir.mkdir(parents=True)
        self.chat = FakeChat()
        self.trust = WorkspaceTrustStore(tmp_path / "trusted.json")
        self.approvals = ApprovalStore(tmp_path / "approvals.json")
        self.settings: dict = {}
        self.emitted: list = []
        self.service = PipelineService(
            self.engine, chat=self.chat, approvals=self.approvals, trust_store=self.trust, user_dir=self.user_dir,
            favorites=lambda user_id: FAVORITES, agent_names=lambda user_id, root: None,
            settings_loader=lambda root: _effective(**self.settings),
        )
        self.service._emit = lambda turn_id, event: self.emitted.append((turn_id, event))  # type: ignore[method-assign]

    def write(self, where: str, pipeline_id: str, text: str | None = None) -> Path:
        folder = self.project if where == "project" else self.user_dir
        path = folder / f"{pipeline_id}.yaml"
        path.write_text(text if text is not None else doc(pipeline_id), encoding="utf-8")
        return path

    def start(self, pipeline_id: str, **kwargs):  # noqa: ANN003, ANN201
        return self.service.start(USER, CONV, self.root, pipeline_id, **kwargs)

    def run(self, run_id: str = "r1", state: str = "running", **kwargs):  # noqa: ANN003, ANN201
        text = self.write("user", "meu", doc("meu")).read_text(encoding="utf-8")
        from agentos.pipelines.model import compute_definition_hash

        defaults = dict(
            run_id=run_id, user_id=USER, conversation_id=CONV, workspace_root=str(self.root), pipeline_id="meu",
            definition_hash=compute_definition_hash(text), source="user", inputs={}, definition_snapshot=text, state=state,
        )
        defaults.update(kwargs)
        return self.service.store.create_run(**defaults)


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def code(call) -> str:  # noqa: ANN001
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value.code


# ------------------------------------------------------------------------------------------------ start
def test_start_creates_the_run_with_the_snapshot_and_asks_for_a_pipeline_turn(world: World) -> None:
    path = world.write("user", "meu")
    result = world.start("meu", supplied={"arquivo": "a b.py"})
    run = world.service.store.get_run(result["run_id"])
    assert run is not None and run.state == "running" and run.turn_id == result["turn_id"] == "turn-1"
    assert run.definition_snapshot == path.read_text(encoding="utf-8")
    assert run.inputs == {"testes": "pytest -q", "arquivo": "a b.py"}
    assert world.chat.sent[0]["run_id"] == result["run_id"] and world.chat.sent[0]["conversation"] == CONV
    assert result["budget"]["max_tokens"] == 1000000 and result["quota"]["available"] is False


def test_a_failed_send_does_not_leave_a_running_run(world: World) -> None:
    world.write("user", "meu")
    world.chat.fail = True
    with pytest.raises(RuntimeError):
        world.start("meu")
    (run,) = world.service.store.list_runs(CONV)
    assert run.state == "cancelled" and run.stop_reason == "send_failed"


def test_unknown_pipeline_and_unknown_input_are_refused(world: World) -> None:
    assert code(lambda: world.start("nao-existe")) == "PIPELINE_NOT_FOUND"
    world.write("user", "meu")
    assert code(lambda: world.start("meu", supplied={"fantasma": "x"})) == "PIPELINE_INPUT_UNKNOWN"
    assert code(lambda: world.start("meu", supplied={"arquivo": 3})) == "PIPELINE_INPUT_INVALID"


def test_pipelines_disabled_refuses_everything(world: World) -> None:
    world.write("user", "meu")
    world.settings = {"pipelines": {"enabled": False}}
    assert code(lambda: world.start("meu")) == "E_DISABLED"
    assert world.service.list_pipelines(USER, world.root)["enabled"] is False
    assert code(lambda: world.service.estimate(USER, CONV, world.root, "meu")) == "E_DISABLED"


def test_a_project_pipeline_needs_trust_and_an_approval_by_hash(world: World) -> None:
    world.write("project", "do-projeto")
    assert code(lambda: world.start("do-projeto")) == "PIPELINE_NOT_FOUND"  # untrusted folder: not even listed
    assert world.service.list_pipelines(USER, world.root)["project_skipped"] == 1
    world.trust.trust(USER, world.root)
    assert code(lambda: world.start("do-projeto")) == "PIPELINE_NOT_APPROVED"
    detail = world.service.get_pipeline(USER, world.root, "do-projeto")
    assert detail["review"]["commands"][0]["command"] == "pytest -q"
    with pytest.raises(ServiceError) as stale:
        world.service.approve(USER, world.root, "do-projeto", "0" * 64)
    assert stale.value.code == "PIPELINE_CHANGED"
    world.service.approve(USER, world.root, "do-projeto", detail["definition_hash"])
    assert world.start("do-projeto")["state"] == "running"
    world.service.revoke(USER, world.root, "do-projeto", detail["definition_hash"])
    assert code(lambda: world.start("do-projeto")) == "PIPELINE_NOT_APPROVED"


def test_plan_mode_conversations_refuse_to_start(world: World) -> None:
    world.write("user", "meu")
    world.chat.store.header["permission_mode"] = "plan_mode"
    assert code(lambda: world.start("meu")) == "PIPELINE_PLAN_MODE"


def test_models_auto_is_resolved_stored_and_degrades_when_families_are_missing(world: World) -> None:
    world.service._favorites = lambda user_id: {"anthropic": ("claude-sonnet-5-5",), "openai-codex": ("gpt-6-luna",)}
    result = world.start("revisao-multimodelo", supplied={"alvo": "tudo"})
    revisores = result["inputs"]["revisores"]
    assert 1 <= len(revisores) <= 2 and all(set(item) == {"provider", "model"} for item in revisores)
    assert world.service.store.get_run(result["run_id"]).inputs["revisores"] == revisores  # stable on resume
    assert result["degraded"][0]["reason"] == "models_auto_partial"
    (turn_id, event), = world.emitted
    assert turn_id == "turn-1" and event.type.value == "pipeline.degraded"


def test_models_input_accepts_provider_model_texts_of_favorites_only(world: World) -> None:
    ok = world.start("revisao-multimodelo", supplied={"revisores": ["anthropic/claude-sonnet-5-5", "openai-codex/gpt-6-luna"]})
    assert ok["inputs"]["revisores"] == [
        {"provider": "anthropic", "model": "claude-sonnet-5-5"}, {"provider": "openai-codex", "model": "gpt-6-luna"},
    ]
    assert not ok["degraded"]
    assert code(lambda: world.start("revisao-multimodelo", supplied={"revisores": ["anthropic/outro"]})) == "PIPELINE_INPUT_INVALID"
    assert code(lambda: world.start("revisao-multimodelo", supplied={"revisores": ["sem-barra"]})) == "PIPELINE_INPUT_INVALID"


def test_estimate_lists_subscriptions_and_never_tokens(world: World) -> None:
    result = world.service.estimate(USER, CONV, world.root, "revisao-multimodelo")
    assert {item["provider"] for item in result["subscriptions"]} <= set(FAVORITES)
    assert result["max_parallel"] >= 1 and result["budget"]["max_tokens"] > 0
    assert "tokens_estimate" not in result and any("não é uma estimativa de tokens" in n for n in result["notices"])


def test_a_project_budget_is_reported_lowered_by_the_user_ceiling(world: World) -> None:
    world.trust.trust(USER, world.root)
    world.write("project", "do-projeto")
    world.settings = {"orchestration": {"budget": {"subagent": {"maxTokens": 50000}}}}
    detail = world.service.get_pipeline(USER, world.root, "do-projeto")
    world.service.approve(USER, world.root, "do-projeto", detail["definition_hash"])
    budget = world.start("do-projeto")["budget"]
    assert budget["max_tokens"] == 50000 and budget["lowered_by_user_ceiling"] is True


# ------------------------------------------------------------------------------------------ pause, cancel
def test_pause_without_a_live_engine_leaves_the_flag_the_worker_polls(world: World) -> None:
    from agentos.pipelines.driver import pause_flag_set

    world.run()
    assert world.service.pause(USER, "r1")["where"] == "worker"
    assert pause_flag_set(world.service.store, "r1") is True


def test_pause_of_a_live_engine_calls_it_directly(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.run()
    calls: list[str] = []
    monkeypatch.setattr(service_module, "pause_run", lambda run_id: calls.append(run_id) or True)
    assert world.service.pause(USER, "r1")["where"] == "this_process"
    assert calls == ["r1"]


def test_cancel_a_running_run_in_another_process_cancels_the_conversation_turn(world: World) -> None:
    world.run()
    assert world.service.cancel(USER, "r1")["where"] == "worker"
    assert world.chat.cancelled == [(CONV, USER)]


def test_cancel_a_paused_run_closes_it_in_the_journal_and_a_finished_one_is_refused(world: World) -> None:
    world.run("r1", "interrupted")
    world.service.cancel(USER, "r1")
    assert world.service.store.get_run("r1").state == "cancelled"
    assert code(lambda: world.service.cancel(USER, "r1")) == "PIPELINE_NOT_CANCELLABLE"
    assert code(lambda: world.service.pause(USER, "r1")) == "PIPELINE_NOT_RUNNING"


def test_a_run_of_another_user_or_conversation_is_not_found(world: World) -> None:
    world.run()
    assert code(lambda: world.service.get_run("user-2", "r1")) == "PIPELINE_RUN_NOT_FOUND"
    assert code(lambda: world.service.get_run(USER, "r1", "outra")) == "PIPELINE_RUN_NOT_FOUND"
    assert code(lambda: world.service.cancel("user-2", "r1")) == "PIPELINE_RUN_NOT_FOUND"
    assert world.service.get_run(USER, "r1")["summary"]["run_id"] == "r1"
    assert [r["run_id"] for r in world.service.list_runs(USER, conversation_id=CONV)] == ["r1"]


# ----------------------------------------------------------------------------------------------- resume
def test_resume_sends_a_resume_turn_with_the_flags(world: World) -> None:
    world.run("r1", "interrupted")
    result = world.service.resume(USER, "r1", rerun_gates=True)
    assert result["resumed"] is True
    assert world.chat.sent[0]["resume"] is True and world.chat.sent[0]["rerun_gates"] is True


def test_resume_of_a_running_or_finished_run_is_refused(world: World) -> None:
    world.run("r1", "running")
    assert code(lambda: world.service.resume(USER, "r1")) == "PIPELINE_NOT_RESUMABLE"


def test_resume_of_budget_exceeded_needs_an_explicit_override(world: World) -> None:
    world.run("r1", "budget_exceeded")
    assert code(lambda: world.service.resume(USER, "r1")) == "PIPELINE_BUDGET_OVERRIDE_REQUIRED"
    world.service.resume(USER, "r1", budget_override=True)
    assert world.chat.sent[0]["budget_override"] is True


def test_a_changed_definition_answers_409_with_the_two_ways_out(world: World) -> None:
    world.run("r1", "failed")
    world.write("user", "meu", doc("meu", command="pytest -x"))
    with pytest.raises(ServiceError) as caught:
        world.service.resume(USER, "r1")
    error = caught.value
    assert (error.status, error.code) == (409, "PIPELINE_DEFINITION_CHANGED")
    assert [o["id"] for o in error.details["options"]] == ["original", "new_run"]
    assert world.chat.sent == []
    world.service.resume(USER, "r1", definition="original")  # the snapshot is what runs
    assert world.chat.sent[0]["run_id"] == "r1"
    again = world.service.resume(USER, "r1", definition="new_run")
    assert again["run_id"] != "r1" and again["state"] == "running"


def test_a_removed_definition_only_offers_the_original(world: World) -> None:
    world.run("r1", "failed")
    (world.user_dir / "meu.yaml").unlink()
    with pytest.raises(ServiceError) as caught:
        world.service.resume(USER, "r1")
    assert [o["id"] for o in caught.value.details["options"]] == ["original"]


def test_resuming_a_project_run_needs_the_approval_to_still_hold(world: World) -> None:
    world.trust.trust(USER, world.root)
    path = world.write("project", "do-projeto")
    detail = world.service.get_pipeline(USER, world.root, "do-projeto")
    world.service.approve(USER, world.root, "do-projeto", detail["definition_hash"])
    started = world.start("do-projeto")
    world.service.store.set_run_state(started["run_id"], "interrupted")
    world.service.revoke(USER, world.root, "do-projeto", detail["definition_hash"])
    assert path.exists()
    assert code(lambda: world.service.resume(USER, started["run_id"])) == "PIPELINE_NOT_APPROVED"


# -------------------------------------------------------------------------------- RunPipeline (G17)
def agent_start(world: World, pipeline_id: str, supplied=None, turn_id: str = "turn-agent"):  # noqa: ANN001, ANN201
    return world.service.request_agent_start(USER, CONV, turn_id, world.root, pipeline_id, supplied)


def test_the_agent_start_is_refused_while_the_key_is_off(world: World) -> None:
    assert code(lambda: agent_start(world, "pesquisa", {"pergunta": "x"})) == "PIPELINES_AGENT_START_DISABLED"


def test_the_agent_start_only_registers_a_pending_start_and_the_hook_runs_it(world: World) -> None:
    world.settings = {"pipelines": {"agentMayStart": True}}
    pending, info = agent_start(world, "pesquisa", {"pergunta": "o que é X?"})
    assert world.chat.sent == [] and world.service.store.list_runs(CONV) == []  # nothing inline
    assert info["budget"]["max_tokens"] > 0
    start_pending_handler_for(world, {"turn_id": "turn-agent", "user_id": USER}, "completed")
    (sent,) = world.chat.sent
    run = world.service.store.get_run(sent["run_id"])
    assert run is not None and run.pipeline_id == "pesquisa" and run.inputs["pergunta"] == "o que é X?"
    assert pending.pipeline_id == "pesquisa"


def start_pending_handler_for(world: World, turn: dict, state: str) -> None:
    world.service.run_pending(turn, state)


def test_a_pending_start_is_dropped_when_the_turn_does_not_complete(world: World) -> None:
    world.settings = {"pipelines": {"agentMayStart": True}}
    agent_start(world, "pesquisa", {"pergunta": "x"})
    start_pending_handler_for(world, {"turn_id": "turn-agent", "user_id": USER}, "failed")
    assert world.chat.sent == []
    assert service_module.pending_for("turn-agent") == ()


def test_the_agent_may_only_start_approved_pipelines_and_never_pick_a_whole_command(world: World) -> None:
    world.settings = {"pipelines": {"agentMayStart": True}}
    whole = "version: 1\nid: so-cmd\nname: N\nbudget: {max_tokens: 1000}\ninputs:\n  comando: {type: string, default: pytest -q}\nsteps:\n  - id: g\n    gate:\n      all:\n        - command: ${{ inputs.comando }}\n"
    world.write("user", "so-cmd", whole)
    assert code(lambda: agent_start(world, "so-cmd")) == "PIPELINE_NOT_APPROVED"  # user pipeline: not approved for the agent
    detail = world.service.get_pipeline(USER, world.root, "so-cmd")
    world.service.approve(USER, world.root, "so-cmd", detail["definition_hash"])
    assert code(lambda: agent_start(world, "so-cmd", {"comando": "rm -rf x"})) == "PIPELINE_INPUT_NOT_ALLOWED"
    pending, _ = agent_start(world, "so-cmd")  # the default is used
    assert pending.inputs == {"comando": "pytest -q"}
    names = [item["id"] for item in world.service.agent_startable(USER, world.root)]
    assert "so-cmd" in names and "pesquisa" in names


def test_an_approved_user_pipeline_edited_later_is_refused_to_the_agent(world: World) -> None:
    world.settings = {"pipelines": {"agentMayStart": True}}
    world.write("user", "meu")
    detail = world.service.get_pipeline(USER, world.root, "meu")
    world.service.approve(USER, world.root, "meu", detail["definition_hash"])
    agent_start(world, "meu")
    world.write("user", "meu", doc("meu", command="pytest -x"))
    assert code(lambda: agent_start(world, "meu")) == "PIPELINE_NOT_APPROVED"
    service_module._take_pending("turn-agent")


def test_the_pending_registry_caps_the_starts_of_one_turn(world: World) -> None:
    world.settings = {"pipelines": {"agentMayStart": True}}
    for _ in range(service_module.MAX_PENDING_PER_TURN):
        agent_start(world, "pesquisa", {"pergunta": "x"}, turn_id="t-cap")
    assert code(lambda: agent_start(world, "pesquisa", {"pergunta": "x"}, turn_id="t-cap")) == "PIPELINE_PENDING_LIMIT"
    service_module._take_pending("t-cap")


# ---------------------------------------------------------------------------------------- files and goal
def test_validate_save_and_delete_a_user_pipeline_with_if_match(world: World) -> None:
    text = doc("novo")
    assert world.service.validate_text(USER, world.root, text)["valid"] is True
    bad = world.service.validate_text(USER, world.root, text.replace("steps:", "passos:"))
    assert bad["valid"] is False and bad["errors"][0]["line"] is not None
    saved = world.service.save_pipeline(USER, world.root, "novo", text, scope="user")
    assert saved["id"] == "novo"
    assert code(lambda: world.service.save_pipeline(USER, world.root, "novo", text, scope="user")) == "PIPELINE_CONFLICT"
    world.service.save_pipeline(USER, world.root, "novo", text.replace("Nome", "Outro"), scope="user", if_match=saved["definition_hash"])
    assert code(lambda: world.service.save_pipeline(USER, world.root, "x/../y", text, scope="user")) == "E_ID"
    assert code(lambda: world.service.save_pipeline(USER, world.root, "outro", text, scope="user")) == "PIPELINE_INVALID"  # id mismatch
    world.service.delete_pipeline(USER, world.root, "novo", scope="user")
    assert code(lambda: world.service.delete_pipeline(USER, world.root, "novo", scope="user")) == "PIPELINE_NOT_FOUND"


def test_saving_a_project_pipeline_needs_a_trusted_folder(world: World) -> None:
    assert code(lambda: world.service.save_pipeline(USER, world.root, "p", doc("p"), scope="project")) == "PIPELINE_UNTRUSTED"
    world.trust.trust(USER, world.root)
    world.service.save_pipeline(USER, world.root, "p", doc("p"), scope="project")
    assert (world.project / "p.yaml").is_file()


def test_goal_clear_keeps_the_running_turn_unless_asked(world: World) -> None:
    calls: list[str] = []

    class Controller:
        deps = SimpleNamespace(default_max_tokens=0)

        def clear(self, conversation_id, user_id):  # noqa: ANN001, ANN201
            calls.append("clear")
            return SimpleNamespace(to_dict=lambda: {"state": "cancelled"})

    world.service._goal = Controller()  # type: ignore[assignment]
    assert world.service.goal_clear(USER, CONV, world.root)["turn_cancelled"] is False
    assert world.chat.cancelled == []
    assert world.service.goal_clear(USER, CONV, world.root, cancel_turn=True)["turn_cancelled"] is True
    assert world.chat.cancelled == [(CONV, USER)]


def test_goal_clear_without_an_objective_is_not_found(world: World) -> None:
    class Controller:
        deps = SimpleNamespace(default_max_tokens=0)

        def clear(self, conversation_id, user_id):  # noqa: ANN001, ANN201
            return None

    world.service._goal = Controller()  # type: ignore[assignment]
    with pytest.raises(GoalError) as caught:
        world.service.goal_clear(USER, CONV, world.root)
    assert caught.value.status == 404


def test_the_goal_controller_gets_the_default_cap_from_the_settings(world: World) -> None:
    captured = SimpleNamespace(deps=SimpleNamespace(default_max_tokens=0))
    world.service._goal = captured  # type: ignore[assignment]
    world.settings = {"goal": {"defaultMaxTokens": 123456}}
    world.service.goals(world.root)
    assert captured.deps.default_max_tokens == 123456


def test_the_after_turn_handler_is_registered_and_ignores_turns_without_pending_starts() -> None:
    from agentos.pipelines import after_turn

    after_turn.install_default_handlers()  # other tests clear the registry
    assert any(entry.handler is start_pending_handler for entry in after_turn._HANDLERS)
    start_pending_handler(SimpleNamespace(_engine=object()), {"turn_id": "sem-pendencia"}, "completed")


# ---------------------------------------------------------------- the two driver hooks C10 added
def test_the_driver_lowers_a_project_budget_to_the_user_ceiling(tmp_path: Path) -> None:
    from agentos.agentic.usage_budget import BudgetLimits
    from agentos.pipelines.driver import DriverPorts, PipelineDriver, PipelineRequest

    from .engine_support import make_harness
    from .helpers import pipeline

    text = pipeline("- id: a\n  agent: general\n  prompt: um\n", "version: 1\nid: teste\nname: T\nbudget: {max_tokens: 900000}\n")
    h = make_harness(tmp_path, text)
    h.store.create_run(
        run_id="run-p", user_id="u1", conversation_id="c1", workspace_root=h.workspace.as_posix(), pipeline_id="teste",
        definition_hash=h.definition.definition_hash, source="project", inputs={}, definition_snapshot=text,
    )
    seen: list = []

    def factory(definition, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen.append(definition)
        raise RuntimeError("stop here")

    ports = DriverPorts(launcher=h.launcher, gate_runner=h.gates, sink=h.sink, clock=h.clock, budget_ceiling=BudgetLimits(max_tokens=70000))
    turn = {"turn_id": "t", "conversation_id": "c1", "user_id": "u1"}
    PipelineDriver(h.store, PipelineRequest("run-p"), turn, ports, engine_factory=factory).run()
    assert seen[0].budget.max_tokens == 70000


def test_the_driver_pauses_when_the_flag_is_set_from_another_process(tmp_path: Path) -> None:
    import threading

    from agentos.pipelines import driver as drv
    from agentos.pipelines.driver import DriverPorts, PipelineDriver, PipelineRequest
    from agentos.pipelines.fakes import Reply

    from .engine_support import make_harness
    from .helpers import pipeline

    two = "- id: a\n  agent: general\n  prompt: um\n- id: b\n  agent: general\n  prompt: dois\n"
    h = make_harness(tmp_path, pipeline(two, "version: 1\nid: teste\nname: T\nbudget: {max_tokens: 1000000}\n"))
    entered, release = threading.Event(), threading.Event()

    def slow(call, control):  # noqa: ANN001, ANN202
        entered.set()
        assert release.wait(10)
        return Reply()

    h.launcher.script("a", slow)
    ports = DriverPorts(
        launcher=h.launcher, gate_runner=h.gates, sink=h.sink, clock=h.clock,
        pause_requested=lambda: drv.pause_flag_set(h.store, "run-1"),
    )
    box: dict = {}
    turn = {"turn_id": "t", "conversation_id": "c1", "user_id": "u1"}
    driver = PipelineDriver(h.store, PipelineRequest("run-1"), turn, ports, poll_seconds=0.02, engine_poll_seconds=0.05, engine_monitor_seconds=0.02)
    thread = threading.Thread(target=lambda: box.setdefault("r", driver.run()))
    thread.start()
    assert entered.wait(10)
    from sqlalchemy import update

    from agentos.persistence.postgres.schema import pipeline_runs

    with h.sqlite.begin() as connection:
        connection.execute(update(pipeline_runs).where(pipeline_runs.c.run_id == "run-1").values(stop_reason=drv.PAUSE_REQUESTED))
    threading.Event().wait(0.3)
    release.set()
    thread.join(timeout=20)
    assert not thread.is_alive()
    run = h.store.get_run("run-1")
    assert run.state == "interrupted" and run.stop_reason == "paused"

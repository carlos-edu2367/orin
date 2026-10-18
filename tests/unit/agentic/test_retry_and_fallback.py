from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import NormalizedStreamItem, ProviderKeyRejected, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.settings.paths import orin_user_dir


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e",
            "provider": "openrouter", "model_id": "primary", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.states: list[tuple[str, dict]] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        self.states.append((state, payload))

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass


class Toolset:
    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return True

    def argument_names(self, name):
        return frozenset()

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


class Flaky:
    def __init__(self, failures: list[BaseException]) -> None:
        self.failures = list(failures)
        self.calls = 0
        self.models: list[str] = []

    def stream(self, request):
        self.calls += 1
        self.models.append(str(request.get("model")))
        if self.failures:
            raise self.failures.pop(0)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="ok"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))


def _runtime(provider, *, store=None, sleeps=None, base=0.5, retries=3, **kwargs) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(
        store=store or Store(), provider=provider, toolset=Toolset(),
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1),
                             max_provider_retries=retries, retry_base_delay_seconds=base),
        sleep=(sleeps.append if sleeps is not None else (lambda seconds: None)), **kwargs,
    )


def test_retries_back_off_exponentially() -> None:
    sleeps: list[float] = []
    provider = Flaky([RuntimeError("reset"), RuntimeError("reset")])
    assert _runtime(provider, sleeps=sleeps).run("t").state == "completed"
    assert provider.calls == 3
    assert 1.125 <= sum(sleeps) <= 1.875


def test_retry_after_is_respected() -> None:
    sleeps: list[float] = []
    rejected = ProviderKeyRejected("rate limited")
    rejected.status_code, rejected.retry_after_seconds = 429, 3.0
    assert _runtime(Flaky([rejected]), sleeps=sleeps).run("t").state == "completed"
    assert abs(sum(sleeps) - 3.0) < 1e-9


def test_a_retry_after_longer_than_allowed_is_not_waited_for() -> None:
    sleeps: list[float] = []
    rejected = ProviderKeyRejected("rate limited")
    rejected.status_code, rejected.retry_after_seconds = 429, 600.0
    provider = Flaky([rejected])
    result = _runtime(provider, sleeps=sleeps).run("t")
    assert (result.state, result.error_code, provider.calls, sleeps) == ("failed", "PROVIDER_STREAM_FAILED", 1, [])


def test_a_client_error_is_not_retried() -> None:
    provider = Flaky([_status_error(400)])
    result = _runtime(provider).run("t")
    assert (result.state, provider.calls) == ("failed", 1)


def test_without_backoff_the_old_immediate_retry_is_kept() -> None:
    sleeps: list[float] = []
    provider = Flaky([RuntimeError("reset")])
    assert _runtime(provider, sleeps=sleeps, base=0.0, retries=1).run("t").state == "completed"
    assert (provider.calls, sleeps) == (2, [])


def test_cancellation_during_the_wait_cancels_the_turn() -> None:
    flag = {"cancelled": False}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        flag["cancelled"] = True

    runtime = AgenticTurnRuntime(
        store=Store(), provider=Flaky([RuntimeError("reset")]), toolset=Toolset(),
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1), max_provider_retries=3, retry_base_delay_seconds=0.5),
        sleep=sleep, cancelled=lambda turn: flag["cancelled"],
    )
    assert runtime.run("t").state == "cancelled"
    assert len(sleeps) == 1


class AlwaysDown:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        self.calls += 1
        raise RuntimeError("overloaded")


def test_the_fallback_chain_takes_over_after_retries() -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    built: list[str] = []
    store = Store()

    def factory(model_id: str):
        built.append(model_id)
        if model_id == "broken":
            raise RuntimeError("cannot build")
        return Flaky([])

    runtime = _runtime(AlwaysDown(), store=store, base=0.0, retries=1, lifecycle=bus,
                       fallback_models=("broken", "backup"), fallback_provider_factory=factory)
    result = runtime.run("t")
    assert result.state == "completed"
    assert built == ["broken", "backup"] and store.turn["model_id"] == "backup"
    switches = [dict(event.payload) for event in events if event.name in {HookEvent.PRE_MODEL_SWITCH, HookEvent.POST_MODEL_SWITCH}]
    assert [(item["to_model"], item["hook_event_name"]) for item in switches] == [("backup", "PreModelSwitch"), ("backup", "PostModelSwitch")]
    assert switches[-1]["from_model"] == "primary" and switches[-1]["reason"] == "fallback"
    assert any(state == "model_switched" for state, _ in store.states)


def test_a_pre_model_switch_hook_can_refuse_the_fallback() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="no") if event.name is HookEvent.PRE_MODEL_SWITCH else None)
    built: list[str] = []
    runtime = _runtime(AlwaysDown(), base=0.0, retries=1, lifecycle=bus,
                       fallback_models=("backup",), fallback_provider_factory=lambda model_id: built.append(model_id) or Flaky([]))
    result = runtime.run("t")
    assert (result.state, result.error_code, built) == ("failed", "PROVIDER_STREAM_FAILED", ["backup"])


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _session(tmp_path: Path):
    from agentos.agentic.session import TurnSession

    class SessionStore(Store):
        def record(self, *args, **kwargs):
            pass

        def main_agent_id(self, turn):
            return "agent:c:main"

    class Agents:
        def list(self):
            return []

    store = SessionStore()
    return TurnSession(
        turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: Flaky([]), workspace_root=tmp_path, enable_subagents=False,
        fallback_provider_factory=lambda model_id: Flaky([]),
    )


def test_fallback_models_come_from_user_settings_only(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"fallbackModel": "a, b, primary, a"})
    session = _session(tmp_path)
    _write(session.workspace.root / ".orin" / "settings.json", {"fallbackModel": "repo-model"})
    runtime = session.build_runtime()
    assert runtime._fallback_models == ["a", "b"]
    assert session.effective_settings.get("fallbackModel") == "a, b, primary, a"

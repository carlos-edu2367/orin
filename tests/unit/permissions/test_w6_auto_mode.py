"""W6 (W1-R10): the auto-mode classifier's threads are bounded, its breaker spans a conversation, and
``autoMode.model`` really picks the model."""
from __future__ import annotations

import threading
import time
import uuid

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.permissions.auto_mode import MAX_CLASSIFIER_THREADS, MAX_CONSECUTIVE_DENIALS, AutoModeClassifier


class _Hanging:
    def __init__(self) -> None:
        self.release = threading.Event()

    def stream(self, request):
        self.release.wait(10)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text='{"decision": "allow", "reason": "x"}')])


class _Denying:
    def stream(self, request):
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text='{"decision": "deny", "reason": "no"}')])


def _call(classifier: AutoModeClassifier):
    return classifier("run_command", {"command": "make deploy"})


def test_timed_out_calls_never_grow_the_threads_past_the_pool() -> None:
    provider = _Hanging()
    before = threading.active_count()
    try:
        for _ in range(6):
            decision = _call(AutoModeClassifier(provider, provider_name="openrouter", model_id="fast", timeout_seconds=0.1))
            assert decision.behavior == "ask"
        assert threading.active_count() <= before + MAX_CLASSIFIER_THREADS
    finally:
        provider.release.set()
        time.sleep(0.2)


def test_the_breaker_spans_the_conversations_turns() -> None:
    key = f"chat_{uuid.uuid4().hex}"
    for _ in range(MAX_CONSECUTIVE_DENIALS):  # one classifier per turn, as build_runtime makes them
        assert _call(AutoModeClassifier(_Denying(), provider_name="openrouter", model_id="fast", breaker_key=key)).behavior == "deny"
    paused = _call(AutoModeClassifier(_Denying(), provider_name="openrouter", model_id="fast", breaker_key=key))
    assert paused.behavior == "ask" and "paused" in paused.reason
    other = _call(AutoModeClassifier(_Denying(), provider_name="openrouter", model_id="fast", breaker_key=f"chat_{uuid.uuid4().hex}"))
    assert other.behavior == "deny"  # another conversation has its own counters


def test_automode_model_gets_its_own_transport(tmp_path, monkeypatch) -> None:
    import json

    from agentos.settings.paths import orin_user_dir

    from tests.unit.cli_code.test_cli_turn_session import _session

    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"autoMode": {"model": "fast-model"}}), encoding="utf-8")
    asked: list[str] = []
    session = _session(tmp_path, permission_mode="auto")
    session.fallback_provider_factory = lambda model_id: asked.append(model_id) or _Denying()
    session.build_runtime()
    classifier = session._auto_mode_classifier()
    assert classifier is not None and classifier.model_id == "fast-model" and asked and set(asked) == {"fast-model"}  # build_runtime made one too
    assert isinstance(classifier.provider, _Denying)

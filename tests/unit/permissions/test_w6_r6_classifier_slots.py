"""W6-R6: one conversation's hung classifier calls never starve another conversation's classifier (W6 review)."""
from __future__ import annotations

import threading
import time
import uuid

from agentos.permissions.auto_mode import AutoModeClassifier
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind

ALLOW = '{"decision": "allow", "reason": "fine"}'


class _Hanging:
    def __init__(self) -> None:
        self.release = threading.Event()

    def stream(self, request):
        self.release.wait(30)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=ALLOW)])


class _Healthy:
    def stream(self, request):
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=ALLOW)])


class _Failing:
    def stream(self, request):
        raise RuntimeError("the provider refused")


def _classifier(provider, key: str, timeout: float = 0.5) -> AutoModeClassifier:
    return AutoModeClassifier(provider, provider_name="x", model_id="m", timeout_seconds=timeout, breaker_key=key)


def _write(classifier: AutoModeClassifier):
    return classifier("run_command", {"command": "make build"})


def test_hung_calls_in_one_conversation_do_not_starve_another() -> None:
    hung = _Hanging()
    try:
        a, b = f"A-{uuid.uuid4().hex}", f"B-{uuid.uuid4().hex}"
        assert _write(_classifier(hung, a)).behavior == "ask"  # times out; its thread stays hung
        assert _write(_classifier(hung, a)).behavior == "ask"  # the second slot of A
        started = time.monotonic()
        healthy = _write(_classifier(_Healthy(), b))
        assert healthy.behavior == "allow", healthy.reason
        assert time.monotonic() - started < 0.4  # B does not wait out A's timeout
    finally:
        hung.release.set()
        time.sleep(0.3)


def test_a_third_call_in_the_hung_conversation_is_busy_and_asks() -> None:
    hung = _Hanging()
    try:
        key = f"A-{uuid.uuid4().hex}"
        _write(_classifier(hung, key))
        _write(_classifier(hung, key))
        third = _write(_classifier(hung, key))
        assert third.behavior == "ask" and "busy" in third.reason
    finally:
        hung.release.set()
        time.sleep(0.3)


def test_the_slot_is_released_when_the_hung_call_ends_not_at_its_timeout() -> None:
    hung = _Hanging()
    key = f"A-{uuid.uuid4().hex}"
    try:
        _write(_classifier(hung, key))
        _write(_classifier(hung, key))
        assert "busy" in _write(_classifier(hung, key)).reason
    finally:
        hung.release.set()
        time.sleep(0.3)
    assert _write(_classifier(_Healthy(), key)).behavior == "allow"


def test_a_failing_call_releases_its_slot() -> None:
    key = f"F-{uuid.uuid4().hex}"
    results = [_write(_classifier(_Failing(), key)) for _ in range(3)]
    assert [item.reason for item in results] == ["auto mode classifier failed"] * 3
    assert _write(_classifier(_Healthy(), key)).behavior == "allow"


def test_a_call_queued_behind_a_full_pool_asks_and_frees_its_slot() -> None:
    hung = _Hanging()
    try:
        # Eight conversations hang: the shared pool is full, so a ninth conversation's call never starts.
        for _ in range(8):
            _write(_classifier(hung, f"H-{uuid.uuid4().hex}"))
        queued_key = f"Q-{uuid.uuid4().hex}"
        queued = _write(_classifier(hung, queued_key))
        assert queued.behavior == "ask"
    finally:
        hung.release.set()
        time.sleep(0.5)
    assert _write(_classifier(_Healthy(), queued_key)).behavior == "allow"

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
import json
import tempfile
import threading
import time
from typing import Any

import pytest

from agentos.agentic.agent_definitions import resolve_agent_model
from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
import agentos.provider_catalog.claude_code as claude_code_mod
from agentos.provider_catalog.claude_code import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL as CLAUDE_CODE_LOCAL_BASE_URL,
    ProviderTransportError,
    stream_claude_code_bridge,
)
from agentos.provider_catalog.opencode import _format_bridge_conversation_transcript
from agentos.workers.chat import ChatWorker


def test_format_bridge_transcript_does_not_duplicate_mid_turn_system_into_system_parts() -> None:
    messages = [
        {"role": "system", "content": "Initial system prompt"},
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "Mid-turn phase reminder"},
    ]
    system_parts, prompt_text = _format_bridge_conversation_transcript(messages)
    assert system_parts == ["Initial system prompt"]
    assert "[System]\nMid-turn phase reminder" in prompt_text


def test_format_bridge_transcript_preserves_image_blocks_metadata() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect this UI"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
                },
            ],
        },
        {
            "role": "user",
            "_tool_image_result": True,
            "content": [
                {"type": "text", "text": "Captura da página atual:"},
                {"type": "image", "media_type": "image/jpeg", "data": "/9j/4AAQSkZJRg=="},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_img_1",
                    "content": [
                        {"type": "text", "text": "Tool screenshot attached"},
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/webp", "data": "UklGRg=="},
                        },
                    ],
                }
            ],
        },
    ]
    system_parts, prompt_text, image_blocks = _format_bridge_conversation_transcript(
        messages, return_images=True
    )
    assert system_parts == []
    assert "Inspect this UI" in prompt_text
    assert "Tool screenshot attached" in prompt_text
    assert len(image_blocks) == 3
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    assert image_blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "/9j/4AAQSkZJRg==",
    }
    assert image_blocks[2]["source"]["media_type"] == "image/webp"


def test_stream_claude_code_bridge_sends_multimodal_content_list_when_images_present(monkeypatch) -> None:
    captured_stdin: list[str] = []
    captured_cmd: list[str] = []

    class _FakeStdin(io.StringIO):
        def close(self) -> None:
            captured_stdin.append(self.getvalue())
            super().close()

    class _FakePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured_cmd.extend(cmd)
            self.stdin = _FakeStdin()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Visual check complete.",
                        "usage": {"input_tokens": 25, "output_tokens": 5},
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda *_a, **_kw: "claude",
    )

    request = {
        "model": "claude-opus-5-5",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check this screenshot"},
                    {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
                ],
            }
        ],
    }
    events = list(
        stream_claude_code_bridge(
            request=request,
            base_url=CLAUDE_CODE_LOCAL_BASE_URL,
            popen_factory=_FakePopen,
        )
    )
    assert any(getattr(ev, "text", None) == "Visual check complete." for ev in events)
    normalize_fn = getattr(claude_code_mod, "_normalize_claude_model_id", lambda m: m)
    assert normalize_fn("claude-opus-5-5") == "claude-opus-4-7"
    assert "claude-opus-4-7" in captured_cmd
    assert "claude-opus-5-5" not in captured_cmd
    assert len(captured_stdin) == 1
    envelope = json.loads(captured_stdin[0].strip())
    content = envelope["message"]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == "iVBORw0KGgo="
    assert content[1]["type"] == "text"
    assert "Check this screenshot" in content[1]["text"]


def test_resolve_agent_model_supports_claude_aliases_without_manual_settings() -> None:
    assert resolve_agent_model("haiku", {}) == "claude-haiku-4-5-20251001"
    assert resolve_agent_model("sonnet", {}) == "claude-sonnet-4-6"
    assert resolve_agent_model("opus", {}) == "claude-opus-4-7"
    assert resolve_agent_model("fable", None) == "claude-sonnet-4-6"


def test_http_provider_stream_transport_forwards_cancel_check_to_claude_bridge(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_bridge(**kwargs: Any):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr("agentos.agentic.provider_stream.stream_claude_code_bridge", _fake_bridge)
    transport = HTTPProviderStreamTransport(
        provider="anthropic",
        base_url=CLAUDE_CODE_LOCAL_BASE_URL,
        api_key="",
        model="claude-sonnet-4-6",
    )
    cancel_fn = lambda: True
    list(transport.stream({"messages": [{"role": "user", "content": "hi"}]}, cancel_check=cancel_fn))
    assert captured.get("cancel_check") is cancel_fn


def test_runtime_prefix_caching_is_false_on_claude_cli_bridge_and_ages_tool_results() -> None:
    class _StubProvider:
        base_url = CLAUDE_CODE_LOCAL_BASE_URL

    class _DummyStore:
        pass

    class _DummyActions:
        pass

    rt = AgenticTurnRuntime(
        store=_DummyStore(),
        provider=_StubProvider(),
        actions=_DummyActions(),
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=50_000,
        ),
    )
    assert rt._prefix_caching({"provider": "anthropic"}) is False

    cloud_rt = AgenticTurnRuntime(
        store=_DummyStore(),
        provider=type("_CloudProvider", (), {"base_url": "https://api.anthropic.com/v1"})(),
        actions=_DummyActions(),
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=50_000,
        ),
    )
    assert cloud_rt._prefix_caching({"provider": "anthropic"}) is True


def test_runtime_retry_wait_honors_retry_after_when_base_delay_is_zero() -> None:
    slept: list[float] = []

    class _DummyStore:
        pass

    class _DummyProvider:
        base_url = CLAUDE_CODE_LOCAL_BASE_URL

    class _DummyActions:
        pass

    rt = AgenticTurnRuntime(
        store=_DummyStore(),
        provider=_DummyProvider(),
        actions=_DummyActions(),
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=50_000,
            retry_base_delay_seconds=0.0,
            max_retry_after_seconds=60.0,
        ),
        sleep=lambda secs: slept.append(secs),
    )
    err = ProviderTransportError("Rate limited", status_code=429, retry_after_seconds=1.5)
    outcome = rt._retry_wait({"turn_id": "t1"}, 1, err)
    assert outcome == "retry"
    assert sum(slept) == pytest.approx(1.5, rel=1e-3)

    too_long_err = ProviderTransportError("Rate limited long", status_code=429, retry_after_seconds=120.0)
    assert rt._retry_wait({"turn_id": "t1"}, 1, too_long_err) == "exhausted"


def test_chat_worker_provider_transport_bypasses_multikey_wrapper_for_claude_cli_bridge_even_with_stored_keys(
    monkeypatch,
) -> None:
    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *_args, **_kwargs):
            class _Res:
                def mappings(self):
                    return self

                def first(self):
                    return {
                        "base_url": CLAUDE_CODE_LOCAL_BASE_URL,
                        "enabled": True,
                        "key_cooldown_seconds": 60,
                    }

            return _Res()

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    class _FakeStore:
        _engine = _FakeEngine()

    class _FakeKeyRecord:
        plaintext = "sk-ant-stale-cloud-key"

    class _FakeKeyAdapter:
        def __init__(self, *_a, **_kw):
            pass

        def next_available_key(self, *_a, **_kw):
            return _FakeKeyRecord()

    monkeypatch.setattr("agentos.workers.chat.PostgresProviderApiKeyAdapter", _FakeKeyAdapter)
    monkeypatch.setattr(
        "agentos.workers.chat.ProviderSecretCipher.from_environment",
        classmethod(lambda cls, required=True: object()),
    )

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = _FakeStore()

    turn = {"user_id": "u1", "provider": "anthropic", "model_id": "claude-sonnet-4-6"}
    transport = worker._provider_transport(turn)
    assert isinstance(transport, HTTPProviderStreamTransport)
    assert not isinstance(transport, MultiKeyProviderStreamTransport)
    assert transport.base_url == CLAUDE_CODE_LOCAL_BASE_URL
    assert transport._api_key == ""

    sub_transport = worker._transport_for("u1", "anthropic", "claude-haiku-4-5-20251001")
    assert isinstance(sub_transport, HTTPProviderStreamTransport)
    assert not isinstance(sub_transport, MultiKeyProviderStreamTransport)
    assert sub_transport.base_url == CLAUDE_CODE_LOCAL_BASE_URL
    assert sub_transport._api_key == ""


def test_chat_worker_child_provider_keeps_anthropic_on_claude_cli_subscription_and_caps_context_window(
    monkeypatch,
) -> None:
    worker = ChatWorker.__new__(ChatWorker)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *_args, **_kwargs):
            class _Res:
                def all(self):
                    return [type("_Row", (), {"provider": "openrouter"})()]

                def mappings(self):
                    return self

                def first(self):
                    return {
                        "base_url": CLAUDE_CODE_LOCAL_BASE_URL,
                        "enabled": True,
                        "key_cooldown_seconds": 60,
                        "context_window": 1_000_000,
                    }

            return _Res()

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    class _FakeStore:
        _engine = _FakeEngine()

    worker.store = _FakeStore()
    monkeypatch.setattr(worker, "_favorite_child_model_ids", lambda _turn: ("claude-haiku-4-5-20251001",))
    monkeypatch.setattr(worker, "_allowed_child_providers", lambda _conn, _turn: {"anthropic", "openrouter"})

    captured_calls: list[tuple[str, str, str]] = []

    def _spy_transport_for(user_id, provider, model_id="", num_ctx=None):
        captured_calls.append((str(user_id), str(provider), str(model_id)))
        return HTTPProviderStreamTransport(
            provider=provider,
            base_url=CLAUDE_CODE_LOCAL_BASE_URL,
            api_key="",
            model=model_id,
        )

    monkeypatch.setattr(worker, "_transport_for", _spy_transport_for)

    turn = {"user_id": "u1", "provider": "anthropic", "model_id": "claude-opus-4-7"}
    worker._child_provider_for_turn(turn, "claude-haiku-4-5-20251001")
    assert captured_calls[-1] == ("u1", "anthropic", "claude-haiku-4-5-20251001")

    # Also verify context window capping at 200_000 for Claude CLI bridge unless [1m] is in model_id
    max_ctx = worker._max_context_tokens_for(turn)
    assert max_ctx <= 200_000

    turn_1m = {"user_id": "u1", "provider": "anthropic", "model_id": "claude-opus-4-7[1m]"}
    max_ctx_1m = worker._max_context_tokens_for(turn_1m)
    assert max_ctx_1m > 200_000


def test_stream_claude_code_bridge_parses_resets_at_into_retry_after_and_isolates_cwd_and_semaphore(
    monkeypatch,
) -> None:
    captured_cwds: list[str | None] = []
    active_count = 0
    max_concurrent = 0
    lock = threading.Lock()

    future_reset = time.time() + 42.0

    class _FakeRateLimitPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured_cwds.append(kwargs.get("cwd"))
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {
                            "status": "rejected",
                            "rateLimitType": "five_hour",
                            "resetsAt": future_reset,
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "result": "You've hit your limit",
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = 1

        def poll(self) -> int | None:
            return 1

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda *_a, **_kw: "claude",
    )

    with pytest.raises(ProviderTransportError) as exc_info:
        list(
            stream_claude_code_bridge(
                request={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
                base_url=CLAUDE_CODE_LOCAL_BASE_URL,
                popen_factory=_FakeRateLimitPopen,
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds is not None
    assert 35.0 <= exc_info.value.retry_after_seconds <= 45.0
    assert captured_cwds == [tempfile.gettempdir()]

    # Verify semaphore bounds concurrency to at most 3 simultaneous Popen processes
    class _SlowPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            nonlocal active_count, max_concurrent
            with lock:
                active_count += 1
                if active_count > max_concurrent:
                    max_concurrent = active_count
            time.sleep(0.08)
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "ok",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            nonlocal active_count
            with lock:
                if active_count > 0:
                    active_count -= 1
            return 0

        def kill(self) -> None:
            pass

    threads = [
        threading.Thread(
            target=lambda: list(
                stream_claude_code_bridge(
                    request={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
                    base_url=CLAUDE_CODE_LOCAL_BASE_URL,
                    popen_factory=_SlowPopen,
                )
            )
        )
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert max_concurrent <= 3

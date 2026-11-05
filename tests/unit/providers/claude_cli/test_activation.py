from __future__ import annotations

import logging
from typing import Any

import pytest

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.providers.claude_cli.catalog import CatalogModel
from agentos.providers.claude_cli.errors import NativeLoopUnavailable
import agentos.providers.claude_cli.provider as provider_mod
from agentos.providers.claude_cli.provider import (
    CLAUDE_CODE_LOCAL_BASE_URL,
    ClaudeCliProvider,
    native_tools_enabled,
)
from agentos.workers.chat import ChatWorker


@pytest.mark.parametrize(
    "env,mode,expected",
    [
        ({}, "code", True),
        ({"ORIN_CLAUDE_CLI_NATIVE_TOOLS": "off"}, "code", False),
        ({}, "chat", False),
        ({"ORIN_CLAUDE_CLI_NATIVE_TOOLS": "bogus"}, "code", True),
    ],
)
def test_native_tools_enabled_flag_and_mode(
    env: dict[str, str],
    mode: str,
    expected: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_mod, "NATIVE_MODES", frozenset({"code"}))
    caplog.set_level(logging.WARNING, logger="agentos.providers.claude_cli")
    result = native_tools_enabled(
        conversation_mode=mode,
        code_mode="code" if mode == "code" else "",
        env=env,
        version=(2, 1, 309),
    )
    assert result is expected
    if env.get("ORIN_CLAUDE_CLI_NATIVE_TOOLS") == "bogus":
        assert any("bogus" in rec.message for rec in caplog.records)


def test_native_disabled_outside_validated_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_mod, "NATIVE_MODES", frozenset({"code"}))
    assert (
        native_tools_enabled(
            conversation_mode="code",
            code_mode="code",
            env={},
            version=(2, 2, 0),
        )
        is False
    )
    assert (
        native_tools_enabled(
            conversation_mode="code",
            code_mode="code",
            env={},
            version=(2, 1, 308),
        )
        is False
    )
    assert (
        native_tools_enabled(
            conversation_mode="code",
            code_mode="code",
            env={},
            version=None,
        )
        is False
    )


def test_circuit_breaker_after_native_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clear_fn = getattr(provider_mod, "clear_circuit_breaker", None)
    if callable(clear_fn):
        clear_fn()

    now = [1000.0]
    monkeypatch.setattr(
        provider_mod,
        "claude_cli_version",
        lambda binary=None, **kw: (2, 1, 309),
        raising=False,
    )
    monkeypatch.setattr(
        "agentos.providers.claude_cli.command.claude_cli_version",
        lambda binary=None, **kw: (2, 1, 309),
    )

    provider = ClaudeCliProvider(
        binary=r"C:\fake\claude.exe",
        env={},
        clock=lambda: now[0],
    )
    other_provider = ClaudeCliProvider(
        binary=r"C:\other\claude.exe",
        env={},
        clock=lambda: now[0],
    )
    turn = {"conversation_mode": "code", "code_mode": "code"}

    try:
        assert provider.native_turn_available(turn) is True

        def _fail_start(_prov: Any, _req: Any) -> Any:
            raise NativeLoopUnavailable("MCP status=failed in system/init")

        monkeypatch.setattr(
            "agentos.providers.claude_cli.native_loop.start_native_turn_for_provider",
            _fail_start,
        )
        caplog.set_level(logging.WARNING, logger="agentos.providers.claude_cli")

        with pytest.raises(NativeLoopUnavailable):
            provider.start_native_turn(object())

        # Circuit breaker is now open for C:\fake\claude.exe for 10 min (600s)
        assert provider.native_turn_available(turn) is False
        # Other binary is unaffected
        assert other_provider.native_turn_available(turn) is True

        # Second failure while open does not duplicate warning log
        warning_count = sum(
            1 for r in caplog.records if "circuit breaker" in r.message.lower()
        )
        assert warning_count == 1
        with pytest.raises(NativeLoopUnavailable):
            provider.start_native_turn(object())
        assert (
            sum(1 for r in caplog.records if "circuit breaker" in r.message.lower())
            == 1
        )

        # At +300s still open
        now[0] = 1300.0
        assert provider.native_turn_available(turn) is False

        # After 600s, circuit closes again
        now[0] = 1601.0
        assert provider.native_turn_available(turn) is True
    finally:
        provider.close()
        other_provider.close()
        if callable(clear_fn):
            clear_fn()


def test_worker_returns_claude_cli_provider_for_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, *_args: object, **_kwargs: object) -> Any:
            class _Res:
                def mappings(self) -> _Res:
                    return self

                def first(self) -> dict[str, object]:
                    return {
                        "base_url": CLAUDE_CODE_LOCAL_BASE_URL,
                        "enabled": True,
                        "key_cooldown_seconds": 60,
                    }

            return _Res()

    class _FakeEngine:
        def connect(self) -> _FakeConn:
            return _FakeConn()

    class _FakeStore:
        _engine = _FakeEngine()

    class _FakeKeyRecord:
        plaintext = "sk-ant-stale-cloud-key"

    class _FakeKeyAdapter:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        def next_available_key(self, *_a: object, **_kw: object) -> _FakeKeyRecord:
            return _FakeKeyRecord()

    monkeypatch.setattr("agentos.workers.chat.PostgresProviderApiKeyAdapter", _FakeKeyAdapter)
    monkeypatch.setattr(
        "agentos.workers.chat.ProviderSecretCipher.from_environment",
        classmethod(lambda cls, required=True: object()),
    )

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = _FakeStore()  # type: ignore[assignment]

    turn = {"user_id": "u1", "provider": "anthropic", "model_id": "claude-opus-5-5"}
    transport = worker._provider_transport(turn)
    assert isinstance(transport, ClaudeCliProvider)
    assert not isinstance(transport, HTTPProviderStreamTransport)
    assert not isinstance(transport, MultiKeyProviderStreamTransport)
    assert transport.base_url == CLAUDE_CODE_LOCAL_BASE_URL
    assert transport.model == "claude-opus-5-5"
    transport.close()

    sub_transport = worker._transport_for("u1", "anthropic", "claude-haiku-5-5")
    assert isinstance(sub_transport, ClaudeCliProvider)
    assert not isinstance(sub_transport, HTTPProviderStreamTransport)
    assert not isinstance(sub_transport, MultiKeyProviderStreamTransport)
    assert sub_transport.base_url == CLAUDE_CODE_LOCAL_BASE_URL
    assert sub_transport.model == "claude-haiku-5-5"
    sub_transport.close()


def test_child_alias_resolved_by_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = ChatWorker.__new__(ChatWorker)

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, *_args: object, **_kwargs: object) -> Any:
            class _Res:
                def all(self) -> list[Any]:
                    return []

                def mappings(self) -> _Res:
                    return self

                def first(self) -> dict[str, object]:
                    return {
                        "base_url": CLAUDE_CODE_LOCAL_BASE_URL,
                        "enabled": True,
                        "key_cooldown_seconds": 60,
                        "context_window": 1_000_000,
                    }

            return _Res()

    class _FakeEngine:
        def connect(self) -> _FakeConn:
            return _FakeConn()

    class _FakeStore:
        _engine = _FakeEngine()

    worker.store = _FakeStore()  # type: ignore[assignment]
    monkeypatch.setattr(worker, "_favorite_child_model_ids", lambda _turn: ())
    monkeypatch.setattr(worker, "_allowed_child_providers", lambda _conn, _turn: {"anthropic"})
    monkeypatch.setattr(
        "agentos.providers.claude_cli.catalog.discover_cached_models",
        lambda **_kw: [
            CatalogModel(
                id="claude-opus-5-6",
                name="Claude Opus 5.6",
                context_window=1_000_000,
            )
        ],
    )

    captured_calls: list[tuple[str, str, str]] = []

    def _spy_transport_for(user_id: str, provider: str, model_id: str = "", num_ctx: int | None = None) -> Any:
        captured_calls.append((str(user_id), str(provider), str(model_id)))
        return ClaudeCliProvider(base_url=CLAUDE_CODE_LOCAL_BASE_URL, model=model_id)

    monkeypatch.setattr(worker, "_transport_for", _spy_transport_for)

    turn = {"user_id": "u1", "provider": "anthropic", "model_id": "claude-sonnet-5-5"}
    child_1 = worker._child_provider_for_turn(turn, "opus")
    assert captured_calls[-1] == ("u1", "anthropic", "claude-opus-5-6")
    child_1.close()

    child_2 = worker._child_provider_for_turn(turn, "claude-opus-5-5")
    assert captured_calls[-1] == ("u1", "anthropic", "claude-opus-5-6")
    child_2.close()

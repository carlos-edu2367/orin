from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import threading
import time
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, insert, select

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import (
    HTTPProviderStreamTransport,
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
)
from agentos.conversations.btw import (
    BtwConcurrencyGate,
    BtwEvent,
    async_stream_btw_events,
    build_btw_context,
    iter_btw_events,
    resolve_btw_effort,
    resolve_btw_transport,
)
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_compact_boundaries,
    conversation_messages,
    conversation_turn_steps,
    conversation_turns,
    conversations,
    metadata,
    provider_model_catalog,
)
from agentos.provider_catalog.opencode import stream_opencode_bridge
from agentos.providers.cli_queue import CliSlotQueue
from agentos.providers.models import (
    FinishReason,
    ProviderError,
    ProviderErrorCategory,
    ProviderUsage,
    Retryability,
)


def _make_db():
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return engine, PostgresChatStore(engine)


class FakeBtwTransport:
    def __init__(self, items: list[NormalizedStreamItem] | None = None, *, error: Exception | None = None, wait_for_cancel: threading.Event | None = None) -> None:
        self._items = list(items or [])
        self._error = error
        self._wait_for_cancel = wait_for_cancel
        self.seen_requests: list[dict[str, Any]] = []
        self.closed = False
        self.cancel_observed = threading.Event()

    def stream(self, request: dict[str, Any], *, cancel_check=None) -> Iterator[NormalizedStreamItem]:
        self.seen_requests.append(dict(request))
        if self._error is not None:
            raise self._error
        for item in self._items:
            if callable(cancel_check) and cancel_check():
                self.cancel_observed.set()
                return
            yield item
        if self._wait_for_cancel is not None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if callable(cancel_check) and cancel_check():
                    self.cancel_observed.set()
                    return
                time.sleep(0.01)

    def close(self) -> None:
        self.closed = True


def test_build_btw_context_reads_compaction_steps_and_active_turn_without_mutating_db() -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Crie o endpoint de pagamentos.",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="k1",
    )
    t1 = store.claim(r1.turn_id)
    assert t1 is not None
    store.record_step(
        t1,
        kind="assistant_tool_call",
        payload={
            "text": "Vou ler o arquivo de rotas.",
            "calls": [{"id": "c1", "name": "Read", "arguments": json.dumps({"file_path": "src/payments.py"})}],
        },
        tool_name="Read",
        tool_call_id="c1",
    )
    store.record_step(
        t1,
        kind="tool_result",
        payload={"id": "c1", "name": "Read", "status": "ok", "content": "def charge(): pass", "content_bytes": 18, "truncated": False},
        tool_name="Read",
        tool_call_id="c1",
    )
    store.delta(t1, "Endpoint criado em src/payments.py.")
    store.finish(t1)

    # Second turn still running with an in-flight tool call (no tool_result yet)
    r2 = store.create(
        user_id="user-a",
        message="Agora escreva os testes de carga.",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="k2",
        conversation_id=r1.conversation_id,
    )
    t2 = store.claim(r2.turn_id)
    assert t2 is not None
    store.record_step(
        t2,
        kind="assistant_tool_call",
        payload={
            "text": "Rodando pytest no módulo de pagamentos.",
            "calls": [{"id": "c2", "name": "Bash", "arguments": json.dumps({"command": "pytest tests/payments"})}],
        },
        tool_name="Bash",
        tool_call_id="c2",
    )
    store.delta(t2, "Iniciando execução dos testes...")

    with engine.connect() as c:
        before_msgs = c.execute(select(conversation_messages)).all()
        before_turns = c.execute(select(conversation_turns)).all()
        before_steps = c.execute(select(conversation_turn_steps)).all()
        before_conv = c.execute(select(conversations)).mappings().first()

    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Qual arquivo foi alterado no passo anterior?",
        workspace_path="C:/repo/app",
    )

    assert snapshot.provider == "anthropic"
    assert snapshot.model_id == "claude-sonnet-5-5"
    assert snapshot.active_turn_included is True
    assert snapshot.active_turn_state in {"running", "starting", "streaming"}
    assert snapshot.messages[0]["role"] == "system"
    assert "NÃO tem ferramentas" in snapshot.messages[0]["content"]
    assert "C:/repo/app" in snapshot.messages[0]["content"]

    # Every non-system message must be plain text string with strict user/assistant alternation (no raw tool_use blocks)
    non_system = snapshot.messages[1:]
    assert non_system[0]["role"] == "user"
    assert non_system[-1]["role"] == "user"
    assert "Qual arquivo foi alterado no passo anterior?" in non_system[-1]["content"]
    for idx, msg in enumerate(non_system):
        assert isinstance(msg["content"], str)
        assert "tool_calls" not in msg
        if idx > 0:
            assert msg["role"] != non_system[idx - 1]["role"]

    joined = "\n".join(m["content"] for m in non_system)
    assert "Read(file_path=src/payments.py)" in joined
    assert "def charge(): pass" in joined
    assert "Bash(command=pytest tests/payments) -> [em execução]" in joined
    assert "Iniciando execução dos testes..." in joined

    # Strict read-only check: zero rows added or modified
    with engine.connect() as c:
        assert len(c.execute(select(conversation_messages)).all()) == len(before_msgs)
        assert len(c.execute(select(conversation_turns)).all()) == len(before_turns)
        assert len(c.execute(select(conversation_turn_steps)).all()) == len(before_steps)
        after_conv = c.execute(select(conversations)).mappings().first()
        assert after_conv["updated_at"] == before_conv["updated_at"]
        assert after_conv["state"] == before_conv["state"]


def test_build_btw_context_respects_compaction_boundary_and_ownership() -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Mensagem antiga secreta antes da compactação.",
        provider="openai",
        model_id="gpt-5",
        idempotency_key="c1",
    )
    t1 = store.claim(r1.turn_id)
    assert t1 is not None
    store.delta(t1, "Resposta antiga.")
    store.finish(t1)

    with engine.begin() as c:
        c.execute(
            insert(conversation_compact_boundaries).values(
                boundary_id="cb_1",
                conversation_id=r1.conversation_id,
                user_id="user-a",
                turn_id=r1.turn_id,
                kind="compact",
                compact_trigger="manual",
                pre_tokens=1000,
                post_tokens=200,
                summary="### Primary Request and Intent\nResumo oficial da etapa 1.",
                through_sequence=2,
                created_at=datetime.now(UTC),
            )
        )

    r2 = store.create(
        user_id="user-a",
        message="Continuando depois da compactação.",
        provider="openai",
        model_id="gpt-5",
        idempotency_key="c2",
        conversation_id=r1.conversation_id,
    )
    t2 = store.claim(r2.turn_id)
    assert t2 is not None
    store.delta(t2, "Seguindo a partir do resumo.")
    store.finish(t2)

    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="O que foi resumido?",
    )
    joined = "\n".join(m["content"] for m in snapshot.messages[1:])
    assert "Resumo oficial da etapa 1." in joined
    assert "Seguindo a partir do resumo." in joined
    assert "Resposta antiga." not in joined

    with pytest.raises(LookupError):
        build_btw_context(
            engine,
            user_id="user-b",
            conversation_id=r1.conversation_id,
            question="Tentativa de outro perfil",
        )


def test_build_btw_context_trims_huge_history_to_budget() -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="A" * 10_000,
        provider="google-antigravity",
        model_id="gemini-4-neon-high",
        idempotency_key="big1",
    )
    t1 = store.claim(r1.turn_id)
    assert t1 is not None
    store.delta(t1, "B" * 10_000)
    store.finish(t1)

    now = datetime.now(UTC)
    with engine.begin() as c:
        c.execute(
            insert(provider_model_catalog).values(
                user_id="user-a",
                provider="google-antigravity",
                model_id="gemini-4-neon-high",
                display_name="Gemini 4 Neon",
                context_window=8_000,
                capabilities=["reasoning"],
                input_modalities=["text"],
                output_modalities=["text"],
                refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    for idx in range(5):
        r = store.create(
            user_id="user-a",
            message=f"Turn {idx} " + ("X" * 4_000),
            provider="google-antigravity",
            model_id="gemini-4-neon-high",
            idempotency_key=f"big_{idx + 2}",
            conversation_id=r1.conversation_id,
        )
        t = store.claim(r.turn_id)
        assert t is not None
        store.delta(t, f"Reply {idx} " + ("Y" * 4_000))
        store.finish(t)

    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Qual foi o último turno?",
    )
    total_chars = sum(len(m["content"]) for m in snapshot.messages[1:])
    assert total_chars < 30_000
    assert "Turn 4" in "\n".join(m["content"] for m in snapshot.messages[1:])
    assert "omitida(s)" in "\n".join(m["content"] for m in snapshot.messages[1:])


def test_resolve_btw_effort_picks_lightest_supported_level() -> None:
    assert resolve_btw_effort("anthropic", "claude-opus-5-5") == "low"
    assert resolve_btw_effort("openai", "gpt-5") == "low"
    assert resolve_btw_effort("google-antigravity", "gemini-4-neon-high") == "high"
    assert resolve_btw_effort("anthropic", "claude-haiku-4") is None


def test_iter_btw_events_streams_sanitized_text_usage_and_closes_transport() -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Olá",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="s1",
    )
    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Me diga em uma frase.",
    )
    transport = FakeBtwTransport(
        items=[
            NormalizedStreamItem(StreamKind.WAITING, 1, queue_position=1, queue_limit=2, wait_reason="limit"),
            NormalizedStreamItem(StreamKind.THINKING, 2, thinking="checando contexto"),
            NormalizedStreamItem(StreamKind.TEXT, 3, text="Resposta "),
            NormalizedStreamItem(StreamKind.TEXT, 4, text='<invoke name="Read"><parameter name="p">x</parameter></invoke>limpa.'),
            NormalizedStreamItem(
                StreamKind.USAGE,
                5,
                usage=ProviderUsage(input_tokens=120, output_tokens=14, total_tokens=134, cached_input_tokens=80),
            ),
            NormalizedStreamItem(StreamKind.FINISH, 6, finish_reason=FinishReason.STOP),
        ]
    )

    events = list(iter_btw_events(snapshot, transport=transport))
    kinds = [ev.event for ev in events]
    assert kinds[0] == "start"
    assert "waiting" in kinds
    assert "thinking" in kinds
    thinking_events = [ev for ev in events if ev.event == "thinking"]
    assert thinking_events[0].data["delta"] == "checando contexto"
    assert "delta" in kinds
    assert kinds[-1] == "done"
    assert transport.closed is True

    req = transport.seen_requests[0]
    assert req["tools"] == []
    assert req["tool_choice"] == "none"
    assert req["effort"] == "low"
    assert req["conversation_id"] == f"btw:{r1.conversation_id}"

    done_ev = events[-1]
    assert "<invoke" not in done_ev.data["text"]
    assert "Resposta" in done_ev.data["text"]
    assert "limpa." in done_ev.data["text"]
    assert done_ev.data["usage"] == {
        "input_tokens": 120,
        "output_tokens": 14,
        "cached_input_tokens": 80,
    }


def test_iter_btw_events_redacts_secrets_in_errors(caplog) -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Oi",
        provider="openai",
        model_id="gpt-5",
        idempotency_key="err1",
    )
    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Teste erro",
    )
    leaked_tokens = [
        "sk-supersecretkey123456",
        "AIzaSyD1234567890123456789012345678901234",
        "ghp_123456789012345678901234567890123456",
        "AKIAIOSFODNN7EXAMPLE",
        "nvapi-supersecretkey1234567890",
        "secretpass99",
    ]
    raw_secret_msg = (
        "Falha com sk-supersecretkey123456 AIzaSyD1234567890123456789012345678901234 "
        "ghp_123456789012345678901234567890123456 AKIAIOSFODNN7EXAMPLE "
        "nvapi-supersecretkey1234567890 http://alice:secretpass99@proxy.internal:11434"
    )
    with caplog.at_level("WARNING", logger="agentos.conversations.btw"):
        transport = FakeBtwTransport(error=RuntimeError(raw_secret_msg))
        events = list(iter_btw_events(snapshot, transport=transport))
    assert events[-1].event == "error"
    for token in leaked_tokens:
        assert token not in events[-1].data["message"]
        assert token not in caplog.text
    assert "[REDACTED]" in caplog.text
    assert transport.closed is True

    stream_err_transport = FakeBtwTransport(
        items=[
            NormalizedStreamItem(
                StreamKind.ERROR,
                1,
                error=ProviderError(
                    ProviderErrorCategory.PROVIDER_INTERNAL,
                    "TOOL_FREE_UNSUPPORTED",
                    f"Erro upstream com {raw_secret_msg}",
                    Retryability.NEVER,
                ),
            )
        ]
    )
    stream_err_events = list(iter_btw_events(snapshot, transport=stream_err_transport))
    assert stream_err_events[-1].event == "error"
    assert stream_err_events[-1].data["retryable"] is False
    assert "[REDACTED]" in stream_err_events[-1].data["message"]
    for token in leaked_tokens:
        assert token not in stream_err_events[-1].data["message"]

    rejected_transport = FakeBtwTransport(error=ProviderKeyRejected("HTTP 401", status_code=401, exhausted=True))
    events_rej = list(iter_btw_events(snapshot, transport=rejected_transport))
    assert events_rej[-1].event == "error"
    assert events_rej[-1].data["code"] == "provider_auth_or_quota"
    assert events_rej[-1].data["retryable"] is False


def test_async_stream_btw_events_cancels_producer_on_disconnect() -> None:
    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Oi",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="disc1",
    )
    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Vai cancelar",
    )
    disconnected = False
    transport = FakeBtwTransport(
        items=[NormalizedStreamItem(StreamKind.TEXT, 1, text="Parcial...")],
        wait_for_cancel=threading.Event(),
    )

    async def _is_disconnected() -> bool:
        return disconnected

    async def _run() -> list[BtwEvent]:
        nonlocal disconnected
        received: list[BtwEvent] = []
        async for ev in async_stream_btw_events(
            snapshot,
            transport=transport,
            is_disconnected=_is_disconnected,
            heartbeat_seconds=10.0,
        ):
            received.append(ev)
            if ev.event == "delta":
                disconnected = True
        return received

    collected = asyncio.run(_run())
    assert any(ev.event == "start" for ev in collected)
    assert any(ev.event == "delta" for ev in collected)
    assert not any(ev.event == "done" for ev in collected)
    assert transport.cancel_observed.wait(timeout=2.0)
    assert transport.closed is True


def test_async_stream_btw_events_cancels_blocked_multi_key_http_transport() -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace
    import httpx

    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Oi",
        provider="openai",
        model_id="gpt-5",
        idempotency_key="http-cancel-1",
    )
    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Cancela no HTTP bloqueado",
    )

    unblock_event = threading.Event()
    response_closed = threading.Event()
    client_closed = threading.Event()

    class _BlockingResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"Primeiro pedaço"}}]}'
            unblock_event.wait(timeout=5.0)
            if response_closed.is_set():
                raise httpx.ReadError("stream closed")
            yield 'data: {"choices":[{"delta":{"content":"Nunca deve aparecer"}}]}'

        def close(self) -> None:
            response_closed.set()
            unblock_event.set()

    class _FakeHttpxClient:
        @contextmanager
        def stream(self, method: str, endpoint: str, **kwargs):
            resp = _BlockingResponse()
            try:
                yield resp
            finally:
                resp.close()

        def close(self) -> None:
            client_closed.set()
            unblock_event.set()

    class _FakeKeyPool:
        def next_available_key(self, user_id: str, provider: str, *, exclude=frozenset()):
            if 1 in exclude:
                return None
            return SimpleNamespace(id=1, plaintext="sk-test-1234567890123456")

        def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
            pass

        def mark_exhausted(self, key_id: int) -> None:
            pass

        def mark_active(self, key_id: int) -> None:
            pass

    multi = MultiKeyProviderStreamTransport(
        key_pool=_FakeKeyPool(),
        user_id="user-a",
        provider="openai",
        cooldown_seconds=60,
        transport_factory=lambda api_key: HTTPProviderStreamTransport(
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key=api_key,
            model="gpt-5",
            client=_FakeHttpxClient(),  # type: ignore[arg-type]
        ),
        model="gpt-5",
        base_url="https://api.openai.com/v1",
    )

    disconnected = False

    async def _is_disconnected() -> bool:
        return disconnected

    async def _run() -> list[BtwEvent]:
        nonlocal disconnected
        received: list[BtwEvent] = []
        async for ev in async_stream_btw_events(
            snapshot,
            transport=multi,
            is_disconnected=_is_disconnected,
            heartbeat_seconds=10.0,
        ):
            received.append(ev)
            if ev.event == "delta":
                disconnected = True
        return received

    collected = asyncio.run(_run())
    assert any(ev.event == "delta" and "Primeiro pedaço" in ev.data["delta"] for ev in collected)
    assert not any(ev.event == "done" for ev in collected)
    assert response_closed.wait(timeout=2.0)


def test_http_transport_idle_timeout_maps_to_btw_timeout() -> None:
    from contextlib import contextmanager
    import httpx

    engine, store = _make_db()
    r1 = store.create(
        user_id="user-a",
        message="Oi",
        provider="openai",
        model_id="gpt-5",
        idempotency_key="http-timeout-1",
    )
    snapshot = build_btw_context(
        engine,
        user_id="user-a",
        conversation_id=r1.conversation_id,
        question="Timeout",
    )
    seen_timeout: dict[str, Any] = {}

    class _TimeoutClient:
        @contextmanager
        def stream(self, method: str, endpoint: str, **kwargs):
            seen_timeout["timeout"] = kwargs.get("timeout")
            raise httpx.ReadTimeout("timed out")
            yield  # pragma: no cover

        def close(self) -> None:
            return None

    transport = HTTPProviderStreamTransport(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-1234567890123456",
        model="gpt-5",
        client=_TimeoutClient(),  # type: ignore[arg-type]
    )
    events = list(iter_btw_events(snapshot, transport=transport, idle_timeout_seconds=12.5))
    assert seen_timeout["timeout"] is not None
    assert seen_timeout["timeout"].read == 12.5
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "btw_timeout"
    assert events[-1].data["retryable"] is True


def test_opencode_bridge_tool_choice_none_disables_all_native_tools_and_cleans_session() -> None:
    import httpx

    seen_posts: list[tuple[str, dict[str, Any]]] = []
    seen_deletes: list[str] = []

    class _FakeBridgeManager:
        def ensure_running(self, base_url: str) -> None:
            return None

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/session":
            return httpx.Response(200, json={"id": "sess_ephemeral_1"})
        if request.method == "POST" and request.url.path == "/session/sess_ephemeral_1/message":
            body = json.loads(request.content.decode("utf-8"))
            seen_posts.append((request.url.path, body))
            return httpx.Response(
                200,
                json={
                    "info": {"tokens": {"input": 25, "output": 8}},
                    "parts": [{"type": "text", "text": "Resposta sem ferramentas."}],
                },
            )
        if request.method == "DELETE" and request.url.path == "/session/sess_ephemeral_1":
            seen_deletes.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        items = list(
            stream_opencode_bridge(
                base_url="http://127.0.0.1:18446",
                model="opencode/big-pickle",
                messages=[
                    {"role": "system", "content": "Contexto /btw"},
                    {"role": "user", "content": "Qual o nome da tabela?"},
                ],
                tools=[],
                tool_choice="none",
                client=client,
                bridge_manager=_FakeBridgeManager(),
            )
        )

    assert len(seen_posts) == 1
    payload = seen_posts[0][1]
    assert payload["tools"]
    assert all(enabled is False for enabled in payload["tools"].values())
    assert payload["tools"]["read"] is False
    assert "Tool execution is strictly DISABLED" in str(payload.get("system") or "")
    assert seen_deletes == ["/session/sess_ephemeral_1"]
    assert any(item.kind is StreamKind.TEXT and "Resposta sem ferramentas." in (item.text or "") for item in items)

    seen_deletes.clear()

    def _free_tier_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/session":
            return httpx.Response(200, json={"id": "sess_ephemeral_2"})
        if request.method == "POST" and request.url.path == "/session/sess_ephemeral_2/message":
            return httpx.Response(403, json={"error": "FreeTierError"})
        if request.method == "DELETE" and request.url.path == "/session/sess_ephemeral_2":
            seen_deletes.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(_free_tier_handler)) as client:
        err_items = list(
            stream_opencode_bridge(
                base_url="http://127.0.0.1:18446",
                model="opencode/big-pickle",
                messages=[{"role": "user", "content": "Oi"}],
                tools=[],
                tool_choice="none",
                client=client,
                bridge_manager=_FakeBridgeManager(),
            )
        )
    assert seen_deletes == ["/session/sess_ephemeral_2"]
    assert err_items[0].kind is StreamKind.ERROR
    assert err_items[0].error is not None
    assert str(err_items[0].error.code) == "TOOL_FREE_UNSUPPORTED"
    assert err_items[0].error.retryability is Retryability.NEVER


def test_ollama_btw_budget_matches_num_ctx_and_resolve_transport_uses_worker_window(monkeypatch) -> None:
    from agentos.persistence.postgres.schema import provider_configurations

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine, store = _make_db()
    now = datetime.now(UTC)

    with engine.begin() as c:
        c.execute(
            insert(provider_configurations).values(
                user_id="user-a",
                provider="ollama",
                secret_ref="",
                base_url="http://127.0.0.1:11434",
                enabled=True,
                key_cooldown_seconds=60,
                created_at=now,
                updated_at=now,
            )
        )
        c.execute(
            insert(provider_model_catalog).values(
                user_id="user-a",
                provider="ollama",
                model_id="qwen3:32b",
                display_name="Qwen 3 32B",
                context_window=128_000,
                capabilities=["reasoning"],
                input_modalities=["text"],
                output_modalities=["text"],
                refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        c.execute(
            insert(provider_model_catalog).values(
                user_id="user-a",
                provider="ollama",
                model_id="llama3:8b",
                display_name="Llama 3 8B",
                context_window=8_192,
                capabilities=[],
                input_modalities=["text"],
                output_modalities=["text"],
                refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    r_big = store.create(
        user_id="user-a",
        message="Contexto longo " + ("A" * 10_000),
        provider="ollama",
        model_id="qwen3:32b",
        idempotency_key="ol1",
    )
    snap_big = build_btw_context(engine, user_id="user-a", conversation_id=r_big.conversation_id, question="Resumo?")
    transport_big = resolve_btw_transport(engine, user_id="user-a", snapshot=snap_big)
    try:
        assert isinstance(transport_big, HTTPProviderStreamTransport)
        assert transport_big._num_ctx == 72_000
    finally:
        transport_big.close()

    r_small = store.create(
        user_id="user-a",
        message="Texto pequeno 1 " + ("B" * 6_000),
        provider="ollama",
        model_id="llama3:8b",
        idempotency_key="ol2",
    )
    for idx in range(3):
        store.create(
            user_id="user-a",
            message=f"Msg {idx} " + ("C" * 5_000),
            provider="ollama",
            model_id="llama3:8b",
            idempotency_key=f"ol2_{idx}",
            conversation_id=r_small.conversation_id,
        )
    snap_small = build_btw_context(engine, user_id="user-a", conversation_id=r_small.conversation_id, question="Qual o último?")
    total_chars_small = sum(len(m["content"]) for m in snap_small.messages[1:])
    assert total_chars_small < 12_000


def test_cli_slot_queue_prioritizes_primary_turns_over_ephemeral_btw() -> None:
    queue = CliSlotQueue(limit=1)
    holder = queue.enqueue("conv_other")
    assert holder.wait(0.05) is True

    queue._use_seq = 10
    queue._last_used_seq_by_conv["conv_main"] = 10

    btw_ticket = queue.enqueue("btw:conv_main")
    main_ticket = queue.enqueue("conv_main")

    assert main_ticket.position() == 1
    assert btw_ticket.position() == 2

    holder.release()
    assert main_ticket.wait(0.2) is True
    assert btw_ticket.wait(0.0) is False

    main_ticket.release()
    assert btw_ticket.wait(0.2) is True
    btw_ticket.release()


def test_btw_concurrency_gate_bounds_per_conversation_and_per_user() -> None:
    gate = BtwConcurrencyGate(max_per_conversation=2, max_per_user=3)
    assert gate.try_acquire("u1", "c1") is True
    assert gate.try_acquire("u1", "c1") is True
    assert gate.try_acquire("u1", "c1") is False
    assert gate.try_acquire("u1", "c2") is True
    assert gate.try_acquire("u1", "c2") is False
    gate.release("u1", "c1")
    assert gate.try_acquire("u1", "c1") is True
    gate.release("u1", "c1")
    gate.release("u1", "c1")
    gate.release("u1", "c2")

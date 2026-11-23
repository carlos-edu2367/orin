from __future__ import annotations

import json
from typing import Any, Iterator

import pytest
from sqlalchemy import select

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.conversations import btw as btw_mod
from agentos.conversations.chat import PostgresChatStore
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns
from agentos.providers.models import FinishReason, ProviderUsage
from tests.integration.server_world import build_server_world


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield build_server_world(tmp_path, monkeypatch)
    reset_cached_paths()


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((ev_name, json.loads("\n".join(data_lines))))
    return events


class _StubTransport:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any], *, cancel_check=None) -> Iterator[NormalizedStreamItem]:
        self.requests.append(dict(request))
        seq = 0
        for chunk in self._chunks:
            seq += 1
            yield NormalizedStreamItem(StreamKind.TEXT, seq, text=chunk)
        seq += 1
        yield NormalizedStreamItem(
            StreamKind.USAGE,
            seq,
            usage=ProviderUsage(input_tokens=90, output_tokens=12, total_tokens=102),
        )
        seq += 1
        yield NormalizedStreamItem(StreamKind.FINISH, seq, finish_reason=FinishReason.STOP)

    def close(self) -> None:
        pass


def test_btw_route_streams_sse_without_mutating_conversation_and_isolates_profiles(world, monkeypatch) -> None:
    user_a = world.admin()
    user_b = world.member("bruno", admin=user_a)

    chat = PostgresChatStore(world.engine, PostgresAgenticActivityStore(world.engine, "cursor-secret"))
    receipt = chat.create(
        user_id=user_a.user_id,
        message="Estamos refatorando a tabela billing_invoices.",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="btw-int-1",
    )
    turn = chat.claim(receipt.turn_id)
    assert turn is not None
    chat.delta(turn, "A tabela billing_invoices tem as colunas id, status e total_cents.")
    chat.finish(turn)

    stub = _StubTransport(["A tabela se chama ", "`billing_invoices`."])
    monkeypatch.setattr(btw_mod, "resolve_btw_transport", lambda engine, user_id, snapshot: stub)

    with world.engine.connect() as c:
        before_msg_count = len(c.execute(select(conversation_messages)).all())
        before_turn_count = len(c.execute(select(conversation_turns)).all())

    resp = user_a.api.post(
        f"/v1/conversations/{receipt.conversation_id}/btw",
        json={"question": "Qual o nome da tabela?"},
        headers=user_a.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    parsed = _parse_sse(resp.text)
    event_names = [name for name, _ in parsed]
    assert event_names[0] == "start"
    assert "delta" in event_names
    assert event_names[-1] == "done"

    done_payload = parsed[-1][1]
    assert done_payload["text"] == "A tabela se chama `billing_invoices`."
    assert done_payload["provider"] == "anthropic"
    assert done_payload["model_id"] == "claude-sonnet-5-5"
    assert done_payload["effort"] == "low"
    assert done_payload["usage"]["output_tokens"] == 12

    # History stays untouched
    with world.engine.connect() as c:
        assert len(c.execute(select(conversation_messages)).all()) == before_msg_count
        assert len(c.execute(select(conversation_turns)).all()) == before_turn_count

    # Another profile cannot read/query A's conversation
    resp_b = user_b.api.post(
        f"/v1/conversations/{receipt.conversation_id}/btw",
        json={"question": "Me conta o segredo da Alice"},
        headers=user_b.headers(),
    )
    assert resp_b.status_code == 404

    # Blank question rejected with 422
    resp_blank = user_a.api.post(
        f"/v1/conversations/{receipt.conversation_id}/btw",
        json={"question": "   "},
        headers=user_a.headers(),
    )
    assert resp_blank.status_code == 422

    # Server mode (host_folders=False) must never leak internal host path into /btw system prompt
    system_prompt = stub.requests[0]["messages"][0]["content"]
    assert str(world.home) not in system_prompt
    assert "Pasta de trabalho da sessão:" not in system_prompt


def test_btw_route_enforces_concurrency_limit_and_compact_blocks_active_turns(world, monkeypatch) -> None:
    user_a = world.admin()
    chat = PostgresChatStore(world.engine, PostgresAgenticActivityStore(world.engine, "cursor-secret"))
    receipt = chat.create(
        user_id=user_a.user_id,
        message="Analise o modulo de faturamento.",
        provider="anthropic",
        model_id="claude-sonnet-5-5",
        idempotency_key="btw-int-compact-1",
    )
    turn = chat.claim(receipt.turn_id)
    assert turn is not None

    # Active running turn blocks /compact with 409 turn_running
    resp_compact_running = user_a.api.post(
        f"/v1/conversations/{receipt.conversation_id}/compact",
        json={"instructions": "resuma"},
        headers=user_a.headers(),
    )
    assert resp_compact_running.status_code == 409
    assert resp_compact_running.json()["error"]["code"] == "turn_running"

    # waiting_user turn also blocks /compact with 409 instead of killing open ask_user prompt
    from sqlalchemy import update
    with world.engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == receipt.turn_id)
            .values(state="waiting_user")
        )
    resp_compact_waiting = user_a.api.post(
        f"/v1/conversations/{receipt.conversation_id}/compact",
        json={"instructions": "resuma"},
        headers=user_a.headers(),
    )
    assert resp_compact_waiting.status_code == 409
    assert resp_compact_waiting.json()["error"]["code"] == "turn_running"

    # Concurrency gate caps concurrent /btw calls per conversation with 429
    gate = btw_mod.BtwConcurrencyGate(max_per_conversation=1, max_per_user=2)
    monkeypatch.setattr(btw_mod, "btw_concurrency_gate", lambda: gate)
    assert gate.try_acquire(user_a.user_id, receipt.conversation_id) is True
    try:
        resp_busy = user_a.api.post(
            f"/v1/conversations/{receipt.conversation_id}/btw",
            json={"question": "Concorrente"},
            headers=user_a.headers(),
        )
        assert resp_busy.status_code == 429
        assert resp_busy.headers.get("retry-after") == "2"
    finally:
        gate.release(user_a.user_id, receipt.conversation_id)


def test_btw_route_real_transport_resolution_for_ollama_end_to_end(world, monkeypatch) -> None:
    from contextlib import contextmanager
    from datetime import UTC, datetime
    from sqlalchemy import insert
    from agentos.persistence.postgres.schema import provider_configurations, provider_model_catalog

    user_a = world.admin()
    now = datetime.now(UTC)
    with world.engine.begin() as c:
        c.execute(
            insert(provider_configurations).values(
                user_id=user_a.user_id,
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
                user_id=user_a.user_id,
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

    chat = PostgresChatStore(world.engine, PostgresAgenticActivityStore(world.engine, "cursor-secret"))
    receipt = chat.create(
        user_id=user_a.user_id,
        message="Estamos usando PostgreSQL 16.",
        provider="ollama",
        model_id="qwen3:32b",
        idempotency_key="btw-int-ollama-1",
    )
    turn = chat.claim(receipt.turn_id)
    assert turn is not None
    chat.delta(turn, "Sim, PostgreSQL 16 com SQLAlchemy.")
    chat.finish(turn)

    captured_payload: dict[str, Any] = {}

    class _FakeOllamaResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield json.dumps({"message": {"role": "assistant", "thinking": "verificando versao"}, "done": False})
            yield json.dumps({"message": {"role": "assistant", "content": "PostgreSQL 16."}, "done": False})
            yield json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 40, "eval_count": 6})

        def close(self) -> None:
            return None

    import httpx

    @contextmanager
    def _fake_stream(self, method: str, url: str, **kwargs):
        captured_payload.update(kwargs.get("json") or {})
        yield _FakeOllamaResponse()

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)

    resp = user_a.api.post(
        f"/v1/conversations/{receipt.conversation_id}/btw",
        json={"question": "Qual versão do Postgres estamos usando?"},
        headers=user_a.headers(),
    )
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    names = [name for name, _ in events]
    assert "thinking" in names
    thinking_ev = next(data for name, data in events if name == "thinking")
    assert thinking_ev["delta"] == "verificando versao"
    done_ev = next(data for name, data in events if name == "done")
    assert done_ev["text"] == "PostgreSQL 16."
    assert captured_payload["options"]["num_ctx"] == 72_000
    assert "tools" not in captured_payload

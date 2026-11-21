from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.api.custom_agent_routes import CustomAgentFields
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_turns, metadata
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_adapter import CliTurnRequest


def _setup(tmp_path: Path):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    app_service = ChatApplication(store)
    security = InMemorySecurityService()
    security.add_pat("pat-1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    client = TestClient(
        create_app(
            ApiServices(
                security=security,
                conversation_application=app_service,
                workspace_root=tmp_path / "workspaces",
            )
        )
    )
    return client, store, engine


def test_post_create_and_send_with_effort_persists_claims_and_reaches_cli_command(
    tmp_path: Path,
) -> None:
    client, store, engine = _setup(tmp_path)
    headers = {"Authorization": "Bearer pat-1", "Idempotency-Key": "k-create-1"}

    resp = client.post(
        "/v1/conversations",
        json={
            "message": "Analyze this module",
            "selection": {"provider": "anthropic", "model_id": "claude-sonnet-5-5"},
            "effort": "xhigh",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    turn_id = body["turn_id"]
    conv_id = body["conversation_id"]

    # Stored in conversation_turns.effort
    with engine.connect() as conn:
        row = conn.execute(
            select(conversation_turns.c.effort).where(conversation_turns.c.turn_id == turn_id)
        ).first()
    assert row is not None and row[0] == "xhigh"

    # Returned on claim() so TurnSession._effort reads turn["effort"] == "xhigh"
    claimed = store.claim(turn_id)
    assert claimed is not None
    assert claimed["effort"] == "xhigh"

    recorded_events: list[tuple] = []
    orig_record = store.record

    def _capture(turn, event_type, summary, payload=None, **kwargs):
        recorded_events.append((event_type, summary, dict(payload or {})))
        return orig_record(turn, event_type, summary, payload, **kwargs)

    store.record = _capture  # type: ignore[method-assign]

    class _Agents:
        def list(self):
            return []

    session = TurnSession(
        turn=claimed,
        store=store,
        agents_store=_Agents(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path / "ws",
        enable_subagents=False,
    )
    runtime = session.build_runtime()
    assert runtime.effort == "xhigh"

    # Claude CLI adapter produces --effort xhigh
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("system", encoding="utf-8")
    argv = ClaudeCliAdapter().build_command(
        CliTurnRequest(
            binary="claude",
            system_prompt_file=prompt_file,
            model="claude-sonnet-5-5",
            effort=runtime.effort,
        )
    )
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "xhigh"

    # turn.started carries effort and effort_source
    turn_started_with_effort = [
        payload
        for ev_type, _summary, payload in recorded_events
        if ev_type == AgentActivityEventType.TURN_STARTED and payload.get("effort") == "xhigh"
    ]
    assert len(turn_started_with_effort) >= 1
    assert turn_started_with_effort[0]["effort_source"] == "turn"

    # Complete turn 1 so we can send a follow-up with boost=True
    store.finish(claimed)

    resp2 = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "message": "Think even deeper",
            "selection": {"provider": "anthropic", "model_id": "claude-sonnet-5-5"},
            "effort": "high",
            "boost": True,
        },
        headers={"Authorization": "Bearer pat-1", "Idempotency-Key": "k-send-2"},
    )
    assert resp2.status_code == 201, resp2.text
    turn_id_2 = resp2.json()["turn_id"]
    claimed_2 = store.claim(turn_id_2)
    assert claimed_2 is not None
    assert claimed_2["effort"] == "high"
    assert claimed_2["effort_boost"] is True

    session2 = TurnSession(
        turn=claimed_2,
        store=store,
        agents_store=_Agents(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path / "ws",
        enable_subagents=False,
    )
    # boost steps high -> xhigh
    assert session2.build_runtime().effort == "xhigh"

    engine.dispose()


def test_post_rejects_invalid_effort_with_422_and_preserves_omitted_default(tmp_path: Path) -> None:
    client, store, engine = _setup(tmp_path)

    bad = client.post(
        "/v1/conversations",
        json={
            "message": "Hello",
            "selection": {"provider": "anthropic", "model_id": "claude-sonnet-5-5"},
            "effort": "extreme",
        },
        headers={"Authorization": "Bearer pat-1", "Idempotency-Key": "k-bad-1"},
    )
    assert bad.status_code == 422

    # Omitted effort behaves identically to 0.12.31 (effort is None)
    ok = client.post(
        "/v1/conversations",
        json={
            "message": "Hello",
            "selection": {"provider": "anthropic", "model_id": "claude-sonnet-5-5"},
        },
        headers={"Authorization": "Bearer pat-1", "Idempotency-Key": "k-ok-1"},
    )
    assert ok.status_code == 201
    claimed = store.claim(ok.json()["turn_id"])
    assert claimed is not None
    assert claimed["effort"] is None
    assert claimed["effort_boost"] is False

    # Invalid effort on send message also gives 422
    bad_send = client.post(
        f"/v1/conversations/{ok.json()['conversation_id']}/messages",
        json={"message": "Follow up", "effort": "supermax"},
        headers={"Authorization": "Bearer pat-1", "Idempotency-Key": "k-bad-2"},
    )
    assert bad_send.status_code == 422

    engine.dispose()


def test_custom_agent_fields_accepts_ultra_effort() -> None:
    fields = CustomAgentFields(
        description="Codex deep reviewer",
        prompt="Review thoroughly",
        effort="ultra",
    )
    assert fields.effort == "ultra"

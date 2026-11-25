from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.conversations.snapshot_queries import (
    compact_snapshot_activities,
    query_activity_window,
)
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import metadata


def _build_store() -> tuple[PostgresChatStore, PostgresAgenticActivityStore]:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret")
    store = PostgresChatStore(engine, activity_store)
    return store, activity_store


def _populate_heavy_subagent_turn(
    store: PostgresChatStore,
    *,
    conversation_id: str | None = None,
    idempotency_key: str,
    rounds: int = 8,
    subagents_per_round: int = 2,
    tools_per_subagent: int = 35,
    finish_turn: bool = True,
    leave_last_subagent_open: bool = False,
) -> tuple[str, dict[str, object], list[str]]:
    """Create a turn with >2,000 raw activity events interleaving main-agent deltas and noisy subagents."""
    receipt = store.create(
        conversation_id=conversation_id,
        user_id="user-1",
        message=f"Execute pipeline {idempotency_key}",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4.5",
        idempotency_key=idempotency_key,
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    cid = str(turn["conversation_id"])
    main_agent_id = f"agent:{cid}:main"
    expected_paragraphs: list[str] = []

    # Structural turn.started events that must survive even after the turn settles.
    store.record(
        turn,
        AgentActivityEventType.TURN_STARTED,
        "Turn started",
        {"state": "working"},
        agent_id=main_agent_id,
    )
    store.record(
        turn,
        AgentActivityEventType.TURN_STARTED,
        "Conexão com o modelo caiu; retomando",
        {"stream_resuming": True},
        agent_id=main_agent_id,
    )
    store.record(
        turn,
        AgentActivityEventType.TURN_STARTED,
        "Mensagem da fila entregue ao agente",
        {"queue_id": f"q-{idempotency_key}"},
        agent_id=main_agent_id,
    )

    for r in range(rounds):
        paragraph = f"Rodada {r + 1}: iniciando lote paralelo de subagentes."
        expected_paragraphs.append(paragraph)
        # Emit paragraph in multiple chunks to also verify delta compaction.
        store.delta(turn, f"Rodada {r + 1}: ")
        store.delta(turn, "iniciando lote paralelo de subagentes.")

        # Emit noisy main-agent waiting heartbeats and tool.requested events.
        for hb in range(3):
            store.record(
                turn,
                AgentActivityEventType.TURN_STARTED,
                f"pensando há {(hb + 1) * 20} s",
                {"state": "waiting_model", "waiting_model": True, "seconds": (hb + 1) * 20},
                agent_id=main_agent_id,
            )

        for s in range(subagents_per_round):
            sub_id = f"agent:{cid}:sub-r{r}-s{s}"
            sub_name = f"worker-r{r}-s{s}"
            is_last_sub = (r == rounds - 1) and (s == subagents_per_round - 1)

            store.record(
                turn,
                AgentActivityEventType.AGENT_MESSAGE_SENT,
                f"Delegou tarefa para {sub_name}",
                {"to_agent_id": sub_id, "agent_name": sub_name},
                agent_id=main_agent_id,
            )

            for t_idx in range(tools_per_subagent):
                inv_id = f"inv-{r}-{s}-{t_idx}"
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_REQUESTED,
                    f"Solicitou read_file {t_idx}",
                    {"tool_name": "read_file", "invocation_id": inv_id, "path": f"src/f_{r}_{s}_{t_idx}.py"},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TURN_STARTED,
                    f"{sub_name} trabalhando",
                    {"state": "working", "seconds": t_idx * 5},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_STARTED,
                    f"Lendo src/f_{r}_{s}_{t_idx}.py",
                    {"tool_name": "read_file", "invocation_id": inv_id, "path": f"src/f_{r}_{s}_{t_idx}.py"},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_PROGRESSED,
                    f"Progresso 50% {t_idx}",
                    {"tool_name": "read_file", "invocation_id": inv_id},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_FINISHED,
                    f"Leu arquivo {t_idx}",
                    {"tool_name": "read_file", "invocation_id": inv_id, "status": "succeeded"},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                if t_idx % 5 == 0:
                    store.record(
                        turn,
                        AgentActivityEventType.AGENT_PROGRESS,
                        f"{sub_name} concluiu {t_idx + 1} passos",
                        {"agent_id": sub_id, "tool_calls": t_idx + 1},
                        agent_id=sub_id,
                        parent_agent_id=main_agent_id,
                    )

            if leave_last_subagent_open and is_last_sub:
                # Leave one tool open with multiple tool.progressed updates and multiple heartbeats/progress.
                open_inv = "inv-open-live"
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_STARTED,
                    "Executando pytest",
                    {"tool_name": "run_command", "invocation_id": open_inv, "command": "pytest -q"},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_PROGRESSED,
                    "pytest 10%",
                    {"tool_name": "run_command", "invocation_id": open_inv},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TOOL_PROGRESSED,
                    "pytest 80%",
                    {"tool_name": "run_command", "invocation_id": open_inv},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.AGENT_PROGRESS,
                    f"{sub_name} em andamento final",
                    {"agent_id": sub_id, "tool_calls": tools_per_subagent + 1},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
                store.record(
                    turn,
                    AgentActivityEventType.TURN_STARTED,
                    f"{sub_name} trabalhando agora",
                    {"state": "working", "seconds": 120},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )
            else:
                store.record(
                    turn,
                    AgentActivityEventType.AGENT_MESSAGE_RECEIVED,
                    f"Recebeu resposta de {sub_name}",
                    {"from_agent_id": sub_id, "agent_name": sub_name},
                    agent_id=sub_id,
                    parent_agent_id=main_agent_id,
                )

    final_paragraph = "Resumo final consolidado de todas as rodadas."
    expected_paragraphs.append(final_paragraph)
    store.delta(turn, final_paragraph)

    if finish_turn:
        store.finish(turn)

    return cid, turn, expected_paragraphs


def test_completed_heavy_subagent_turn_preserves_main_backbone_and_drops_noise() -> None:
    store, _ = _build_store()
    # 8 rounds * 2 subagents * 35 tools * ~5 events each = ~2,800+ raw events in a single completed turn
    cid, _, expected_paragraphs = _populate_heavy_subagent_turn(
        store,
        idempotency_key="heavy-completed",
        rounds=8,
        subagents_per_round=2,
        tools_per_subagent=35,
        finish_turn=True,
    )

    snap = store.get(cid, "user-1")
    activities = snap["activities"]

    # Default activity_limit is 600
    assert len(activities) <= 600

    # (a) tool.requested is unconditionally discarded
    assert not any(a["event_type"] == "tool.requested" for a in activities)

    # (b) tool.started / tool.progressed are absorbed into tool.finished and metadata enriched
    assert not any(a["event_type"] in {"tool.started", "tool.progressed"} for a in activities)
    sub_finished = [
        a for a in activities if a["event_type"] == "tool.finished" and a["parent_agent_id"] is not None
    ]
    assert len(sub_finished) > 0
    assert all(
        isinstance(a.get("payload"), dict) and str(a["payload"].get("path") or "").startswith("src/f_")
        for a in sub_finished
    )

    # (c) settled turns keep 0 agent.progress and 0 non-structural turn.started, while keeping structural ones
    assert not any(a["event_type"] == "agent.progress" for a in activities)
    surviving_turn_started = [a for a in activities if a["event_type"] == "turn.started"]
    assert len(surviving_turn_started) == 2
    assert any(a["payload"].get("stream_resuming") is True for a in surviving_turn_started)
    assert any(a["payload"].get("queue_id") == "q-heavy-completed" for a in surviving_turn_started)

    # (d) ALL main-agent assistant.delta paragraphs and ALL agent.message_sent / agent.message_received survive
    main_deltas = [
        str((a.get("payload") or {}).get("content") or "")
        for a in activities
        if a["event_type"] == "assistant.delta" and a["parent_agent_id"] is None
    ]
    assert main_deltas == expected_paragraphs

    sent = [a for a in activities if a["event_type"] == "agent.message_sent"]
    received = [a for a in activities if a["event_type"] == "agent.message_received"]
    assert len(sent) == 16
    assert len(received) == 16

    # Fair Tier 2 share: every single one of the 16 subagents keeps its latest tool calls
    subagents_with_tools = {a["agent_id"] for a in sub_finished}
    assert len(subagents_with_tools) == 16


def test_running_heavy_subagent_turn_absorbs_completed_tools_and_dedups_live_heartbeats() -> None:
    store, _ = _build_store()
    # Running turn with >2,000 events and one still-active subagent with an open tool call
    cid, turn, expected_paragraphs = _populate_heavy_subagent_turn(
        store,
        idempotency_key="heavy-running",
        rounds=6,
        subagents_per_round=2,
        tools_per_subagent=35,
        finish_turn=False,
        leave_last_subagent_open=True,
    )
    open_sub_id = f"agent:{cid}:sub-r5-s1"

    snap = store.get(cid, "user-1", activity_limit=200)
    activities = snap["activities"]
    assert len(activities) <= 200

    # (a) tool.requested is discarded even in running turns
    assert not any(a["event_type"] == "tool.requested" for a in activities)

    # (b) Completed tool.started/tool.progressed are absorbed even while the turn is running;
    # only the genuinely open tool keeps its tool.started and its SINGLE latest tool.progressed
    started_events = [a for a in activities if a["event_type"] == "tool.started"]
    progressed_events = [a for a in activities if a["event_type"] == "tool.progressed"]
    assert len(started_events) == 1
    assert started_events[0]["payload"].get("invocation_id") == "inv-open-live"
    assert len(progressed_events) == 1
    assert progressed_events[0]["summary"] == "pytest 80%"

    # Finished tools on the running turn still received path enrichment from their absorbed tool.started
    sub_finished = [
        a for a in activities if a["event_type"] == "tool.finished" and a["parent_agent_id"] is not None
    ]
    assert len(sub_finished) > 0
    assert all(
        isinstance(a.get("payload"), dict) and str(a["payload"].get("path") or "").startswith("src/f_")
        for a in sub_finished
    )

    # (c) In running turns, at most 1 non-structural turn.started and 1 agent.progress per (turn_id, agent_id)
    non_structural_ts_by_agent: dict[str, int] = {}
    progress_by_agent: dict[str, int] = {}
    for a in activities:
        if a["event_type"] == "turn.started":
            p = a.get("payload") or {}
            if not (p.get("stream_resuming") or p.get("queue_id") or p.get("origin") == "friend"):
                non_structural_ts_by_agent[a["agent_id"]] = non_structural_ts_by_agent.get(a["agent_id"], 0) + 1
        elif a["event_type"] == "agent.progress":
            progress_by_agent[a["agent_id"]] = progress_by_agent.get(a["agent_id"], 0) + 1

    assert all(count <= 1 for count in non_structural_ts_by_agent.values())
    assert all(count <= 1 for count in progress_by_agent.values())
    assert non_structural_ts_by_agent.get(open_sub_id) == 1
    assert progress_by_agent.get(open_sub_id) == 1

    # (d) ALL main-agent assistant.delta paragraphs survive even with a tight activity_limit=200
    main_deltas = [
        str((a.get("payload") or {}).get("content") or "")
        for a in activities
        if a["event_type"] == "assistant.delta" and a["parent_agent_id"] is None
    ]
    assert main_deltas == expected_paragraphs

    # Active subagent gets prioritized in Tier 2 budget over settled subagents
    open_sub_tools = [a for a in sub_finished if a["agent_id"] == open_sub_id]
    settled_sub_counts = [
        sum(1 for a in sub_finished if a["agent_id"] == aid)
        for aid in {a["agent_id"] for a in sub_finished if a["agent_id"] != open_sub_id}
    ]
    assert len(open_sub_tools) >= max(settled_sub_counts)


def test_pagination_across_turns_and_tier1_overflow_never_duplicates_or_loops() -> None:
    store, _ = _build_store()

    # Turn 1 (older) + Turn 2 (newer), both with many main-agent tools so Tier 1 itself exceeds limit=40
    cid = None
    for turn_idx in range(2):
        receipt = store.create(
            conversation_id=cid,
            user_id="user-1",
            message=f"Turn {turn_idx}",
            provider="openrouter",
            model_id="anthropic/claude-sonnet-4.5",
            idempotency_key=f"page-turn-{turn_idx}",
        )
        turn = store.claim(receipt.turn_id)
        assert turn is not None
        cid = str(turn["conversation_id"])
        main_id = f"agent:{cid}:main"
        for step in range(30):
            store.delta(turn, f"T{turn_idx}-P{step}")
            inv_id = f"inv-{turn_idx}-{step}"
            store.record(
                turn,
                AgentActivityEventType.TOOL_STARTED,
                f"Start {step}",
                {"tool_name": "read_file", "invocation_id": inv_id, "path": f"file_{turn_idx}_{step}.py"},
                agent_id=main_id,
            )
            store.record(
                turn,
                AgentActivityEventType.TOOL_FINISHED,
                f"Done {step}",
                {"tool_name": "read_file", "invocation_id": inv_id, "status": "succeeded"},
                agent_id=main_id,
            )
        store.finish(turn)

    assert cid is not None
    snap = store.get(cid, "user-1", activity_limit=40)
    assert len(snap["activities"]) == 40
    assert snap["has_older_activities"] is True
    assert isinstance(snap["oldest_activity_id"], int)

    seen_event_ids = [str(a["event_id"]) for a in snap["activities"]]
    assert len(seen_event_ids) == len(set(seen_event_ids))

    oldest_id = int(snap["oldest_activity_id"])
    has_older = True
    pages = 0
    while has_older:
        pages += 1
        assert pages <= 10, "Pagination loop detected"
        page = store.list_older_activities(cid, "user-1", before_id=oldest_id, limit=40)
        page_events = page["activities"]
        for ev in page_events:
            assert str(ev["event_id"]) not in seen_event_ids, f"Duplicate event_id {ev['event_id']}"
            seen_event_ids.append(str(ev["event_id"]))
        has_older = bool(page["has_older_activities"])
        next_oldest = page["oldest_activity_id"]
        if has_older:
            assert isinstance(next_oldest, int)
            assert next_oldest < oldest_id, "oldest_activity_id did not strictly decrease"
            oldest_id = next_oldest

    # Every turn's paragraphs were recovered across initial window + older pages
    assert len(seen_event_ids) >= 120


def test_chat_application_default_limit_600_and_cap_1000() -> None:
    store, _ = _build_store()
    receipt = store.create(
        user_id="user-1",
        message="Many main tools",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4.5",
        idempotency_key="cap-test",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    cid = str(turn["conversation_id"])
    for idx in range(700):
        store.record(
            turn,
            AgentActivityEventType.FILESYSTEM_READ,
            f"Leu arquivo_{idx}.py",
            {"tool_name": "read_file", "path": f"arquivo_{idx}.py"},
        )
    store.finish(turn)

    app = ChatApplication(store=store)
    default_snap = app.get(cid, "user-1")
    assert len(default_snap["activities"]) == 600
    assert default_snap["has_older_activities"] is True

    large_snap = app.get(cid, "user-1", activity_limit=1200)
    # 700 filesystem.read + 1 turn.completed = 701 (all <= 1000 cap)
    assert len(large_snap["activities"]) == 701
    assert large_snap["has_older_activities"] is False


def test_compact_snapshot_activities_preserves_structural_turn_started_variants() -> None:
    now = datetime.now(UTC)
    turns = [{"turn_id": "turn-1", "state": "completed"}]
    rows = [
        {
            "id": 1,
            "event_id": "e1",
            "event_type": "turn.started",
            "sequence": 1,
            "summary": "pensando há 20 s",
            "payload": {"waiting_model": True, "seconds": 20},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:main",
            "parent_agent_id": None,
        },
        {
            "id": 2,
            "event_id": "e2",
            "event_type": "turn.started",
            "sequence": 2,
            "summary": "Mensagem via Friend entregue",
            "payload": {"origin": "friend"},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:main",
            "parent_agent_id": None,
        },
        {
            "id": 3,
            "event_id": "e3",
            "event_type": "turn.started",
            "sequence": 3,
            "summary": "Trocou modelo",
            "payload": {"from_model": "model-a", "to_model": "model-b"},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:main",
            "parent_agent_id": None,
        },
        {
            "id": 4,
            "event_id": "e4",
            "event_type": "turn.started",
            "sequence": 4,
            "summary": "Conexão com o modelo caiu; retomando (1/3)",
            "payload": {},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:main",
            "parent_agent_id": None,
        },
        {
            "id": 5,
            "event_id": "e5",
            "event_type": "tool.requested",
            "sequence": 5,
            "summary": "Solicitou read_file",
            "payload": {"tool_name": "read_file"},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:main",
            "parent_agent_id": None,
        },
        {
            "id": 6,
            "event_id": "e6",
            "event_type": "agent.progress",
            "sequence": 6,
            "summary": "Subagente avançou",
            "payload": {"tool_calls": 10},
            "created_at": now,
            "turn_id": "turn-1",
            "execution_id": "exe-1",
            "agent_id": "agent:sub-1",
            "parent_agent_id": "agent:main",
        },
    ]

    result = compact_snapshot_activities(rows, turns)
    assert [item["event_id"] for item in result] == ["e2", "e3", "e4"]

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, insert, select

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns, metadata, projects
from agentos.plugins.command_library import CommandLibrary
from agentos.plugins.models import CommandContribution


def test_overview_returns_model_and_usage_for_each_agent_and_the_whole_conversation() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Research the source material.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    main_agent_id = store.main_agent_id(turn)
    agents = ConversationAgentStore(engine, conversation_id=receipt.conversation_id, user_id="user-1")
    child = agents.create(
        "Researcher",
        "Research the source material.",
        parent_agent_id=main_agent_id,
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
    )
    agents.record_usage(
        main_agent_id,
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=13,
        output_tokens=5,
        total_tokens=18,
    )
    agents.record_usage(
        child["agent_id"],
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )

    overview = store.overview(receipt.conversation_id, "user-1")
    researcher = next(agent for agent in overview["agents"] if agent["agent_id"] == child["agent_id"])

    assert overview["token_usage"] == {
        "input_tokens": 24,
        "output_tokens": 12,
        "total_tokens": 36,
        "usage_reported": True,
        "estimated_cost_usd": 0.000252,
    }
    assert researcher["provider"] == "openrouter"
    assert researcher["model_id"] == "anthropic/claude-sonnet-4"
    assert researcher["token_usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "usage_reported": True,
        "estimated_cost_usd": 0.000138,
    }


def test_overview_marks_usage_as_unavailable_when_the_provider_never_reported_it() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Research the source material.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        idempotency_key="request-1",
    )

    overview = store.overview(receipt.conversation_id, "user-1")

    assert overview["token_usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage_reported": False,
        "estimated_cost_usd": None,
    }


def test_reopened_conversation_keeps_text_deltas_interleaved_with_activity() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1",
        message="Check the file.",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    store.delta(turn, "Vou verificar o arquivo. ")
    store.record(
        turn,
        AgentActivityEventType.FILESYSTEM_READ,
        "Leu o arquivo config.txt",
        {"path": "config.txt", "tool_name": "read_file", "tool_kind": "filesystem"},
    )
    store.delta(turn, "Entendi; vou aplicar a correção.")

    reopened = store.get(receipt.conversation_id, "user-1")
    timeline = [
        (item["event_type"], item["payload"].get("content") or item["summary"])
        for item in reopened["activities"]
        if item["event_type"] in {"assistant.delta", "filesystem.read"}
    ]

    assert timeline == [
        ("assistant.delta", "Vou verificar o arquivo. "),
        ("filesystem.read", "Leu o arquivo config.txt"),
        ("assistant.delta", "Entendi; vou aplicar a correção."),
    ]


def test_reopened_conversation_repairs_context_prompt_count_from_legacy_redacted_activity() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1", message="Mostre o contexto.", provider="ollama", model_id="model-a", idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    store.record(
        turn,
        AgentActivityEventType.CONTEXT_UPDATED,
        "Contexto atualizado",
        {
            "used_tokens": 4200,
            "limit_tokens": 16000,
            "system_prompt_tokens": "[REDACTED]",
            "history_tokens": 1900,
            "input_tokens": 300,
            "tools_tokens": 800,
            "skills_tokens": 300,
            "mcps_tokens": 200,
            "omitted_messages": 0,
            "compaction_count": 0,
            "compaction_enabled": True,
        },
    )

    snapshot = store.get(receipt.conversation_id, "user-1")

    assert snapshot["context_usage"]["system_prompt_tokens"] == 700
    context_event = next(item for item in snapshot["activities"] if item["event_type"] == "context.updated")
    assert context_event["payload"]["system_prompt_tokens"] == 700


def _user_message_id(engine, turn_id):
    with engine.connect() as c:
        return c.execute(select(conversation_turns.c.user_message_id).where(conversation_turns.c.turn_id == turn_id)).scalar_one()


def _content(engine, turn_id):
    with engine.connect() as c:
        return c.execute(
            select(conversation_messages.c.content).where(
                conversation_messages.c.message_id == _user_message_id(engine, turn_id)
            )
        ).scalar_one()


def _store_with_command(engine, tmp_path, body="Daily note for $ARGUMENTS."):
    (tmp_path / "commands").mkdir(exist_ok=True)
    (tmp_path / "commands" / "daily.md").write_text(body, encoding="utf-8")
    library = CommandLibrary()
    library.install_plugin_commands(
        user_id="user-1", plugin_id="demo", install_path=tmp_path,
        commands=(CommandContribution("demo:daily", "daily", "d", "", "commands/daily.md"),),
    )
    return PostgresChatStore(engine, command_library=library)


class _SkillLibrary:
    def get(self, query):
        if query["user_id"] != "user-1" or query["skill_id"] != "safe-review":
            raise ValueError("skill not found")
        return {
            "id": "safe-review", "name": "Safe review", "version": "1.2.0",
            "available": True, "instructions": "Review the change before approving it.",
        }


def test_hook_context_round_trips_and_accumulates_per_conversation() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    assert store.hook_context("conversation-1") is None

    store.record_hook_context("conversation-1", "VAULT CONTEXT", user_id="user-1", plugin_id="demo", hook_id="demo:SessionStart:0")

    assert store.hook_context("conversation-1") == "VAULT CONTEXT"
    assert store.hook_context("conversation-2") is None


def test_the_conversation_payload_reports_the_command_behind_a_message(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = _store_with_command(engine, tmp_path)
    receipt = store.create(
        user_id="user-1", message="/daily amanhã", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-5",
    )

    payload = store.get(receipt.conversation_id, "user-1")

    user_message = next(item for item in payload["messages"] if item["role"] == "user")
    assert user_message["command"] == {"command_id": "demo:daily", "slug": "daily", "arguments": "amanhã"}


def test_an_ordinary_message_reports_no_command(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = _store_with_command(engine, tmp_path)
    receipt = store.create(
        user_id="user-1", message="olá", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-6",
    )

    payload = store.get(receipt.conversation_id, "user-1")

    user_message = next(item for item in payload["messages"] if item["role"] == "user")
    assert user_message["command"] is None


def test_a_command_message_stores_what_the_person_typed(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = _store_with_command(engine, tmp_path)

    receipt = store.create(
        user_id="user-1", message="/daily amanhã", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-1",
    )

    history = store.history_for_turn({
        "conversation_id": receipt.conversation_id,
        "user_message_id": _user_message_id(engine, receipt.turn_id),
    })
    assert history[-1]["content"] == "Daily note for amanhã."
    assert _content(engine, receipt.turn_id) == "/daily amanhã"


def test_a_skill_can_be_invoked_directly_with_a_slash() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, skill_library=_SkillLibrary())

    receipt = store.create(
        user_id="user-1", message="/safe-review confira este diff", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-skill-1",
    )

    payload = store.get(receipt.conversation_id, "user-1")
    user_message = next(item for item in payload["messages"] if item["role"] == "user")
    assert user_message["command"] == {"command_id": "skill:safe-review", "slug": "safe-review", "arguments": "confira este diff"}
    history = store.history_for_turn({"conversation_id": receipt.conversation_id, "user_message_id": _user_message_id(engine, receipt.turn_id)})
    assert "Safe review (safe-review@1.2.0)" in history[-1]["content"]
    assert "Pedido da pessoa: confira este diff" in history[-1]["content"]


def test_a_slash_message_that_is_not_a_command_passes_through_untouched(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = _store_with_command(engine, tmp_path)

    receipt = store.create(
        user_id="user-1", message="/usr/local/bin exists?", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-2",
    )

    assert _content(engine, receipt.turn_id) == "/usr/local/bin exists?"
    history = store.history_for_turn({
        "conversation_id": receipt.conversation_id,
        "user_message_id": _user_message_id(engine, receipt.turn_id),
    })
    assert history[-1]["content"] == "/usr/local/bin exists?"


def test_arguments_are_appended_when_the_body_has_no_placeholder(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = _store_with_command(engine, tmp_path, body="Just do the thing.")

    receipt = store.create(
        user_id="user-1", message="/daily amanhã", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-3",
    )

    history = store.history_for_turn({
        "conversation_id": receipt.conversation_id,
        "user_message_id": _user_message_id(engine, receipt.turn_id),
    })
    assert history[-1]["content"] == "Just do the thing.\n\nArgumentos: amanhã"


def test_expansion_survives_the_plugin_being_removed(tmp_path) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    library = CommandLibrary()
    (tmp_path / "commands").mkdir(exist_ok=True)
    (tmp_path / "commands" / "daily.md").write_text("Body.", encoding="utf-8")
    library.install_plugin_commands(
        user_id="user-1", plugin_id="demo", install_path=tmp_path,
        commands=(CommandContribution("demo:daily", "daily", "d", "", "commands/daily.md"),),
    )
    store = PostgresChatStore(engine, command_library=library)
    receipt = store.create(
        user_id="user-1", message="/daily", provider="anthropic",
        model_id="claude-opus-5", idempotency_key="request-4",
    )

    library.remove_plugin_commands(user_id="user-1", plugin_id="demo")

    history = store.history_for_turn({
        "conversation_id": receipt.conversation_id,
        "user_message_id": _user_message_id(engine, receipt.turn_id),
    })
    assert history[-1]["content"] == "Body."


def test_a_follow_up_message_closes_a_waiting_user_turn() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    first = store.create(
        user_id="user-1", message="Configure isto.", provider="openrouter", model_id="model-a", idempotency_key="request-1",
    )
    waiting = store.claim(first.turn_id)
    assert waiting is not None
    store.finish(waiting, code="WAITING_USER")

    follow_up = store.create(
        user_id="user-1", conversation_id=first.conversation_id, message="Use a opção segura.", provider="", model_id="", idempotency_key="request-2",
    )
    snapshot = store.get(first.conversation_id, "user-1")

    assert follow_up.state == "queued"
    assert snapshot["turns"][0]["state"] == "completed"
    assert snapshot["state"] == "queued"


def test_a_follow_up_message_in_a_project_queues_a_new_turn() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(projects).values(
            project_id="project-1", user_id="user-1", workspace_id="workspace-1",
            name="Projeto", description=None, created_at=now, updated_at=now, archived_at=None,
        ))

    first = store.create(
        user_id="user-1", project_id="project-1", message="Leia o arquivo.",
        provider="ollama", model_id="model-a", idempotency_key="project-request-1",
    )
    follow_up = store.create(
        user_id="user-1", conversation_id=first.conversation_id, message="Continue.",
        provider="", model_id="", idempotency_key="project-request-2",
    )

    assert follow_up.state == "queued"
    with engine.connect() as connection:
        execution_id = connection.execute(
            select(conversation_turns.c.execution_id).where(conversation_turns.c.turn_id == follow_up.turn_id)
        ).scalar_one()
    assert execution_id


def test_cancelling_another_profiles_conversation_is_not_found() -> None:
    import pytest

    from agentos.api.contracts import ApplicationNotFoundError

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))
    receipt = store.create(user_id="u1", message="oi", provider="openrouter", model_id="m", idempotency_key="k1")
    with pytest.raises(ApplicationNotFoundError):
        store.request_cancel(receipt.conversation_id, "u2")
    assert store.request_cancel(receipt.conversation_id, "u1")["conversation_id"] == receipt.conversation_id


def test_snapshot_paginates_recent_activities_and_loads_older_pages() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1",
        message="Execute muitas ferramentas.",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="request-paginate",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    # Emit 180 distinct tool events plus multi-token delta segments, then finish the turn
    for tok in ("Iniciando ", "varredura ", "de arquivos."):
        store.delta(turn, tok)
    for idx in range(180):
        store.record(
            turn,
            AgentActivityEventType.FILESYSTEM_READ,
            f"Leu arquivo {idx}.txt",
            {"path": f"{idx}.txt", "tool_name": "read_file", "tool_kind": "filesystem"},
        )
    for tok in ("Concluído ", "com ", "sucesso."):
        store.delta(turn, tok)
    store.finish(turn)

    snapshot = store.get(receipt.conversation_id, "user-1")
    assert len(snapshot["activities"]) <= 150
    assert snapshot["has_older_activities"] is True
    assert isinstance(snapshot["oldest_activity_id"], int)
    # Settled turns compact consecutive assistant.delta tokens into a single segment event
    delta_events = [item for item in snapshot["activities"] if item["event_type"] == "assistant.delta"]
    assert len(delta_events) == 1
    assert delta_events[0]["payload"]["content"] == "Concluído com sucesso."

    older_page = store.list_older_activities(
        receipt.conversation_id,
        "user-1",
        before_id=snapshot["oldest_activity_id"],
        limit=150,
    )
    assert len(older_page["activities"]) > 0
    assert older_page["has_older_activities"] is False
    assert older_page["oldest_activity_id"] is None
    assert older_page["activities"][0]["summary"] == "Execução iniciada" or "Leu arquivo 0.txt" in [
        item["summary"] for item in older_page["activities"]
    ]


def test_settled_turn_compacts_consecutive_assistant_and_thought_deltas_preserving_interleaving() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1",
        message="Verifique o sistema.",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="request-interleave-settled",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    from types import SimpleNamespace

    ctx = SimpleNamespace(conversation_id=receipt.conversation_id, turn_id=receipt.turn_id)
    store.lifecycle(ctx, "thinking_delta", delta="Pensando parte 1, ", iteration=1)
    store.lifecycle(ctx, "thinking_delta", delta="parte 2.", iteration=1)
    store.delta(turn, "Vou ler ")
    store.delta(turn, "o arquivo.")
    store.lifecycle(
        ctx,
        "generating_tool_call",
        tool_name="read_file",
        call_id="call-1",
        bytes_received=24,
        iteration=1,
    )
    store.record(
        turn,
        AgentActivityEventType.FILESYSTEM_READ,
        "Leu config.txt",
        {"path": "config.txt", "tool_name": "read_file", "tool_kind": "filesystem"},
    )
    store.delta(turn, "Tudo ")
    store.delta(turn, "certo!")
    store.finish(turn)

    snapshot = store.get(receipt.conversation_id, "user-1")
    relevant = [
        (item["event_type"], item["payload"].get("content") or item["payload"].get("delta") or item["summary"])
        for item in snapshot["activities"]
        if item["event_type"]
        in {"agent.thought_delta", "assistant.delta", "tool.progressed", "filesystem.read"}
    ]
    assert relevant == [
        ("agent.thought_delta", "Pensando parte 1, parte 2."),
        ("assistant.delta", "Vou ler o arquivo."),
        ("tool.progressed", "Preparando argumentos de read_file"),
        ("filesystem.read", "Leu config.txt"),
        ("assistant.delta", "Tudo certo!"),
    ]



def test_claim_sets_turn_running_and_cancelled_finish_blocks_code_mode_run_and_deduplicates_turn_failed() -> None:
    from agentos.persistence.postgres.schema import code_mode_runs

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1",
        message="Build the app.",
        provider="ollama",
        model_id="glm-5.1:cloud",
        idempotency_key="request-cancel-code",
        code_mode="code",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    assert turn["state"] == "running"

    with engine.connect() as conn:
        db_state = conn.execute(
            select(conversation_turns.c.state).where(conversation_turns.c.turn_id == receipt.turn_id)
        ).scalar_one()
    assert db_state == "running"

    # Attempt to record duplicate turn.failed events for the same turn and agent
    store.record(turn, AgentActivityEventType.TURN_FAILED, "Execução interrompida", {"reason": "TURN_CANCELLED"})
    store.finish(turn, failed=True, code="TURN_CANCELLED")

    snapshot = store.get(receipt.conversation_id, "user-1")
    turn_row = next(t for t in snapshot["turns"] if t["turn_id"] == receipt.turn_id)
    assert turn_row["state"] == "cancelled"

    failed_events = [a for a in snapshot["activities"] if a["event_type"] == "turn.failed"]
    assert len(failed_events) == 1

    with engine.connect() as conn:
        code_row = conn.execute(
            select(code_mode_runs.c.stage, code_mode_runs.c.caveats).where(code_mode_runs.c.turn_id == receipt.turn_id)
        ).mappings().one()
    assert code_row["stage"] == "blocked"
    assert code_row["caveats"] == "TURN_CANCELLED"




def test_a_prompt_blocked_by_a_hook_never_reaches_a_later_turn() -> None:
    # W2-R6: the blocked text is erased from storage and skipped by history_for_turn of later turns.
    from sqlalchemy import select as _select

    from agentos.persistence.postgres.schema import conversation_message_commands

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    first = store.create(
        user_id="user-1", message="my password is hunter2", provider="openrouter", model_id="model-a", idempotency_key="request-1",
    )
    turn = store.claim(first.turn_id)
    assert turn is not None
    store.delta(turn, "⚠️ Mensagem bloqueada por um hook: no secrets")
    store.exclude_from_context(turn)
    store.finish(turn, code="HOOK_BLOCKED_PROMPT")

    second = store.create(
        user_id="user-1", conversation_id=first.conversation_id, message="what is two plus two", provider="openrouter",
        model_id="model-a", idempotency_key="request-2",
    )
    next_turn = store.claim(second.turn_id)
    assert next_turn is not None
    history = store.history_for_turn(next_turn)
    assert [item["role"] for item in history] == ["user"]
    assert history[0]["content"] == "what is two plus two"
    with engine.connect() as c:
        stored = c.execute(_select(conversation_messages.c.content).where(conversation_messages.c.message_id == turn["user_message_id"])).scalar()
    assert "hunter2" not in str(stored)

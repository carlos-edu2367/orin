from __future__ import annotations

from sqlalchemy import create_engine, select

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns, metadata
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
    }
    assert researcher["provider"] == "openrouter"
    assert researcher["model_id"] == "anthropic/claude-sonnet-4"
    assert researcher["token_usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "usage_reported": True,
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

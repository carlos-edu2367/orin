"""W4: CronCreate, CronDelete and CronList schedule prompts in this conversation through Orin's scheduler."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, select

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    metadata, provider_configurations, provider_model_catalog, schedules,
)
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService


def _setup(tmp_path: Path, *, user_id: str = "user-1"):
    now = datetime.now(UTC)
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    conversation = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="c1")
    scheduler = ScheduledChatService(engine)
    context = {"user_id": user_id, "provider": "openrouter", "model_id": "model-1", "timezone": "America/Sao_Paulo"}
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"), conversation_id=conversation.conversation_id,
        scheduler=scheduler, schedule_context=context, public_tool_names=True,
    )
    return engine, scheduler, conversation.conversation_id, toolset


def test_create_list_and_delete(tmp_path: Path) -> None:
    engine, scheduler, conversation_id, toolset = _setup(tmp_path)
    created = toolset.invoke("CronCreate", {"cron": "0 9 * * 1-5", "prompt": "check the build"})
    assert created.status == "succeeded", created.content
    output = created.data["tool_output"]
    assert output["humanSchedule"] == "weekdays at 09:00 (America/Sao_Paulo)"
    assert output["recurring"] is True and output["durable"] is True and isinstance(output["nextFireAt"], int)
    with engine.connect() as connection:
        row = connection.execute(select(schedules).where(schedules.c.schedule_id == output["id"])).mappings().one()
    assert row["ends_at"] is not None  # recurring jobs expire after 7 days
    assert row["rule"]["source"] == "cron" and row["rule"]["cron"] == "0 9 * * 1-5"

    listed = toolset.invoke("CronList", {})
    assert listed.status == "succeeded"
    jobs = listed.data["tool_output"]["jobs"]
    assert [job["id"] for job in jobs] == [output["id"]] and jobs[0]["prompt"] == "check the build"

    deleted = toolset.invoke("CronDelete", {"id": output["id"]})
    assert deleted.status == "succeeded", deleted.content
    assert toolset.invoke("CronList", {}).data["tool_output"]["jobs"] == []


def test_a_one_shot_job_fires_once(tmp_path: Path) -> None:
    _, scheduler, conversation_id, toolset = _setup(tmp_path)
    created = toolset.invoke("CronCreate", {"cron": "*/5 * * * *", "prompt": "ping", "recurring": False})
    assert created.status == "succeeded", created.content
    assert created.data["tool_output"]["recurring"] is False
    rows = scheduler.list_for_conversation("user-1", conversation_id, sources=("cron",))
    assert rows[0]["recurrence"] == "once"


def test_a_null_recurring_flag_keeps_the_default(tmp_path: Path) -> None:
    _, _, _, toolset = _setup(tmp_path)
    created = toolset.invoke("CronCreate", {"cron": "0 9 * * *", "prompt": "x", "recurring": None})
    assert created.status == "succeeded" and created.data["tool_output"]["recurring"] is True


def test_bad_input_is_refused(tmp_path: Path) -> None:
    _, _, _, toolset = _setup(tmp_path)
    assert toolset.invoke("CronCreate", {"cron": "61 * * * *", "prompt": "x"}).status == "failed"
    assert toolset.invoke("CronCreate", {"cron": "0 9 * * *", "prompt": "   "}).status == "failed"
    assert toolset.invoke("CronCreate", {"cron": "0 9 * * *", "prompt": "x" * 16_001}).status == "failed"


def test_a_conversation_holds_at_most_twenty_jobs(tmp_path: Path) -> None:
    _, _, _, toolset = _setup(tmp_path)
    for index in range(20):
        assert toolset.invoke("CronCreate", {"cron": f"{index} 9 * * *", "prompt": "x"}).status == "succeeded"
    refused = toolset.invoke("CronCreate", {"cron": "30 9 * * *", "prompt": "x"})
    assert refused.status == "failed" and "20" in refused.content


def test_delete_touches_only_this_conversations_tool_jobs(tmp_path: Path) -> None:
    _, scheduler, conversation_id, toolset = _setup(tmp_path)
    from agentos.scheduler.scheduled_chats import ScheduledChatInput

    page_made = scheduler.create("user-1", ScheduledChatInput("mine", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="page")
    refused = toolset.invoke("CronDelete", {"id": page_made["schedule_id"]})
    assert refused.status == "failed"
    assert scheduler.list("user-1")  # the Schedules-page job is still there


def test_another_users_context_cannot_reach_the_conversation(tmp_path: Path) -> None:
    _, _, _, toolset = _setup(tmp_path, user_id="user-2")
    refused = toolset.invoke("CronCreate", {"cron": "0 9 * * *", "prompt": "x"})
    assert refused.status == "failed"


def test_without_a_scheduler_the_tools_are_not_offered(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), conversation_id="chat-1", public_tool_names=True)
    names = {definition.name for definition in toolset.definitions()}
    assert not names & {"cron_create", "cron_delete", "cron_list"}


def test_permissions_list_is_read_only_and_create_asks() -> None:
    from agentos.permissions.engine import READ_ONLY_TOOLS
    from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
    from agentos.agentic.tools.public_names import PUBLIC_NAMES

    assert "cron_list" in READ_ONLY_TOOLS
    assert "cron_create" not in READ_ONLY_TOOLS and "cron_delete" not in READ_ONLY_TOOLS
    assert {"cron_create", "cron_delete", "cron_list"} <= CODE_MODE_CORE_TOOLS
    assert (PUBLIC_NAMES["cron_create"], PUBLIC_NAMES["cron_delete"], PUBLIC_NAMES["cron_list"]) == ("CronCreate", "CronDelete", "CronList")

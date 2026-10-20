"""W4: ScheduleWakeup continues this conversation later through Orin's scheduler."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService


def _setup(tmp_path: Path):
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
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"), conversation_id=conversation.conversation_id, scheduler=scheduler,
        schedule_context={"user_id": "user-1", "provider": "openrouter", "model_id": "model-1", "timezone": "UTC"},
        public_tool_names=True,
    )
    return scheduler, conversation.conversation_id, toolset


def _wake(toolset, **fields):
    return toolset.invoke("ScheduleWakeup", {"reason": "watching CI", "prompt": "check CI again", **fields})


@pytest.mark.parametrize(("requested", "clamped", "was_clamped"), [(5, 60, True), (120, 120, False), (99999, 3600, True), (float("inf"), 3600, True)])
def test_the_delay_is_clamped(tmp_path: Path, requested: float, clamped: int, was_clamped: bool) -> None:
    _, _, toolset = _setup(tmp_path)
    before = datetime.now(UTC)
    outcome = _wake(toolset, delaySeconds=requested)
    assert outcome.status == "succeeded", outcome.content
    output = outcome.data["tool_output"]
    assert output["clampedDelaySeconds"] == clamped and output["wasClamped"] is was_clamped
    scheduled = datetime.fromtimestamp(output["scheduledFor"] / 1000, UTC)
    assert before + timedelta(seconds=clamped - 2) <= scheduled <= datetime.now(UTC) + timedelta(seconds=clamped + 2)


def test_a_new_wakeup_replaces_the_pending_one_and_stop_cancels(tmp_path: Path) -> None:
    scheduler, conversation_id, toolset = _setup(tmp_path)
    assert _wake(toolset, delaySeconds=600).status == "succeeded"
    second = _wake(toolset, delaySeconds=900)
    assert second.data["tool_output"]["cancelledWakeups"] == 1
    assert len(scheduler.list_for_conversation("user-1", conversation_id, sources=("wakeup",))) == 1
    stopped = toolset.invoke("ScheduleWakeup", {"stop": True})
    assert stopped.status == "succeeded", stopped.content
    assert stopped.data["tool_output"]["stopped"] is True and stopped.data["tool_output"]["cancelledWakeups"] == 1
    assert scheduler.list_for_conversation("user-1", conversation_id, sources=("wakeup",)) == []


def test_it_needs_a_reason_a_prompt_and_a_number(tmp_path: Path) -> None:
    _, _, toolset = _setup(tmp_path)
    assert toolset.invoke("ScheduleWakeup", {"delaySeconds": 60, "prompt": "x"}).status == "failed"
    assert toolset.invoke("ScheduleWakeup", {"delaySeconds": 60, "reason": "r"}).status == "failed"
    assert _wake(toolset, delaySeconds="soon").status == "failed"
    assert _wake(toolset, delaySeconds=float("nan")).status == "failed"


def test_a_day_holds_at_most_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.agentic.tools.scheduling import wakeup

    monkeypatch.setattr(wakeup, "MAX_WAKEUPS_PER_DAY", 3)
    _, _, toolset = _setup(tmp_path)
    for _ in range(3):
        assert _wake(toolset, delaySeconds=60).status == "succeeded"
    refused = _wake(toolset, delaySeconds=60)
    assert refused.status == "failed" and "24 hours" in refused.content


def test_it_is_allowed_without_a_prompt() -> None:
    from agentos.permissions.engine import NO_PROMPT_TOOLS as READ_ONLY_TOOLS  # W4-R26: it never prompts
    from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
    from agentos.agentic.tools.public_names import PUBLIC_NAMES, arguments_in

    assert "schedule_wakeup" in READ_ONLY_TOOLS and "schedule_wakeup" in CODE_MODE_CORE_TOOLS
    assert PUBLIC_NAMES["schedule_wakeup"] == "ScheduleWakeup"
    assert arguments_in("schedule_wakeup", {"delaySeconds": 60})["delay_seconds"] == 60

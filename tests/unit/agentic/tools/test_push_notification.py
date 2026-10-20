"""W4: PushNotification shows the person a short message and fires the Notification hook."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path, shown: list[str], **kwargs) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), on_notification=shown.append, public_tool_names=True, **kwargs)


def test_the_message_is_shown_and_reported(tmp_path: Path) -> None:
    shown: list[str] = []
    outcome = _toolset(tmp_path, shown).invoke("PushNotification", {"message": "  Build   finished ", "status": "proactive"})
    assert outcome.status == "succeeded", outcome.content
    assert shown == ["Build finished"]
    output = outcome.data["tool_output"]
    assert output["message"] == "Build finished" and output["localSent"] is True and output["pushSent"] is False
    assert output["sentAt"].endswith("Z") and "disabledReason" not in output


def test_long_messages_are_cut_and_turns_are_capped(tmp_path: Path) -> None:
    shown: list[str] = []
    toolset = _toolset(tmp_path, shown)
    assert len(toolset.invoke("PushNotification", {"message": "x" * 500}).data["tool_output"]["message"]) == 180  # W4-R14: 180 UTF-16 units
    assert toolset.invoke("PushNotification", {"message": "two"}).status == "succeeded"
    assert toolset.invoke("PushNotification", {"message": "three"}).status == "succeeded"
    refused = toolset.invoke("PushNotification", {"message": "four"})
    assert refused.status == "failed" and len(shown) == 3
    assert toolset.invoke("PushNotification", {"message": "   "}).status == "failed"


def test_a_failing_transport_is_reported_not_raised(tmp_path: Path) -> None:
    def broken(message: str) -> None:
        raise RuntimeError("store down")

    outcome = AgentToolset(ConversationWorkspace(tmp_path, "c1"), on_notification=broken, public_tool_names=True).invoke("PushNotification", {"message": "hi"})
    assert outcome.status == "succeeded"
    assert outcome.data["tool_output"]["localSent"] is False and outcome.data["tool_output"]["disabledReason"] == "no_transport"


def test_the_notification_hook_fires(tmp_path: Path) -> None:
    events = []
    bus = LifecycleBus()
    bus.subscribe(events.append)
    _toolset(tmp_path, [], lifecycle=bus).invoke("PushNotification", {"message": "done"})
    seen = [dict(event.payload) for event in events if event.name is HookEvent.NOTIFICATION]
    assert [(item["message"], item["notification_type"]) for item in seen] == [("done", "push_notification")]


def test_without_a_transport_it_is_not_offered(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    assert "push_notification" not in {definition.name for definition in toolset.definitions()}


def test_it_is_allowed_and_published_in_code_mode() -> None:
    from agentos.agentic.events import AgentActivityEventType
    from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
    from agentos.agentic.tools.public_names import PUBLIC_NAMES
    from agentos.permissions.engine import NO_PROMPT_TOOLS as READ_ONLY_TOOLS  # W4-R26: it never prompts

    assert "push_notification" in READ_ONLY_TOOLS and "push_notification" in CODE_MODE_CORE_TOOLS
    assert PUBLIC_NAMES["push_notification"] == "PushNotification"
    assert AgentActivityEventType.NOTIFICATION_PUSHED.value == "notification.pushed"

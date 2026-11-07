"""Tell the model when a core tool it had in the previous turn is gone in this one.

A tool can drop out between turns (plan mode left on after a mode switch, a policy, the terminal turned off) and the
model otherwise only finds out by calling something that no longer exists. The core tools offered in a conversation's
previous turn are kept in process memory, keyed by conversation id.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from agentos.agentic.tools.public_names import public_name

CORE_TOOLS: tuple[str, ...] = ("run_command", "powershell", "edit_file", "write_file", "read_file")
# Blocked (though still listed) while Plan Mode is active.
_BLOCKED_IN_PLAN_MODE = frozenset({"run_command", "powershell", "edit_file", "write_file"})
MAX_CONVERSATIONS = 512

_previous: OrderedDict[str, frozenset[str]] = OrderedDict()
_lock = threading.Lock()


def clear_previous_tools() -> None:
    with _lock:
        _previous.clear()


def offered_core_tools(toolset: Any) -> frozenset[str]:
    """The core tools this toolset really lets the model use now (defined, and not blocked by Plan Mode)."""
    defined = {item.name for item in toolset.definitions()}
    plan = bool(getattr(toolset, "_plan_mode_active", False))
    return frozenset(name for name in CORE_TOOLS if name in defined and not (plan and name in _BLOCKED_IN_PLAN_MODE))


def _reason(name: str, toolset: Any) -> str:
    if getattr(toolset, "_plan_mode_active", False) and name in _BLOCKED_IN_PLAN_MODE:
        return "plan mode"
    if getattr(toolset, "_read_only_mode", False):
        return "read-only mode"
    if name in {"run_command", "powershell"} and not getattr(toolset, "_enable_terminal", True):
        return "the terminal is off"
    return ""


def lost_tools_notice(conversation_id: str, toolset: Any) -> str:
    """The reminder text for this turn ("" when nothing core disappeared); records this turn's set for the next one."""
    now = offered_core_tools(toolset)
    key = str(conversation_id or "").strip()
    if not key:
        return ""
    with _lock:
        before = _previous.pop(key, None)
        _previous[key] = now
        while len(_previous) > MAX_CONVERSATIONS:
            _previous.popitem(last=False)
    if not before:
        return ""
    lost = [name for name in CORE_TOOLS if name in before and name not in now]
    if not lost:
        return ""
    show = (lambda name: public_name(name)) if getattr(toolset, "name_translator", None) is not None else (lambda name: name)
    parts = []
    for name in lost:
        reason = _reason(name, toolset)
        parts.append(f"{show(name)} ({reason})" if reason else show(name))
    return "Tools no longer available this turn: " + ", ".join(parts)


def note_lost_tools(reminders: Any, conversation_id: str, toolset: Any) -> None:
    """Queue the notice as a system reminder; never lets a failure here stop the turn."""
    try:
        text = lost_tools_notice(conversation_id, toolset)
        if text:
            reminders.add("tools", text, key=f"tools-lost:{conversation_id}")
    except Exception:  # noqa: BLE001 - an advisory notice is never the reason a turn fails
        pass

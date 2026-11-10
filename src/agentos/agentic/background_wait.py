"""End-of-turn wait for background work: subagents and this turn's background jobs, together.

When the model answers while a job it started is still running (a 400 s TTS render that outgrew one
tool call, say), the turn does not end on a promise. It waits, the way it already waits for background
subagents, and hands the model how the job ended so the model can finish the work.
Servers and watchers are never waited for: they are meant to outlive the turn.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .background_agents import BACKGROUND_AGENTS, format_subagent_notifications

PROCESS_POLL_SECONDS = 0.25


def awaited_processes(toolset: Any) -> list[dict[str, Any]]:
    probe = getattr(toolset, "awaited_background_processes", None)
    if not callable(probe):
        return []
    try:
        return list(probe())
    except Exception:  # noqa: BLE001 - a broken probe never holds a turn open
        return []


def _process_finished(toolset: Any) -> bool:
    probe = getattr(toolset, "has_finished_background_process", None)
    try:
        return bool(callable(probe) and probe())
    except Exception:  # noqa: BLE001
        return False


def has_pending_background_work(conversation_id: str, toolset: Any, *, since_monotonic: float | None) -> bool:
    """Something the turn should wait for or report before it ends."""
    if conversation_id and (
        BACKGROUND_AGENTS.running(conversation_id)
        or BACKGROUND_AGENTS.has_unnotified(conversation_id, since_monotonic=since_monotonic)
    ):
        return True
    return bool(awaited_processes(toolset)) or _process_finished(toolset)


def waiting_labels(conversation_id: str, toolset: Any) -> list[str]:
    names = [entry.name for entry in BACKGROUND_AGENTS.running(conversation_id)] if conversation_id else []
    names += [f"processo {row['pid']} ({row['label']})" if row.get("label") else f"processo {row['pid']}" for row in awaited_processes(toolset)]
    return names


def wait_for_background_work(
    conversation_id: str,
    toolset: Any,
    *,
    timeout: float,
    cancelled: Callable[[], bool],
    extra_wake: Callable[[], bool] | None = None,
) -> None:
    """Return when a subagent or awaited job finishes, on cancel or a queued message, or at the deadline."""
    deadline = time.monotonic() + max(0.0, float(timeout))

    def wake() -> bool:
        return bool(extra_wake is not None and extra_wake()) or _process_finished(toolset)

    while True:
        if cancelled() or wake():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if conversation_id and BACKGROUND_AGENTS.running(conversation_id):
            # Returns when an agent finishes, on cancel, or as soon as wake() (a job ended) is true.
            BACKGROUND_AGENTS.wait_any(conversation_id, timeout=remaining, cancelled=cancelled, extra_wake=wake)
            return
        if not awaited_processes(toolset):
            return
        time.sleep(min(PROCESS_POLL_SECONDS, remaining))


def take_background_notifications(conversation_id: str, toolset: Any, *, since_monotonic: float | None) -> str:
    """What finished, for the model: subagent reports first, then the jobs, then what is still running."""
    parts: list[str] = []
    if conversation_id:
        unnotified = BACKGROUND_AGENTS.take_unnotified(conversation_id, since_monotonic=since_monotonic)
        body = format_subagent_notifications(unnotified, still_running=BACKGROUND_AGENTS.running(conversation_id))
        if body:
            parts.append(body)
    take = getattr(toolset, "take_finished_process_notices", None)
    if callable(take):
        try:
            notices = str(take() or "").strip()
        except Exception:  # noqa: BLE001
            notices = ""
        if notices:
            parts.append(notices)
    if parts:
        still = awaited_processes(toolset)
        if still:
            parts.append(
                "Still running in the background: "
                + ", ".join(f"pid {row['pid']} ({row['label']})" for row in still)
                + ". Wait for it with read_process_output(pid=..., block=true) or end your answer to keep waiting."
            )
    return "\n\n".join(parts)

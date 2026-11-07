"""Per-conversation registry of background subagents (T3).

Survives across turns within the same Orin process so the leader can wait for
background subagents in the same turn (T4), receive a continuation turn when one
finishes after the turn closed (T5), or inspect its result in a later turn via
``check_subagent`` even after the originating ``TurnSession`` has closed.
"""
from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from pathlib import Path
import re
import threading
import time
from typing import Callable, Literal

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import conversation_agents

logger = logging.getLogger("agentos.agentic.background_agents")

BackgroundAgentStatus = Literal["running", "completed", "failed", "stopped"]

POLL_WAIT_SECONDS = 0.2


def extract_report_summary(text: str) -> str:
    """First useful non-empty line of a subagent report, capped at 300 characters."""
    for raw_line in str(text or "").splitlines():
        cleaned = raw_line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:300]
    return ""


def format_subagent_notifications(
    finished: Sequence[BackgroundAgentEntry],
    *,
    still_running: Sequence[BackgroundAgentEntry] = (),
) -> str:
    blocks: list[str] = []
    for entry in finished:
        summary_line = entry.summary or f"Status: {entry.status}"
        blocks.append(
            f'<subagent-finished agent_id="{entry.agent_id}" name="{entry.name}" status="{entry.status}">'
            f"{summary_line}\nRelatório: {entry.output_file}</subagent-finished>"
        )
    if still_running:
        names = ", ".join(f"{e.name} ({e.agent_id})" for e in still_running)
        blocks.append(f"Subagents still running in the background: {names}.")
    return "\n\n".join(blocks)


def _normalize_status(status: str) -> BackgroundAgentStatus:
    raw = str(status or "").strip().lower()
    if raw in {"completed", "succeeded", "success", "idle"}:
        return "completed"
    if raw in {"stopped", "cancelled", "canceled"}:
        return "stopped"
    if raw == "running":
        return "running"
    return "failed"


@dataclass
class BackgroundAgentEntry:
    agent_id: str
    conversation_id: str
    name: str
    output_file: str
    status: BackgroundAgentStatus
    started_at: float
    finished_at: float | None
    summary: str
    cancel: threading.Event
    future: Future | None
    notified: bool = False
    callback_fired: bool = field(default=False, repr=False)


class BackgroundAgentRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._by_conv: dict[str, dict[str, BackgroundAgentEntry]] = {}
        self._by_id: dict[str, BackgroundAgentEntry] = {}
        self._callbacks: dict[str, list[Callable[[BackgroundAgentEntry], None]]] = {}
        self._active_waiters: dict[str, int] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}

    def executor_for(self, conversation_id: str, *, max_workers: int = 32) -> ThreadPoolExecutor:
        conv_key = str(conversation_id or "_default")
        with self._lock:
            pool = self._executors.get(conv_key)
            if pool is None:
                pool = ThreadPoolExecutor(
                    max_workers=max(4, int(max_workers)),
                    thread_name_prefix=f"orin-bg-{conv_key[:12]}",
                )
                self._executors[conv_key] = pool
            return pool

    def register(self, entry: BackgroundAgentEntry) -> None:
        conv_id = str(entry.conversation_id or "")
        agent_id = str(entry.agent_id or "")
        if not conv_id or not agent_id:
            return
        with self._cond:
            self._by_conv.setdefault(conv_id, {})[agent_id] = entry
            self._by_id[agent_id] = entry
            self._cond.notify_all()

    def finish(self, agent_id: str, status: str, summary: str) -> None:
        clean_id = str(agent_id or "")
        callbacks_to_run: list[Callable[[BackgroundAgentEntry], None]] = []
        entry: BackgroundAgentEntry | None = None
        with self._cond:
            entry = self._by_id.get(clean_id)
            if entry is None:
                return
            entry.status = _normalize_status(status)
            entry.finished_at = time.monotonic()
            entry.summary = extract_report_summary(summary)
            if (
                not entry.callback_fired
                and not entry.notified
                and self._active_waiters.get(entry.conversation_id, 0) <= 0
            ):
                callbacks_to_run = list(self._callbacks.get(entry.conversation_id, ()))
                if callbacks_to_run:
                    entry.callback_fired = True
            self._cond.notify_all()
        for cb in callbacks_to_run:
            try:
                cb(entry)
            except Exception:  # noqa: BLE001
                logger.warning("background agent on_finish callback failed for %s", clean_id, exc_info=True)

    def get(self, conversation_id: str, agent_id: str) -> BackgroundAgentEntry | None:
        conv_id = str(conversation_id or "")
        clean_id = str(agent_id or "")
        with self._lock:
            conv_map = self._by_conv.get(conv_id)
            if not conv_map:
                return None
            return conv_map.get(clean_id)

    def running(self, conversation_id: str) -> list[BackgroundAgentEntry]:
        conv_id = str(conversation_id or "")
        with self._lock:
            conv_map = self._by_conv.get(conv_id, {})
            return [entry for entry in conv_map.values() if entry.status == "running"]

    def take_unnotified(
        self,
        conversation_id: str,
        *,
        since_monotonic: float | None = None,
    ) -> list[BackgroundAgentEntry]:
        conv_id = str(conversation_id or "")
        with self._lock:
            conv_map = self._by_conv.get(conv_id, {})
            taken: list[BackgroundAgentEntry] = []
            for entry in conv_map.values():
                if entry.status != "running" and not entry.notified:
                    entry.notified = True
                    if (
                        since_monotonic is None
                        or entry.started_at >= since_monotonic
                        or (entry.finished_at is not None and entry.finished_at >= since_monotonic)
                    ):
                        taken.append(entry)
            return taken

    def has_unnotified(
        self,
        conversation_id: str,
        *,
        since_monotonic: float | None = None,
    ) -> bool:
        conv_id = str(conversation_id or "")
        with self._lock:
            conv_map = self._by_conv.get(conv_id, {})
            return any(
                entry.status != "running"
                and not entry.notified
                and (
                    since_monotonic is None
                    or entry.started_at >= since_monotonic
                    or (entry.finished_at is not None and entry.finished_at >= since_monotonic)
                )
                for entry in conv_map.values()
            )

    def mark_notified(self, conversation_id: str, agent_id: str) -> None:
        conv_id = str(conversation_id or "")
        clean_id = str(agent_id or "")
        with self._lock:
            conv_map = self._by_conv.get(conv_id)
            if not conv_map:
                return
            entry = conv_map.get(clean_id)
            if entry is not None:
                entry.notified = True

    def wait_any(
        self,
        conversation_id: str,
        timeout: float,
        cancelled: Callable[[], bool],
        extra_wake: Callable[[], bool] | None = None,
    ) -> bool:
        conv_id = str(conversation_id or "")
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            self._active_waiters[conv_id] = self._active_waiters.get(conv_id, 0) + 1
        try:
            while True:
                if cancelled() or (extra_wake is not None and extra_wake()):
                    return True
                with self._cond:
                    conv_map = self._by_conv.get(conv_id, {})
                    if any(e.status != "running" and not e.notified for e in conv_map.values()):
                        return True
                    if not any(e.status == "running" for e in conv_map.values()):
                        return True
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(timeout=min(POLL_WAIT_SECONDS, remaining))
        finally:
            with self._cond:
                cur = self._active_waiters.get(conv_id, 0)
                if cur <= 1:
                    self._active_waiters.pop(conv_id, None)
                else:
                    self._active_waiters[conv_id] = cur - 1

    def on_finish(
        self,
        conversation_id: str,
        callback: Callable[[BackgroundAgentEntry], None],
    ) -> None:
        conv_id = str(conversation_id or "")
        if not conv_id:
            return
        pending_entries: list[BackgroundAgentEntry] = []
        with self._cond:
            self._callbacks[conv_id] = [callback]
            if self._active_waiters.get(conv_id, 0) <= 0:
                conv_map = self._by_conv.get(conv_id, {})
                for entry in conv_map.values():
                    if entry.status != "running" and not entry.notified and not entry.callback_fired:
                        entry.callback_fired = True
                        pending_entries.append(entry)
        for entry in pending_entries:
            try:
                callback(entry)
            except Exception:  # noqa: BLE001
                logger.warning("background agent on_finish callback failed for %s", entry.agent_id, exc_info=True)

    def clear_for_tests(self) -> None:
        with self._cond:
            self._by_conv.clear()
            self._by_id.clear()
            self._callbacks.clear()
            self._active_waiters.clear()
            self._cond.notify_all()


BACKGROUND_AGENTS = BackgroundAgentRegistry()


def fail_orphaned_running_agents(
    engine: Engine,
    *,
    workspace_root_resolver: Callable[[str], Path] | None = None,
    reason: str = "Orin reiniciou",
) -> list[str]:
    """Mark database agent records left in ``running``/``working`` state without an active in-process entry as ``failed``."""
    swept: list[str] = []
    now = datetime.now(UTC)
    with engine.begin() as connection:
        rows = connection.execute(
            select(
                conversation_agents.c.agent_id,
                conversation_agents.c.conversation_id,
            ).where(conversation_agents.c.state.in_(("running", "working")))
        ).mappings().all()
        for row in rows:
            agent_id = str(row["agent_id"] or "")
            conv_id = str(row["conversation_id"] or "")
            if not agent_id:
                continue
            live = BACKGROUND_AGENTS.get(conv_id, agent_id)
            if live is not None and live.status == "running":
                continue
            connection.execute(
                update(conversation_agents)
                .where(conversation_agents.c.agent_id == agent_id)
                .values(state="failed", updated_at=now)
            )
            swept.append(agent_id)
            if workspace_root_resolver is not None and conv_id:
                try:
                    ws_root = workspace_root_resolver(conv_id)
                    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id)[:120] or "agent"
                    report_file = Path(ws_root) / ".orin" / "agent-outputs" / f"{safe}.md"
                    if not report_file.exists():
                        report_file.parent.mkdir(parents=True, exist_ok=True)
                        report_file.write_text(reason, encoding="utf-8")
                except Exception:  # noqa: BLE001
                    logger.debug("could not write orphan report for %s", agent_id, exc_info=True)
    return swept

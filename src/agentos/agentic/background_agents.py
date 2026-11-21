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
from typing import Any, Callable, Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import agent_runs, conversation_agents

logger = logging.getLogger("agentos.agentic.background_agents")

BackgroundAgentStatus = Literal["running", "stalled", "interrupted", "completed", "failed", "stopped"]

POLL_WAIT_SECONDS = 0.2


def extract_report_summary(text: str) -> str:
    """First useful non-empty line of a subagent report, capped at 300 characters."""
    for raw_line in str(text or "").splitlines():
        cleaned = raw_line.strip().lstrip("#").strip()
        if not cleaned:
            continue
        if re.fullmatch(r"[\w ._-]+ reported:", cleaned, flags=re.IGNORECASE):
            continue
        return cleaned[:300]
    return ""


def _escape_attr(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_subagent_notifications(
    finished: Sequence[BackgroundAgentEntry],
    *,
    still_running: Sequence[BackgroundAgentEntry] = (),
) -> str:
    blocks: list[str] = []
    for entry in finished:
        summary_line = (entry.summary or f"Status: {entry.status}").replace(
            "</subagent-finished>", "<\\/subagent-finished>"
        )
        aid = _escape_attr(entry.agent_id)
        name = _escape_attr(entry.name)
        status = _escape_attr(entry.status)
        blocks.append(
            f'<subagent-finished agent_id="{aid}" name="{name}" status="{status}">'
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
    if raw == "stalled":
        return "stalled"
    if raw == "interrupted":
        return "interrupted"
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
    run_id: str = field(default_factory=lambda: uuid4().hex[:12], repr=False)
    worktree_path: str | None = None


class BackgroundAgentRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._by_conv: dict[str, dict[str, BackgroundAgentEntry]] = {}
        self._by_id: dict[str, BackgroundAgentEntry] = {}
        self._callbacks: dict[str, list[Callable[[BackgroundAgentEntry], None]]] = {}
        self._active_waiters: dict[str, int] = {}
        self._active_turns: dict[str, int] = {}
        self._histories: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}

    def executor_for(self, conversation_id: str, *, max_workers: int = 32) -> ThreadPoolExecutor:
        conv_key = str(conversation_id or "_default")
        with self._lock:
            pool = self._executors.get(conv_key)
            if pool is None or getattr(pool, "_shutdown", False):
                pool = ThreadPoolExecutor(
                    max_workers=max(4, int(max_workers)),
                    thread_name_prefix=f"orin-bg-{conv_key[:12]}",
                )
                self._executors[conv_key] = pool
            return pool

    def submit_for(
        self,
        conversation_id: str,
        fn: Callable[..., Any],
        /,
        *args: Any,
        max_workers: int = 32,
        **kwargs: Any,
    ) -> tuple[ThreadPoolExecutor, Future]:
        conv_key = str(conversation_id or "_default")
        with self._lock:
            pool = self._executors.get(conv_key)
            if pool is None or getattr(pool, "_shutdown", False):
                pool = ThreadPoolExecutor(
                    max_workers=max(4, int(max_workers)),
                    thread_name_prefix=f"orin-bg-{conv_key[:12]}",
                )
                self._executors[conv_key] = pool
            return pool, pool.submit(fn, *args, **kwargs)

    def begin_turn(self, conversation_id: str) -> None:
        conv_id = str(conversation_id or "")
        if not conv_id:
            return
        with self._cond:
            self._active_turns[conv_id] = self._active_turns.get(conv_id, 0) + 1
            self._callbacks.pop(conv_id, None)

    def _maybe_retire_executor_locked(self, conv_id: str) -> ThreadPoolExecutor | None:
        if not conv_id:
            return None
        if self._active_turns.get(conv_id, 0) > 0:
            return None
        conv_map = self._by_conv.get(conv_id)
        if conv_map and any(entry.status == "running" for entry in conv_map.values()):
            return None
        return self._executors.pop(conv_id, None)

    def end_turn(self, conversation_id: str) -> None:
        conv_id = str(conversation_id or "")
        if not conv_id:
            return
        retired: ThreadPoolExecutor | None = None
        with self._cond:
            cur = self._active_turns.get(conv_id, 0)
            if cur <= 1:
                self._active_turns.pop(conv_id, None)
                retired = self._maybe_retire_executor_locked(conv_id)
            else:
                self._active_turns[conv_id] = cur - 1
        if retired is not None:
            retired.shutdown(wait=False)

    def remove_conversation(self, conversation_id: str) -> None:
        conv_id = str(conversation_id or "")
        if not conv_id:
            return
        self.cancel_conversation(conv_id)
        retired: ThreadPoolExecutor | None = None
        with self._cond:
            self._active_turns.pop(conv_id, None)
            self._callbacks.pop(conv_id, None)
            self._active_waiters.pop(conv_id, None)
            entries = self._by_conv.pop(conv_id, {})
            for aid in entries:
                self._by_id.pop(aid, None)
                self._histories.pop((conv_id, aid), None)
            retired = self._executors.pop(conv_id, None)
        if retired is not None:
            retired.shutdown(wait=False)

    def turn_active(self, conversation_id: str) -> bool:
        """Whether a leader turn of this conversation is running now (between begin_turn and end_turn)."""
        with self._cond:
            return self._active_turns.get(str(conversation_id or ""), 0) > 0

    def save_history(self, conversation_id: str, agent_id: str, history: list[dict[str, Any]]) -> None:
        conv_id = str(conversation_id or "")
        clean_id = str(agent_id or "")
        if not conv_id or not clean_id:
            return
        with self._lock:
            self._histories[(conv_id, clean_id)] = [dict(item) for item in history]

    def get_history(self, conversation_id: str, agent_id: str) -> list[dict[str, Any]] | None:
        conv_id = str(conversation_id or "")
        clean_id = str(agent_id or "")
        if not conv_id or not clean_id:
            return None
        with self._lock:
            saved = self._histories.get((conv_id, clean_id))
            return [dict(item) for item in saved] if saved else None

    def register(self, entry: BackgroundAgentEntry) -> None:
        conv_id = str(entry.conversation_id or "")
        agent_id = str(entry.agent_id or "")
        if not conv_id or not agent_id:
            return
        with self._cond:
            self._by_conv.setdefault(conv_id, {})[agent_id] = entry
            self._by_id[agent_id] = entry
            self._cond.notify_all()

    def finish(self, agent_id: str, status: str, summary: str, *, conversation_id: str | None = None) -> None:
        clean_id = str(agent_id or "")
        callbacks_to_run: list[Callable[[BackgroundAgentEntry], None]] = []
        entry: BackgroundAgentEntry | None = None
        with self._cond:
            if conversation_id:
                entry = self._by_conv.get(str(conversation_id), {}).get(clean_id)
            if entry is None:
                entry = self._by_id.get(clean_id)
            if entry is None:
                return
            entry.status = _normalize_status(status)
            entry.finished_at = time.monotonic()
            entry.summary = extract_report_summary(summary)
            retired_pool = self._maybe_retire_executor_locked(entry.conversation_id)
            if (
                not entry.callback_fired
                and not entry.notified
                and self._active_waiters.get(entry.conversation_id, 0) <= 0
                and self._active_turns.get(entry.conversation_id, 0) <= 0
            ):
                callbacks_to_run = list(self._callbacks.get(entry.conversation_id, ()))
                if callbacks_to_run:
                    entry.callback_fired = True
                    entry.notified = True
            self._cond.notify_all()
        if retired_pool is not None:
            retired_pool.shutdown(wait=False)
        for cb in callbacks_to_run:
            try:
                cb(entry)
            except Exception:  # noqa: BLE001
                logger.warning("background agent on_finish callback failed for %s", clean_id, exc_info=True)

    def cancel_conversation(self, conversation_id: str) -> list[str]:
        conv_id = str(conversation_id or "")
        if not conv_id:
            return []
        cancelled_ids: list[str] = []
        with self._cond:
            self._callbacks.pop(conv_id, None)
            conv_map = self._by_conv.get(conv_id, {})
            for entry in conv_map.values():
                if entry.status == "running":
                    try:
                        entry.cancel.set()
                    except Exception:
                        pass
                    if entry.future is not None:
                        try:
                            entry.future.cancel()
                        except Exception:
                            pass
                    entry.status = "stopped"
                    entry.finished_at = time.monotonic()
                    entry.summary = entry.summary or "Cancelled"
                    entry.notified = True
                    cancelled_ids.append(entry.agent_id)
            self._cond.notify_all()
        return cancelled_ids

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
                    if (
                        since_monotonic is None
                        or entry.started_at >= since_monotonic
                        or (entry.finished_at is not None and entry.finished_at >= since_monotonic)
                    ):
                        entry.notified = True
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
            if self._active_waiters.get(conv_id, 0) <= 0 and self._active_turns.get(conv_id, 0) <= 0:
                conv_map = self._by_conv.get(conv_id, {})
                for entry in conv_map.values():
                    if entry.status != "running" and not entry.notified and not entry.callback_fired:
                        entry.callback_fired = True
                        entry.notified = True
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
            self._active_turns.clear()
            self._histories.clear()
            pools = list(self._executors.values())
            self._executors.clear()
            self._cond.notify_all()
        for pool in pools:
            pool.shutdown(wait=False)


BACKGROUND_AGENTS = BackgroundAgentRegistry()


def get_background_agent_registry() -> BackgroundAgentRegistry:
    return BACKGROUND_AGENTS


DEFAULT_INTERRUPTED_REPORT = (
    "Execução interrompida pelo reinício do Orin. "
    "Este subagente ficou no estado interrompido (interrupted) e é retomável a partir do último histórico salvo."
)


def fail_orphaned_running_agents(
    engine: Engine,
    *,
    workspace_root_resolver: Callable[[str], Path] | None = None,
    reason: str = DEFAULT_INTERRUPTED_REPORT,
) -> list[str]:
    """Mark database agent records left in ``running``/``working``/``stalled`` state without an active in-process entry as ``interrupted`` (B-RF4)."""
    swept: list[str] = []
    swept_set: set[str] = set()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        rows = connection.execute(
            select(
                conversation_agents.c.agent_id,
                conversation_agents.c.conversation_id,
            ).where(conversation_agents.c.state.in_(("running", "working", "stalled")))
        ).mappings().all()
        for row in rows:
            agent_id = str(row["agent_id"] or "")
            conv_id = str(row["conversation_id"] or "")
            if not agent_id:
                continue
            live = BACKGROUND_AGENTS.get(conv_id, agent_id)
            if live is not None and live.status in {"running", "stalled"}:
                continue
            connection.execute(
                update(conversation_agents)
                .where(conversation_agents.c.agent_id == agent_id)
                .values(state="interrupted", updated_at=now)
            )
            if agent_id not in swept_set:
                swept_set.add(agent_id)
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

        # Also sweep orphaned rows in agent_runs (WS-B4 source of truth).
        try:
            run_rows = connection.execute(
                select(
                    agent_runs.c.agent_id,
                    agent_runs.c.conversation_id,
                ).where(agent_runs.c.state.in_(("running", "stalled")))
            ).mappings().all()
            for rrow in run_rows:
                raid = str(rrow["agent_id"] or "")
                rcid = str(rrow["conversation_id"] or "")
                if not raid:
                    continue
                live = BACKGROUND_AGENTS.get(rcid, raid)
                if live is not None and live.status in {"running", "stalled"}:
                    continue
                connection.execute(
                    update(agent_runs)
                    .where(agent_runs.c.agent_id == raid)
                    .values(state="interrupted", finished_at=now)
                )
                if raid not in swept_set:
                    swept_set.add(raid)
                    swept.append(raid)
        except Exception:  # noqa: BLE001 - tolerate legacy schemas without agent_runs
            logger.debug("could not sweep orphaned agent_runs rows", exc_info=True)
    return swept

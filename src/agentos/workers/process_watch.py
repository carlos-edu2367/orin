"""Worker observer for background processes that outlive a conversation turn (Fase 4).

When a turn ends paused, cancelled, failed, or completed while a background
command is still alive, nobody in the turn loop is left watching its PID. This
observer periodically inspects ``.orin/logs/processes.json`` for conversations
without an active turn, records one public ``process.exited`` activity event
per newly exited process, atomically persists ``exit_event_recorded: true`` in
the manifest (leaving ``notified_exit`` untouched so the next turn still informs
the model), and immediately fires any pending ``ScheduleWakeup(wake_on_pid=...)``
bound to that PID.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import resolve_effective_workspace_id
from agentos.agentic.tools._shared import (
    MAX_TRACKED_PROCESSES,
    _PROCESS_MANIFEST_PATH,
    _process_is_running,
    poll_background_popen,
)
from agentos.agentic.workspace import _directory_name
from agentos.installation import orin_paths
from agentos.persistence.postgres.schema import (
    conversation_turns,
    conversations,
    projects,
    workspace_roots,
)
from agentos.process_identity import process_created
from agentos.redaction import redact_text
from agentos.scheduler.scheduled_chats import ScheduledChatService

_LOGGER = logging.getLogger("agentos.workers.process_watch")

MAX_EXIT_TAIL_LINES = 20
MAX_EXIT_TAIL_CHARS = 2_000
MAX_LOG_READ_BYTES = 16_384
# Only processes started recently, by a build that tags manifest entries with their conversation, are watched:
# older entries would flood old conversations with stale exits, and an old PID may belong to another program now.
MAX_WATCH_AGE = timedelta(days=7)


def _started_recently(info: Mapping[str, Any], now: datetime) -> bool:
    raw = info.get("started_at")
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        started = datetime.fromisoformat(raw.strip())
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return now - started <= MAX_WATCH_AGE


def _resolve_folder_without_mkdir(
    row: Mapping[str, object],
    *,
    workspace_root: Path | str | None,
) -> Path | None:
    """Resolve a conversation's workspace root path without creating directories on disk."""
    local_root = row.get("workspace_root_path")
    if isinstance(local_root, str) and local_root.strip():
        try:
            return Path(local_root.strip()).resolve()
        except Exception:  # noqa: BLE001
            return None
    try:
        effective_id = resolve_effective_workspace_id(row)
        managed_base = (
            Path(workspace_root)
            if workspace_root is not None
            else orin_paths().user_workspaces(str(row.get("user_id") or ""))
        )
        return managed_base.resolve() / _directory_name(effective_id)
    except Exception:  # noqa: BLE001
        return None


def _load_manifest_file(manifest_path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(data, list):
        out: dict[str, dict[str, Any]] = {}
        for item in data:
            if isinstance(item, Mapping) and item.get("pid") is not None:
                out[str(item["pid"])] = dict(item)
        return out
    if isinstance(data, Mapping):
        return {
            str(key): dict(value)
            for key, value in data.items()
            if isinstance(value, Mapping)
        }
    return {}


def _save_manifest_atomic(manifest_path: Path, manifest: Mapping[str, Mapping[str, Any]]) -> None:
    trimmed = dict(list(manifest.items())[-MAX_TRACKED_PROCESSES:])
    serialized = json.dumps(trimmed)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_name(f"{manifest_path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp_path.write_text(serialized, encoding="utf-8")
        os.replace(tmp_path, manifest_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _read_bounded_log_tail(workspace_folder: Path, relative_log_path: object) -> str:
    if not isinstance(relative_log_path, str) or not relative_log_path.strip():
        return ""
    try:
        root_resolved = workspace_folder.resolve()
        target = (root_resolved / relative_log_path.strip()).resolve()
        target.relative_to(root_resolved)
    except (OSError, ValueError):
        return ""
    if not target.is_file():
        return ""
    try:
        with target.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_READ_BYTES))
            raw_text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = raw_text.splitlines()[-MAX_EXIT_TAIL_LINES:]
    tail = "\n".join(lines).strip()
    if len(tail) > MAX_EXIT_TAIL_CHARS:
        tail = tail[-MAX_EXIT_TAIL_CHARS:]
    return redact_text(tail)


def _entry_status(pid: int, info: Mapping[str, Any]) -> tuple[bool, int | None]:
    """Return ``(running, exit_code)`` for a tracked manifest entry."""
    polled = poll_background_popen(pid)
    if polled is not None:
        running, code = polled
        if not running and code is None:
            raw_code = info.get("exit_code")
            if isinstance(raw_code, int) and not isinstance(raw_code, bool):
                code = int(raw_code)
        return running, code
    running = _process_is_running(pid)
    if running:
        created = info.get("created")
        if created not in (None, ""):
            current_created = process_created(pid)
            if current_created is not None and current_created != created:
                running = False
    if running:
        return True, None
    raw_code = info.get("exit_code")
    exit_code = int(raw_code) if isinstance(raw_code, int) and not isinstance(raw_code, bool) else None
    return False, exit_code


def format_process_exited_summary(pid: int, label: str, exit_code: int | None) -> str:
    clean_label = " ".join(str(label or "").split())[:80]
    prefix = f"Processo {pid} ({clean_label})" if clean_label else f"Processo {pid}"
    if exit_code is not None:
        if exit_code != 0:
            return f"{prefix} terminou com erro (exit {exit_code})"
        return f"{prefix} terminou (exit 0)"
    return f"{prefix} terminou"


def format_wakeup_exit_notice(
    pid: int,
    label: str,
    exit_code: int | None,
    log_path: str,
    output_tail: str,
) -> str:
    clean_label = " ".join(str(label or "").split())[:120]
    target = f"{pid} ({clean_label})" if clean_label else str(pid)
    if exit_code is None:
        outcome = "exited"
    elif exit_code == 0:
        outcome = "exited (exit 0)"
    else:
        outcome = f"failed (exit {exit_code})"
    header = f"[Background process {target} {outcome}" + (f", log: {log_path}]" if log_path else "]")
    if output_tail:
        return f"{header}\nLast output:\n{output_tail}"
    return header


def sweep_exited_processes(
    store: Any,
    *,
    workspace_root: Path | str | None = None,
    scheduler: ScheduledChatService | None = None,
) -> tuple[dict[str, object], ...]:
    """Scan inactive conversations' ``processes.json`` and record ``process.exited`` events."""
    engine = getattr(store, "_engine", None)
    if engine is None:
        return ()
    if scheduler is None:
        try:
            scheduler = ScheduledChatService(engine)
        except Exception:  # noqa: BLE001
            scheduler = None

    now = datetime.now(UTC)
    with engine.connect() as connection:
        active_turn_convs = {
            str(cid)
            for cid in connection.execute(
                select(conversation_turns.c.conversation_id).where(
                    conversation_turns.c.state.in_(("running", "queued", "starting"))
                    | conversation_turns.c.state.like("waiting_%")
                )
            ).scalars()
        }
        effective_workspace_id = func.coalesce(projects.c.workspace_id, conversations.c.conversation_id)
        conv_rows = connection.execute(
            select(
                conversations.c.conversation_id,
                conversations.c.user_id,
                conversations.c.project_id,
                conversations.c.state,
                projects.c.workspace_id.label("project_workspace_id"),
                workspace_roots.c.root_path.label("workspace_root_path"),
            )
            .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
            .outerjoin(
                workspace_roots,
                (workspace_roots.c.workspace_id == effective_workspace_id)
                & (workspace_roots.c.user_id == conversations.c.user_id),
            )
            .where(conversations.c.updated_at >= now - MAX_WATCH_AGE)
            .order_by(conversations.c.updated_at.desc())
        ).mappings().all()

    active_folders: set[Path] = set()
    candidates: list[tuple[Mapping[str, object], Path]] = []
    for row in conv_rows:
        conv_id = str(row["conversation_id"])
        conv_state = str(row.get("state") or "").strip().lower()
        is_active = (
            conv_id in active_turn_convs
            or conv_state in {"running", "queued", "starting"}
            or conv_state.startswith("waiting_")
        )
        folder = _resolve_folder_without_mkdir(row, workspace_root=workspace_root)
        if folder is None:
            continue
        if is_active:
            active_folders.add(folder)
        else:
            candidates.append((row, folder))

    recorded: list[dict[str, object]] = []
    for row, folder in candidates:
        if folder in active_folders:
            continue
        manifest_path = folder / _PROCESS_MANIFEST_PATH
        if not manifest_path.is_file():
            continue
        manifest = _load_manifest_file(manifest_path)
        if not manifest:
            continue
        conv_id = str(row["conversation_id"])
        user_id = str(row["user_id"])

        dirty = False
        exited_items: list[tuple[int, dict[str, Any], int | None, str]] = []
        for pid_text, info in list(manifest.items()):
            if not isinstance(info, Mapping):
                continue
            try:
                pid = int(pid_text)
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            owner_conv = info.get("conversation_id")
            if not isinstance(owner_conv, str) or owner_conv.strip() != conv_id:
                continue
            if info.get("exit_event_recorded") or info.get("notified_exit"):
                continue
            if not _started_recently(info, now):
                continue
            running, exit_code = _entry_status(pid, info)
            if running:
                continue
            exited_at = str(info.get("exited_at") or datetime.now(UTC).isoformat())
            updated = dict(info)
            updated["exit_event_recorded"] = True
            updated["exit_code"] = exit_code
            updated["exited_at"] = exited_at
            updated.setdefault("notified_exit", False)
            manifest[pid_text] = updated
            dirty = True
            tail = _read_bounded_log_tail(folder, info.get("log_path"))
            exited_items.append((pid, updated, exit_code, tail))

        if scheduler is not None:
            exited_pids = {item[0] for item in exited_items}
            try:
                pending_wakeups = scheduler.list_for_conversation(user_id, conv_id, sources=("wakeup",))
            except Exception:  # noqa: BLE001
                pending_wakeups = []
            for wake in pending_wakeups:
                wake_pid = wake.get("wake_on_pid")
                if not isinstance(wake_pid, int) or isinstance(wake_pid, bool) or wake_pid in exited_pids:
                    continue
                info = manifest.get(str(wake_pid))
                if not isinstance(info, Mapping):
                    continue
                running, exit_code = _entry_status(wake_pid, info)
                if running:
                    continue
                command = redact_text(str(info.get("command") or "").strip())[:400]
                description = redact_text(str(info.get("description") or "").strip())[:200]
                label = (description or command)[:120]
                log_path = str(info.get("log_path") or "").strip()[:512]
                tail = _read_bounded_log_tail(folder, info.get("log_path"))
                try:
                    notice = format_wakeup_exit_notice(wake_pid, label, exit_code, log_path, tail)
                    scheduler.trigger_wakeup_for_pid(user_id, conv_id, wake_pid, exit_notice=notice)
                except Exception:
                    _LOGGER.exception("could not trigger pending wakeup for %s pid %s", conv_id, wake_pid)

        if not exited_items:
            continue

        if dirty:
            try:
                _save_manifest_atomic(manifest_path, manifest)
            except Exception:
                _LOGGER.exception("could not persist updated process manifest at %s", manifest_path)
                continue

        # Find latest turn context for activity emission.
        with engine.connect() as connection:
            latest_turn = connection.execute(
                select(conversation_turns.c.turn_id, conversation_turns.c.execution_id)
                .where(conversation_turns.c.conversation_id == conv_id)
                .order_by(conversation_turns.c.created_at.desc(), conversation_turns.c.id.desc())
                .limit(1)
            ).mappings().first()
        turn_id = str(latest_turn["turn_id"]) if latest_turn is not None else f"process:{conv_id}"
        execution_id = str(latest_turn["execution_id"]) if latest_turn is not None else f"exec:{turn_id}"
        turn_ctx = {
            "conversation_id": conv_id,
            "user_id": user_id,
            "turn_id": turn_id,
            "execution_id": execution_id,
        }

        for pid, info, exit_code, tail in exited_items:
            command = redact_text(str(info.get("command") or "").strip())[:400]
            description = redact_text(str(info.get("description") or "").strip())[:200]
            label = (description or command)[:120]
            log_path = str(info.get("log_path") or "").strip()[:512]
            failed = exit_code is not None and exit_code != 0
            summary = format_process_exited_summary(pid, label, exit_code)
            payload: dict[str, object] = {
                "pid": pid,
                "command": command,
                "description": description,
                "label": label,
                "exit_code": exit_code,
                "log_path": log_path,
                "output_tail": tail,
                "status": "failed" if failed else "completed",
                "failed": failed,
            }
            try:
                store._activity(turn_ctx, AgentActivityEventType.PROCESS_EXITED, summary, payload)
            except Exception:
                _LOGGER.exception("could not record process.exited activity for %s pid %s", conv_id, pid)

            woke_schedule_id: str | None = None
            if scheduler is not None:
                try:
                    notice = format_wakeup_exit_notice(pid, label, exit_code, log_path, tail)
                    woke_schedule_id = scheduler.trigger_wakeup_for_pid(
                        user_id,
                        conv_id,
                        pid,
                        exit_notice=notice,
                    )
                except Exception:
                    _LOGGER.exception("could not trigger wakeup for %s pid %s", conv_id, pid)

            recorded.append(
                {
                    "conversation_id": conv_id,
                    "turn_id": turn_id,
                    "pid": pid,
                    "exit_code": exit_code,
                    "summary": summary,
                    "woke_schedule_id": woke_schedule_id,
                }
            )

    return tuple(recorded)


__all__ = [
    "MAX_EXIT_TAIL_CHARS",
    "MAX_EXIT_TAIL_LINES",
    "format_process_exited_summary",
    "format_wakeup_exit_notice",
    "sweep_exited_processes",
]

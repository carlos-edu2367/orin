"""Durable persistence for background subagent runs and message histories (WS-B4, B-RF4).

Writes structured state to ``agent_runs`` and atomically saves redacted message
history snapshots to ``<data>/agent-runs/<safe_agent_id>.json`` so interrupted
background agents survive process restarts and can resume cleanly.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any

from sqlalchemy.engine import Engine

from agentos.installation import orin_paths
from agentos.persistence.postgres.agent_runs import AgentRunRecord, AgentRunStore
from agentos.redaction import redact_value

logger = logging.getLogger("agentos.agentic.agent_run_persistence")

DEFAULT_MAX_HISTORY_BYTES = 512 * 1024  # 512 KiB per agent history file
DEFAULT_MAX_HISTORY_MESSAGES = 200
DEFAULT_MAX_HISTORY_FILES = 500
DEFAULT_MIN_TOUCH_INTERVAL_SECONDS = 5.0
HISTORY_SCHEMA_VERSION = 1

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


class PathContainmentError(ValueError):
    """Raised when a target path attempts to escape its allowed directory."""


def default_agent_runs_dir() -> Path:
    """Return the configured directory for persisted subagent histories."""
    override = os.environ.get("ORIN_AGENT_RUNS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return orin_paths().data / "agent-runs"


def safe_agent_run_filename(agent_id: str) -> str:
    """Build a Windows-safe, collision-resistant JSON filename for ``agent_id``.

    Real Orin agent IDs contain ``:`` (e.g. ``agent:conv-1:abcd``), which is
    forbidden in Windows filenames. Combines a sanitized slug with a 12-hex
    SHA-256 digest of the original ``agent_id``.
    """
    clean = str(agent_id or "").strip()
    if not clean:
        raise ValueError("agent_id must be non-blank")
    slug = _SAFE_SLUG_RE.sub("_", clean).strip("._")[:64] or "agent"
    digest = sha256(clean.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}.json"


def resolve_contained(base_dir: Path, filename: str) -> Path:
    """Resolve ``filename`` strictly as a direct child inside ``base_dir``.

    Rejects path separators, drive letters, parent traversal (``..``), NUL bytes,
    and any symlink or resolved path escaping ``base_dir``.
    """
    raw = str(filename or "")
    if not raw or "\0" in raw:
        raise PathContainmentError("invalid filename")
    if "/" in raw or "\\" in raw or _DRIVE_PREFIX_RE.match(raw):
        raise PathContainmentError("filename must not contain path separators or drive letters")
    if raw in {".", ".."} or ".." in Path(raw).parts:
        raise PathContainmentError("filename must not traverse parent directories")
    base = Path(base_dir).resolve()
    try:
        candidate = (base / raw).resolve()
    except (OSError, ValueError) as exc:
        raise PathContainmentError("filename cannot be resolved") from exc
    if candidate == base or candidate.parent != base or not candidate.is_relative_to(base):
        raise PathContainmentError("resolved path escapes base directory")
    return candidate


@dataclass(frozen=True, slots=True)
class RestoredHistory:
    """Result of loading a persisted subagent history file."""

    messages: list[dict[str, Any]]
    corrupted: bool = False
    missing: bool = False
    warning: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def resume_notice(self) -> str:
        """Build the interruption notice injected into the resumed subagent task."""
        parts = [
            "Note: Your previous background execution was interrupted before completion "
            "(for example due to an Orin restart). Any prior work or side-effecting tool "
            "calls may have been partial — inspect the current workspace state before "
            "repeating mutating actions, and continue from where you left off."
        ]
        if self.corrupted:
            parts.append(
                "Warning: The saved message history file was corrupted or unreadable and "
                "could not be restored; resuming without prior transcript messages."
            )
        elif self.missing:
            parts.append(
                "Note: No prior saved message history was found; resuming from the original task."
            )
        return " ".join(parts)


def _redact_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, Mapping):
            continue
        redacted = redact_value(dict(item))
        if isinstance(redacted, dict):
            sanitized.append(redacted)
    return sanitized


def _trim_messages_to_limit(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
    max_bytes: int,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    trimmed = list(messages[-max(1, int(max_messages)) :]) if messages else []

    def _encoded_size(items: list[dict[str, Any]]) -> int:
        payload = {
            "version": HISTORY_SCHEMA_VERSION,
            "metadata": metadata,
            "messages": items,
        }
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    while len(trimmed) > 1 and _encoded_size(trimmed) > max_bytes:
        trimmed.pop(0)

    if len(trimmed) == 1 and _encoded_size(trimmed) > max_bytes:
        single = dict(trimmed[0])
        content = single.get("content")
        if isinstance(content, str) and len(content) > 256:
            overhead = _encoded_size([{**single, "content": ""}])
            budget = max(128, max_bytes - overhead - 32)
            single["content"] = content[-budget:]
            trimmed = [single]
    return trimmed


class AgentRunRecorder:
    """Coordinates ``agent_runs`` DB records and atomic JSON history snapshots."""

    def __init__(
        self,
        engine: Engine,
        *,
        runs_dir: Path | None = None,
        store: AgentRunStore | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        max_history_bytes: int = DEFAULT_MAX_HISTORY_BYTES,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
        max_files: int = DEFAULT_MAX_HISTORY_FILES,
        min_touch_interval_seconds: float = DEFAULT_MIN_TOUCH_INTERVAL_SECONDS,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self.store = store or AgentRunStore(engine, clock=self._clock)
        self.runs_dir = Path(runs_dir) if runs_dir is not None else default_agent_runs_dir()
        self.max_history_bytes = max(512, int(max_history_bytes))
        self.max_history_messages = max(1, int(max_history_messages))
        self.max_files = max(1, int(max_files))
        self.min_touch_interval_seconds = max(0.0, float(min_touch_interval_seconds))
        self._lock = threading.Lock()
        self._last_touch_mono: dict[str, float] = {}

    def history_path_for(self, agent_id: str) -> Path:
        """Return the validated, contained path for ``agent_id``'s JSON history file."""
        filename = safe_agent_run_filename(agent_id)
        return resolve_contained(self.runs_dir, filename)

    def _read_raw_payload(self, path: Path) -> tuple[dict[str, Any] | None, bool, bool]:
        """Return ``(payload, corrupted, missing)`` without ever raising on bad JSON."""
        if not path.exists():
            return None, False, True
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, True, False
        if not isinstance(data, dict):
            return None, True, False
        messages = data.get("messages")
        if messages is not None and not isinstance(messages, list):
            return None, True, False
        return data, False, False

    def _atomic_write_json(self, target: Path, payload: Mapping[str, Any]) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        # Re-validate containment after directory creation.
        contained = resolve_contained(self.runs_dir, target.name)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tmp_path: Path | None = None
        try:
            fd, raw_tmp = tempfile.mkstemp(
                dir=str(contained.parent),
                prefix=f".{contained.name}.",
                suffix=".tmp",
            )
            tmp_path = Path(raw_tmp)
            try:
                os.write(fd, encoded)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
            os.replace(tmp_path, contained)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._prune_old_files(keep_path=contained)
        return contained

    def _prune_old_files(self, *, keep_path: Path | None = None) -> None:
        if not self.runs_dir.is_dir():
            return
        try:
            files = [p for p in self.runs_dir.glob("*.json") if p.is_file()]
        except OSError:
            return
        if len(files) <= self.max_files:
            return
        keep_resolved = keep_path.resolve() if keep_path is not None else None
        candidates = []
        for path in files:
            try:
                if keep_resolved is not None and path.resolve() == keep_resolved:
                    continue
                stat = path.stat()
                candidates.append((stat.st_mtime_ns, path))
            except OSError:
                continue
        candidates.sort(key=lambda item: item[0])
        to_remove = max(0, len(files) - self.max_files)
        for _, victim in candidates[:to_remove]:
            try:
                victim.unlink(missing_ok=True)
            except OSError:
                pass

    def save_history(
        self,
        agent_id: str,
        history: Sequence[Mapping[str, Any]],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Atomically persist redacted ``history`` for ``agent_id``."""
        target = self.history_path_for(agent_id)
        existing_meta: dict[str, Any] = {}
        existing_payload, corrupted, missing = self._read_raw_payload(target)
        if not corrupted and not missing and isinstance(existing_payload, dict):
            raw_meta = existing_payload.get("metadata")
            if isinstance(raw_meta, dict):
                existing_meta.update(raw_meta)

        merged_meta = dict(existing_meta)
        if metadata:
            redacted_meta = redact_value(dict(metadata))
            if isinstance(redacted_meta, dict):
                merged_meta.update(redacted_meta)
        merged_meta["agent_id"] = str(agent_id)
        merged_meta["updated_at"] = self._clock().astimezone(UTC).isoformat()

        sanitized = _redact_messages(history)
        bounded = _trim_messages_to_limit(
            sanitized,
            max_messages=self.max_history_messages,
            max_bytes=self.max_history_bytes,
            metadata=merged_meta,
        )
        payload = {
            "version": HISTORY_SCHEMA_VERSION,
            "metadata": merged_meta,
            "messages": bounded,
        }
        return self._atomic_write_json(target, payload)

    def restore_history(
        self,
        agent_id: str,
        *,
        history_path: str | Path | None = None,
    ) -> RestoredHistory:
        """Restore persisted history for ``agent_id``; never raises on corruption."""
        target = self.history_path_for(agent_id)
        if history_path is not None:
            candidate = Path(history_path)
            try:
                base = self.runs_dir.resolve()
                resolved = candidate.resolve()
                if resolved.is_relative_to(base) and resolved.parent == base:
                    target = resolved
            except (OSError, ValueError):
                pass
        data, corrupted, missing = self._read_raw_payload(target)
        if missing:
            return RestoredHistory(messages=[], corrupted=False, missing=True, warning=None, metadata={})
        if corrupted or data is None:
            logger.warning("corrupted agent run history file ignored for %s: %s", agent_id, target)
            return RestoredHistory(
                messages=[],
                corrupted=True,
                missing=False,
                warning=(
                    f"Saved history for agent '{agent_id}' was corrupted or truncated and could not be loaded; "
                    "resuming with empty history."
                ),
                metadata={},
            )
        raw_messages = data.get("messages") or []
        messages = [dict(item) for item in raw_messages if isinstance(item, Mapping)]
        raw_meta = data.get("metadata")
        meta = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}
        return RestoredHistory(
            messages=messages,
            corrupted=False,
            missing=False,
            warning=None,
            metadata=meta,
        )

    def has_auto_resumed(self, agent_id: str) -> bool:
        restored = self.restore_history(agent_id)
        return bool(restored.metadata.get("auto_resumed"))

    def mark_auto_resumed(self, agent_id: str) -> None:
        restored = self.restore_history(agent_id)
        meta = dict(restored.metadata)
        meta["auto_resumed"] = True
        meta["auto_resumed_at"] = self._clock().astimezone(UTC).isoformat()
        self.save_history(agent_id, restored.messages, metadata=meta)

    def clear_auto_resumed(self, agent_id: str) -> None:
        restored = self.restore_history(agent_id)
        meta = dict(restored.metadata)
        meta["auto_resumed"] = False
        meta["auto_resumed_at"] = None
        self.save_history(agent_id, restored.messages, metadata=meta)

    def start(
        self,
        *,
        agent_id: str,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        parent_agent_id: str | None = None,
        subagent_type: str = "general-purpose",
        provider: str | None = None,
        model_id: str | None = None,
        effort: str | None = None,
        task: str = "",
        agent_name: str | None = None,
        state: str = "running",
        worktree_path: str | None = None,
        pipeline_run_id: str | None = None,
        result_ref: str | None = None,
        initial_history: Sequence[Mapping[str, Any]] | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> AgentRunRecord:
        """Create or update the ``agent_runs`` row and initialize its history file."""
        clean_task = str(task or "")
        digest = sha256(clean_task.encode("utf-8")).hexdigest()[:64] if clean_task else ""
        hpath = self.history_path_for(agent_id)

        meta: dict[str, Any] = {
            "subagent_type": str(subagent_type or "general-purpose"),
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
        }
        if agent_name:
            meta["agent_name"] = str(agent_name)
        if clean_task:
            meta["task"] = clean_task[:4000]
        if pipeline_run_id:
            meta["pipeline_run_id"] = str(pipeline_run_id)
        if extra_metadata:
            for k, v in extra_metadata.items():
                if v is not None:
                    meta[str(k)] = v

        if initial_history is not None:
            self.save_history(agent_id, initial_history, metadata=meta)
        else:
            # Preserve any existing messages/auto_resumed flag when restarting/updating metadata.
            existing = self.restore_history(agent_id)
            if not existing.corrupted:
                self.save_history(agent_id, existing.messages, metadata=meta)

        now = self._clock()
        record = self.store.upsert(
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            provider=provider,
            model_id=model_id,
            effort=effort,
            state=state,
            task_digest=digest,
            history_path=str(hpath),
            worktree_path=worktree_path,
            pipeline_run_id=pipeline_run_id,
            last_activity_at=now,
            result_ref=result_ref,
            created_at=now,
            finished_at=None,
        )
        with self._lock:
            self._last_touch_mono[str(agent_id)] = self._monotonic()
        return record

    def touch(
        self,
        agent_id: str,
        *,
        tool_calls: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        state: str | None = None,
        result_ref: str | None = None,
        worktree_path: str | None = None,
        history: Sequence[Mapping[str, Any]] | None = None,
        force: bool = False,
    ) -> AgentRunRecord | None:
        """Persist history (if provided) and batch-update ``agent_runs`` (<= once per 5s unless forced)."""
        if history is not None:
            try:
                self.save_history(agent_id, history)
            except Exception:  # noqa: BLE001
                logger.warning("failed saving history snapshot for %s", agent_id, exc_info=True)

        now_mono = self._monotonic()
        with self._lock:
            last = self._last_touch_mono.get(str(agent_id))
            if not force and state is None and last is not None:
                if (now_mono - last) < self.min_touch_interval_seconds:
                    return None
            self._last_touch_mono[str(agent_id)] = now_mono

        # RT-B-06: preserve monotonically aggregated subtree metrics (child + grandchildren)
        # so direct caller touches never overwrite subtree tokens/cost recorded by ProgressTracker.
        from agentos.agentic.agent_progress import shared_progress_snapshot

        snap = shared_progress_snapshot(self._engine, agent_id)
        existing = self.store.get(agent_id)
        eff_tool_calls = tool_calls
        eff_input = input_tokens
        eff_output = output_tokens
        eff_cost = cost_usd
        if snap is not None:
            eff_tool_calls = max(int(tool_calls or 0), int(snap.tool_calls)) if (tool_calls is not None or snap.tool_calls > 0) else None
            eff_input = max(int(input_tokens or 0), int(snap.input_tokens)) if (input_tokens is not None or snap.input_tokens > 0) else None
            eff_output = max(int(output_tokens or 0), int(snap.output_tokens)) if (output_tokens is not None or snap.output_tokens > 0) else None
            if eff_cost is None and snap.cost_usd > 0:
                eff_cost = snap.cost_usd
            elif eff_cost is not None:
                eff_cost = max(float(eff_cost), float(snap.cost_usd))
        if existing is not None:
            if eff_tool_calls is not None:
                eff_tool_calls = max(int(eff_tool_calls), int(existing.tool_calls))
            if eff_input is not None:
                eff_input = max(int(eff_input), int(existing.input_tokens))
            if eff_output is not None:
                eff_output = max(int(eff_output), int(existing.output_tokens))
            if eff_cost is None and existing.cost_usd is not None:
                eff_cost = float(existing.cost_usd)
            elif eff_cost is not None and existing.cost_usd is not None:
                eff_cost = max(float(eff_cost), float(existing.cost_usd))

        return self.store.touch(
            agent_id,
            tool_calls=eff_tool_calls,
            input_tokens=eff_input,
            output_tokens=eff_output,
            cost_usd=eff_cost,
            last_activity_at=self._clock(),
            state=state,
            result_ref=result_ref,
            worktree_path=worktree_path,
        )

    def finish(
        self,
        agent_id: str,
        *,
        state: str,
        tool_calls: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        result_ref: str | None = None,
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> AgentRunRecord | None:
        """Flush final history and terminal state to ``agent_runs`` unconditionally."""
        if history is not None:
            try:
                self.save_history(agent_id, history)
            except Exception:  # noqa: BLE001
                logger.warning("failed saving final history for %s", agent_id, exc_info=True)

        now = self._clock()
        with self._lock:
            self._last_touch_mono[str(agent_id)] = self._monotonic()

        # RT-B-06 & RT-B-13: preserve aggregated subtree metrics and never overwrite 'cancelled'
        # (e.g. from stall.autoCancel) with 'failed' when the worker thread unwinds.
        from agentos.agentic.agent_progress import shared_progress_snapshot

        snap = shared_progress_snapshot(self._engine, agent_id)
        existing = self.store.get(agent_id)
        eff_state = state
        if state == "failed" and (
            (existing is not None and existing.state == "cancelled")
            or (snap is not None and snap.state == "cancelled")
        ):
            eff_state = "cancelled"

        eff_tool_calls = tool_calls
        eff_input = input_tokens
        eff_output = output_tokens
        eff_cost = cost_usd
        if snap is not None:
            eff_tool_calls = max(int(tool_calls or 0), int(snap.tool_calls)) if (tool_calls is not None or snap.tool_calls > 0) else None
            eff_input = max(int(input_tokens or 0), int(snap.input_tokens)) if (input_tokens is not None or snap.input_tokens > 0) else None
            eff_output = max(int(output_tokens or 0), int(snap.output_tokens)) if (output_tokens is not None or snap.output_tokens > 0) else None
            if eff_cost is None and snap.cost_usd > 0:
                eff_cost = snap.cost_usd
            elif eff_cost is not None:
                eff_cost = max(float(eff_cost), float(snap.cost_usd))
        if existing is not None:
            if eff_tool_calls is not None:
                eff_tool_calls = max(int(eff_tool_calls), int(existing.tool_calls))
            if eff_input is not None:
                eff_input = max(int(eff_input), int(existing.input_tokens))
            if eff_output is not None:
                eff_output = max(int(eff_output), int(existing.output_tokens))
            if eff_cost is None and existing.cost_usd is not None:
                eff_cost = float(existing.cost_usd)
            elif eff_cost is not None and existing.cost_usd is not None:
                eff_cost = max(float(eff_cost), float(existing.cost_usd))

        return self.store.touch(
            agent_id,
            tool_calls=eff_tool_calls,
            input_tokens=eff_input,
            output_tokens=eff_output,
            cost_usd=eff_cost,
            last_activity_at=now,
            state=eff_state,
            result_ref=result_ref,
            finished_at=now,
        )

    def get(self, agent_id: str) -> AgentRunRecord | None:
        return self.store.get(agent_id)

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        state: str | None = None,
    ) -> list[AgentRunRecord]:
        return self.store.list_for_conversation(conversation_id, user_id=user_id, state=state)


__all__ = [
    "AgentRunRecorder",
    "DEFAULT_MAX_HISTORY_BYTES",
    "DEFAULT_MAX_HISTORY_FILES",
    "DEFAULT_MAX_HISTORY_MESSAGES",
    "DEFAULT_MIN_TOUCH_INTERVAL_SECONDS",
    "PathContainmentError",
    "RestoredHistory",
    "default_agent_runs_dir",
    "resolve_contained",
    "safe_agent_run_filename",
]

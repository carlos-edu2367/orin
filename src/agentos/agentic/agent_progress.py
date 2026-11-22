"""Live progress tracking and stall detection for subagents (WS-B5, B-RF5 & B-RF6).

Tracks per-agent activity, active tool, tool call counts, subtree token/cost
aggregation (child + grandchildren), throttled ``agent.progress`` emission
(<= 1 per 10 s per agent), batched ``agent_runs.touch`` persistence (<= 1 per
5 s and unconditionally on finish), and configurable stall detection/auto-cancel.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import threading
import time
from typing import Any

DEFAULT_PROGRESS_INTERVAL_SECONDS = 10.0
DEFAULT_STALL_MINUTES = 10.0


@dataclass(frozen=True, slots=True)
class AgentProgressSnapshot:
    """Point-in-time progress view for one subagent (including subtree usage)."""

    agent_id: str
    state: str
    elapsed_seconds: float
    last_activity_at: datetime
    current_tool: str | None
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stalled: bool
    parent_agent_id: str | None = None
    conversation_id: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "state": self.state,
            "elapsed_seconds": round(max(0.0, float(self.elapsed_seconds)), 3),
            "last_activity_at": self.last_activity_at.astimezone(UTC).isoformat(),
            "current_tool": self.current_tool,
            "tool_calls": int(self.tool_calls),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cost_usd": round(float(self.cost_usd), 8),
            "stalled": bool(self.stalled),
        }


@dataclass
class _AgentProgressEntry:
    agent_id: str
    conversation_id: str
    parent_agent_id: str | None
    state: str
    started_mono: float
    started_at: datetime
    last_activity_mono: float
    last_activity_at: datetime
    finished_mono: float | None = None
    current_tool: str | None = None
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stalled: bool = False
    stall_notified: bool = False
    last_progress_emit_mono: float | None = None


@dataclass
class _SharedProgressBucket:
    lock: threading.Lock = field(default_factory=threading.Lock)
    entries: dict[str, _AgentProgressEntry] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)


_BUCKETS_GUARD = threading.Lock()
_BUCKETS_BY_ENGINE_ID: dict[int, _SharedProgressBucket] = {}


def _engine_id_for_recorder(recorder: Any) -> int | None:
    if recorder is None:
        return None
    engine = getattr(recorder, "_engine", None)
    if engine is None:
        store = getattr(recorder, "store", None)
        engine = getattr(store, "_engine", None)
    return id(engine) if engine is not None else None


def _shared_bucket_for_engine_id(engine_id: int) -> _SharedProgressBucket:
    with _BUCKETS_GUARD:
        bucket = _BUCKETS_BY_ENGINE_ID.get(engine_id)
        if bucket is None:
            bucket = _SharedProgressBucket()
            _BUCKETS_BY_ENGINE_ID[engine_id] = bucket
        return bucket


def clear_shared_progress_for_tests() -> None:
    with _BUCKETS_GUARD:
        _BUCKETS_BY_ENGINE_ID.clear()


def shared_progress_snapshot(engine: Any, agent_id: str) -> AgentProgressSnapshot | None:
    """Read live subtree metrics/state for ``agent_id`` from the engine's shared ProgressTracker bucket."""
    if engine is None:
        return None
    aid = str(agent_id or "").strip()
    if not aid:
        return None
    with _BUCKETS_GUARD:
        bucket = _BUCKETS_BY_ENGINE_ID.get(id(engine))
    if bucket is None:
        return None
    now_m = time.monotonic()
    with bucket.lock:
        entry = bucket.entries.get(aid)
        if entry is None:
            return None
        visited: set[str] = set()
        stack = [aid]
        in_tok = 0
        out_tok = 0
        cost = 0.0
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            cur_entry = bucket.entries.get(cur)
            if cur_entry is not None:
                in_tok += cur_entry.input_tokens
                out_tok += cur_entry.output_tokens
                cost += cur_entry.cost_usd
            for child_id in bucket.children.get(cur, ()):
                if child_id not in visited:
                    stack.append(child_id)
        end_m = entry.finished_mono if entry.finished_mono is not None else now_m
        elapsed = max(0.0, end_m - entry.started_mono)
        return AgentProgressSnapshot(
            agent_id=entry.agent_id,
            state=entry.state,
            elapsed_seconds=elapsed,
            last_activity_at=entry.last_activity_at,
            current_tool=entry.current_tool,
            tool_calls=entry.tool_calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            stalled=entry.stalled,
            parent_agent_id=entry.parent_agent_id,
            conversation_id=entry.conversation_id,
        )


class ProgressTracker:
    """Thread-safe progress tracker with injectable clocks for deterministic testing."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        on_progress: Callable[[AgentProgressSnapshot], None] | None = None,
        on_stalled: Callable[[AgentProgressSnapshot], None] | None = None,
        recorder: Any = None,
        progress_interval_seconds: float = DEFAULT_PROGRESS_INTERVAL_SECONDS,
    ) -> None:
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._on_progress = on_progress
        self._on_stalled = on_stalled
        self._recorder = recorder
        self._progress_interval = max(0.0, float(progress_interval_seconds))
        engine_id = _engine_id_for_recorder(recorder)
        if engine_id is not None:
            # RT-B-06: share live progress state across TurnSessions backed by the same DB engine
            # so background subagents crossing turn boundaries retain current_tool, subtree tokens,
            # stall detection, and stall.autoCancel.
            bucket = _shared_bucket_for_engine_id(engine_id)
            self._lock = bucket.lock
            self._entries = bucket.entries
            self._children = bucket.children
        else:
            self._lock = threading.Lock()
            self._entries = {}
            self._children = {}

    def _now_mono(self) -> float:
        return float(self._clock())

    def _now_wall(self) -> datetime:
        dt = self._wall_clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def start(
        self,
        agent_id: str,
        *,
        conversation_id: str = "",
        parent_agent_id: str | None = None,
        state: str = "running",
    ) -> None:
        aid = str(agent_id or "").strip()
        if not aid:
            return
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            existing = self._entries.get(aid)
            if existing is not None:
                existing.state = str(state or "running")
                existing.last_activity_mono = now_m
                existing.last_activity_at = now_w
                existing.finished_mono = None
                existing.stalled = False
                existing.stall_notified = False
                if parent_agent_id and parent_agent_id != aid:
                    existing.parent_agent_id = str(parent_agent_id)
                    self._children.setdefault(str(parent_agent_id), set()).add(aid)
                return
            entry = _AgentProgressEntry(
                agent_id=aid,
                conversation_id=str(conversation_id or ""),
                parent_agent_id=str(parent_agent_id) if parent_agent_id else None,
                state=str(state or "running"),
                started_mono=now_m,
                started_at=now_w,
                last_activity_mono=now_m,
                last_activity_at=now_w,
            )
            self._entries[aid] = entry
            if parent_agent_id and str(parent_agent_id) != aid:
                self._children.setdefault(str(parent_agent_id), set()).add(aid)

    def _subtree_usage_locked(self, agent_id: str) -> tuple[int, int, float]:
        visited: set[str] = set()
        stack = [agent_id]
        in_tok = 0
        out_tok = 0
        cost = 0.0
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            entry = self._entries.get(cur)
            if entry is not None:
                in_tok += entry.input_tokens
                out_tok += entry.output_tokens
                cost += entry.cost_usd
            for child_id in self._children.get(cur, ()):
                if child_id not in visited:
                    stack.append(child_id)
        return in_tok, out_tok, cost

    def _build_snapshot_locked(self, entry: _AgentProgressEntry, now_m: float) -> AgentProgressSnapshot:
        end_m = entry.finished_mono if entry.finished_mono is not None else now_m
        elapsed = max(0.0, end_m - entry.started_mono)
        in_tok, out_tok, cost = self._subtree_usage_locked(entry.agent_id)
        return AgentProgressSnapshot(
            agent_id=entry.agent_id,
            state=entry.state,
            elapsed_seconds=elapsed,
            last_activity_at=entry.last_activity_at,
            current_tool=entry.current_tool,
            tool_calls=entry.tool_calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            stalled=entry.stalled,
            parent_agent_id=entry.parent_agent_id,
            conversation_id=entry.conversation_id,
        )

    def _maybe_emit_and_touch(
        self,
        entry: _AgentProgressEntry,
        now_m: float,
        *,
        force_touch: bool = False,
        touch_state: str | None = None,
    ) -> None:
        emit_snap: AgentProgressSnapshot | None = None
        touch_snap: AgentProgressSnapshot | None = None
        with self._lock:
            if (
                self._on_progress is not None
                and (
                    entry.last_progress_emit_mono is None
                    or (now_m - entry.last_progress_emit_mono) >= self._progress_interval
                )
            ):
                entry.last_progress_emit_mono = now_m
                emit_snap = self._build_snapshot_locked(entry, now_m)
            if self._recorder is not None:
                touch_snap = self._build_snapshot_locked(entry, now_m)

        if emit_snap is not None and self._on_progress is not None:
            try:
                self._on_progress(emit_snap)
            except Exception:  # noqa: BLE001
                pass

        if touch_snap is not None and self._recorder is not None:
            try:
                self._recorder.touch(
                    entry.agent_id,
                    tool_calls=touch_snap.tool_calls,
                    input_tokens=touch_snap.input_tokens,
                    output_tokens=touch_snap.output_tokens,
                    cost_usd=touch_snap.cost_usd,
                    state=touch_state,
                    force=force_touch,
                )
            except Exception:  # noqa: BLE001
                pass

    def _touch_activity_locked(self, entry: _AgentProgressEntry, now_m: float, now_w: datetime) -> bool:
        entry.last_activity_mono = now_m
        entry.last_activity_at = now_w
        recovered = False
        if entry.stalled and entry.state == "stalled":
            entry.stalled = False
            entry.stall_notified = False
            entry.state = "running"
            recovered = True
        return recovered

    def on_activity(self, agent_id: str) -> None:
        aid = str(agent_id or "").strip()
        if not aid:
            return
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return
            recovered = self._touch_activity_locked(entry, now_m, now_w)
        self._maybe_emit_and_touch(entry, now_m, force_touch=recovered, touch_state="running" if recovered else None)

    def on_tool_start(self, agent_id: str, tool_name: str) -> None:
        aid = str(agent_id or "").strip()
        if not aid:
            return
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return
            recovered = self._touch_activity_locked(entry, now_m, now_w)
            entry.current_tool = str(tool_name or "").strip() or None
            entry.tool_calls += 1
        self._maybe_emit_and_touch(entry, now_m, force_touch=recovered, touch_state="running" if recovered else None)

    def on_tool_finish(self, agent_id: str, tool_name: str | None = None) -> None:
        aid = str(agent_id or "").strip()
        if not aid:
            return
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return
            recovered = self._touch_activity_locked(entry, now_m, now_w)
            if tool_name is None or entry.current_tool == str(tool_name or "").strip():
                entry.current_tool = None
        self._maybe_emit_and_touch(entry, now_m, force_touch=recovered, touch_state="running" if recovered else None)

    def on_usage(
        self,
        agent_id: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        aid = str(agent_id or "").strip()
        if not aid:
            return
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return
            recovered = self._touch_activity_locked(entry, now_m, now_w)
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens > 0:
                entry.input_tokens += input_tokens
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens > 0:
                entry.output_tokens += output_tokens
            if cost_usd is not None:
                try:
                    cval = float(cost_usd)
                    if cval > 0:
                        entry.cost_usd += cval
                except (TypeError, ValueError):
                    pass
        self._maybe_emit_and_touch(entry, now_m, force_touch=recovered, touch_state="running" if recovered else None)

    def finish(self, agent_id: str, *, state: str = "completed") -> AgentProgressSnapshot | None:
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        now_m = self._now_mono()
        now_w = self._now_wall()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return None
            # RT-B-13: preserve 'cancelled' (and stalled=True) set by stall.autoCancel
            # so thread unwind / finally calling finish(..., state='failed') never overwrites it.
            if entry.state == "cancelled":
                entry.current_tool = None
                if entry.finished_mono is None:
                    entry.finished_mono = now_m
            else:
                entry.state = str(state or "completed")
                entry.current_tool = None
                entry.finished_mono = now_m
                entry.last_activity_mono = now_m
                entry.last_activity_at = now_w
                if entry.state in {"completed", "failed"}:
                    entry.stalled = False
            snap = self._build_snapshot_locked(entry, now_m)

        if self._recorder is not None:
            try:
                self._recorder.finish(
                    aid,
                    state=snap.state,
                    tool_calls=snap.tool_calls,
                    input_tokens=snap.input_tokens,
                    output_tokens=snap.output_tokens,
                    cost_usd=snap.cost_usd,
                )
            except Exception:  # noqa: BLE001
                pass
        return snap

    def check_stall(
        self,
        agent_id: str,
        *,
        stall_minutes: float = DEFAULT_STALL_MINUTES,
        auto_cancel: bool = False,
        cancel_fn: Callable[[str], Any] | None = None,
        on_stalled: Callable[[AgentProgressSnapshot], None] | None = None,
    ) -> AgentProgressSnapshot | None:
        """Evaluate inactivity threshold for ``agent_id``; mark stalled and optionally auto-cancel."""
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        threshold_seconds = max(1.0, float(stall_minutes) * 60.0)
        now_m = self._now_mono()
        newly_stalled = False
        should_cancel = False
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                # RT-B-06 fallback: if no in-memory entry exists (e.g., tracker created after start),
                # evaluate stall directly against the persisted agent_runs row.
                run = self._recorder.get(aid) if self._recorder is not None else None
                if run is None:
                    return None
                if run.state not in {"running", "stalled"} or run.last_activity_at is None:
                    return None
                now_w = self._now_wall()
                idle_seconds = max(0.0, (now_w - run.last_activity_at).total_seconds())
                if idle_seconds < threshold_seconds:
                    return None
                elapsed = max(0.0, (now_w - run.created_at).total_seconds())
                was_stalled = run.state == "stalled"
                effective_state = "cancelled" if auto_cancel else "stalled"
                snap = AgentProgressSnapshot(
                    agent_id=aid,
                    state=effective_state,
                    elapsed_seconds=elapsed,
                    last_activity_at=run.last_activity_at,
                    current_tool=None,
                    tool_calls=int(run.tool_calls),
                    input_tokens=int(run.input_tokens),
                    output_tokens=int(run.output_tokens),
                    cost_usd=float(run.cost_usd) if run.cost_usd is not None else 0.0,
                    stalled=True,
                    parent_agent_id=run.parent_agent_id,
                    conversation_id=run.conversation_id,
                )
                newly_stalled = not was_stalled
                should_cancel = bool(auto_cancel)
            else:
                if entry.state in {"running", "stalled"} and entry.finished_mono is None:
                    idle_seconds = max(0.0, now_m - entry.last_activity_mono)
                    if idle_seconds >= threshold_seconds:
                        if not entry.stalled:
                            newly_stalled = True
                        entry.stalled = True
                        entry.state = "stalled"
                        if auto_cancel:
                            should_cancel = True
                snap = self._build_snapshot_locked(entry, now_m)

        if newly_stalled:
            callback = on_stalled or self._on_stalled
            if callback is not None:
                try:
                    callback(snap)
                except Exception:  # noqa: BLE001
                    pass
            if self._recorder is not None and not should_cancel:
                try:
                    self._recorder.touch(
                        aid,
                        tool_calls=snap.tool_calls,
                        input_tokens=snap.input_tokens,
                        output_tokens=snap.output_tokens,
                        cost_usd=snap.cost_usd,
                        state="stalled",
                        force=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        if should_cancel:
            if cancel_fn is not None:
                try:
                    cancel_fn(aid)
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                entry = self._entries.get(aid)
                if entry is not None:
                    entry.state = "cancelled"
                    entry.stalled = True
                    entry.current_tool = None
                    entry.finished_mono = now_m
                    snap = self._build_snapshot_locked(entry, now_m)
            if self._recorder is not None:
                try:
                    self._recorder.finish(
                        aid,
                        state="cancelled",
                        tool_calls=snap.tool_calls,
                        input_tokens=snap.input_tokens,
                        output_tokens=snap.output_tokens,
                        cost_usd=snap.cost_usd,
                    )
                except Exception:  # noqa: BLE001
                    pass
        return snap

    def rebind(
        self,
        *,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        on_progress: Callable[[AgentProgressSnapshot], None] | None = None,
        on_stalled: Callable[[AgentProgressSnapshot], None] | None = None,
        recorder: Any | None = None,
    ) -> None:
        """Rebind session-scoped callbacks/recorder/clocks on a conversation-shared tracker (RT-B-06)."""
        with self._lock:
            if clock is not None:
                self._clock = clock
            if wall_clock is not None:
                self._wall_clock = wall_clock
            if on_progress is not None:
                self._on_progress = on_progress
            if on_stalled is not None:
                self._on_stalled = on_stalled
            if recorder is not None:
                self._recorder = recorder

    def snapshot(self, agent_id: str) -> AgentProgressSnapshot | None:
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        now_m = self._now_mono()
        with self._lock:
            entry = self._entries.get(aid)
            if entry is None:
                return None
            return self._build_snapshot_locked(entry, now_m)


__all__ = [
    "AgentProgressSnapshot",
    "DEFAULT_PROGRESS_INTERVAL_SECONDS",
    "DEFAULT_STALL_MINUTES",
    "ProgressTracker",
    "clear_shared_progress_for_tests",
    "shared_progress_snapshot",
]

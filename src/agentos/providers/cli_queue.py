"""Fair per-conversation CLI process queue with free-memory protection."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
import inspect
import os
from pathlib import Path
import sys
import threading
import time
from typing import Literal

CLI_MAX_PROCESSES_ENV = "ORIN_CLI_MAX_PROCESSES"
CLI_MIN_FREE_MEMORY_ENV = "ORIN_CLI_MIN_FREE_MEMORY_MB"
DEFAULT_MIN_FREE_MEMORY_MB = 1024


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def free_memory_mb() -> int | None:
    """Return available physical RAM in MiB, or None if unavailable on this platform."""
    if os.name == "nt" or sys.platform == "win32":
        try:
            kernel32 = getattr(ctypes, "windll", None)
            if kernel32 is None:
                return None
            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not kernel32.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return None
            return int(stat.ullAvailPhys // (1024 * 1024))
        except Exception:
            return None
    if sys.platform.startswith("linux"):
        try:
            text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) // 1024
        except Exception:
            return None
    return None


def _parse_optional_limit(raw: object) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed >= 1 else None


def _parse_min_free_memory_mb(raw: object, default: int = DEFAULT_MIN_FREE_MEMORY_MB) -> int:
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError:
        return default
    return max(0, parsed)


class CliSlotTicket:
    """Ticket representing one queued or active CLI process slot."""

    def __init__(self, queue: CliSlotQueue, conversation_id: str) -> None:
        self._queue = queue
        self.conversation_id = str(conversation_id or "")
        self._granted = False
        self._released = False
        self._last_wait_reason: Literal["limit", "memory"] | None = None

    def wait(self, timeout: float = 0.0) -> bool:
        return self._queue._wait_for_ticket(self, timeout)

    def position(self) -> int:
        return self._queue._ticket_position(self)

    def wait_reason(self) -> Literal["limit", "memory"] | None:
        return self._queue._ticket_wait_reason(self)

    def release(self) -> None:
        self._queue._release_ticket(self)


class CliSlotQueue:
    """Fair per-conversation CLI slot queue with optional process cap and free-memory guard."""

    def __init__(
        self,
        limit: int | None = None,
        *,
        min_free_memory_mb: int | None = None,
        memory_probe: Callable[[], int | None] = free_memory_mb,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if limit is None and env is not None:
            limit = _parse_optional_limit(env.get(CLI_MAX_PROCESSES_ENV))
        if min_free_memory_mb is None:
            if env is not None and CLI_MIN_FREE_MEMORY_ENV in env:
                min_free_memory_mb = _parse_min_free_memory_mb(env.get(CLI_MIN_FREE_MEMORY_ENV))
            elif env is not None:
                # Explicit env dict without memory key in legacy tests: disable memory probe unless specified
                min_free_memory_mb = 0
            else:
                min_free_memory_mb = DEFAULT_MIN_FREE_MEMORY_MB

        self.limit: int | None = (
            int(limit)
            if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1
            else None
        )
        self.min_free_memory_mb: int = max(0, int(min_free_memory_mb))
        self._memory_probe = memory_probe
        self._cond = threading.Condition()
        self._waiting: list[CliSlotTicket] = []
        self._active_by_conv: dict[str, int] = {}
        self._last_used_seq_by_conv: dict[str, int] = {}
        self._use_seq: int = 0
        self._active_total: int = 0
        self._compat_tickets: list[CliSlotTicket] = []

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> CliSlotQueue:
        source = os.environ if env is None else env
        limit = _parse_optional_limit(source.get(CLI_MAX_PROCESSES_ENV))
        min_mem = _parse_min_free_memory_mb(
            source.get(CLI_MIN_FREE_MEMORY_ENV),
            default=DEFAULT_MIN_FREE_MEMORY_MB,
        )
        return cls(limit=limit, min_free_memory_mb=min_mem)

    def active_count(self) -> int:
        with self._cond:
            return self._active_total

    def enqueue(self, conversation_id: str = "") -> CliSlotTicket:
        ticket = CliSlotTicket(self, str(conversation_id or ""))
        with self._cond:
            self._waiting.append(ticket)
            self._try_grant_locked()
        return ticket

    def acquire(
        self,
        cancel_check: Callable[[], bool] | None = None,
        *,
        conversation_id: str = "",
        on_wait: Callable[..., None] | None = None,
    ) -> bool:
        """Compatibility helper acquiring a ticket and storing it for `.release()`."""
        ticket = acquire_cli_slot(
            conversation_id,
            cancel_check=cancel_check,
            on_wait=on_wait,
            queue=self,
        )
        if ticket is None:
            return False
        with self._cond:
            self._compat_tickets.append(ticket)
        return True

    def release(self) -> None:
        """Compatibility helper releasing the most recently acquired compatibility ticket."""
        ticket: CliSlotTicket | None = None
        with self._cond:
            if self._compat_tickets:
                ticket = self._compat_tickets.pop()
        if ticket is not None:
            ticket.release()

    def _block_reason_locked(self) -> Literal["limit", "memory"] | None:
        if self.limit is not None and self._active_total >= self.limit:
            return "limit"
        if self._active_total > 0 and self.min_free_memory_mb > 0:
            try:
                free_mb = self._memory_probe()
            except Exception:
                free_mb = None
            if free_mb is not None and free_mb < self.min_free_memory_mb:
                return "memory"
        return None

    def _try_grant_locked(self) -> None:
        while self._waiting:
            reason = self._block_reason_locked()
            if reason is not None:
                for waiting_ticket in self._waiting:
                    waiting_ticket._last_wait_reason = reason
                return
            best_idx = 0
            best_conv = self._waiting[0].conversation_id
            best_key = (
                self._active_by_conv.get(best_conv, 0),
                self._last_used_seq_by_conv.get(best_conv, 0),
            )
            for idx in range(1, len(self._waiting)):
                cand_conv = self._waiting[idx].conversation_id
                cand_key = (
                    self._active_by_conv.get(cand_conv, 0),
                    self._last_used_seq_by_conv.get(cand_conv, 0),
                )
                if cand_key < best_key:
                    best_key = cand_key
                    best_idx = idx
            chosen = self._waiting.pop(best_idx)
            chosen._granted = True
            chosen._last_wait_reason = None
            self._active_total += 1
            self._active_by_conv[chosen.conversation_id] = (
                self._active_by_conv.get(chosen.conversation_id, 0) + 1
            )
            if chosen.conversation_id:
                self._use_seq += 1
                self._last_used_seq_by_conv[chosen.conversation_id] = self._use_seq
            self._cond.notify_all()

    def _wait_for_ticket(self, ticket: CliSlotTicket, timeout: float) -> bool:
        wait_total = max(0.0, float(timeout))
        deadline = time.monotonic() + wait_total
        with self._cond:
            self._try_grant_locked()
            if ticket._granted:
                return True
            if ticket._released or wait_total <= 0.0:
                return False
            while not ticket._granted and not ticket._released:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._try_grant_locked()
                    return ticket._granted
                step = min(remaining, 0.05)
                self._cond.wait(timeout=step)
                self._try_grant_locked()
            return ticket._granted

    def _ticket_position(self, ticket: CliSlotTicket) -> int:
        with self._cond:
            if ticket._granted or ticket._released:
                return 0
            if ticket not in self._waiting:
                return 0
            sim_active = dict(self._active_by_conv)
            sim_last_used = dict(self._last_used_seq_by_conv)
            sim_seq = self._use_seq
            remaining = list(self._waiting)
            step = 0
            while remaining:
                step += 1
                best_idx = 0
                best_conv = remaining[0].conversation_id
                best_key = (
                    sim_active.get(best_conv, 0),
                    sim_last_used.get(best_conv, 0),
                )
                for idx in range(1, len(remaining)):
                    cand_conv = remaining[idx].conversation_id
                    cand_key = (
                        sim_active.get(cand_conv, 0),
                        sim_last_used.get(cand_conv, 0),
                    )
                    if cand_key < best_key:
                        best_key = cand_key
                        best_idx = idx
                picked = remaining.pop(best_idx)
                if picked is ticket:
                    return step
                sim_active[picked.conversation_id] = (
                    sim_active.get(picked.conversation_id, 0) + 1
                )
                if picked.conversation_id:
                    sim_seq += 1
                    sim_last_used[picked.conversation_id] = sim_seq
            return 0

    def _ticket_wait_reason(self, ticket: CliSlotTicket) -> Literal["limit", "memory"] | None:
        with self._cond:
            if ticket._granted or ticket._released:
                return None
            reason = self._block_reason_locked()
            if reason is not None:
                ticket._last_wait_reason = reason
                return reason
            return ticket._last_wait_reason

    def _release_ticket(self, ticket: CliSlotTicket) -> None:
        with self._cond:
            if ticket._released:
                return
            ticket._released = True
            if ticket._granted:
                ticket._granted = False
                self._active_total = max(0, self._active_total - 1)
                prev = self._active_by_conv.get(ticket.conversation_id, 0)
                if prev <= 1:
                    self._active_by_conv.pop(ticket.conversation_id, None)
                else:
                    self._active_by_conv[ticket.conversation_id] = prev - 1
                if ticket.conversation_id:
                    self._use_seq += 1
                    self._last_used_seq_by_conv[ticket.conversation_id] = self._use_seq
            else:
                try:
                    self._waiting.remove(ticket)
                except ValueError:
                    pass
            self._try_grant_locked()
            self._cond.notify_all()


_QUEUE_LOCK = threading.Lock()
_GLOBAL_QUEUE: CliSlotQueue | None = None


def cli_slot_queue() -> CliSlotQueue:
    """Return the process-wide `CliSlotQueue` singleton."""
    global _GLOBAL_QUEUE
    with _QUEUE_LOCK:
        if _GLOBAL_QUEUE is None:
            _GLOBAL_QUEUE = CliSlotQueue.from_env()
        return _GLOBAL_QUEUE


def set_cli_slot_queue(queue: CliSlotQueue | None) -> None:
    """Override or reset the process-wide `CliSlotQueue` singleton (for tests)."""
    global _GLOBAL_QUEUE
    with _QUEUE_LOCK:
        _GLOBAL_QUEUE = queue


def _invoke_on_wait(
    on_wait: Callable[..., None],
    position: int,
    limit: int | None,
    reason: Literal["limit", "memory"] | None,
) -> None:
    try:
        sig = inspect.signature(on_wait)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL
            for p in sig.parameters.values()
        )
        if has_varargs or len(params) >= 3:
            on_wait(position, limit, reason)
            return
        if "reason" in sig.parameters:
            on_wait(position, limit, reason=reason)
            return
    except (ValueError, TypeError):
        pass
    on_wait(position, limit)


def acquire_cli_slot(
    conversation_id: str = "",
    *,
    cancel_check: Callable[[], bool] | None = None,
    on_wait: Callable[..., None] | None = None,
    poll_seconds: float = 0.05,
    queue: CliSlotQueue | None = None,
) -> CliSlotTicket | None:
    """Block until a CLI slot is granted for `conversation_id`, or return None if cancelled."""
    if cancel_check is not None and cancel_check():
        return None
    q = queue if queue is not None else cli_slot_queue()
    ticket = q.enqueue(conversation_id)
    if ticket.wait(0.0):
        return ticket

    last_wait_state: tuple[int, str | None] | None = None
    step = max(0.005, float(poll_seconds))
    try:
        while True:
            if cancel_check is not None and cancel_check():
                ticket.release()
                return None
            pos = ticket.position()
            reason = ticket.wait_reason()
            if pos > 0 and (pos, reason) != last_wait_state:
                last_wait_state = (pos, reason)
                if on_wait is not None:
                    _invoke_on_wait(on_wait, pos, q.limit, reason)
            if ticket.wait(step):
                if cancel_check is not None and cancel_check():
                    ticket.release()
                    return None
                return ticket
    except BaseException:
        ticket.release()
        raise


__all__ = [
    "CLI_MAX_PROCESSES_ENV",
    "CLI_MIN_FREE_MEMORY_ENV",
    "DEFAULT_MIN_FREE_MEMORY_MB",
    "CliSlotQueue",
    "CliSlotTicket",
    "acquire_cli_slot",
    "cli_slot_queue",
    "free_memory_mb",
    "set_cli_slot_queue",
]

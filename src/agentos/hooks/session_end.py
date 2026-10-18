"""SessionEnd: run when the sessions a process served end (worker shutdown today; /clear in W7)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleBus

logger = logging.getLogger(__name__)
MAX_SESSIONS = 500
_REGISTRY: dict[str, tuple[Path, str, bool]] = {}
_LOCK = threading.Lock()


def clear_registry() -> None:
    """Forget every registered session without running hooks (tests)."""
    with _LOCK:
        _REGISTRY.clear()


def register_session(conversation_id: str, *, workspace_root: Path | str, user_id: str, trusted: bool) -> None:
    if not conversation_id:
        return
    with _LOCK:
        if conversation_id in _REGISTRY or len(_REGISTRY) < MAX_SESSIONS:
            _REGISTRY[conversation_id] = (Path(workspace_root), str(user_id), bool(trusted))


def emit_session_end(conversation_id: str, *, workspace_root: Path | str, trusted: bool, reason: str) -> bool:
    """Run the SessionEnd hooks of one session (command and http only). True when a hook was run."""
    from agentos.settings import load_effective_settings

    from .config import load_hook_specs
    from .dispatcher import HookDispatcher
    from .runners import CommandHookRunner, HttpHookRunner

    try:
        settings = load_effective_settings(workspace_root, trusted=trusted)
        specs, _errors = load_hook_specs(settings)
        specs = tuple(spec for spec in specs if spec.event is HookEvent.SESSION_END and spec.type in {"command", "http"})
        if not specs:
            return False
        dispatcher = HookDispatcher(specs, command_runner=CommandHookRunner(Path(workspace_root)), http_runner=HttpHookRunner())
        bus = LifecycleBus()
        bus.bind(session_id=conversation_id, cwd=str(workspace_root))
        bus.subscribe(dispatcher)
        try:
            bus.emit(HookEvent.SESSION_END, reason=reason)
        finally:
            dispatcher.close()
        return True
    except Exception:  # noqa: BLE001 - shutting down never fails because of a hook
        logger.exception("SessionEnd hooks failed for %s", conversation_id)
        return False


def end_sessions(reason: str = "other", *, timeout_seconds: float = 5.0) -> int:
    """End every registered session within ``timeout_seconds``; returns how many ran a hook."""
    with _LOCK:
        items = list(_REGISTRY.items())
        _REGISTRY.clear()
    if not items:
        return 0
    count = [0]

    def work() -> None:
        for conversation_id, (root, _user, trusted) in items:
            if emit_session_end(conversation_id, workspace_root=root, trusted=trusted, reason=reason):
                count[0] += 1

    thread = threading.Thread(target=work, name="orin-session-end", daemon=True)
    thread.start()
    thread.join(max(0.0, float(timeout_seconds)))
    return count[0]


__all__ = ["clear_registry", "emit_session_end", "end_sessions", "register_session"]

"""The single hook that runs after a turn ends (WS-C7, decision D8).

``workers/chat.py`` calls ``run_after_turn`` once per turn, right after the queued input is promoted. Handlers
(``/goal`` continuation in WS-C9, runs requested by ``RunPipeline`` in WS-C10) register here at import time of
their own module. A handler that raises is logged and does not stop the others, nor the worker.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("agentos.pipelines.after_turn")

Handler = Callable[[Any, Mapping[str, Any], str], None]
_COMPLETED = frozenset({"completed", "completed_with_caveats"})
_LOCK = threading.Lock()
_HANDLERS: list[Handler] = []


def register(handler: Handler) -> None:
    """Add a handler ``handler(store, turn, result_state)``; registering the same function twice keeps one copy."""
    with _LOCK:
        if handler not in _HANDLERS:
            _HANDLERS.append(handler)


def clear_handlers() -> None:
    with _LOCK:
        _HANDLERS.clear()


def run_after_turn(store: Any, turn: Mapping[str, Any], result_state: str) -> int:
    """Call every handler for a turn that completed; return how many ran without error. Nothing for failed or cancelled turns."""
    if result_state not in _COMPLETED:
        return 0
    with _LOCK:
        handlers = list(_HANDLERS)
    ran = 0
    for handler in handlers:
        try:
            handler(store, turn, result_state)
            ran += 1
        except Exception:  # noqa: BLE001 - one handler must not stop the others or the worker
            logger.exception("after-turn handler %s failed for turn %s", getattr(handler, "__name__", handler), turn.get("turn_id"))
    return ran


__all__ = ["Handler", "clear_handlers", "register", "run_after_turn"]

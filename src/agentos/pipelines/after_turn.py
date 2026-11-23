"""The single hook that runs after a turn ends (WS-C7, decision D8; the ``/goal`` controller registers in WS-C9).

``workers/chat.py`` calls ``run_after_turn`` once per turn, right after the queued input is promoted. Handlers
(``/goal`` continuation, runs requested by ``RunPipeline`` in WS-C10) register here. A handler that raises is logged
and does not stop the others, nor the worker.

By default a handler runs only for a turn that completed. A handler registered with ``states=ALL_STATES`` also hears
about failed, cancelled, waiting and reconciling turns (``/goal`` needs them to park its objective). A handler
registered with ``with_context=True`` receives an ``AfterTurnContext`` as a fourth argument: the few worker services
a model call needs, so the handler never builds a worker itself.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger("agentos.pipelines.after_turn")

Handler = Callable[..., None]
COMPLETED_STATES: Final = frozenset({"completed", "completed_with_caveats"})
ALL_STATES: Final = frozenset(COMPLETED_STATES | {"failed", "cancelled", "waiting_user", "reconciliation_required"})
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AfterTurnContext:
    """Worker services for handlers that call a model.

    ``provider_transport(turn_like)`` is ``ChatWorker._provider_transport`` (``turn_like`` has ``user_id``,
    ``provider``, ``model_id``); ``favorite_models(turn)`` is the worker's favorites-by-provider catalog.
    """

    provider_transport: Callable[[Mapping[str, Any]], Any] | None = None
    favorite_models: Callable[[Mapping[str, Any]], Mapping[str, Sequence[str]]] | None = None


@dataclass(frozen=True, slots=True)
class _Entry:
    handler: Handler
    states: frozenset[str]
    with_context: bool


_HANDLERS: list[_Entry] = []


def register(handler: Handler, *, states: frozenset[str] | None = None, with_context: bool = False) -> None:
    """Add a handler ``handler(store, turn, result_state[, context])``; registering the same function twice keeps one copy."""
    with _LOCK:
        if all(entry.handler is not handler for entry in _HANDLERS):
            _HANDLERS.append(_Entry(handler, frozenset(states) if states is not None else COMPLETED_STATES, with_context))


def clear_handlers() -> None:
    with _LOCK:
        _HANDLERS.clear()


def run_after_turn(
    store: Any, turn: Mapping[str, Any], result_state: str, *, context: AfterTurnContext | None = None
) -> int:
    """Call every handler that listens to ``result_state``; return how many ran without error."""
    with _LOCK:
        entries = [entry for entry in _HANDLERS if result_state in entry.states]
    ran = 0
    for entry in entries:
        try:
            if entry.with_context:
                entry.handler(store, turn, result_state, context or AfterTurnContext())
            else:
                entry.handler(store, turn, result_state)
            ran += 1
        except Exception:  # noqa: BLE001 - one handler must not stop the others or the worker
            logger.exception(
                "after-turn handler %s failed for turn %s", getattr(entry.handler, "__name__", entry.handler), turn.get("turn_id")
            )
    return ran


def install_default_handlers() -> None:
    """Register the package's own handlers (the ``/goal`` controller). Idempotent."""
    from .goal import after_turn_handler

    register(after_turn_handler, states=ALL_STATES, with_context=True)


install_default_handlers()

__all__ = [
    "ALL_STATES",
    "COMPLETED_STATES",
    "AfterTurnContext",
    "Handler",
    "clear_handlers",
    "install_default_handlers",
    "register",
    "run_after_turn",
]

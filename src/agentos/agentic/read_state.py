"""Per-conversation read-file state registry and turn-boundary preparation."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from dataclasses import replace
import hashlib
import threading

from agentos.agentic.tools._shared import EXTERNAL_CHANGE_HASH_LIMIT_BYTES, ReadFileRecord
from agentos.agentic.workspace import ConversationWorkspace


MAX_TRACKED_CONVERSATIONS = 64
MAX_CARRIED_READS = 200


class ConversationReadStates:
    """Thread-safe LRU registry of per-conversation read states."""

    def __init__(self, max_conversations: int = MAX_TRACKED_CONVERSATIONS) -> None:
        self._max_conversations = max(1, int(max_conversations))
        self._lock = threading.Lock()
        self._states: OrderedDict[tuple[str, str], dict[str, ReadFileRecord]] = OrderedDict()

    def state_for(self, user_id: str, conversation_id: str) -> dict[str, ReadFileRecord]:
        key = (str(user_id), str(conversation_id))
        with self._lock:
            existing = self._states.get(key)
            if existing is not None:
                self._states.move_to_end(key)
                return existing
            while len(self._states) >= self._max_conversations:
                self._states.popitem(last=False)
            state: dict[str, ReadFileRecord] = {}
            self._states[key] = state
            return state


def copy_read_state(state: Mapping[str, object]) -> dict[str, object]:
    """Return a shallow copy of ``state`` with each ``ReadFileRecord`` cloned via ``dataclasses.replace``."""
    return {
        key: replace(value) if isinstance(value, ReadFileRecord) else value
        for key, value in state.items()
    }


def prepare_carried_state(state: MutableMapping[str, object], workspace: ConversationWorkspace) -> int:
    """Prune stale, oversized, missing, or excess entries and clear ``explicit_read`` on survivors."""
    items = list(state.items())
    valid_records: list[tuple[str, ReadFileRecord]] = []
    removed = 0
    for key, value in items:
        if isinstance(value, ReadFileRecord):
            valid_records.append((key, value))
        else:
            state.pop(key, None)
            removed += 1

    valid_records.sort(key=lambda item: item[1].read_seq, reverse=True)
    for key, _ in valid_records[MAX_CARRIED_READS:]:
        state.pop(key, None)
        removed += 1

    for key, record in valid_records[:MAX_CARRIED_READS]:
        try:
            target = workspace.resolve(key)
            if not target.is_file():
                state.pop(key, None)
                removed += 1
                continue
            stat = target.stat()
            if stat.st_size > EXTERNAL_CHANGE_HASH_LIMIT_BYTES:
                state.pop(key, None)
                removed += 1
                continue
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except Exception:
            state.pop(key, None)
            removed += 1
            continue
        if digest != record.sha256:
            state.pop(key, None)
            removed += 1
            continue
        record.mtime_ns = stat.st_mtime_ns
        record.explicit_read = False

    return removed

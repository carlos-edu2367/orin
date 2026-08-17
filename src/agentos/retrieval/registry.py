"""Keeps one retrieval bundle alive per workspace, across turns.

A bundle built fresh every turn would open a new sqlite3 connection, a new
embedder HTTP client, and start a new background indexing thread every single
time — and nothing would ever tear the previous one down, since a turn has no
natural end-of-conversation hook. This hands out the same bundle for repeated
turns against one workspace, and only tears it down once it has sat idle past
``idle_seconds`` or once the number of live bundles exceeds ``max_bundles``
(evicting the least-recently-used one first) — the same shape already used for
per-conversation browsers in ``agentic.browser_tools.ConversationBrowserRegistry``,
except keyed by workspace rather than by conversation, because one project's
index is meant to be shared across every conversation about that project.

Eviction is opportunistic: it runs on every ``acquire`` call rather than on a
background timer, which is enough for a local, single-user app and needs no
extra thread.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Callable

from .bundle import RetrievalBundle


DEFAULT_IDLE_SECONDS = 1_800.0
DEFAULT_MAX_BUNDLES = 8


class _RegistryEntry:
    __slots__ = ("bundle", "local_root", "last_used")

    def __init__(self, bundle: RetrievalBundle, local_root: str | None, last_used: float) -> None:
        self.bundle = bundle
        self.local_root = local_root
        self.last_used = last_used


def _close_quietly(bundle: RetrievalBundle | None) -> None:
    if bundle is None:
        return
    try:
        bundle.close()
    except Exception:
        pass


class RetrievalRegistry:
    def __init__(
        self,
        *,
        factory: Callable[[str, str | None], RetrievalBundle | None],
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        max_bundles: int = DEFAULT_MAX_BUNDLES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._idle_seconds = idle_seconds
        self._max_bundles = max_bundles
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[str, _RegistryEntry] = {}

    def acquire(self, *, workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        with self._lock:
            self._evict_idle_locked()
            entry = self._entries.get(workspace_id)
            if entry is not None:
                if entry.local_root == local_root:
                    entry.last_used = self._clock()
                    return entry.bundle
                # The bound folder changed under this workspace id (a rebind):
                # the cached index no longer matches what it should be indexing.
                self._entries.pop(workspace_id)
                _close_quietly(entry.bundle)
            if not workspace_id:
                # Nothing to key a bundle by: behave exactly like calling the
                # factory directly, uncached.
                return self._factory(workspace_id, local_root)
            bundle = self._factory(workspace_id, local_root)
            if bundle is not None:
                self._evict_lru_locked_to_make_room()
                self._entries[workspace_id] = _RegistryEntry(bundle, local_root, self._clock())
            return bundle

    def discard(self, workspace_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(workspace_id, None)
        _close_quietly(entry.bundle if entry is not None else None)

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            _close_quietly(entry.bundle)

    def _evict_idle_locked(self) -> None:
        now = self._clock()
        stale = [key for key, entry in self._entries.items() if now - entry.last_used > self._idle_seconds]
        for key in stale:
            _close_quietly(self._entries.pop(key).bundle)

    def _evict_lru_locked_to_make_room(self) -> None:
        while len(self._entries) >= self._max_bundles:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].last_used)
            _close_quietly(self._entries.pop(oldest_key).bundle)


__all__ = ["DEFAULT_IDLE_SECONDS", "DEFAULT_MAX_BUNDLES", "RetrievalRegistry"]

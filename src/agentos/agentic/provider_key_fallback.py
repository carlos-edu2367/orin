"""Rotates through a user's ordered provider API keys on a key-shaped failure.

Wraps a single ``HTTPProviderStreamTransport``-shaped ``.stream()`` call so
the seam ``chat.py`` already uses to hand the turn's provider to
``AgenticTurnRuntime`` (its ``provider_factory`` callback, built once per
turn) needs no changes at all: this class satisfies the same
``.stream(request) -> Iterator[NormalizedStreamItem]`` contract, and only
rotates keys when nothing has been yielded yet in the current attempt --
matching docs/superpowers/specs/2026-08-18-multi-api-key-provider-fallback-design.md
decision #4 (fallback only before the first token) without either transport
needing to know why.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, Mapping, Protocol

from .provider_stream import (
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
    _classify_stream_error_item,
)


class ProviderKeyPool(Protocol):
    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()) -> Any: ...
    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None: ...
    def mark_exhausted(self, key_id: int) -> None: ...
    def mark_active(self, key_id: int) -> None: ...


class MultiKeyProviderStreamTransport:
    def __init__(
        self,
        *,
        key_pool: object,
        user_id: str,
        provider: str,
        cooldown_seconds: int,
        transport_factory: Callable[[str], object],
        model: str = "",
        base_url: str = "",
    ) -> None:
        self._key_pool = key_pool
        self._user_id = user_id
        self._provider = provider
        self._cooldown_seconds = cooldown_seconds
        self._transport_factory = transport_factory
        self._transport: object | None = None
        self.provider = provider
        self.model = model
        self.base_url = base_url

    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        tried: set[int] = set()
        credential = self._key_pool.next_available_key(self._user_id, self._provider, exclude=frozenset(tried))
        if credential is None:
            raise ValueError("no api key configured for provider")
        while True:
            tried.add(credential.id)
            transport = self._transport_factory(credential.plaintext)
            self._transport = transport
            yielded = False
            try:
                for item in transport.stream(request):
                    if not yielded and item.kind == StreamKind.ERROR:
                        rejected = _classify_stream_error_item(item)
                        if rejected is not None:
                            raise rejected
                    yielded = True
                    yield item
                # A key that just served a full turn is proof it works now,
                # even if a past failure had it sitting in cooldown -- without
                # this, next_available_key's own timestamp check would still
                # let it be picked again once cooldown_until passes, but the
                # DB row (and the UI reading it) would claim "cooldown"
                # forever for a key quietly carrying every request.
                self._key_pool.mark_active(credential.id)
                return
            except ProviderKeyRejected as rejected:
                if yielded:
                    raise
                if rejected.exhausted and hasattr(self._key_pool, "mark_exhausted"):
                    self._key_pool.mark_exhausted(credential.id)
                else:
                    cooldown = (
                        max(1, min(int(rejected.retry_after_seconds), 3600))
                        if rejected.retry_after_seconds is not None
                        else self._cooldown_seconds
                    )
                    self._key_pool.mark_cooldown(credential.id, cooldown)
                next_credential = self._key_pool.next_available_key(self._user_id, self._provider, exclude=frozenset(tried))
                if next_credential is None:
                    raise
                if hasattr(transport, "close"):
                    try:
                        transport.close()
                    except Exception:
                        pass
                credential = next_credential

    def close(self) -> None:
        transport = self._transport
        if transport is not None and hasattr(transport, "close"):
            transport.close()


__all__ = ["MultiKeyProviderStreamTransport", "ProviderKeyPool"]

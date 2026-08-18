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

from typing import Callable, Iterator, Mapping

from .provider_stream import NormalizedStreamItem, ProviderKeyRejected


class MultiKeyProviderStreamTransport:
    def __init__(
        self,
        *,
        key_pool: object,
        user_id: str,
        provider: str,
        cooldown_seconds: int,
        transport_factory: Callable[[str], object],
    ) -> None:
        self._key_pool = key_pool
        self._user_id = user_id
        self._provider = provider
        self._cooldown_seconds = cooldown_seconds
        self._transport_factory = transport_factory
        self._transport: object | None = None

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
                    yielded = True
                    yield item
                return
            except ProviderKeyRejected:
                if yielded:
                    raise
                self._key_pool.mark_cooldown(credential.id, self._cooldown_seconds)
                next_credential = self._key_pool.next_available_key(self._user_id, self._provider, exclude=frozenset(tried))
                if next_credential is None:
                    raise
                credential = next_credential

    def close(self) -> None:
        transport = self._transport
        if transport is not None and hasattr(transport, "close"):
            transport.close()


__all__ = ["MultiKeyProviderStreamTransport"]

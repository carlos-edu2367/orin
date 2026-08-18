from __future__ import annotations

import pytest

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import NormalizedStreamItem, ProviderKeyRejected, StreamKind
from agentos.persistence.postgres.provider_api_keys import ProviderApiKeyCredential


class _FakeKeyPool:
    def __init__(self, credentials: list[ProviderApiKeyCredential]) -> None:
        self._order = list(credentials)
        self.cooldowns: list[tuple[int, int]] = []

    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()):
        for credential in self._order:
            if credential.id not in exclude:
                return credential
        return None

    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
        self.cooldowns.append((key_id, cooldown_seconds))


class _FakeTransport:
    def __init__(self, *, items=(), error: Exception | None = None, fail_after: int = 0) -> None:
        self._items = list(items)
        self._error = error
        self._fail_after = fail_after
        self.closed = False

    def stream(self, request):
        for index, item in enumerate(self._items):
            if self._error is not None and index == self._fail_after:
                raise self._error
            yield item
        if self._error is not None and self._fail_after >= len(self._items):
            raise self._error

    def close(self) -> None:
        self.closed = True


def _text(value: str) -> NormalizedStreamItem:
    return NormalizedStreamItem(StreamKind.TEXT, 1, text=value)


def test_the_principal_key_is_used_when_it_succeeds() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-principal")
    pool = _FakeKeyPool([key])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        return _FakeTransport(items=[_text("hi")])

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=60, transport_factory=factory)

    items = list(transport.stream({}))

    assert built == ["sk-principal"]
    assert items == [_text("hi")]
    assert pool.cooldowns == []


def test_a_key_rejected_before_any_output_rotates_to_the_next_key() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="sk-first")
    second = ProviderApiKeyCredential(id=2, plaintext="sk-second")
    pool = _FakeKeyPool([first, second])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        if plaintext == "sk-first":
            return _FakeTransport(items=[], error=ProviderKeyRejected("nope"), fail_after=0)
        return _FakeTransport(items=[_text("hi")])

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=45, transport_factory=factory)

    items = list(transport.stream({}))

    assert built == ["sk-first", "sk-second"]
    assert items == [_text("hi")]
    assert pool.cooldowns == [(1, 45)]


def test_a_key_rejected_after_output_already_started_does_not_rotate() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="sk-first")
    second = ProviderApiKeyCredential(id=2, plaintext="sk-second")
    pool = _FakeKeyPool([first, second])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        return _FakeTransport(items=[_text("partial")], error=ProviderKeyRejected("dropped"), fail_after=1)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=45, transport_factory=factory)

    stream = transport.stream({})
    assert next(stream) == _text("partial")
    with pytest.raises(ProviderKeyRejected):
        next(stream)

    assert built == ["sk-first"]
    assert pool.cooldowns == []


def test_every_key_rejected_before_output_re_raises_after_the_last_one() -> None:
    only = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([only])

    def factory(plaintext: str):
        return _FakeTransport(items=[], error=ProviderKeyRejected("nope"), fail_after=0)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)

    with pytest.raises(ProviderKeyRejected):
        list(transport.stream({}))

    assert pool.cooldowns == [(1, 30)]


def test_a_non_key_error_propagates_without_rotating() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([key])

    def factory(plaintext: str):
        return _FakeTransport(items=[], error=ValueError("bad request"), fail_after=0)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)

    with pytest.raises(ValueError):
        list(transport.stream({}))

    assert pool.cooldowns == []


def test_no_configured_keys_raises_immediately() -> None:
    pool = _FakeKeyPool([])
    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=lambda plaintext: _FakeTransport())

    with pytest.raises(ValueError):
        list(transport.stream({}))


def test_close_closes_the_last_active_inner_transport() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([key])
    built: list[_FakeTransport] = []

    def factory(plaintext: str):
        inner = _FakeTransport(items=[_text("hi")])
        built.append(inner)
        return inner

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)
    list(transport.stream({}))

    transport.close()

    assert built[0].closed is True

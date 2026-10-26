from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, ProviderKeyRejected, StreamKind
from agentos.persistence.postgres.provider_api_keys import ProviderApiKeyCredential


class _FakeKeyPool:
    def __init__(self, credentials: list[ProviderApiKeyCredential]) -> None:
        self._order = list(credentials)
        self.cooldowns: list[tuple[int, int]] = []
        self.exhausted: list[int] = []
        self.activated: list[int] = []

    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()):
        for credential in self._order:
            if credential.id not in exclude:
                return credential
        return None

    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
        self.cooldowns.append((key_id, cooldown_seconds))

    def mark_exhausted(self, key_id: int) -> None:
        self.exhausted.append(key_id)

    def mark_active(self, key_id: int) -> None:
        self.activated.append(key_id)


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
    assert pool.activated == [1]


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
    assert pool.activated == [2]


def test_a_key_recovering_from_cooldown_is_marked_active_again_on_success() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([key])

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=60, transport_factory=lambda plaintext: _FakeTransport(items=[_text("hi")]))
    list(transport.stream({}))

    assert pool.activated == [1]


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


def test_exhausted_key_rejection_calls_mark_exhausted_instead_of_cooldown_and_rotates() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="nvapi-free-1")
    second = ProviderApiKeyCredential(id=2, plaintext="nvapi-paid-2")
    pool = _FakeKeyPool([first, second])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        if plaintext == "nvapi-free-1":
            return _FakeTransport(items=[], error=ProviderKeyRejected("out of credits", status_code=402, exhausted=True), fail_after=0)
        return _FakeTransport(items=[_text("ok from key 2")])

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool, user_id="u", provider="nvidia", cooldown_seconds=60, transport_factory=factory
    )

    items = list(transport.stream({}))

    assert built == ["nvapi-free-1", "nvapi-paid-2"]
    assert items == [_text("ok from key 2")]
    assert pool.exhausted == [1]
    assert pool.cooldowns == []
    assert pool.activated == [2]


def test_rate_limit_rejection_uses_retry_after_bounded_to_1_3600() -> None:
    k1 = ProviderApiKeyCredential(id=1, plaintext="nvapi-1")
    k2 = ProviderApiKeyCredential(id=2, plaintext="nvapi-2")
    k3 = ProviderApiKeyCredential(id=3, plaintext="nvapi-3")
    pool = _FakeKeyPool([k1, k2, k3])

    def factory(plaintext: str):
        if plaintext == "nvapi-1":
            return _FakeTransport(items=[], error=ProviderKeyRejected("rate limit", status_code=429, exhausted=False, retry_after_seconds=120), fail_after=0)
        if plaintext == "nvapi-2":
            return _FakeTransport(items=[], error=ProviderKeyRejected("huge retry after", status_code=429, exhausted=False, retry_after_seconds=99999), fail_after=0)
        return _FakeTransport(items=[_text("ok")])

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool, user_id="u", provider="nvidia", cooldown_seconds=60, transport_factory=factory
    )

    items = list(transport.stream({}))

    assert items == [_text("ok")]
    assert pool.cooldowns == [(1, 120), (2, 3600)]
    assert pool.exhausted == []
    assert pool.activated == [3]


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (402, {"error": {"message": "Payment Required"}}),
        (401, {"error": {"message": "Invalid authentication"}}),
        (403, {"error": {"message": "Forbidden"}}),
        (429, {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}),
        (400, {"error": {"message": "Your credit balance is too low to access the API"}}),
        (422, {"error": {"message": "Account is out of credits"}}),
    ],
)
def test_http_transport_raises_exhausted_provider_key_rejected_on_auth_or_quota_errors(
    status_code: int, body: dict[str, object]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    transport = HTTPProviderStreamTransport(
        provider="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="nvapi-test",
        model="meta/llama-3.1-70b-instruct",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ProviderKeyRejected) as exc_info:
        list(transport.stream({"messages": [{"role": "user", "content": "hi"}], "tools": []}))

    assert exc_info.value.status_code == status_code
    assert exc_info.value.exhausted is True


def test_http_transport_raises_non_exhausted_provider_key_rejected_on_429_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "15"},
            json={"error": {"message": "Too many requests per minute"}},
            request=request,
        )

    transport = HTTPProviderStreamTransport(
        provider="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="nvapi-test",
        model="meta/llama-3.1-70b-instruct",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ProviderKeyRejected) as exc_info:
        list(transport.stream({"messages": [{"role": "user", "content": "hi"}], "tools": []}))

    assert exc_info.value.status_code == 429
    assert exc_info.value.exhausted is False
    assert exc_info.value.retry_after_seconds == 15


def test_rotates_when_first_sse_event_before_content_is_quota_or_rate_limit_error() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="nvapi-exhausted")
    second = ProviderApiKeyCredential(id=2, plaintext="nvapi-rate-limited")
    third = ProviderApiKeyCredential(id=3, plaintext="nvapi-good")
    pool = _FakeKeyPool([first, second, third])

    def factory(plaintext: str):
        if plaintext == "nvapi-exhausted":
            def handler_exhausted(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    text='data: {"error": {"code": "insufficient_quota", "message": "Out of credits"}}\n\n',
                    request=request,
                )
            return HTTPProviderStreamTransport(
                provider="nvidia",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=plaintext,
                model="meta/llama-3.1-70b-instruct",
                client=httpx.Client(transport=httpx.MockTransport(handler_exhausted)),
            )
        if plaintext == "nvapi-rate-limited":
            return _FakeTransport(
                items=[NormalizedStreamItem(StreamKind.ERROR, 1, error_code="RATE_LIMITED", text="Too many requests")]
            )
        return _FakeTransport(items=[_text("streamed from key 3")])

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool, user_id="u", provider="nvidia", cooldown_seconds=45, transport_factory=factory
    )

    items = list(transport.stream({"messages": [{"role": "user", "content": "hi"}], "tools": []}))

    assert [item.text for item in items] == ["streamed from key 3"]
    assert pool.exhausted == [1]
    assert pool.cooldowns == [(2, 45)]
    assert pool.activated == [3]



def test_rejected_inner_transports_are_closed_when_rotating_to_next_key() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="nvapi-1")
    second = ProviderApiKeyCredential(id=2, plaintext="nvapi-2")
    pool = _FakeKeyPool([first, second])
    built: list[_FakeTransport] = []

    def factory(plaintext: str):
        if plaintext == "nvapi-1":
            inner = _FakeTransport(
                items=[], error=ProviderKeyRejected("exhausted", status_code=402, exhausted=True), fail_after=0
            )
        else:
            inner = _FakeTransport(items=[_text("ok")])
        built.append(inner)
        return inner

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool, user_id="u", provider="nvidia", cooldown_seconds=60, transport_factory=factory
    )
    list(transport.stream({}))

    assert built[0].closed is True
    assert built[1].closed is False
    transport.close()
    assert built[1].closed is True


def test_non_key_sse_error_on_first_event_does_not_rotate_and_yields_error() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="nvapi-1")
    second = ProviderApiKeyCredential(id=2, plaintext="nvapi-2")
    pool = _FakeKeyPool([first, second])
    err_item = NormalizedStreamItem(StreamKind.ERROR, 1, error_code="context_length_exceeded", text="Prompt too long")

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool,
        user_id="u",
        provider="nvidia",
        cooldown_seconds=60,
        transport_factory=lambda plaintext: _FakeTransport(items=[err_item]),
    )

    items = list(transport.stream({}))

    assert items == [err_item]
    assert pool.exhausted == []
    assert pool.cooldowns == []
    assert pool.activated == [1]


def test_quota_sse_error_after_text_already_yielded_does_not_rotate() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="nvapi-1")
    second = ProviderApiKeyCredential(id=2, plaintext="nvapi-2")
    pool = _FakeKeyPool([first, second])
    err_item = NormalizedStreamItem(StreamKind.ERROR, 2, error_code="QUOTA_EXCEEDED", text="insufficient_quota")

    transport = MultiKeyProviderStreamTransport(
        key_pool=pool,
        user_id="u",
        provider="nvidia",
        cooldown_seconds=60,
        transport_factory=lambda plaintext: _FakeTransport(items=[_text("partial"), err_item]),
    )

    items = list(transport.stream({}))

    assert items == [_text("partial"), err_item]
    assert pool.exhausted == []
    assert pool.cooldowns == []


def test_nvidia_ecosystem_effort_http_adapter_cli_and_production_settings() -> None:
    from agentos.agentic.effort import apply_effort, reasoning_supported
    from agentos.bootstrap.production import ProductionSettings
    from agentos.cli_code.selection import KNOWN_PROVIDERS
    from agentos.providers.http import NvidiaHTTPAdapter
    from agentos.providers.models import ProviderErrorCategory

    assert "nvidia" in KNOWN_PROVIDERS
    assert reasoning_supported("nvidia", "deepseek-ai/deepseek-r1", ["reasoning"]) is True
    payload = apply_effort("nvidia", {"model": "deepseek-ai/deepseek-r1"}, "high")
    assert payload.get("reasoning_effort") == "high"

    assert NvidiaHTTPAdapter.provider_kind == "nvidia"
    assert NvidiaHTTPAdapter._category_for_status(402) == ProviderErrorCategory.QUOTA_EXCEEDED
    assert "NVIDIA_API_KEY" in ProductionSettings.model_fields
    assert "NVIDIA_ENABLED" in ProductionSettings.model_fields
    assert "NVIDIA_MODEL" in ProductionSettings.model_fields

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, ProviderKeyRejected
from agentos.agentic.retry import backoff_delay, is_retryable, parse_retry_after, retry_after_of, status_code_of


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_parse_retry_after_seconds_milliseconds_and_dates() -> None:
    assert parse_retry_after({"retry-after": "7"}) == 7.0
    assert parse_retry_after({"retry-after-ms": "1500"}) == 1.5
    future = datetime.now(UTC) + timedelta(seconds=30)
    value = parse_retry_after({"retry-after": format_datetime(future, usegmt=True)})
    assert value is not None and 25 <= value <= 31
    for bad in ({}, {"retry-after": "soon"}, {"retry-after": "nan"}, {"retry-after": "-3"}, {"retry-after": "inf"}, None):
        assert parse_retry_after(bad) is None


def test_status_and_retry_after_follow_the_cause_chain() -> None:
    wrapped = ProviderKeyRejected("rejected")
    wrapped.__cause__ = _status_error(429, {"retry-after": "4"})
    assert status_code_of(wrapped) == 429 and retry_after_of(wrapped) == 4.0
    explicit = ProviderKeyRejected("x")
    explicit.status_code = 503
    explicit.retry_after_seconds = 2.0
    assert status_code_of(explicit) == 503 and retry_after_of(explicit) == 2.0


def test_retryable_statuses() -> None:
    assert is_retryable(RuntimeError("connection reset"))
    for status in (408, 429, 500, 502, 503, 504, 529):
        assert is_retryable(_status_error(status))
    for status in (400, 401, 403, 404, 422):
        assert not is_retryable(_status_error(status))


def test_backoff_is_exponential_capped_and_jittered() -> None:
    assert backoff_delay(1, base=0.5, maximum=32, rand=lambda: 0.5) == 0.5
    assert backoff_delay(3, base=0.5, maximum=32, rand=lambda: 0.5) == 2.0
    assert backoff_delay(20, base=0.5, maximum=32, rand=lambda: 0.5) == 32
    assert backoff_delay(1, base=0.5, maximum=32, rand=lambda: 0.0) == pytest.approx(0.375)
    assert backoff_delay(1, base=0.5, maximum=32, rand=lambda: 1.0) == pytest.approx(0.625)
    assert backoff_delay(2, base=0.0, maximum=32) == 0.0
    assert backoff_delay(2, base=0.5, maximum=32, retry_after=9.0) == 9.0


def test_the_transport_carries_status_and_retry_after_on_a_rejection() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"retry-after": "7"}, request=request))
    provider = HTTPProviderStreamTransport(
        base_url="https://x.test", api_key="k", model="m", provider="openrouter", client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderKeyRejected) as caught:
        list(provider.stream({"messages": [{"role": "user", "content": "hi"}], "tools": []}))
    assert caught.value.status_code == 429 and caught.value.retry_after_seconds == 7.0

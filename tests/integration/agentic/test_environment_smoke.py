from __future__ import annotations

import os

import pytest

from agentos.providers.http import OpenRouterHTTPAdapter, ProviderHTTPSettings
from agentos.providers.models import GenerationFailed, GenerationSucceeded, ProviderErrorCategory, ReadProviderStream, StreamCompleted
from tests.fixtures.agentic.provider_server import DeterministicProviderServer, make_provider_request


def test_deterministic_provider_json_round_trip_uses_real_local_http() -> None:
    secret = "sk-agentic-fixture-secret"
    with DeterministicProviderServer(secret=secret) as provider:
        adapter = OpenRouterHTTPAdapter(
            ProviderHTTPSettings(
                provider_ref=__import__("agentos.providers.models", fromlist=["ProviderRef"]).ProviderRef("provider:deterministic"),
                base_url=provider.base_url,
                api_key=secret,
                model="deterministic-model",
            )
        )
        try:
            outcome = adapter.generate(make_provider_request())
        finally:
            adapter.close()

    assert isinstance(outcome, GenerationSucceeded)
    assert outcome.message.parts[0].text == "deterministic answer"
    assert provider.call_count == 1
    assert provider.requests[0]["path"] == "/chat/completions"
    assert provider.requests[0]["headers"]["authorization"] == f"Bearer {secret}"


def test_deterministic_provider_stream_has_ordered_terminal_event() -> None:
    with DeterministicProviderServer("stream") as provider:
        adapter = OpenRouterHTTPAdapter(
            ProviderHTTPSettings(
                __import__("agentos.providers.models", fromlist=["ProviderRef"]).ProviderRef("provider:deterministic"),
                provider.base_url,
                "sk-agentic-stream-secret",
                "deterministic-model",
            )
        )
        request = make_provider_request(invocation_id="invocation:stream")
        try:
            stream = adapter.open_stream(request)
            events = adapter.read_stream(ReadProviderStream(request.context, stream.stream_id, 0, 20, request.limits.timeout))
        finally:
            adapter.close()

    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert "".join(event.delta for event in events if hasattr(event, "delta")) == "deterministic stream"
    assert isinstance(events[-1], StreamCompleted)


def test_deterministic_provider_cases_expose_public_failure_categories() -> None:
    cases = (("rate_limited", ProviderErrorCategory.RATE_LIMITED), ("invalid_response", ProviderErrorCategory.INVALID_RESPONSE))
    for case, category in cases:
        with DeterministicProviderServer(case) as provider:
            adapter = OpenRouterHTTPAdapter(
                ProviderHTTPSettings(
                    __import__("agentos.providers.models", fromlist=["ProviderRef"]).ProviderRef("provider:deterministic"),
                    provider.base_url,
                    "sk-agentic-case-secret",
                    "deterministic-model",
                )
            )
            try:
                outcome = adapter.generate(make_provider_request(invocation_id=f"invocation:{case}"))
            finally:
                adapter.close()
        assert isinstance(outcome, GenerationFailed)
        assert outcome.error.category is category
        assert outcome.error.message == "provider operation failed"


_POSTGRES_DSN = os.getenv("AGENTOS_TEST_POSTGRES_DSN") or os.getenv("AGENTOS_POSTGRES_URL")
_REDIS_URL = os.getenv("AGENTOS_REDIS_URL")


@pytest.mark.skipif(not (_POSTGRES_DSN and _REDIS_URL), reason="AGENTOS_TEST_POSTGRES_DSN/AGENTOS_POSTGRES_URL and AGENTOS_REDIS_URL are not configured")
def test_real_postgres_and_redis_readiness_is_opt_in() -> None:
    from agentos.bootstrap.production import DependencyProbe, ProductionSettings

    settings = ProductionSettings(DATABASE_URL=_POSTGRES_DSN, REDIS_URL=_REDIS_URL)
    assert DependencyProbe.from_settings(settings).ready()


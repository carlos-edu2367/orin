from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from agentos.agentic.retry import is_retryable
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.providers.claude_cli.errors import (
    ProviderTransportError,
    RateLimitState,
    classify_result_error,
    format_reset_local,
)
from agentos.providers.claude_cli.stream import CliResult, CliUsage

T = 1_793_200_000.0


def _result(
    *,
    api_error_status: int | None = None,
    text: str = "error",
    subtype: str = "error_during_execution",
) -> CliResult:
    return CliResult(
        subtype=subtype,
        is_error=True,
        result_text=text,
        stop_reason=None,
        num_turns=1,
        duration_ms=100,
        session_id="s1",
        api_error_status=api_error_status,
        usage=CliUsage(),
        model_usage={},
    )


def test_404_is_not_retryable_and_triggers_fallback() -> None:
    err = classify_result_error(
        _result(api_error_status=404, text="model not found"), None, now=T
    )
    assert err.status_code == 404
    assert not is_retryable(err)


def test_rate_limit_shows_local_reset_and_retry_after() -> None:
    tz = ZoneInfo("America/Sao_Paulo") if _has_zoneinfo("America/Sao_Paulo") else timezone.utc
    rl = RateLimitState("rejected", "five_hour", T + 3600)
    err = classify_result_error(
        _result(api_error_status=429, text="limit"), rl, now=T, tz=tz
    )
    assert err.status_code == 429
    assert err.retry_after_seconds == pytest.approx(3600)
    assert "five_hour" in str(err)
    assert format_reset_local(T + 3600, tz=tz) in str(err)


def _has_zoneinfo(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def test_auth_errors_exhaust_the_key() -> None:
    for code in (401, 403):
        err = classify_result_error(
            _result(api_error_status=code, text="unauthorized"), None, now=T
        )
        assert err.status_code == 401
        assert err.exhausted is True
        assert not is_retryable(err)


def test_overloaded_is_retryable() -> None:
    err = classify_result_error(
        _result(api_error_status=529, text="Overloaded"), None, now=T
    )
    assert err.status_code == 529
    assert is_retryable(err)


def test_prompt_too_long_is_context_length() -> None:
    err = classify_result_error(
        _result(api_error_status=400, text="API Error: 400 prompt is too long: 210000 tokens > 200000 maximum"),
        None,
        now=T,
    )
    assert err.status_code == 400
    assert AgenticTurnRuntime._is_context_length_error(err) is True


def test_error_class_name_is_kept() -> None:
    err = classify_result_error(_result(api_error_status=500), None, now=T)
    assert type(err).__name__ == "ProviderTransportError"
    assert isinstance(err, ProviderTransportError)



def test_429_with_allowed_warning_does_not_claim_subscription_exhausted() -> None:
    rl = RateLimitState.from_info(
        {"status": "allowed_warning", "rateLimitType": "five_hour", "resetsAt": T + 7200}
    )
    err = classify_result_error(
        _result(api_error_status=429, text="API Error: 429 Too many concurrent requests"),
        rl,
        now=T,
    )
    assert err.status_code == 429
    assert err.retry_after_seconds is None
    assert "Limite de uso da assinatura" not in str(err)


def test_rate_limit_handles_millis_and_overflow_timestamps() -> None:
    rl_ms = RateLimitState.from_info(
        {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": (T + 600) * 1000}
    )
    assert rl_ms.resets_at == pytest.approx(T + 600)
    assert format_reset_local(1e20) == "horário indisponível"

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
import re
import time
from typing import TYPE_CHECKING, Any, Mapping

from agentos.agentic.provider_stream import ProviderKeyRejected

if TYPE_CHECKING:
    from agentos.providers.claude_cli.stream import CliResult

_API_ERROR_STATUS_RE = re.compile(r"\bAPI Error:\s*(\d{3})\b")


class ProviderTransportError(ProviderKeyRejected):
    """Raised when `claude -p` fails auth, security, protocol, or execution."""


class NativeLoopUnavailable(ProviderTransportError):
    """Raised before the first user-visible delta when native MCP loop cannot start."""


class CliSecurityAbort(ProviderTransportError):
    """The CLI would not run on the OAuth subscription (apiKeySource != "none", or no init first).

    Never retried, never a fallback to another path or model: the same credentials would be used again.
    Not a NativeLoopUnavailable on purpose, so the native runner does not fall back to the text path.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message, status_code=401, exhausted=True)


@dataclass(frozen=True, slots=True)
class RateLimitState:
    status: str
    rate_limit_type: str | None = None
    resets_at: float | None = None

    @classmethod
    def from_info(cls, info: Mapping[str, Any]) -> RateLimitState:
        status = str(info.get("status") or "").strip()
        rl_type = (
            str(info.get("rateLimitType") or info.get("rate_limit_type") or "").strip()
            or None
        )
        resets_raw = info.get("resetsAt")
        if resets_raw is None:
            windows = info.get("unifiedWindows")
            if isinstance(windows, Mapping):
                for win in windows.values():
                    if isinstance(win, Mapping) and win.get("resetsAt") is not None:
                        resets_raw = win.get("resetsAt")
                        break
        resets_at: float | None = None
        if (
            isinstance(resets_raw, (int, float))
            and not isinstance(resets_raw, bool)
            and resets_raw > 0
        ):
            val = float(resets_raw)
            if val > 10_000_000_000:
                val /= 1000.0
            resets_at = val
        return cls(status=status, rate_limit_type=rl_type, resets_at=resets_at)


def format_reset_local(resets_at: float, *, tz: tzinfo | None = None) -> str:
    """Format an epoch reset timestamp in the local timezone (or `tz`) as `%d/%m às %H:%M`."""
    try:
        dt = (
            datetime.fromtimestamp(float(resets_at), tz=tz)
            if tz is not None
            else datetime.fromtimestamp(float(resets_at)).astimezone()
        )
        return dt.strftime("%d/%m às %H:%M")
    except (OSError, OverflowError, ValueError):
        return "horário indisponível"


def classify_result_error(
    result: CliResult,
    rate_limit: RateLimitState | None,
    *,
    now: float | None = None,
    tz: tzinfo | None = None,
) -> ProviderTransportError:
    """Classify a `result` error packet into a `ProviderTransportError` with accurate HTTP status and retry metadata."""
    status = result.api_error_status
    raw_text = (result.result_text or "").strip() or "Claude Code CLI execution failed"

    if status is None:
        match = _API_ERROR_STATUS_RE.search(raw_text)
        if match:
            status = int(match.group(1))

    subscription_rejected = (
        rate_limit is not None and rate_limit.status.lower() == "rejected"
    )
    is_rate_limited = (
        status == 429
        or subscription_rejected
        or (status is None and "rate limit" in raw_text.lower())
    )

    if is_rate_limited:
        retry_after_seconds: float | None = None
        message = raw_text
        if subscription_rejected and rate_limit is not None and rate_limit.resets_at is not None:
            now_ts = time.time() if now is None else float(now)
            if rate_limit.resets_at > 1_000_000_000:
                retry_after_seconds = max(1.0, rate_limit.resets_at - now_ts)
            else:
                retry_after_seconds = max(1.0, rate_limit.resets_at)
            rl_type = rate_limit.rate_limit_type or "assinatura"
            formatted = format_reset_local(rate_limit.resets_at, tz=tz)
            message = f"Limite de uso da assinatura ({rl_type}) atingido; libera em {formatted} ({raw_text})"
        return ProviderTransportError(
            message,
            status_code=429,
            exhausted=False,
            retry_after_seconds=retry_after_seconds,
        )

    if status in (401, 403):
        return ProviderTransportError(
            raw_text,
            status_code=401,
            exhausted=True,
        )

    if status == 404:
        return ProviderTransportError(
            raw_text,
            status_code=404,
            exhausted=False,
        )

    if status == 529:
        return ProviderTransportError(
            raw_text,
            status_code=529,
            exhausted=False,
        )

    if status == 400:
        return ProviderTransportError(
            raw_text,
            status_code=400,
            exhausted=False,
        )

    effective_status = (
        status if isinstance(status, int) and status >= 400 else 500
    )
    return ProviderTransportError(
        raw_text,
        status_code=effective_status,
        exhausted=False,
    )

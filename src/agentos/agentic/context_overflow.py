"""Helpers for detecting, parsing, sanitizing, and reporting CLI context overflows."""
from __future__ import annotations

import re
from typing import Any, Mapping

from agentos.providers.cli_adapter import CliErrorKind

_OVERFLOW_LIMIT_RE = re.compile(
    r"(\d[\d,_]*)\s*tokens?\s*>\s*(\d[\d,_]*)\s*maximum",
    re.IGNORECASE,
)

_OVERFLOW_TEXT_NEEDLES = (
    "context_length_exceeded",
    "prompt is too long",
    "maximum context length",
    "too many tokens",
    "exceeds the context window",
    "context window exceeded",
    "request too large",
    "input is too long",
    # agy (1.5.x): the system prompt plus the earlier turns left no room for the conversation.
    "maxmessagetokens must be positive",
)

_LONG_CONTEXT_CREDITS_NEEDLES = (
    "usage credits required for 1m context",
    "usage credits are required for long context",
    "extra usage is required for long context",
    "credits_clamp_200k",
)

CONTEXT_OVERFLOW_FAILURE_MESSAGE_PT_BR = (
    "O contexto desta conversa excedeu o limite do modelo mesmo após compactação automática. "
    "Use /compact para reduzir o histórico ou inicie uma nova conversa."
)


def parse_overflow_limit(raw: str) -> int | None:
    """Extract the maximum token limit ``M`` from ``'<N> tokens > <M> maximum'`` errors."""
    if not isinstance(raw, str) or not raw:
        return None
    match = _OVERFLOW_LIMIT_RE.search(raw)
    if not match:
        return None
    cleaned = match.group(2).replace(",", "").replace("_", "")
    try:
        value = int(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def format_context_overflow_message(raw_error: str | None = None) -> str:
    """Clear pt-BR failure message for E-RF12 when reactive compaction cannot recover."""
    limit = parse_overflow_limit(str(raw_error or ""))
    if limit is not None:
        return (
            f"O contexto desta conversa excedeu o limite do modelo ({limit:,} tokens) "
            "mesmo após compactação automática. Use /compact para reduzir o histórico "
            "ou inicie uma nova conversa."
        )
    return CONTEXT_OVERFLOW_FAILURE_MESSAGE_PT_BR


def is_context_overflow_error(
    exc: BaseException,
    runtime: Any | None = None,
) -> bool:
    """Return True when ``exc`` represents a model context-length overflow."""
    kind = getattr(exc, "error_kind", None)
    if kind == CliErrorKind.CONTEXT_OVERFLOW:
        return True
    if isinstance(kind, str) and kind.strip().lower() == CliErrorKind.CONTEXT_OVERFLOW.value:
        return True
    if runtime is not None:
        checker = getattr(runtime, "_is_context_length_error", None)
        if callable(checker) and checker(exc):
            return True
    lowered = str(exc or "").lower()
    if any(needle in lowered for needle in _OVERFLOW_TEXT_NEEDLES):
        return True
    tail = str(getattr(exc, "output_tail", "") or "").lower()
    return bool(tail) and any(needle in tail for needle in _OVERFLOW_TEXT_NEEDLES)


def is_zero_usage(usage: Any) -> bool:
    """True when a usage snapshot carries all-zero token counts (E-RF15 guard)."""
    if usage is None:
        return False
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_create = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    ctx_used = getattr(usage, "context_window_used_tokens", None)
    if ctx_used is not None and int(ctx_used) > 0:
        return False
    return (
        input_tokens == 0
        and output_tokens == 0
        and cache_read == 0
        and cache_create == 0
    )


def _normalize_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def is_synthetic_overflow_text(
    text: str,
    *,
    synthetic_error_text: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Return True if ``text`` is just a leaked CLI overflow error string (V5 fallback)."""
    norm = _normalize_text(text)
    if not norm:
        return False
    for candidate in (synthetic_error_text, error_message):
        cand_norm = _normalize_text(candidate)
        if cand_norm and (norm == cand_norm or norm in cand_norm):
            return True
    if len(norm) <= 400 and any(needle in norm for needle in _OVERFLOW_TEXT_NEEDLES):
        return True
    return False


def _strip_synthetic_suffix_from_text(
    text: str,
    *,
    synthetic_error_text: str | None = None,
    error_message: str | None = None,
) -> tuple[str, bool]:
    """Strip a leaked trailing overflow error suffix from ``text`` while preserving legitimate prefix text."""
    for candidate in (synthetic_error_text, error_message):
        cand = str(candidate or "").strip()
        if not cand:
            continue
        lower_text = text.rstrip()
        if lower_text.lower().endswith(cand.lower()):
            prefix = lower_text[: len(lower_text) - len(cand)].rstrip()
            return prefix, True

    # Also check the trailing non-empty line(s) separated by newline(s)
    stripped_right = text.rstrip()
    nl_idx = stripped_right.rfind("\n")
    if nl_idx != -1:
        tail = stripped_right[nl_idx + 1 :].strip()
        if tail and is_synthetic_overflow_text(
            tail,
            synthetic_error_text=synthetic_error_text,
            error_message=error_message,
        ):
            prefix = stripped_right[:nl_idx].rstrip()
            return prefix, True

    return text, False


def _starts_like_synthetic_overflow(
    text: str,
    *,
    synthetic_error_text: str | None = None,
    error_message: str | None = None,
) -> bool:
    norm = _normalize_text(text)
    if not norm:
        return True
    for candidate in (synthetic_error_text, error_message):
        cand_norm = _normalize_text(candidate)
        if cand_norm and (norm == cand_norm or cand_norm.startswith(norm) or norm.startswith(cand_norm)):
            return True
    for needle in _OVERFLOW_TEXT_NEEDLES:
        if norm.startswith(needle) or needle.startswith(norm):
            return True
    return False


def strip_synthetic_error_from_parts(
    parts: list[str],
    *,
    synthetic_error_text: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Remove leaked synthetic/overflow error chunks from ``parts`` in place.

    Preserves legitimate assistant text emitted in earlier rounds and only strips
    trailing synthetic/overflow chunks or a trailing leaked error suffix.
    Returns True when any text was removed.
    """
    if not parts:
        return False

    removed = False

    # 1. Pop whole trailing chunks that are themselves synthetic overflow errors.
    while parts and is_synthetic_overflow_text(
        parts[-1],
        synthetic_error_text=synthetic_error_text,
        error_message=error_message,
    ):
        # Guard: if parts[-1] actually contains legitimate prefix text plus a trailing error suffix,
        # trim only the suffix rather than dropping legitimate text.
        trimmed, trimmed_ok = _strip_synthetic_suffix_from_text(
            parts[-1],
            synthetic_error_text=synthetic_error_text,
            error_message=error_message,
        )
        if trimmed_ok and trimmed:
            parts[-1] = trimmed
            removed = True
            break
        parts.pop()
        removed = True

    # 2. Handle a synthetic overflow error split across multiple small trailing streaming chunks
    #    (e.g. ["Analisando...", "\n\nPrompt ", "is too long"]) without wiping earlier legitimate parts.
    if parts:
        for idx in range(len(parts) - 1, -1, -1):
            tail_joined = "".join(parts[idx:])
            if is_synthetic_overflow_text(
                tail_joined,
                synthetic_error_text=synthetic_error_text,
                error_message=error_message,
            ):
                if _starts_like_synthetic_overflow(
                    parts[idx],
                    synthetic_error_text=synthetic_error_text,
                    error_message=error_message,
                ):
                    del parts[idx:]
                    removed = True
                    break

    # 3. Trim a leaked trailing error suffix concatenated onto the last legitimate chunk.
    if parts:
        trimmed, trimmed_ok = _strip_synthetic_suffix_from_text(
            parts[-1],
            synthetic_error_text=synthetic_error_text,
            error_message=error_message,
        )
        if trimmed_ok:
            if trimmed:
                parts[-1] = trimmed
            else:
                parts.pop()
            removed = True

    return removed


def strip_synthetic_error_from_messages(
    messages: list[dict[str, object]],
    *,
    synthetic_error_text: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Drop trailing assistant message if it only holds leaked synthetic overflow error text."""
    if not messages:
        return False
    last = messages[-1]
    if not isinstance(last, Mapping) or last.get("role") != "assistant":
        return False
    content = last.get("content")
    if isinstance(content, str) and is_synthetic_overflow_text(
        content,
        synthetic_error_text=synthetic_error_text,
        error_message=error_message,
    ):
        messages.pop()
        return True
    return False


def is_long_context_credits_error(exc: BaseException | str) -> bool:
    """Return True when the CLI refused a `[1m]` request due to missing usage credits (E-RF31)."""
    lowered = str(exc or "").lower()
    if not lowered:
        return False
    return any(needle in lowered for needle in _LONG_CONTEXT_CREDITS_NEEDLES)


__all__ = [
    "CONTEXT_OVERFLOW_FAILURE_MESSAGE_PT_BR",
    "format_context_overflow_message",
    "is_context_overflow_error",
    "is_long_context_credits_error",
    "is_synthetic_overflow_text",
    "is_zero_usage",
    "parse_overflow_limit",
    "strip_synthetic_error_from_messages",
    "strip_synthetic_error_from_parts",
]

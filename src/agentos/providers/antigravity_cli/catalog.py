from __future__ import annotations

import os
import subprocess
import sys
from typing import Mapping, Sequence

from agentos.providers.antigravity_cli.command import (
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
)
from agentos.providers.cli_adapter import CliModelInfo

DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW: int = 1_000_000
_ANTIGRAVITY_LABEL_SUFFIX = "(Assinatura Antigravity)"

_EFFORTS_STD: tuple[str, ...] = ("low", "medium", "high")


def _infer_supported_efforts(slug: str) -> tuple[str, ...]:
    lower = slug.lower()
    for eff in ("xhigh", "high", "medium", "low", "max"):
        if lower.endswith(f"-{eff}"):
            return (eff,)
    return _EFFORTS_STD


FALLBACK_ANTIGRAVITY_MODELS: tuple[CliModelInfo, ...] = (
    CliModelInfo(
        "gemini-4-argon-medium",
        "gemini-4-argon-medium (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-4-argon-medium"),
    ),
    CliModelInfo(
        "gemini-4-argon-high",
        "gemini-4-argon-high (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-4-argon-high"),
    ),
    CliModelInfo(
        "gemini-4-argon-low",
        "gemini-4-argon-low (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-4-argon-low"),
    ),
    CliModelInfo(
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-high (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-3.8-flash-high"),
    ),
    CliModelInfo(
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-medium (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-3.8-flash-medium"),
    ),
    CliModelInfo(
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-low (Assinatura Antigravity)",
        1_000_000,
        _infer_supported_efforts("gemini-3.8-flash-low"),
    ),
    CliModelInfo(
        "claude-opus-5-5-low",
        "claude-opus-5-5-low (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-opus-5-5-low"),
    ),
    CliModelInfo(
        "claude-opus-5-5-medium",
        "claude-opus-5-5-medium (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-opus-5-5-medium"),
    ),
    CliModelInfo(
        "claude-opus-5-5-high",
        "claude-opus-5-5-high (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-opus-5-5-high"),
    ),
    CliModelInfo(
        "claude-sonnet-5-5-low",
        "claude-sonnet-5-5-low (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-sonnet-5-5-low"),
    ),
    CliModelInfo(
        "claude-sonnet-5-5-medium",
        "claude-sonnet-5-5-medium (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-sonnet-5-5-medium"),
    ),
    CliModelInfo(
        "claude-sonnet-5-5-high",
        "claude-sonnet-5-5-high (Assinatura Antigravity)",
        200_000,
        _infer_supported_efforts("claude-sonnet-5-5-high"),
    ),
    CliModelInfo(
        "gpt-oss-120b-medium",
        "gpt-oss-120b-medium (Assinatura Antigravity)",
        128_000,
        _infer_supported_efforts("gpt-oss-120b-medium"),
    ),
)

_FALLBACK_BY_ID: dict[str, CliModelInfo] = {
    m.id: m for m in FALLBACK_ANTIGRAVITY_MODELS
}


def _infer_context_window(slug: str) -> int:
    known = _FALLBACK_BY_ID.get(slug)
    if known is not None:
        return known.context_window
    lower = slug.lower()
    if lower.startswith("claude-"):
        return 200_000
    if lower.startswith("gpt-"):
        return 128_000
    return DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW


def discover_antigravity_models(
    binary: str | None = None,
    *,
    run_models_cmd: bool = False,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> tuple[CliModelInfo, ...]:
    """Return Antigravity subscription models (static tuple by default, or parsed from `agy models` when requested)."""
    if not run_models_cmd or not binary:
        return FALLBACK_ANTIGRAVITY_MODELS

    isolated_env = build_isolated_antigravity_env(env)
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    cmd = (
        [sys.executable, binary, "models"]
        if str(binary).lower().endswith(".py")
        else [binary, "models"]
    )
    try:
        completed = subprocess.run(
            cmd,
            env=isolated_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        return FALLBACK_ANTIGRAVITY_MODELS

    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return FALLBACK_ANTIGRAVITY_MODELS

    discovered: list[CliModelInfo] = []
    for raw_line in (completed.stdout or "").splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        raw_slug, _label = line.split("\t", 1)
        try:
            slug = clean_antigravity_model_id(raw_slug)
        except ValueError:
            continue
        if not slug:
            continue
        discovered.append(
            CliModelInfo(
                id=slug,
                display_name=f"{slug} {_ANTIGRAVITY_LABEL_SUFFIX}",
                context_window=_infer_context_window(slug),
                supported_efforts=_infer_supported_efforts(slug),
            )
        )

    if not discovered:
        return FALLBACK_ANTIGRAVITY_MODELS
    return tuple(discovered)


def context_window_for(
    model_id: str | None,
    models: Sequence[CliModelInfo] | None = None,
) -> int:
    """Return the context window for `model_id` (stripping `google-antigravity/` if present)."""
    if model_id is None or not isinstance(model_id, str):
        return DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW
    cleaned = model_id.strip()
    if not cleaned:
        return DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW
    if cleaned.startswith("google-antigravity/"):
        cleaned = cleaned[len("google-antigravity/") :].strip()
    if not cleaned:
        return DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW

    catalog = models if models is not None else FALLBACK_ANTIGRAVITY_MODELS
    for info in catalog:
        if info.id == cleaned:
            return info.context_window
    return _infer_context_window(cleaned)


__all__ = [
    "DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW",
    "FALLBACK_ANTIGRAVITY_MODELS",
    "context_window_for",
    "discover_antigravity_models",
]

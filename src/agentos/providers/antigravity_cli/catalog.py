from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence

from agentos.providers.antigravity_cli.command import (
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
)
from agentos.providers.cli_adapter import CliModelInfo

# Measured against agy v1.5.16 on 2026-11-24 via binary search on .agents/agents/orin.md:
# - gemini-4-{neon,carbon,argon,hydrogen}-* and gemini-pro-orchestrator*: ~257k tokens pass,
#   ~264k fail with "maxMessageTokens must be positive, got 0" -> 256k effective window.
# - gemini-3.8-flash-*: ~128k tokens pass, ~132k fail -> 128k effective window.
DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW: int = 256_000
ANTIGRAVITY_MODELS_CACHE_TTL_SECONDS: float = 30 * 60.0
_ANTIGRAVITY_LABEL_SUFFIX = "(Assinatura Antigravity)"

ANTIGRAVITY_IMAGE_MODEL_ID: str = "antigravity-image"
ANTIGRAVITY_IMAGE_MODEL: CliModelInfo = CliModelInfo(
    id=ANTIGRAVITY_IMAGE_MODEL_ID,
    display_name="Imagem (Antigravity – assinatura)",
    context_window=0,
    supported_efforts=(),
    input_modalities=("text",),
    output_modalities=("image",),
)

_EFFORTS_STD: tuple[str, ...] = ("low", "medium", "high")

_MODELS_CACHE_LOCK = threading.Lock()
_MODELS_CACHE: dict[str, tuple[float, tuple[CliModelInfo, ...]]] = {}


def _infer_supported_efforts(slug: str) -> tuple[str, ...]:
    lower = slug.lower()
    for eff in ("xhigh", "high", "medium", "low", "max"):
        if lower.endswith(f"-{eff}"):
            return (eff,)
    return _EFFORTS_STD


# Gemini and Claude read images. Antigravity's stream-json input accepts only
# text, but an image returned by an MCP tool result (Orin's view_file) reaches
# the model, so these models see attachments through that tool. Gemini models on
# agy also perceive workspace audio and video via agy's gated native read_file.
# gpt-oss is text-only, and an unknown family stays text-only until proven otherwise.
_IMAGE_FAMILIES: tuple[str, ...] = ("gemini-", "claude-")


def antigravity_input_modalities(slug: str) -> tuple[str, ...]:
    """Input modalities of an Antigravity model, inferred from its family."""
    lower = slug.strip().lower()
    if lower.startswith("google-antigravity/"):
        lower = lower[len("google-antigravity/") :]
    if lower.startswith("gemini-"):
        return ("text", "image", "audio", "video")
    if lower.startswith(_IMAGE_FAMILIES):
        return ("text", "image")
    return ("text",)


def _format_antigravity_display_name(
    slug: str, label: str | None = None
) -> str:
    cleaned_label = (label or "").strip()
    base = cleaned_label if cleaned_label else slug
    if base.endswith(_ANTIGRAVITY_LABEL_SUFFIX):
        return base
    return f"{base} {_ANTIGRAVITY_LABEL_SUFFIX}"


def _default_effort_for(efforts: tuple[str, ...]) -> str | None:
    if not efforts:
        return None
    if len(efforts) == 1:
        return efforts[0]
    return "medium" if "medium" in efforts else efforts[0]


def _model(slug: str, label: str, context_window: int) -> CliModelInfo:
    efforts = _infer_supported_efforts(slug)
    return CliModelInfo(
        slug,
        _format_antigravity_display_name(slug, label),
        context_window,
        efforts,
        antigravity_input_modalities(slug),
        default_effort=_default_effort_for(efforts),
    )


FALLBACK_ANTIGRAVITY_MODELS: tuple[CliModelInfo, ...] = (
    _model("gemini-4-carbon-medium", "Gemini 4 Carbon (Medium)", 256_000),
    _model("gemini-4-carbon-high", "Gemini 4 Carbon (High)", 256_000),
    _model("gemini-4-carbon-low", "Gemini 4 Carbon (Low)", 256_000),
    _model("gemini-4-argon-medium", "Gemini 4 Argon (Medium)", 256_000),
    _model("gemini-4-argon-high", "Gemini 4 Argon (High)", 256_000),
    _model("gemini-4-argon-low", "Gemini 4 Argon (Low)", 256_000),
    _model("gemini-4-hydrogen-high", "Gemini 4 Hydrogen (High)", 256_000),
    _model("gemini-4-hydrogen-xhigh", "Gemini 4 Hydrogen (XHigh)", 256_000),
    _model("gemini-4-hydrogen-medium", "Gemini 4 Hydrogen (Medium)", 256_000),
    _model("gemini-4-hydrogen-low", "Gemini 4 Hydrogen (Low)", 256_000),
    _model("gemini-4-neon-high", "Gemini 4 Neon (High)", 256_000),
    _model("gemini-4-neon-xhigh", "Gemini 4 Neon (XHigh)", 256_000),
    _model("gemini-4-neon-medium", "Gemini 4 Neon (Medium)", 256_000),
    _model("gemini-4-neon-low", "Gemini 4 Neon (Low)", 256_000),
    _model("gemini-pro-orchestrator-neon", "Orchestrator (Neon)", 256_000),
    _model("gemini-pro-orchestrator", "Orchestrator (Carbon)", 256_000),
    _model("gemini-pro-orchestrator-argon", "Orchestrator (Argon)", 256_000),
    _model("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)", 128_000),
    _model("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)", 128_000),
    _model("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)", 128_000),
    _model("claude-opus-5-5-low", "Claude Opus 5.5 (Low)", 200_000),
    _model("claude-opus-5-5-medium", "Claude Opus 5.5 (Medium)", 200_000),
    _model("claude-opus-5-5-high", "Claude Opus 5.5 (High)", 200_000),
    _model("claude-sonnet-5-5-low", "Claude Sonnet 5.5 (Low)", 200_000),
    _model("claude-sonnet-5-5-medium", "Claude Sonnet 5.5 (Medium)", 200_000),
    _model("claude-sonnet-5-5-high", "Claude Sonnet 5.5 (High)", 200_000),
    _model("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)", 128_000),
)

_FALLBACK_BY_ID: dict[str, CliModelInfo] = {
    m.id: m for m in FALLBACK_ANTIGRAVITY_MODELS
}


def get_cached_antigravity_models(
    binary: str | None,
    *,
    now: float | None = None,
) -> tuple[CliModelInfo, ...] | None:
    """Return cached live-discovered models for `binary` if present and within TTL."""
    if not binary:
        return None
    key = str(binary).strip()
    if not key:
        return None
    current_time = time.monotonic() if now is None else now
    with _MODELS_CACHE_LOCK:
        entry = _MODELS_CACHE.get(key)
        if entry is None:
            return None
        cached_at, models = entry
        if (current_time - cached_at) >= ANTIGRAVITY_MODELS_CACHE_TTL_SECONDS:
            _MODELS_CACHE.pop(key, None)
            return None
        return models


def set_cached_antigravity_models(
    binary: str,
    models: Sequence[CliModelInfo],
    *,
    now: float | None = None,
) -> tuple[CliModelInfo, ...]:
    """Store live-discovered models for `binary` in the process-wide cache."""
    key = str(binary).strip()
    snapshot = tuple(models)
    if not key or not snapshot:
        return snapshot
    current_time = time.monotonic() if now is None else now
    with _MODELS_CACHE_LOCK:
        _MODELS_CACHE[key] = (current_time, snapshot)
    return snapshot


def clear_antigravity_models_cache() -> None:
    """Clear the process-wide Antigravity models cache."""
    with _MODELS_CACHE_LOCK:
        _MODELS_CACHE.clear()


def _infer_context_window(slug: str) -> int:
    known = _FALLBACK_BY_ID.get(slug)
    if known is not None:
        return known.context_window
    lower = slug.lower()
    if lower.startswith("gemini-") and "flash" in lower:
        return 128_000
    if lower.startswith("gemini-"):
        return 256_000
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
    low = str(binary).lower()
    if low.endswith(".py"):
        cmd = [sys.executable, str(binary), "models"]
    elif os.name == "nt" and low.endswith((".cmd", ".bat")):
        comspec = isolated_env.get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
        cmd = [comspec, "/d", "/s", "/c", str(binary), "models"]
    else:
        cmd = [str(binary), "models"]
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
        if not raw_line.strip() or "\t" not in raw_line:
            continue
        raw_slug, raw_label = raw_line.split("\t", 1)
        try:
            slug = clean_antigravity_model_id(raw_slug)
        except ValueError:
            continue
        if not slug:
            continue
        efforts = _infer_supported_efforts(slug)
        discovered.append(
            CliModelInfo(
                id=slug,
                display_name=_format_antigravity_display_name(slug, raw_label),
                context_window=_infer_context_window(slug),
                supported_efforts=efforts,
                input_modalities=antigravity_input_modalities(slug),
                default_effort=_default_effort_for(efforts),
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
    "ANTIGRAVITY_IMAGE_MODEL",
    "ANTIGRAVITY_IMAGE_MODEL_ID",
    "ANTIGRAVITY_MODELS_CACHE_TTL_SECONDS",
    "DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW",
    "FALLBACK_ANTIGRAVITY_MODELS",
    "_infer_supported_efforts",
    "antigravity_input_modalities",
    "clear_antigravity_models_cache",
    "context_window_for",
    "discover_antigravity_models",
    "get_cached_antigravity_models",
    "set_cached_antigravity_models",
]

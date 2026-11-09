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


# Gemini and Claude read images. Antigravity's stream-json input accepts only
# text, but an image returned by an MCP tool result (Orin's view_file) reaches
# the model, so these models see attachments through that tool. gpt-oss is
# text-only, and an unknown family stays text-only until proven otherwise.
_IMAGE_FAMILIES: tuple[str, ...] = ("gemini-", "claude-")


def antigravity_input_modalities(slug: str) -> tuple[str, ...]:
    """Input modalities of an Antigravity model, inferred from its family."""
    lower = slug.strip().lower()
    if lower.startswith("google-antigravity/"):
        lower = lower[len("google-antigravity/") :]
    if lower.startswith(_IMAGE_FAMILIES):
        return ("text", "image")
    return ("text",)


def _model(slug: str, context_window: int) -> CliModelInfo:
    return CliModelInfo(
        slug,
        f"{slug} {_ANTIGRAVITY_LABEL_SUFFIX}",
        context_window,
        _infer_supported_efforts(slug),
        antigravity_input_modalities(slug),
    )


FALLBACK_ANTIGRAVITY_MODELS: tuple[CliModelInfo, ...] = (
    _model("gemini-4-argon-medium", 1_000_000),
    _model("gemini-4-argon-high", 1_000_000),
    _model("gemini-4-argon-low", 1_000_000),
    _model("gemini-3.8-flash-high", 1_000_000),
    _model("gemini-3.8-flash-medium", 1_000_000),
    _model("gemini-3.8-flash-low", 1_000_000),
    _model("claude-opus-5-5-low", 200_000),
    _model("claude-opus-5-5-medium", 200_000),
    _model("claude-opus-5-5-high", 200_000),
    _model("claude-sonnet-5-5-low", 200_000),
    _model("claude-sonnet-5-5-medium", 200_000),
    _model("claude-sonnet-5-5-high", 200_000),
    _model("gpt-oss-120b-medium", 128_000),
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
                input_modalities=antigravity_input_modalities(slug),
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
    "antigravity_input_modalities",
    "context_window_for",
    "discover_antigravity_models",
]

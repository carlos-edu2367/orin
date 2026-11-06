from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from agentos.providers.cli_adapter import CliModelInfo
from agentos.providers.codex_cli.command import clean_codex_model_id

DEFAULT_CODEX_CONTEXT_WINDOW: int = 272_000

_CODEX_LABEL_SUFFIX = "(Assinatura Codex)"

FALLBACK_CODEX_MODELS: tuple[CliModelInfo, ...] = (
    CliModelInfo(
        "gpt-6.1-sol",
        "GPT-6.1-Sol (Assinatura Codex)",
        500_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-6-astra",
        "GPT-6-Astra (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-6.1-terra",
        "GPT-6.1 Terra (Assinatura Codex)",
        500_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-6-sol",
        "GPT-6-Sol (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-6-luna",
        "GPT-6-Luna (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max"),
    ),
    CliModelInfo(
        "gpt-5.6-sol",
        "GPT-5.6-Sol (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-5.6-terra",
        "GPT-5.6-Terra (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    CliModelInfo(
        "gpt-5.6-luna",
        "GPT-5.6-Luna (Assinatura Codex)",
        272_000,
        ("low", "medium", "high", "xhigh", "max"),
    ),
)


def resolve_codex_home(env: Mapping[str, str] | None = None) -> Path:
    """Resolve `CODEX_HOME`, defaulting to `<USERPROFILE or HOME>/.codex`."""
    source = os.environ if env is None else env
    codex_home = str(source.get("CODEX_HOME") or "").strip()
    if codex_home:
        return Path(codex_home)

    user_home = (
        str(source.get("USERPROFILE") or "").strip()
        or str(source.get("HOME") or "").strip()
    )
    if user_home:
        return Path(user_home) / ".codex"
    return Path.home() / ".codex"


def _extract_supported_efforts(raw_levels: Any) -> tuple[str, ...]:
    if not isinstance(raw_levels, Sequence) or isinstance(
        raw_levels, (str, bytes)
    ):
        return ()
    efforts: list[str] = []
    for item in raw_levels:
        effort_val: str | None = None
        if isinstance(item, Mapping):
            raw_eff = item.get("effort")
            if isinstance(raw_eff, str):
                effort_val = raw_eff.strip().lower()
        elif isinstance(item, str):
            effort_val = item.strip().lower()
        if effort_val and effort_val not in efforts:
            efforts.append(effort_val)
    return tuple(efforts)


def discover_codex_models(
    codex_home: Path | None = None,
) -> tuple[CliModelInfo, ...]:
    """Read `models_cache.json` from `codex_home`, returning visible models or `FALLBACK_CODEX_MODELS`."""
    home = codex_home if codex_home is not None else resolve_codex_home()
    cache_path = Path(home) / "models_cache.json"
    try:
        if not cache_path.is_file():
            return FALLBACK_CODEX_MODELS
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return FALLBACK_CODEX_MODELS

    if not isinstance(payload, Mapping):
        return FALLBACK_CODEX_MODELS

    raw_models = payload.get("models")
    if not isinstance(raw_models, Sequence) or isinstance(
        raw_models, (str, bytes)
    ):
        return FALLBACK_CODEX_MODELS

    discovered: list[CliModelInfo] = []
    for entry in raw_models:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("visibility") != "list":
            continue
        raw_slug = entry.get("slug")
        if not isinstance(raw_slug, str):
            continue
        try:
            slug = clean_codex_model_id(raw_slug)
        except ValueError:
            continue
        if not slug:
            continue

        raw_display = str(
            entry.get("display_name") or entry.get("title") or slug
        ).strip()
        if not raw_display:
            raw_display = slug
        if raw_display.endswith(_CODEX_LABEL_SUFFIX):
            display_name = raw_display
        else:
            display_name = f"{raw_display} {_CODEX_LABEL_SUFFIX}"

        raw_cw = entry.get("context_window")
        if (
            isinstance(raw_cw, int)
            and not isinstance(raw_cw, bool)
            and raw_cw > 0
        ):
            context_window = raw_cw
        else:
            context_window = DEFAULT_CODEX_CONTEXT_WINDOW

        supported_efforts = _extract_supported_efforts(
            entry.get("supported_reasoning_levels")
        )
        discovered.append(
            CliModelInfo(
                id=slug,
                display_name=display_name,
                context_window=context_window,
                supported_efforts=supported_efforts,
            )
        )

    if not discovered:
        return FALLBACK_CODEX_MODELS
    return tuple(discovered)


def context_window_for(
    model_id: str | None,
    models: Sequence[CliModelInfo] | None = None,
) -> int:
    """Return the context window for `model_id` (stripping `openai-codex/` if present)."""
    if model_id is None or not isinstance(model_id, str):
        return DEFAULT_CODEX_CONTEXT_WINDOW
    cleaned = model_id.strip()
    if not cleaned:
        return DEFAULT_CODEX_CONTEXT_WINDOW
    if cleaned.startswith("openai-codex/"):
        cleaned = cleaned[len("openai-codex/") :].strip()
    if not cleaned:
        return DEFAULT_CODEX_CONTEXT_WINDOW

    catalog = models if models is not None else discover_codex_models()
    for info in catalog:
        if info.id == cleaned:
            return info.context_window
    return DEFAULT_CODEX_CONTEXT_WINDOW


__all__ = [
    "DEFAULT_CODEX_CONTEXT_WINDOW",
    "FALLBACK_CODEX_MODELS",
    "context_window_for",
    "discover_codex_models",
    "resolve_codex_home",
]

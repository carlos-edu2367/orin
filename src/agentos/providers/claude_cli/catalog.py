from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import urllib.request

from agentos.providers.claude_cli.command import (
    SAFE_MODEL_RE,
    check_claude_cli_status,
    clean_model_id,
)

PUBLISHED_CLAUDE_MODEL_CATALOG_URL = (
    "https://downloads.claude.ai/model-catalog/v1/catalog.json"
)

FAMILY_DEFAULTS: dict[str, str] = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "fable": "claude-fable-5-1",
}

_SUBSCRIPTION_MODELS: tuple[tuple[str, str, int], ...] = (
    ("claude-opus-5-5", "Claude Opus 5.5 (Assinatura CLI)", 1_000_000),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 (Assinatura CLI)", 200_000),
    ("claude-haiku-5-5", "Claude Haiku 5.5 (Assinatura CLI)", 200_000),
    ("claude-fable-5-1", "Claude Fable 5.1 (Assinatura CLI)", 200_000),
    ("claude-opus-4-7", "Claude Opus 4.7 (Assinatura CLI)", 200_000),
    ("claude-opus-4-6", "Claude Opus 4.6 (Assinatura CLI)", 200_000),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Assinatura CLI)", 200_000),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5 (Assinatura CLI)", 200_000),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (Assinatura CLI)", 200_000),
)

_SUBSCRIPTION_WINDOWS: dict[str, int] = {
    model_id: window for model_id, _, window in _SUBSCRIPTION_MODELS
}

_FAMILY_VERSION_RE = re.compile(
    r"^claude-(?P<family>opus|sonnet|haiku|fable)-(?P<major>\d+)(?:-(?P<minor>\d+))?"
)


@dataclass(frozen=True, slots=True)
class CatalogModel:
    id: str
    name: str
    context_window: int

    def get(self, key: str, default: Any = None) -> Any:
        if key == "id":
            return self.id
        if key in {"name", "display_name"}:
            return self.name
        if key in {"context_window", "context_length", "max_input_tokens"}:
            return self.context_window
        return default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in {
            "id",
            "name",
            "display_name",
            "context_window",
            "context_length",
            "max_input_tokens",
        }:
            raise KeyError(key)
        return val


def _family_version(model_id: str, family: str) -> tuple[int, int] | None:
    match = _FAMILY_VERSION_RE.match(model_id.lower())
    if not match or match.group("family") != family:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    return (major, minor)


def _entry_id(entry: CatalogModel | Mapping[str, Any]) -> str:
    if isinstance(entry, CatalogModel):
        return entry.id
    if isinstance(entry, Mapping):
        return str(entry.get("id") or "").strip()
    return ""


def _entry_window(entry: CatalogModel | Mapping[str, Any]) -> int | None:
    if isinstance(entry, CatalogModel):
        return entry.context_window
    if isinstance(entry, Mapping):
        for key in ("context_window", "max_input_tokens", "context_length"):
            val = entry.get(key)
            if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                return val
        runtime = entry.get("runtime")
        if isinstance(runtime, Mapping):
            max_in = runtime.get("max_input_tokens")
            if isinstance(max_in, int) and not isinstance(max_in, bool) and max_in > 0:
                return max_in
    return None


def resolve_family_alias(
    alias: str,
    catalog: Iterable[CatalogModel | Mapping[str, Any]] = (),
) -> str | None:
    """Resolve a subagent model alias (`opus`, `sonnet`, `haiku`, `fable`) against a live catalog or 5.x defaults."""
    fam = str(alias or "").strip().lower()
    default = FAMILY_DEFAULTS.get(fam)
    if default is None:
        return None

    default_version = _family_version(default, fam) or (5, 0)
    best: tuple[tuple[int, int], str] | None = None
    for entry in catalog:
        raw_id = _entry_id(entry)
        if not raw_id:
            continue
        version = _family_version(raw_id, fam)
        if version is None or version < default_version:
            continue
        if best is None or version > best[0]:
            best = (version, raw_id)
    return best[1] if best is not None else default


def context_window_for(
    model: str | None,
    catalog: Iterable[CatalogModel | Mapping[str, Any]] = (),
) -> int:
    """Return the input context window for `model` from the catalog or 5.x subscription table."""
    cleaned: str | None = None
    if model:
        raw_tail = str(model).strip().rsplit("/", 1)[-1].strip()
        try:
            cleaned = clean_model_id(raw_tail)
        except ValueError:
            cleaned = None
    if cleaned:
        lower = cleaned.lower()
        for entry in catalog:
            if _entry_id(entry).lower() == lower:
                win = _entry_window(entry)
                if win is not None and win > 0:
                    return win
        if lower in _SUBSCRIPTION_WINDOWS:
            return _SUBSCRIPTION_WINDOWS[lower]
        if "[1m]" in lower or lower.startswith("claude-opus-5"):
            return 1_000_000
    return 200_000


model_context_window = context_window_for


def _format_catalog_model_entry(entry: Mapping[str, Any]) -> CatalogModel | None:
    model_id = str(entry.get("id") or "").strip()
    if not model_id or not SAFE_MODEL_RE.match(model_id):
        return None
    raw_name = str(entry.get("name") or model_id).strip()
    display_name = (
        raw_name if raw_name.lower().startswith("claude") else f"Claude {raw_name}"
    )
    if "(assinatura cli)" not in display_name.lower():
        display_name = f"{display_name} (Assinatura CLI)"

    runtime = entry.get("runtime") if isinstance(entry.get("runtime"), Mapping) else {}
    max_input = runtime.get("max_input_tokens")
    if isinstance(max_input, int) and not isinstance(max_input, bool) and max_input > 0:
        context_window = max_input
    else:
        context_window = context_window_for(model_id)
    return CatalogModel(id=model_id, name=display_name, context_window=context_window)


def _fetch_published_claude_cli_models(
    *, timeout: float = 4.0
) -> list[tuple[str, str, int]]:
    catalog_url = os.environ.get(
        "CLAUDE_CODE_MODEL_CATALOG_URL", PUBLISHED_CLAUDE_MODEL_CATALOG_URL
    ).strip()
    if not catalog_url.startswith("https://"):
        return []
    try:
        req = urllib.request.Request(
            catalog_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "claude-code/2.1.309",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []

    if not isinstance(payload, Mapping):
        return []
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, Mapping):
        return []

    discovered: list[tuple[str, str, int]] = []
    seen_ids: set[str] = set()
    for surface_key in ("cc", "ccd", "chat"):
        surface = surfaces.get(surface_key)
        if not isinstance(surface, Mapping):
            continue
        configs = surface.get("model_selector_config")
        if not isinstance(configs, list):
            continue
        for cfg in configs:
            if not isinstance(cfg, Mapping):
                continue
            raw_models = cfg.get("models")
            if not isinstance(raw_models, list):
                continue
            for entry in raw_models:
                if not isinstance(entry, Mapping):
                    continue
                parsed = _format_catalog_model_entry(entry)
                if parsed is not None and parsed.id not in seen_ids:
                    seen_ids.add(parsed.id)
                    discovered.append((parsed.id, parsed.name, parsed.context_window))
    return discovered


def discover_cached_models(
    *,
    org_id: str | None = None,
    cache_dir: Path | None = None,
) -> list[CatalogModel]:
    """Read live served models from `~/.claude/cache/model-catalog/*.json` when present."""
    candidate_dirs: list[Path] = []
    if cache_dir is not None:
        candidate_dirs.append(cache_dir)
    else:
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidate_dirs.append(
                Path(userprofile) / ".claude" / "cache" / "model-catalog"
            )
        try:
            candidate_dirs.append(
                Path.home() / ".claude" / "cache" / "model-catalog"
            )
        except Exception:
            pass

    scored_files: list[tuple[int, int, int, list[Mapping[str, Any]]]] = []
    seen_paths: set[Path] = set()
    norm_org = str(org_id or "").strip().lower()

    for directory in candidate_dirs:
        try:
            if not directory.is_dir():
                continue
            files = list(directory.glob("*.json"))
        except Exception:
            continue
        for file_path in files:
            try:
                resolved = file_path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                payload = json.loads(
                    file_path.read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                continue
            if not isinstance(payload, Mapping):
                continue
            catalog = payload.get("catalog")
            if not isinstance(catalog, Mapping):
                continue
            config = catalog.get("config")
            if not isinstance(config, Mapping):
                continue
            raw_models = config.get("models")
            if not isinstance(raw_models, list) or not raw_models:
                continue
            surface = str(catalog.get("surface") or "").strip().lower()
            file_org = str(payload.get("organizationUuid") or "").strip().lower()
            org_match = (
                1
                if (
                    norm_org
                    and (file_org == norm_org or norm_org in file_path.name.lower())
                )
                else 0
            )
            cc_match = 1 if surface == "cc" else 0
            fetched_at = payload.get("fetchedAt")
            fetched_score = int(fetched_at) if isinstance(fetched_at, (int, float)) else 0
            valid_entries = [m for m in raw_models if isinstance(m, Mapping)]
            if valid_entries:
                scored_files.append(
                    (org_match, cc_match, fetched_score, valid_entries)
                )

    scored_files.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    discovered: list[CatalogModel] = []
    seen_ids: set[str] = set()

    for _, _, _, entries in scored_files:
        for entry in entries:
            parsed = _format_catalog_model_entry(entry)
            if parsed is not None and parsed.id not in seen_ids:
                seen_ids.add(parsed.id)
                discovered.append(parsed)
    return discovered


def _discover_cached_claude_cli_models(
    *,
    org_id: str | None = None,
    cache_dir: Path | None = None,
) -> list[tuple[str, str, int]]:
    return [
        (m.id, m.name, m.context_window)
        for m in discover_cached_models(org_id=org_id, cache_dir=cache_dir)
    ]


class ClaudeCliModelCatalogClient:
    """Model catalog client for the local Claude Code CLI (`claude -p`) subscription."""

    def __init__(self, *, timeout: float = 8.0, cache_dir: Path | None = None) -> None:
        self._timeout = timeout
        self._cache_dir = cache_dir

    def fetch(self, api_key: str = "", *, base_url: str = "") -> list[dict[str, object]]:
        status = check_claude_cli_status(timeout=self._timeout)
        org_id = str(status.get("orgId") or "").strip() or None
        merged: list[tuple[str, str, int]] = []
        seen_ids: set[str] = set()

        for item in _discover_cached_claude_cli_models(
            org_id=org_id, cache_dir=self._cache_dir
        ):
            if item[0] not in seen_ids:
                seen_ids.add(item[0])
                merged.append(item)

        for item in _fetch_published_claude_cli_models(timeout=min(self._timeout, 4.0)):
            if item[0] not in seen_ids:
                seen_ids.add(item[0])
                merged.append(item)

        for item in _SUBSCRIPTION_MODELS:
            if item[0] not in seen_ids:
                seen_ids.add(item[0])
                merged.append(item)

        models: list[dict[str, object]] = []
        for model_id, display_name, context_window in merged:
            models.append(
                {
                    "id": model_id,
                    "name": display_name,
                    "display_name": display_name,
                    "context_length": context_window,
                    "max_input_tokens": context_window,
                    "supported_parameters": ["tools", "vision", "reasoning"],
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                    "pricing": {"prompt": "0", "completion": "0"},
                    "capabilities": {
                        "image_input": {"supported": True},
                        "thinking": {"supported": True},
                        "reasoning": {"supported": True},
                        "tools": {"supported": True},
                        "vision": {"supported": True},
                        "pdf_input": {"supported": True},
                    },
                }
            )
        return models

    def list_models(
        self, api_key: str = "", *, base_url: str = ""
    ) -> list[dict[str, object]]:
        return self.fetch(api_key, base_url=base_url)


ClaudeCodeModelCatalogClient = ClaudeCliModelCatalogClient

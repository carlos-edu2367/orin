"""Effective settings for a workspace: 5 layers, provenance and a project security filter."""

from __future__ import annotations

import copy
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .merge import merge_settings
from .paths import SettingsPaths

LAYER_ORDER = ("user", "project", "local", "cli", "managed")  # lowest -> highest
MAX_SETTINGS_BYTES = 1024 * 1024
_FILTERABLE_SOURCES = {"user", "project", "local"}

# Never honoured from a repository's settings: they are organisation/user decisions.
ALWAYS_IGNORED_FROM_PROJECT = (
    "claudeMd",
    "memory",
    "autoMemoryDirectory",
    "allowedMcpServers",
    "deniedMcpServers",
    "managedMcpServers",
)
# Ignored until the user trusts the workspace: they run code or widen permissions.
UNTRUSTED_IGNORED_FROM_PROJECT = (
    "hooks",
    "apiKeyHelper",
    "statusLine",
    "fileSuggestion",
    "env",
    "awsAuthRefresh",
    "awsCredentialExport",
    "otelHeadersHelper",
    "enableAllProjectMcpServers",
    "enabledMcpjsonServers",
    "extraKnownMarketplaces",
    "enabledPlugins",
    "permissions.allow",
    "permissions.additionalDirectories",
)
# A repository can never turn these on.
PROJECT_TRUE_IGNORED = ("permissions.allowBypassPermissionsMode",)
# A repository can never turn these off.
PROJECT_FALSE_IGNORED = ("disableAllHooks",)


@dataclass(frozen=True, slots=True)
class SettingsError:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class SettingsLayer:
    name: str
    path: Path | None
    data: Mapping[str, Any]


def _split(dotted: str) -> list[str]:
    return [part for part in dotted.split(".") if part]


def _get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in _split(dotted):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _drop(data: dict[str, Any], dotted: str) -> None:
    parts = _split(dotted)
    current: Any = data
    for part in parts[:-1]:
        current = current.get(part) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            return
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _filter_project(data: dict[str, Any], *, trusted: bool) -> dict[str, Any]:
    filtered = copy.deepcopy(data)
    for key in ALWAYS_IGNORED_FROM_PROJECT:
        _drop(filtered, key)
    if not trusted:
        for key in UNTRUSTED_IGNORED_FROM_PROJECT:
            _drop(filtered, key)
    for key in PROJECT_TRUE_IGNORED:
        if _get(filtered, key) is True:
            _drop(filtered, key)
    for key in PROJECT_FALSE_IGNORED:
        if _get(filtered, key) is False:
            _drop(filtered, key)
    return filtered


def _read(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not path.is_file():
            return None, None
        size = path.stat().st_size
        if size > MAX_SETTINGS_BYTES:
            return None, f"file is larger than {MAX_SETTINGS_BYTES} bytes"
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        return None, f"cannot read file: {error}"
    if not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, f"invalid JSON at line {error.lineno} column {error.colno}: {error.msg}"
    if not isinstance(parsed, dict):
        return None, "the top level must be a JSON object"
    return parsed, None


def _flatten(data: Mapping[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            keys.extend(_flatten(value, dotted))
        else:
            keys.append(dotted)
    return keys


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    data: Mapping[str, Any]
    provenance: Mapping[str, str]
    layers: tuple[SettingsLayer, ...]
    errors: tuple[SettingsError, ...]
    trusted: bool
    candidate_files: tuple[Path, ...] = field(default_factory=tuple)

    def get(self, dotted: str, default: Any = None) -> Any:
        value = _get(self.data, dotted)
        return default if value is None else value

    def experimental(self, name: str, default: bool = False) -> bool:
        value = _get(self.data, f"experimental.{name}")
        return value if isinstance(value, bool) else default

    def permission_rules(self, kind: str) -> tuple[tuple[str, str], ...]:
        """``(rule, layer)`` for one of allow/ask/deny, highest precedence first, deduplicated."""
        seen: set[str] = set()
        rules: list[tuple[str, str]] = []
        for layer in reversed(self.layers):
            values = _get(layer.data, f"permissions.{kind}")
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value.strip() and value.strip() not in seen:
                    seen.add(value.strip())
                    rules.append((value.strip(), layer.name))
        return tuple(rules)


def empty_settings() -> EffectiveSettings:
    return EffectiveSettings({}, {}, (), (), True, ())


def load_effective_settings(
    project_root: Path | str | None,
    *,
    cli: Mapping[str, Any] | None = None,
    sources: Collection[str] | None = None,
    trusted: bool = True,
) -> EffectiveSettings:
    paths = SettingsPaths(Path(project_root) if project_root is not None else None)
    allowed_sources = set(sources) if sources is not None else set(_FILTERABLE_SOURCES)
    layers: list[SettingsLayer] = []
    errors: list[SettingsError] = []
    candidates: list[Path] = []
    for layer in LAYER_ORDER:
        if layer == "cli":
            if cli:
                layers.append(SettingsLayer("cli", None, copy.deepcopy(dict(cli))))
            continue
        files = paths.files_for(layer)
        candidates.extend(files)
        if layer in _FILTERABLE_SOURCES and layer not in allowed_sources:
            continue
        for path in files:
            data, error = _read(path)
            if error is not None:
                errors.append(SettingsError(str(path), error))
                continue
            if data is None:
                continue
            if layer in {"project", "local"}:
                data = _filter_project(data, trusted=trusted)
            layers.append(SettingsLayer(layer, path, data))
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for item in layers:
        merged = merge_settings(merged, item.data)
        for key in _flatten(item.data):
            provenance[key] = item.name
    return EffectiveSettings(merged, provenance, tuple(layers), tuple(errors), bool(trusted), tuple(candidates))

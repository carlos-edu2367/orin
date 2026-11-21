"""Typed settings registry with scope-aware resolution across Orin's five layers."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .loader import LAYER_ORDER, EffectiveSettings

EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max", "ultra")
EFFORT_RANK: dict[str, int] = {level: index for index, level in enumerate(EFFORT_LEVELS)}
_LAYER_PRECEDENCE: dict[str, int] = {name: index for index, name in enumerate(LAYER_ORDER)}
_MISSING = object()


class Scope(str, Enum):
    USER_ONLY = "user_only"
    PROJECT_OFF_ONLY = "project_off_only"
    PROJECT_LOWER_ONLY = "project_lower_only"
    ANY = "any"
    AGENT_FILE = "agent_file"


@dataclass(frozen=True, slots=True)
class SettingKey:
    path: str
    type: str
    default: Any
    scope: Scope
    group: str
    label: str
    description: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] | None = None
    item_type: str | None = None
    value_minimum: int | float | None = None
    value_maximum: int | float | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    value: Any
    layer: str
    default: Any


REGISTRY: dict[str, SettingKey] = {}


def register(key: SettingKey) -> SettingKey:
    REGISTRY[key.path] = key
    return key


def _get_raw(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not part:
            continue
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _validate_value(key: SettingKey, value: Any, *, allow_none_if_nullable: bool = True) -> Any:
    if value is None:
        if allow_none_if_nullable and key.default is None:
            return None
        raise ValueError(f"{key.path}: valor nulo não é permitido")

    if key.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{key.path}: esperado booleano")
        return value

    if key.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key.path}: esperado número")
        if not math.isfinite(value):
            raise ValueError(f"{key.path}: número deve ser finito")
        if isinstance(key.default, int) and not isinstance(value, int):
            raise ValueError(f"{key.path}: esperado número inteiro")
        if key.minimum is not None and value < key.minimum:
            raise ValueError(f"{key.path}: valor mínimo é {key.minimum}")
        if key.maximum is not None and value > key.maximum:
            raise ValueError(f"{key.path}: valor máximo é {key.maximum}")
        return value

    if key.type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{key.path}: esperado texto")
        return value

    if key.type == "choice":
        if not isinstance(value, str) or key.choices is None or value not in key.choices:
            allowed = ", ".join(key.choices or ())
            raise ValueError(f"{key.path}: valor inválido; opções permitidas: {allowed}")
        return value

    if key.type == "string_list":
        if not isinstance(value, list):
            raise ValueError(f"{key.path}: esperado lista de textos")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{key.path}: cada item deve ser um texto não vazio")
            cleaned.append(item.strip())
        return cleaned

    if key.type == "map":
        if not isinstance(value, Mapping):
            raise ValueError(f"{key.path}: esperado objeto (mapa chave → valor)")
        cleaned_map: dict[str, Any] = {}
        for raw_k, raw_v in value.items():
            if not isinstance(raw_k, str) or not raw_k.strip():
                raise ValueError(f"{key.path}: chaves do mapa devem ser textos não vazios")
            map_key = raw_k.strip()
            if key.item_type == "number":
                if isinstance(raw_v, bool) or not isinstance(raw_v, int):
                    raise ValueError(f"{key.path}[{map_key}]: esperado número inteiro")
                if key.value_minimum is not None and raw_v < key.value_minimum:
                    raise ValueError(f"{key.path}[{map_key}]: valor mínimo é {key.value_minimum}")
                if key.value_maximum is not None and raw_v > key.value_maximum:
                    raise ValueError(f"{key.path}[{map_key}]: valor máximo é {key.value_maximum}")
                cleaned_map[map_key] = raw_v
            elif key.item_type == "choice":
                if not isinstance(raw_v, str) or key.choices is None or raw_v not in key.choices:
                    allowed = ", ".join(key.choices or ())
                    raise ValueError(f"{key.path}[{map_key}]: valor inválido; opções: {allowed}")
                cleaned_map[map_key] = raw_v
            elif key.item_type == "string":
                if not isinstance(raw_v, str):
                    raise ValueError(f"{key.path}[{map_key}]: esperado texto")
                cleaned_map[map_key] = raw_v
            else:
                cleaned_map[map_key] = copy.deepcopy(raw_v)
        return cleaned_map

    raise ValueError(f"{key.path}: tipo de configuração desconhecido '{key.type}'")


def validate_change(path: str, value: Any) -> Any:
    key = REGISTRY.get(path)
    if key is None:
        raise ValueError(f"Configuração desconhecida: {path}")
    return _validate_value(key, value)


def _layer_entries(settings: EffectiveSettings, path: str) -> list[tuple[str, Any]]:
    if settings.layers:
        entries: list[tuple[str, Any]] = []
        for layer in settings.layers:
            raw = _get_raw(layer.data, path)
            if raw is _MISSING and path == "effortLevel":
                legacy = layer.data.get("effort")
                if isinstance(legacy, str) and legacy.strip():
                    raw = legacy.strip()
            if raw is not _MISSING and raw is not None:
                entries.append((layer.name, raw))
        return entries
    raw = _get_raw(settings.data, path)
    if raw is _MISSING and path == "effortLevel":
        legacy = settings.data.get("effort")
        if isinstance(legacy, str) and legacy.strip():
            raw = legacy.strip()
    if raw is not _MISSING and raw is not None:
        return [(settings.provenance.get(path, "user"), raw)]
    return []


def _try_validate(key: SettingKey, raw: Any) -> tuple[bool, Any]:
    try:
        return True, _validate_value(key, raw, allow_none_if_nullable=False)
    except ValueError:
        return False, None


def _is_effort_choice(key: SettingKey) -> bool:
    return key.type == "choice" and key.choices is not None and all(choice in EFFORT_RANK for choice in key.choices)


def _valid_int_map_entries(key: SettingKey, raw: Any) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    if not isinstance(raw, Mapping):
        return cleaned
    for raw_k, raw_v in raw.items():
        if not isinstance(raw_k, str) or not raw_k.strip():
            continue
        if isinstance(raw_v, bool) or not isinstance(raw_v, int):
            continue
        if key.value_minimum is not None and raw_v < key.value_minimum:
            continue
        if key.value_maximum is not None and raw_v > key.value_maximum:
            continue
        cleaned[raw_k.strip()] = int(raw_v)
    return cleaned


def resolve_window_overrides_split(
    settings: EffectiveSettings,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Split ``context.windowOverrides`` into ``(operator, project, managed)`` maps.

    - ``operator``: overrides explicitly set by the operator (``user`` / ``cli``).
    - ``project``: overrides from repository layers (``project`` / ``local``), which may only lower.
    - ``managed``: organisation policy overrides, which always take precedence.
    """
    key = REGISTRY.get("context.windowOverrides")
    if key is None:
        return {}, {}, {}

    operator_map: dict[str, int] = {}
    project_map: dict[str, int] = {}
    managed_map: dict[str, int] = {}

    for layer_name, raw in _layer_entries(settings, "context.windowOverrides"):
        cleaned = _valid_int_map_entries(key, raw)
        if not cleaned:
            continue
        if layer_name == "managed":
            managed_map.update(cleaned)
        elif layer_name in {"user", "cli"}:
            operator_map.update(cleaned)
        elif layer_name in {"project", "local"}:
            for map_key, val in cleaned.items():
                if map_key not in project_map or val < project_map[map_key]:
                    project_map[map_key] = val

    return operator_map, project_map, managed_map


def _resolve_map_lower_only(key: SettingKey, entries: list[tuple[str, Any]]) -> ResolvedSetting:
    managed_values: dict[str, int] = {}
    best_values: dict[str, int] = {}
    best_layers: dict[str, str] = {}
    for layer_name, raw in entries:
        cleaned = _valid_int_map_entries(key, raw)
        if not cleaned:
            continue
        if layer_name == "managed":
            for map_key, raw_v in cleaned.items():
                managed_values[map_key] = raw_v
            continue
        for map_key, raw_v in cleaned.items():
            if map_key not in best_values:
                best_values[map_key] = raw_v
                best_layers[map_key] = layer_name
            elif raw_v < best_values[map_key]:
                best_values[map_key] = raw_v
                best_layers[map_key] = layer_name
            elif raw_v == best_values[map_key] and layer_name in {"cli", "managed"}:
                best_layers[map_key] = layer_name

    # Managed layer ALWAYS wins for every map subkey it defines.
    for map_key, managed_val in managed_values.items():
        best_values[map_key] = managed_val
        best_layers[map_key] = "managed"

    if not best_values:
        return ResolvedSetting(copy.deepcopy(key.default), "default", copy.deepcopy(key.default))

    winning_layer = max(best_layers.values(), key=lambda name: _LAYER_PRECEDENCE.get(name, -1))
    return ResolvedSetting(best_values, winning_layer, copy.deepcopy(key.default))


def _resolve_scalar_lower_only(key: SettingKey, entries: list[tuple[str, Any]]) -> ResolvedSetting:
    use_effort = _is_effort_choice(key)

    def _rank(val: Any) -> int | float:
        return EFFORT_RANK[val] if use_effort else val

    effective: Any = None
    winning_layer = "default"
    has_active = False

    # Collapse duplicate files in the same layer (later file in same layer wins within that layer)
    per_layer: dict[str, Any] = {}
    for layer_name, raw in entries:
        ok, valid = _try_validate(key, raw)
        if ok:
            per_layer[layer_name] = valid

    # Managed policy ALWAYS wins over user/project/local/cli on PROJECT_LOWER_ONLY keys.
    if "managed" in per_layer:
        return ResolvedSetting(per_layer["managed"], "managed", copy.deepcopy(key.default))

    for layer_name in LAYER_ORDER:
        if layer_name == "managed" or layer_name not in per_layer:
            continue
        candidate = per_layer[layer_name]
        if not has_active:
            effective = candidate
            winning_layer = layer_name
            has_active = True
            continue
        if _rank(candidate) < _rank(effective):
            effective = candidate
            winning_layer = layer_name
        elif _rank(candidate) == _rank(effective) and layer_name == "cli":
            winning_layer = layer_name

    if not has_active:
        return ResolvedSetting(copy.deepcopy(key.default), "default", copy.deepcopy(key.default))
    return ResolvedSetting(effective, winning_layer, copy.deepcopy(key.default))


def resolve(settings: EffectiveSettings, path: str) -> ResolvedSetting:
    key = REGISTRY.get(path)
    if key is None:
        raise KeyError(f"Configuração desconhecida: {path}")

    entries = _layer_entries(settings, path)
    default_copy = copy.deepcopy(key.default)

    if key.scope in {Scope.USER_ONLY, Scope.AGENT_FILE}:
        allowed_layers = {"user", "cli", "managed"}
        effective = default_copy
        winning_layer = "default"
        for layer_name, raw in entries:
            if layer_name not in allowed_layers:
                continue
            ok, valid = _try_validate(key, raw)
            if not ok:
                continue
            if key.type == "map" and isinstance(key.default, dict) and key.default:
                base = dict(effective) if isinstance(effective, dict) else copy.deepcopy(key.default)
                base.update(valid)
                effective = base
            else:
                effective = valid
            winning_layer = layer_name
        return ResolvedSetting(effective, winning_layer, copy.deepcopy(key.default))

    if key.scope is Scope.PROJECT_OFF_ONLY:
        effective = default_copy
        winning_layer = "default"
        for layer_name, raw in entries:
            ok, valid = _try_validate(key, raw)
            if not ok:
                continue
            if layer_name in {"project", "local"}:
                if valid is False:
                    effective = False
                    winning_layer = layer_name
            else:
                effective = valid
                winning_layer = layer_name
        return ResolvedSetting(effective, winning_layer, copy.deepcopy(key.default))

    if key.scope is Scope.PROJECT_LOWER_ONLY:
        if key.type == "map" and key.item_type == "number":
            return _resolve_map_lower_only(key, entries)
        return _resolve_scalar_lower_only(key, entries)

    # Scope.ANY
    effective = default_copy
    winning_layer = "default"
    for layer_name, raw in entries:
        ok, valid = _try_validate(key, raw)
        if not ok:
            continue
        if key.type == "map" and isinstance(key.default, dict) and key.default:
            base = dict(effective) if isinstance(effective, dict) else copy.deepcopy(key.default)
            base.update(valid)
            effective = base
        else:
            effective = valid
        winning_layer = layer_name
    return ResolvedSetting(effective, winning_layer, copy.deepcopy(key.default))


# Register built-in Package E, Messages and Package D memory keys on module import.
from . import keys_effort_quota as _keys_effort_quota  # noqa: E402, F401
from . import keys_memory as _keys_memory  # noqa: E402, F401
from . import keys_messages as _keys_messages  # noqa: E402, F401

__all__ = [
    "EFFORT_LEVELS",
    "EFFORT_RANK",
    "REGISTRY",
    "ResolvedSetting",
    "Scope",
    "SettingKey",
    "register",
    "resolve",
    "resolve_window_overrides_split",
    "validate_change",
]

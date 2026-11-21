"""Layered settings: managed > CLI > local > project > user, with Claude Code interop."""

from .loader import (
    LAYER_ORDER,
    EffectiveSettings,
    SettingsError,
    SettingsLayer,
    empty_settings,
    load_effective_settings,
)
from .merge import merge_settings
from .paths import SettingsPaths
from .registry import REGISTRY, ResolvedSetting, Scope, SettingKey, register, resolve, validate_change
from .watcher import detect_changes
from .writer import SettingsWriteError, add_permission_rule, update_settings_file

__all__ = [
    "LAYER_ORDER",
    "REGISTRY",
    "EffectiveSettings",
    "ResolvedSetting",
    "Scope",
    "SettingKey",
    "SettingsError",
    "SettingsLayer",
    "SettingsPaths",
    "SettingsWriteError",
    "add_permission_rule",
    "detect_changes",
    "empty_settings",
    "load_effective_settings",
    "merge_settings",
    "register",
    "resolve",
    "update_settings_file",
    "validate_change",
]

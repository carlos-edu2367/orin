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

__all__ = [
    "LAYER_ORDER",
    "EffectiveSettings",
    "SettingsError",
    "SettingsLayer",
    "SettingsPaths",
    "empty_settings",
    "load_effective_settings",
    "merge_settings",
]

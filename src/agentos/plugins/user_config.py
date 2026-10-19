"""A plugin's options (``userConfig``, W5): the values a person gives a plugin, kept encrypted per person.

Every value is stored encrypted (``plugin_settings.values_ciphertext``, Fernet, the key Orin already uses for
provider secrets); a sensitive one is never returned, only whether it is set. ``${user_config.KEY}`` in a plugin's
MCP server command, arguments or URL is replaced when the plugin is activated; a sensitive option may not be written
into a command line (it would be stored in the clear with the server).
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import plugin_settings

MAX_OPTION_CHARS = 4_000
# The cipher refuses more than this much plaintext, and every option of a plugin is stored together in one value.
MAX_STORED_CHARS = 4_096
MASK = "********"
_REFERENCE = re.compile(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]{0,63})\}")


class PluginConfigError(ValueError):
    """Option values that do not fit the plugin's declared options."""


def _cipher():
    from agentos.persistence.provider_secrets import ProviderSecretCipher

    return ProviderSecretCipher.from_environment(required=True)


def referenced_options(value: str) -> list[str]:
    return _REFERENCE.findall(str(value or ""))


def substitute_options(value: str, values: Mapping[str, str], sensitive: frozenset[str]) -> str:
    """``value`` with each ``${user_config.KEY}`` replaced; refuses a sensitive or unset option."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in sensitive:
            raise PluginConfigError(f"the sensitive option {key} cannot be written into a command line or URL")
        if key not in values:
            raise PluginConfigError(f"the plugin option {key} is not set")
        return str(values[key])

    return _REFERENCE.sub(replace, str(value))


class PluginConfigStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        plugin_settings.create(engine, checkfirst=True)

    def values(self, user_id: str, plugin_id: str) -> dict[str, str]:
        with self._engine.connect() as connection:
            row = connection.execute(select(plugin_settings.c.values_ciphertext).where(
                plugin_settings.c.user_id == user_id, plugin_settings.c.plugin_id == plugin_id,
            )).first()
        if row is None or not row[0]:
            return {}
        loaded = json.loads(_cipher().decrypt(str(row[0])))
        return {str(key): str(value) for key, value in loaded.items()} if isinstance(loaded, dict) else {}

    def update(self, user_id: str, plugin_id: str, changes: Mapping[str, Any], options: Mapping[str, Any]) -> dict[str, str]:
        """Apply ``changes`` (``None`` or ``""`` clears an option) and return all values. Unknown options are refused."""
        unknown = sorted(set(changes) - set(options))
        if unknown:
            raise PluginConfigError(f"the plugin has no option named {unknown[0][:64]}")
        current = self.values(user_id, plugin_id)
        for key, value in changes.items():
            if value is None or value == "":
                current.pop(key, None)
            elif isinstance(value, (str, int, float, bool)):
                text = str(value).lower() if isinstance(value, bool) else str(value)
                if len(text) > MAX_OPTION_CHARS:
                    raise PluginConfigError(f"the value of {key} is longer than {MAX_OPTION_CHARS} characters")
                current[key] = text
            else:
                raise PluginConfigError(f"the value of {key} must be text, a number or true/false")
        payload = json.dumps(current)
        if len(payload) > MAX_STORED_CHARS:
            raise PluginConfigError("the plugin's options are too large to store together; shorten a value")
        ciphertext = _cipher().encrypt(payload, allow_empty=True)
        with self._engine.begin() as connection:
            connection.execute(delete(plugin_settings).where(plugin_settings.c.user_id == user_id, plugin_settings.c.plugin_id == plugin_id))
            connection.execute(insert(plugin_settings).values(
                user_id=user_id, plugin_id=plugin_id, values_ciphertext=ciphertext, updated_at=datetime.now(UTC),
            ))
        return current

    def remove(self, user_id: str, plugin_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(plugin_settings).where(plugin_settings.c.user_id == user_id, plugin_settings.c.plugin_id == plugin_id))


def public_view(options: Mapping[str, Any], values: Mapping[str, str]) -> list[dict[str, Any]]:
    """The options for a screen: a sensitive value is never shown, only whether it is set."""
    shown: list[dict[str, Any]] = []
    for key, option in options.items():
        set_value = values.get(key)
        shown.append({
            "key": key, "title": option.title, "description": option.description, "sensitive": option.sensitive,
            "required": option.required, "is_set": set_value is not None,
            "value": (MASK if option.sensitive else set_value) if set_value is not None else None,
        })
    return shown


__all__ = [
    "MASK", "PluginConfigError", "PluginConfigStore", "public_view", "referenced_options", "substitute_options",
]

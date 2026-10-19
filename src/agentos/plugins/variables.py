"""The variables a plugin's MCP servers and hooks may name (W5): its folder and its own data folder.

``${CLAUDE_PLUGIN_ROOT}`` / ``${ORIN_PLUGIN_ROOT}`` is the installed package (replaced on every update);
``${CLAUDE_PLUGIN_DATA}`` / ``${ORIN_PLUGIN_DATA}`` is a folder that survives updates, one per person and plugin.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path


def plugin_data_dir(user_id: str, plugin_id: str) -> Path:
    from agentos.settings.paths import orin_user_dir

    person = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
    return orin_user_dir() / "plugin-data" / person / str(plugin_id)


def plugin_variables(*, install_path: Path, plugin_id: str, user_id: str) -> dict[str, str]:
    root, data = str(Path(install_path)), str(plugin_data_dir(user_id, plugin_id))
    return {"CLAUDE_PLUGIN_ROOT": root, "ORIN_PLUGIN_ROOT": root, "CLAUDE_PLUGIN_DATA": data, "ORIN_PLUGIN_DATA": data}


def substitute(value: str, variables: Mapping[str, str]) -> str:
    text = str(value)
    for name, replacement in variables.items():
        text = text.replace("${" + name + "}", replacement)
    return text


__all__ = ["plugin_data_dir", "plugin_variables", "substitute"]

"""Language servers a plugin declares (W5): ``.lsp.json`` at the plugin's root, or ``lspServers`` in its manifest.

The Claude Code shape: ``{"name": {"command", "args", "extensionToLanguage": {".py": "python"}, "env",
"initializationOptions", "settings", "startupTimeout"}}``. A plugin's language server is a program it runs, so it
starts only for a plugin whose hooks the person allowed (the same consent as running its code) and only on an Orin
that runs plugin hooks. ``${CLAUDE_PLUGIN_ROOT}``/``${ORIN_PLUGIN_ROOT}`` and the data folder may be named.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentos.plugins.manifest import manifest_file
from agentos.plugins.runtime import ActivePlugin
from agentos.plugins.variables import plugin_variables, substitute

MAX_SERVERS = 16
DEFAULT_STARTUP_SECONDS = 10.0
_EXTENSION = re.compile(r"\.[A-Za-z0-9_+-]{1,16}")


@dataclass(frozen=True, slots=True)
class LspServerConfig:
    name: str
    plugin_id: str
    command: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    extensions: tuple[tuple[str, str], ...] = ()  # (".py", "python")
    initialization_options: Any = None
    settings: Any = None
    startup_timeout: float = DEFAULT_STARTUP_SECONDS

    def language_for(self, path: Path) -> str | None:
        suffix = Path(path).suffix.lower()
        return next((language for extension, language in self.extensions if extension == suffix), None)


def _declared(plugin: ActivePlugin) -> tuple[Mapping[str, Any] | None, str]:
    """The plugin's language servers: ``lspServers`` in the manifest (an object, or a file inside the package),
    else ``.lsp.json`` at its root."""
    target = manifest_file(plugin.install_path)
    raw: Any = None
    if target is not None:
        try:
            raw = json.loads(target.read_text(encoding="utf-8")).get("lspServers")
        except (OSError, ValueError, AttributeError):
            raw = None
    if isinstance(raw, str):
        candidate = (plugin.install_path / raw).resolve()
        if not candidate.is_relative_to(plugin.install_path.resolve()):
            return None, "lspServers names a file outside the plugin"
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "the lspServers file could not be read"
    if raw is None:
        file = plugin.install_path / ".lsp.json"
        if not file.is_file():
            return None, ""
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, ".lsp.json could not be read"
    return (raw, "") if isinstance(raw, Mapping) else (None, "the language servers must be an object")


def lsp_servers(plugins: Sequence[ActivePlugin], *, user_id: str) -> tuple[list[LspServerConfig], list[str]]:
    """``(the language servers to offer, what was skipped and why)``."""
    servers: list[LspServerConfig] = []
    problems: list[str] = []
    for plugin in plugins:
        declared, problem = _declared(plugin)
        if problem:
            problems.append(f"{plugin.plugin_id}: {problem}")
        if not declared:
            continue
        if not plugin.enabled_hook_ids:
            problems.append(f"{plugin.plugin_id}: its language servers wait for the person to allow the plugin to run code")
            continue
        variables = plugin_variables(install_path=plugin.install_path, plugin_id=plugin.plugin_id, user_id=user_id)
        for name, spec in declared.items():
            label = f"{plugin.plugin_id}:{str(name)[:64]}"
            if not isinstance(spec, Mapping) or not isinstance(spec.get("command"), str) or not spec["command"].strip():
                problems.append(f"{label}: a language server needs a command")
                continue
            mapping = spec.get("extensionToLanguage")
            extensions = tuple(
                (str(extension).lower(), str(language)) for extension, language in (mapping.items() if isinstance(mapping, Mapping) else ())
                if isinstance(extension, str) and _EXTENSION.fullmatch(extension) and isinstance(language, str) and language.strip()
            )
            if not extensions:
                problems.append(f"{label}: extensionToLanguage must map file extensions (\".py\") to languages")
                continue
            args = spec.get("args") if isinstance(spec.get("args"), list) else []
            env = spec.get("env") if isinstance(spec.get("env"), Mapping) else {}
            timeout = spec.get("startupTimeout")
            servers.append(LspServerConfig(
                name=label, plugin_id=plugin.plugin_id, command=substitute(spec["command"].strip(), variables),
                args=tuple(substitute(str(item), variables) for item in args if isinstance(item, (str, int, float))),
                env=tuple(sorted((str(key), substitute(str(value), variables)) for key, value in env.items() if isinstance(value, (str, int, float)))),
                extensions=extensions, initialization_options=spec.get("initializationOptions"), settings=spec.get("settings"),
                startup_timeout=max(1.0, min(float(timeout) / 1000.0, 120.0)) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else DEFAULT_STARTUP_SECONDS,
            ))
            if len(servers) >= MAX_SERVERS:
                return servers, problems
    return servers, problems


__all__ = ["LspServerConfig", "lsp_servers"]

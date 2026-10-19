"""Language servers a plugin declares (W5): ``.lsp.json`` at the plugin's root, or ``lspServers`` in its manifest.

The Claude Code shape: ``{"name": {"command", "args", "extensionToLanguage": {".py": "python"}, "env",
"initializationOptions", "settings", "startupTimeout"}}``. A plugin's language server is a program it runs, so each one
starts only once the person allowed that exact entry (W5-R6: the approval card shows it, and the consent is keyed by its
name and a digest of the entry), and only on an Orin that runs plugin hooks. ``${CLAUDE_PLUGIN_ROOT}``/``${ORIN_PLUGIN_ROOT}``
and the data folder may be named.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentos.plugins.manifest import manifest_file
from agentos.plugins.models import LspServerContribution
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


def _declared(install_path: Path) -> tuple[Mapping[str, Any] | None, str]:
    """The plugin's language servers: ``lspServers`` in the manifest (an object, or a file inside the package),
    else ``.lsp.json`` at its root."""
    target = manifest_file(install_path)
    raw: Any = None
    if target is not None:
        try:
            raw = json.loads(target.read_text(encoding="utf-8")).get("lspServers")
        except (OSError, ValueError, AttributeError):
            raw = None
    if isinstance(raw, str):
        candidate = (install_path / raw).resolve()
        if not candidate.is_relative_to(install_path.resolve()):
            return None, "lspServers names a file outside the plugin"
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "the lspServers file could not be read"
    if raw is None:
        file = install_path / ".lsp.json"
        if not file.is_file():
            return None, ""
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, ".lsp.json could not be read"
    return (raw, "") if isinstance(raw, Mapping) else (None, "the language servers must be an object")


def declared_servers(install_path: Path) -> tuple[tuple[LspServerContribution, ...], tuple[str, ...]]:
    """``(the valid language servers the package declares, the problems with the others)``.

    The one reading of a plugin's language servers: the approval card shows it, the consent is keyed by it, and a server
    starts only from it. The values are as declared; ``${...}`` variables are resolved only when a server starts.
    """
    install_path = Path(install_path)
    declared, problem = _declared(install_path)
    problems: list[str] = [problem] if problem else []
    servers: list[LspServerContribution] = []
    for name, spec in (declared or {}).items():
        label = str(name)[:64]
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
        startup = max(1.0, min(float(timeout) / 1000.0, 120.0)) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else DEFAULT_STARTUP_SECONDS
        digest = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        servers.append(LspServerContribution(
            name=label, command=spec["command"].strip(),
            args=tuple(str(item) for item in args if isinstance(item, (str, int, float))),
            extensions=extensions,
            env=tuple(sorted((str(key), str(value)) for key, value in env.items() if isinstance(value, (str, int, float)))),
            initialization_options=spec.get("initializationOptions"), settings=spec.get("settings"),
            startup_timeout=startup, digest=digest,
        ))
        if len(servers) >= MAX_SERVERS:
            problems.append(f"more than {MAX_SERVERS} language servers; the rest are ignored")
            break
    return tuple(servers), tuple(problems)


def lsp_servers(plugins: Sequence[ActivePlugin], *, user_id: str) -> tuple[list[LspServerConfig], list[str]]:
    """``(the language servers to offer, what was skipped and why)``. A server starts only when the person allowed its
    exact entry (its name and digest, ``enabled_lsp_ids``)."""
    servers: list[LspServerConfig] = []
    problems: list[str] = []
    for plugin in plugins:
        declared, plugin_problems = declared_servers(plugin.install_path)
        problems.extend(f"{plugin.plugin_id}: {problem}" for problem in plugin_problems)
        if not declared:
            continue
        variables = plugin_variables(install_path=plugin.install_path, plugin_id=plugin.plugin_id, user_id=user_id)
        for item in declared:
            label = f"{plugin.plugin_id}:{item.name}"
            if item.consent_reference not in plugin.enabled_lsp_ids:
                problems.append(f"{label}: its language server waits for the person to allow it")
                continue
            servers.append(LspServerConfig(
                name=label, plugin_id=plugin.plugin_id, command=substitute(item.command, variables),
                args=tuple(substitute(value, variables) for value in item.args),
                env=tuple((key, substitute(value, variables)) for key, value in item.env),
                extensions=item.extensions, initialization_options=item.initialization_options, settings=item.settings,
                startup_timeout=item.startup_timeout,
            ))
            if len(servers) >= MAX_SERVERS:
                return servers, problems
    return servers, problems


__all__ = ["LspServerConfig", "declared_servers", "lsp_servers"]

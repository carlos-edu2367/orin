"""A plugin's hooks as hook specs (W5): they run in the same dispatcher as the settings hooks.

A plugin's ``hooks/hooks.json`` (or the folder its manifest names) uses the Claude Code schema; every event Orin
knows may be hooked. A hook runs only if the person allowed it: the plugins screen's consent enables the hooks it
listed, by id and a digest of what each runs, so a hook added or changed by a later version waits for a new consent.
Only ``command`` hooks run; each runs without a shell, confined to the plugin's folder
(``plugins.hook_executor.resolve_argv``), in the project folder, with a minimal environment plus
``CLAUDE_PLUGIN_ROOT``/``ORIN_PLUGIN_ROOT``, the plugin's data folder and its options as
``CLAUDE_PLUGIN_OPTION_<KEY>``/``ORIN_PLUGIN_OPTION_<KEY>``. A hook may name a non-sensitive option
in its command (``${user_config.KEY}``); a sensitive one only reaches it through the environment.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from agentos.agentic.lifecycle import HookEvent
from agentos.hooks.config import HookConfigError, HookSpec

from .hooks_manifest import parse_hooks
from .runtime import ActivePlugin
from .user_config import PluginConfigError, substitute_options
from .variables import plugin_variables

MAX_PLUGIN_HOOKS = 64


def plugin_hook_specs(
    plugins: Sequence[ActivePlugin], *, user_id: str,
    option_values: Callable[[str], Mapping[str, str]] | None = None,
) -> tuple[tuple[HookSpec, ...], tuple[HookConfigError, ...]]:
    specs: list[HookSpec] = []
    errors: list[HookConfigError] = []
    for plugin in plugins:
        if not plugin.enabled_hook_ids:
            continue
        source = f"plugin:{plugin.plugin_id}"
        contributions, warnings = parse_hooks(plugin.install_path / plugin.manifest.hooks_path, plugin_id=plugin.plugin_id)
        errors.extend(HookConfigError(source, warning) for warning in warnings)
        try:
            options = dict(option_values(plugin.plugin_id)) if option_values is not None else {}
        except Exception:  # noqa: BLE001 - unreadable options: the hooks run without them
            errors.append(HookConfigError(source, "the plugin's options could not be read"))
            options = {}
        sensitive = frozenset(item.key for item in plugin.manifest.user_config if item.sensitive)
        environment = dict(plugin_variables(install_path=plugin.install_path, plugin_id=plugin.plugin_id, user_id=user_id))
        for key, value in options.items():
            environment[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = value
            environment[f"ORIN_PLUGIN_OPTION_{key.upper()}"] = value
        for item in contributions:
            if item.consent_reference not in plugin.enabled_hook_ids:
                errors.append(HookConfigError(source, f"hook {item.hook_id} waits for the person's consent"))
                continue
            try:
                event = HookEvent(item.event)
                command = substitute_options(item.command, options, sensitive)
            except (ValueError, PluginConfigError) as error:
                errors.append(HookConfigError(source, f"hook {item.hook_id} skipped: {error}"))
                continue
            specs.append(HookSpec(
                event=event, matcher=item.matcher, type="command", source=source, timeout=float(item.timeout_seconds),
                command=command, run_async=item.run_async,
                plugin_root=str(plugin.install_path), plugin_env=tuple(sorted(environment.items())),
            ))
    if len(specs) > MAX_PLUGIN_HOOKS:
        errors.append(HookConfigError("plugins", f"more than {MAX_PLUGIN_HOOKS} plugin hooks; the rest are ignored"))
    return tuple(specs[:MAX_PLUGIN_HOOKS]), tuple(errors)


__all__ = ["plugin_hook_specs"]

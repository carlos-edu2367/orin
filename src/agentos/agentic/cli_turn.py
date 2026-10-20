"""How a turn started by ``orin code`` differs from one started in the interface (W6).

The session reads the turn's stored CLI options (``agentos.code_mode.cli_options``) once and asks this module what
they mean: the "cli" settings layer, which tools are offered, and how much of a tool's input and result travels on
the turn's events so the CLI can show them as Claude Code's ``tool_use`` and ``tool_result`` blocks.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any

from agentos.code_mode.cli_options import CliTurnOptions, load_cli_options

from .tools.public_names import internal_name, public_name

# The tool input and result each tool event may carry for the CLI (the model saw at most MAX_TOOL_RESULT_CHARS).
TRANSCRIPT_LIMIT = 65536
_TRUNCATED = "\n[truncated by Orin]"


def cli_options_for(turn: Mapping[str, Any]) -> CliTurnOptions | None:
    """The turn's CLI options, or None for an interface turn; raises ``CliOptionsError`` for a corrupt value."""
    return load_cli_options(turn.get("cli_options"))


def _bare_tool_names(rules: object) -> list[str]:
    """``["Bash", "Edit(src/**)", "WebFetch"]`` -> the rules that name a whole tool (no specifier)."""
    if not isinstance(rules, list):
        return []
    return [str(rule).strip() for rule in rules if isinstance(rule, str) and rule.strip() and "(" not in rule]


def tool_selection(options: CliTurnOptions | None, core: Collection[str]) -> tuple[frozenset[str] | None, frozenset[str]]:
    """``(offered, withheld)`` internal tool names: ``--tools`` keeps only the named tools; a bare ``--disallowedTools``
    entry removes its tool from what the model sees (its deny rule refuses the call either way)."""
    if options is None:
        return None, frozenset()
    withheld: set[str] = set()
    permissions = options.settings.get("permissions") if isinstance(options.settings, Mapping) else None
    if isinstance(permissions, Mapping):
        withheld.update(internal_name(name) for name in _bare_tool_names(permissions.get("deny")))
    offered: frozenset[str] | None = None
    if options.tools is not None:
        offered = frozenset(internal_name(name) for name in options.tools)
        withheld.update(name for name in core if name not in offered)
    return offered, frozenset(withheld)


def cli_settings_layer(options: CliTurnOptions | None, core: Collection[str]) -> dict[str, Any] | None:
    """The "cli" settings layer for the turn; a tool left out by ``--tools`` is also denied, so a call to it is refused."""
    if options is None:
        return None
    layer = json.loads(json.dumps(options.settings))
    _, withheld = tool_selection(options, core)
    if options.tools is not None and withheld:
        permissions = layer.setdefault("permissions", {})
        if not isinstance(permissions, dict):
            permissions = layer["permissions"] = {}
        deny = permissions.setdefault("deny", [])
        if not isinstance(deny, list):
            deny = permissions["deny"] = []
        for name in sorted(withheld):
            rule = public_name(name)
            if rule not in deny:
                deny.append(rule)
    return layer or None


def capped_text(value: object, limit: int = TRANSCRIPT_LIMIT) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text if len(text) <= limit else text[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED


def capped_input(value: object, limit: int = TRANSCRIPT_LIMIT) -> dict[str, Any]:
    """A tool's arguments for the CLI, or ``{"_truncated": <json prefix>}`` when they are too large to repeat."""
    if not isinstance(value, Mapping):
        return {}
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(encoded) <= limit:
        return json.loads(encoded)
    return {"_truncated": encoded[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED}


__all__ = ["TRANSCRIPT_LIMIT", "capped_input", "capped_text", "cli_options_for", "cli_settings_layer", "tool_selection"]

"""From ``orin code`` flags to the options a turn carries (``CliTurnOptions``) and the conversation's permission mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.code_mode.cli_options import CliTurnOptions
from agentos.code_mode.models import normalize_permission_mode

from .args import CliUsageError, CodeOptions

# Effort budgets (agentos.agentic.effort.THINKING_BUDGET), smallest first: a thinking budget becomes the smallest
# effort level that holds it.
_EFFORT_BY_BUDGET: tuple[tuple[int, str], ...] = ((2_048, "low"), (6_000, "medium"), (12_000, "high"), (20_000, "xhigh"), (28_000, "max"))
_MAX_FILE_BYTES = 65536


def _read_text_file(path: str, cwd: Path, flag: str) -> str:
    target = Path(path).expanduser()
    target = target if target.is_absolute() else cwd / target
    try:
        data = target.read_bytes()
    except OSError as error:
        raise CliUsageError(f"{flag}: {target} could not be read ({error.strerror or error})") from error
    if len(data) > _MAX_FILE_BYTES:
        raise CliUsageError(f"{flag}: {target} is larger than {_MAX_FILE_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def settings_flag(value: str | None, cwd: Path) -> dict[str, Any]:
    """``--settings``: a JSON object, or the path of a JSON file."""
    if value is None or not value.strip():
        return {}
    text = value.strip()
    if not text.startswith("{"):
        text = _read_text_file(text, cwd, "--settings")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CliUsageError("--settings is neither a JSON object nor a JSON file") from error
    if not isinstance(data, dict):
        raise CliUsageError("--settings must be a JSON object")
    return data


def _effort(options: CodeOptions) -> str | None:
    if options.effort:
        return options.effort
    if options.thinking == "disabled":
        return None
    budget = options.max_thinking_tokens
    if budget is not None and budget > 0:
        return next((level for limit, level in _EFFORT_BY_BUDGET if budget <= limit), "max")
    return None


def _autocompact(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"off", "false", "0", "no"}:
        return False
    if text in {"auto", "on", "true"}:
        return "auto"
    if text.isdigit() and int(text) > 0:
        return int(text)
    raise CliUsageError("--autocompact takes auto, off or a token count")


def _extend(layer: dict[str, Any], dotted: str, items: list[str]) -> None:
    if not items:
        return
    head, _, tail = dotted.partition(".")
    section = layer.setdefault(head, {})
    if not isinstance(section, dict):
        section = layer[head] = {}
    current = section.setdefault(tail, [])
    if not isinstance(current, list):
        current = section[tail] = []
    for item in items:
        if item not in current:
            current.append(item)


def mcp_config_flag(values: list[str], cwd: Path) -> dict[str, Any]:
    """``--mcp-config`` (repeatable): JSON objects or JSON files, each ``{"mcpServers": {...}}``; later ones win."""
    servers: dict[str, Any] = {}
    for value in values:
        text = value.strip()
        if not text:
            continue
        if not text.startswith("{"):
            text = _read_text_file(text, cwd, "--mcp-config")
        try:
            data = json.loads(text)
        except ValueError as error:
            raise CliUsageError("--mcp-config is neither a JSON object nor a JSON file") from error
        found = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(found, dict):
            raise CliUsageError("--mcp-config needs an object with \"mcpServers\"")
        servers.update(found)
    return servers


def agents_flag(value: str | None, cwd: Path) -> dict[str, Any]:
    """``--agents``: a JSON object of agents, or the path of a JSON file holding one."""
    if value is None or not value.strip():
        return {}
    text = value.strip()
    if not text.startswith("{"):
        text = _read_text_file(text, cwd, "--agents")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CliUsageError("--agents is neither a JSON object nor a JSON file") from error
    if not isinstance(data, dict):
        raise CliUsageError("--agents must be a JSON object")
    return data


def _refuse_host_needs(layer: dict[str, Any], servers: dict[str, Any]) -> None:
    """Without an SDK host (input not stream-json), a ``type: sdk`` server or a ``callback`` hook can never be answered."""
    if any(isinstance(server, dict) and server.get("type") == "sdk" for server in servers.values()):
        raise CliUsageError("--mcp-config has a type sdk server, which needs an SDK host: use --input-format stream-json")
    hooks = layer.get("hooks")
    for groups in (hooks.values() if isinstance(hooks, dict) else ()):
        for group in (groups if isinstance(groups, list) else ()):
            entries = group.get("hooks") if isinstance(group, dict) else None
            for hook in (entries if isinstance(entries, list) else ()):
                if isinstance(hook, dict) and hook.get("type") == "callback":
                    raise CliUsageError("--settings has a callback hook, which needs an SDK host: use --input-format stream-json")


def build_turn_options(options: CodeOptions, cwd: Path, *, host_answers_prompts: bool,
                       sdk_hooks: dict[str, list[dict[str, Any]]] | None = None,
                       sdk_agents: dict[str, Any] | None = None, first_turn: bool = False) -> CliTurnOptions:
    """The "cli" settings layer (``--settings`` first, the explicit flags over it) and the headless switches."""
    layer = settings_flag(options.settings, cwd)
    servers = mcp_config_flag(options.mcp_configs, cwd)
    if options.input_format != "stream-json":
        _refuse_host_needs(layer, servers)  # W6-R11: an SDK server or a callback hook needs the SDK host on stream-json
    if sdk_hooks:
        # The SDK host's hook callbacks (initialize.hooks) join the cli layer's hooks, after --settings' own.
        hooks = layer.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = layer["hooks"] = {}
        for event, groups in sdk_hooks.items():
            current = hooks.setdefault(event, [])
            if isinstance(current, list):
                current.extend(groups)
    _extend(layer, "permissions.allow", options.allowed_tools)
    _extend(layer, "permissions.deny", options.disallowed_tools)
    _extend(layer, "permissions.additionalDirectories", [str((cwd / Path(item).expanduser()).resolve()) for item in options.add_dirs])
    if options.max_turns is not None:
        layer["maxTurns"] = options.max_turns
    if options.max_budget_usd is not None:
        layer["maxBudgetUsd"] = options.max_budget_usd
    if options.fallback_model:
        layer["fallbackModel"] = options.fallback_model
    effort = _effort(options)
    if effort:
        layer["effortLevel"] = effort
    compact = _autocompact(options.autocompact)
    if compact is not None:
        layer["autoCompact"] = compact
    if options.system_prompt_snapshot is not None:
        layer["systemPromptSnapshot"] = options.system_prompt_snapshot == "on"
    system_prompt = options.system_prompt
    if options.system_prompt_file:
        system_prompt = _read_text_file(options.system_prompt_file, cwd, "--system-prompt-file")
    append = options.append_system_prompt
    if options.append_system_prompt_file:
        appended = _read_text_file(options.append_system_prompt_file, cwd, "--append-system-prompt-file")
        append = f"{append}\n\n{appended}" if append else appended
    if options.json_schema:
        from .structured import load_schema, schema_instructions

        instructions = schema_instructions(load_schema(options.json_schema))
        append = f"{append}\n\n{instructions}" if append else instructions
    agents = {**agents_flag(options.agents, cwd), **(sdk_agents or {})}  # the host's definition wins over the flag's
    none = options.permission_prompts == "none" or not host_answers_prompts
    setup = ("init" if options.init else "maintenance" if options.maintenance else None) if first_turn else None
    try:
        return CliTurnOptions(
            settings=layer, setting_sources=options.setting_sources, system_prompt=system_prompt,
            append_system_prompt=append, tools=options.tools, permission_prompts="none" if none else "host",
            transcript=True, headless=True, trust_workspace=options.trust, agents=agents or None,
            mcp_servers=servers or None, strict_mcp_config=options.strict_mcp_config,
            setup=setup,
        )
    except ValueError as error:
        raise CliUsageError(f"the run's options are not valid: {error}") from error


def permission_mode_for(options: CodeOptions) -> str:
    """Orin's name for the run's mode. Claude Code's default is ``default`` (ask), not Orin Code's acceptEdits: every
    run sends its mode, so a resumed conversation never keeps a more permissive one by accident."""
    if options.dangerously_skip_permissions:
        return "full_autonomy"
    return normalize_permission_mode(options.permission_mode or "default").value


__all__ = ["build_turn_options", "permission_mode_for", "settings_flag"]

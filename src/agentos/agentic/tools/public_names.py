"""Claude Code–compatible public names and argument shapes for Orin's tools.

Orin's tools keep their snake_case keys (``read_file``) as stable ids. What the *model*,
permission rules and hooks see can already be the Claude Code surface (``Read`` with
``file_path``): this module converts between the two shapes at the model boundary.

Every conversion is idempotent: translating already-translated input is a
no-op, so a value that crosses the boundary twice is never corrupted.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from typing import Any

PUBLIC_NAMES: dict[str, str] = {
    # Claude Code core tools
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "notebook_edit": "NotebookEdit",
    "glob_files": "Glob",
    "search_files": "Grep",
    "run_command": "Bash",
    "powershell": "PowerShell",
    "read_process_output": "TaskOutput",
    "stop_process": "TaskStop",
    "todo_write": "TodoWrite",
    "task_create": "TaskCreate",
    "task_update": "TaskUpdate",
    "task_get": "TaskGet",
    "task_list": "TaskList",
    "ask_user": "AskUserQuestion",
    "enter_plan_mode": "EnterPlanMode",
    "exit_plan_mode": "ExitPlanMode",
    "enter_worktree": "EnterWorktree",
    "exit_worktree": "ExitWorktree",
    "fetch_url": "WebFetch",
    "web_search": "WebSearch",
    "run_subagent": "Agent",
    "use_skill": "Skill",
    "search_mcp_tools": "ToolSearch",
    "cron_create": "CronCreate",
    "cron_delete": "CronDelete",
    "cron_list": "CronList",
    "schedule_wakeup": "ScheduleWakeup",
    "monitor": "Monitor",
    "push_notification": "PushNotification",
    # Orin extras
    "list_files": "ListFiles",
    "view_file": "ViewFile",
    "transcribe_pdf": "TranscribePdf",
    "search_code": "SearchCode",
    "project_map": "ProjectMap",
    "verify_project": "VerifyProject",
    "preview_start": "PreviewStart",
    "preview_stop": "PreviewStop",
    "preview_logs": "PreviewLogs",
    "remember": "Remember",
    "recall": "Recall",
    "create_agent": "CreateAgent",
    "ask_agent": "AskAgent",
    "ask_agents": "AskAgents",
    "check_subagent": "CheckSubagent",
    "search_skills": "SearchSkills",
    "list_skills": "ListSkills",
    "read_skill_resource": "ReadSkillResource",
    "create_skill": "CreateSkill",
    "edit_skill": "EditSkill",
    "list_mcp_catalog": "ListMcpCatalog",
    "list_mcp_servers": "ListMcpServers",
    "configure_mcp": "ConfigureMcp",
    "test_mcp_server": "TestMcpServer",
    "inspect_mcp_server": "InspectMcpServer",
    "call_mcp_tool": "CallMcpTool",
    "search_plugin": "SearchPlugin",
    "inspect_plugin": "InspectPlugin",
    "install_plugin": "InstallPlugin",
    "list_plugins": "ListPlugins",
    "uninstall_plugin": "UninstallPlugin",
    "write_contract": "WriteContract",
    "report_verification": "ReportVerification",
    "propose_code_session": "ProposeCodeSession",
    "list_projects": "ListProjects",
    "create_project_from_session": "CreateProjectFromSession",
    "move_session_to_project": "MoveSessionToProject",
    "read_project_conversation": "ReadProjectConversation",
    "generate_image": "GenerateImage",
    "generate_audio": "GenerateAudio",
    "generate_video": "GenerateVideo",
}

_INTERNAL_BY_PUBLIC: dict[str, str] = {public: internal for internal, public in PUBLIC_NAMES.items()}
_INTERNAL_BY_LOWER_PUBLIC: dict[str, str] = {public.lower(): internal for internal, public in PUBLIC_NAMES.items()}

MAX_BASH_TIMEOUT_SECONDS = 600
_BASH_TASK_ID = re.compile(r"bash_([0-9]{1,18})")

# public key -> internal key, per internal tool
_RENAMES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path"},
    "write_file": {"file_path": "path"},
    "edit_file": {"file_path": "path", "old_string": "old_text", "new_string": "new_text"},
    "search_files": {
        "-A": "after_context", "-B": "before_context", "-C": "context_lines", "context": "context_lines",
        "-i": "ignore_case", "-n": "line_numbers", "-o": "only_matching",
    },
    "run_command": {"run_in_background": "background"},
    "powershell": {"run_in_background": "background"},
    "run_subagent": {"prompt": "task", "resume": "agent_id", "model": "model_id"},
    "use_skill": {"skill": "skill_id"},
    "search_mcp_tools": {"max_results": "limit"},
    "exit_plan_mode": {"plan": "summary", "allowedPrompts": "allowed_prompts"},
    "read_process_output": {"task_id": "pid"},
    "stop_process": {"task_id": "pid", "shell_id": "pid"},
    "exit_worktree": {"discard_changes": "force"},
    "schedule_wakeup": {"delaySeconds": "delay_seconds"},
}

# public keys Orin cannot honour yet (W4 implements them); dropped on the way in
_DROPPED: dict[str, frozenset[str]] = {
    "run_command": frozenset({"dangerouslyDisableSandbox"}),
}

_SUBAGENT_TYPES_IN = {
    "explore": "explore",
    "plan": "plan",
    "general-purpose": "general",
    "general": "general",
    "fork": "fork",
}
_SUBAGENT_TYPES_OUT = {"explore": "Explore", "plan": "Plan", "general": "general-purpose", "fork": "fork"}


def public_name(internal: str) -> str:
    if not internal or internal.startswith("mcp__"):
        return internal
    return PUBLIC_NAMES.get(internal, internal)


def internal_name(name: str) -> str:
    if not name or name.startswith("mcp__") or name in PUBLIC_NAMES:
        return name
    exact = _INTERNAL_BY_PUBLIC.get(name)
    if exact is not None:
        return exact
    return _INTERNAL_BY_LOWER_PUBLIC.get(name.lower(), name)


_UNDERSCORED_INTERNAL = tuple(sorted((name for name in PUBLIC_NAMES if "_" in name), key=len, reverse=True))
_INTERNAL_WORD = re.compile(
    r"(?<![\w./\\-])(" + "|".join(re.escape(name) for name in _UNDERSCORED_INTERNAL) + r")(?![\w-]|\.\w)"
)


def public_text(text: str) -> str:
    """Orin-authored guidance with internal tool ids (``read_file``) rewritten to public names (``Read``).

    Only for text Orin wrote itself. File contents, command output and repository text never pass
    through here: ``read_file(`` inside a source file must stay exactly as it is.
    """
    if not text or "_" not in text:
        return text
    return _INTERNAL_WORD.sub(lambda match: PUBLIC_NAMES.get(match.group(1), match.group(1)), text)


def public_subagent_type(internal: str) -> str:
    """The Claude Code name of a built-in subagent type (``explore`` -> ``Explore``), else unchanged."""
    return _SUBAGENT_TYPES_OUT.get(str(internal or "").strip().lower(), str(internal or ""))


def public_schema(internal: str) -> tuple[str, dict[str, Any]] | None:
    """The Claude Code (description, schema) of a tool: its registry Input model; None for Orin-only tools."""
    # Imported here: the registry imports every tool module, and they import this one.
    from agentos.agentic.tools.registry import default_registry

    spec = default_registry().get(internal)
    return spec.public_schema() if spec is not None and spec.key == internal else None


def public_argument_names(internal: str) -> frozenset[str] | None:
    entry = public_schema(internal)
    if entry is None:
        return None
    return frozenset(entry[1].get("properties", {}))


# Model-supplied numbers are untrusted: only a plain decimal literal of at most
# 18 digits is accepted (ASCII only, so "²" and 5000-digit strings are rejected).
_INTEGER_TEXT = re.compile(r"-?[0-9]{1,18}")
_EXACT_FLOAT_LIMIT = 2**53


def _as_int(value: Any) -> int | None:
    """Total conversion: any value the model can send yields an int or None, never an exception."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > _EXACT_FLOAT_LIMIT:
            return None
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_TEXT.fullmatch(text):
            return int(text)
    return None


def _options_list(raw: Mapping[str, Any]) -> list[Any]:
    options = raw.get("options")
    return options if isinstance(options, list) else []


def _ask_user_in(arguments: dict[str, Any]) -> dict[str, Any]:
    questions = arguments.get("questions")
    if not isinstance(questions, list):
        return arguments
    if all(isinstance(item, Mapping) and "mode" in item and "id" in item for item in questions):
        return arguments  # already Orin-shaped
    converted: list[dict[str, Any]] = []
    for index, raw in enumerate(questions, start=1):
        if not isinstance(raw, Mapping):
            converted.append(raw)  # let the tool's own validation report it
            continue
        options: list[dict[str, str]] = []
        for option_index, option in enumerate(_options_list(raw), start=1):
            if not isinstance(option, Mapping):
                continue
            entry = {"id": f"o{option_index}", "label": str(option.get("label") or "").strip()[:240]}
            detail = str(option.get("description") or "").strip()
            if detail:
                entry["description"] = detail[:240]
            preview = str(option.get("preview") or "")
            if preview.strip():
                entry["preview"] = preview
            options.append(entry)
        if not options:
            mode = "text"
        elif bool(raw.get("multiSelect")) or len(options) < 2:
            mode = "checkbox"
        else:
            mode = "single_choice"
        question = {"id": f"q{index}", "question": str(raw.get("question") or ""), "mode": mode, "options": options}
        header = str(raw.get("header") or "").strip()
        if header:
            question["header"] = header
        converted.append(question)
    return {**{k: v for k, v in arguments.items() if k != "questions"}, "questions": converted}


def _ask_user_out(arguments: dict[str, Any]) -> dict[str, Any]:
    questions = arguments.get("questions")
    if not isinstance(questions, list):
        return arguments
    out: list[Any] = []
    for raw in questions:
        if not isinstance(raw, Mapping) or "mode" not in raw:
            out.append(raw)
            continue
        out.append({
            "question": raw.get("question", ""),
            "header": str(raw.get("header") or raw.get("id", ""))[:12],
            "multiSelect": raw.get("mode") == "checkbox",
            "options": [
                {
                    "label": option.get("label", ""),
                    **({"description": option["description"]} if option.get("description") else {}),
                    **({"preview": option["preview"]} if option.get("preview") else {}),
                }
                for option in _options_list(raw)
                if isinstance(option, Mapping)
            ],
        })
    return {**{k: v for k, v in arguments.items() if k != "questions"}, "questions": out}


def arguments_in(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Public (Claude Code) argument shape -> Orin's internal shape."""
    result: dict[str, Any] = dict(arguments or {})
    public_task_call = internal == "read_process_output" and "task_id" in (arguments or {})
    for public_key, internal_key in _RENAMES.get(internal, {}).items():
        if public_key in result:
            value = result.pop(public_key)
            result.setdefault(internal_key, value)
    for key in _DROPPED.get(internal, ()):
        result.pop(key, None)
    if internal in {"run_command", "powershell"} and "timeout" in result:
        milliseconds = _as_int(result.pop("timeout"))
        if milliseconds is not None and milliseconds > 0 and "timeout_seconds" not in result:
            seconds = -(-milliseconds // 1000)  # integer ceiling: no float division, no OverflowError
            result["timeout_seconds"] = max(1, min(MAX_BASH_TIMEOUT_SECONDS, seconds))
    elif internal == "edit_file" and isinstance(result.get("edits"), list):
        result["edits"] = [
            arguments_in("edit_file", item) if isinstance(item, Mapping) else item
            for item in result["edits"]
        ]
    elif internal in {"read_process_output", "stop_process"} and "pid" in result:
        number = _as_int(result["pid"])
        if number is None and isinstance(result["pid"], str):
            match = _BASH_TASK_ID.fullmatch(result["pid"].strip())
            number = int(match.group(1)) if match else None
        if number is not None:
            result["pid"] = number
        if internal == "read_process_output":
            if "timeout" in result:
                milliseconds = _as_int(result.pop("timeout"))
                if milliseconds is not None and milliseconds > 0:
                    result["wait_seconds"] = max(1, min(MAX_BASH_TIMEOUT_SECONDS, -(-milliseconds // 1000)))
            if public_task_call:
                result.setdefault("block", True)  # Claude Code waits by default
    elif internal == "run_subagent":
        # Aliases the internal handler also accepts must be resolved before the default below,
        # or a call that names its type with `agent_type` silently becomes a `general` subagent.
        if "subagent_type" not in result:
            for alias in ("agent_type", "type", "agent"):
                if alias in result:
                    result["subagent_type"] = result.pop(alias)
                    break
        raw_type = result.get("subagent_type")
        if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
            result["subagent_type"] = "general"  # the documented default, not a widening
        elif isinstance(raw_type, str) and raw_type.strip().lower() in _SUBAGENT_TYPES_IN:
            result["subagent_type"] = _SUBAGENT_TYPES_IN[raw_type.strip().lower()]
        # anything else is kept as sent, so the internal validation rejects it instead of granting general
    elif internal == "ask_user":
        result = _ask_user_in(result)
    return result


def arguments_out(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Orin's internal argument shape -> public (Claude Code) shape. Used for history and hook payloads."""
    result: dict[str, Any] = dict(arguments or {})
    reverse: dict[str, str] = {}
    for public_key, internal_key in _RENAMES.get(internal, {}).items():
        reverse.setdefault(internal_key, public_key)
    for internal_key, public_key in reverse.items():
        if internal_key in result:
            value = result.pop(internal_key)
            result.setdefault(public_key, value)
    if internal in {"run_command", "powershell"} and "timeout_seconds" in result:
        seconds = _as_int(result["timeout_seconds"])
        if seconds is not None:  # an unparseable value is left exactly as it was
            result.pop("timeout_seconds")
            if "timeout" not in result:
                result["timeout"] = seconds * 1000
    elif internal == "edit_file" and isinstance(result.get("edits"), list):
        result["edits"] = [
            arguments_out("edit_file", item) if isinstance(item, Mapping) else item
            for item in result["edits"]
        ]
    elif internal in {"read_process_output", "stop_process"} and "task_id" in result:
        result["task_id"] = str(result["task_id"])
    elif internal == "run_subagent" and "subagent_type" in result:
        raw_type = str(result.get("subagent_type") or "").strip().lower()
        result["subagent_type"] = _SUBAGENT_TYPES_OUT.get(raw_type, result["subagent_type"])
    elif internal == "ask_user":
        result = _ask_user_out(result)
    return result


def permission_category(internal: str, *, kind: str, read_only: bool, policy_tags: tuple[str, ...]) -> str:
    """Coarse family used by permission rules and hooks."""
    if internal.startswith("mcp__") or kind == "mcp" or "mcp" in policy_tags:
        return "mcp"
    if internal in {"edit_file", "write_file", "notebook_edit"}:
        return "edit"
    if kind == "terminal" or internal == "run_command":
        return "exec" if not read_only else "read"
    if "network" in policy_tags or kind == "web":
        return "network"
    if kind == "agent":
        return "agent"
    if kind in {"user_input", "code_handoff"} or "user_input" in policy_tags:
        return "interaction"
    if kind in {"planning", "skill"}:
        return "meta"
    return "read" if read_only else "edit"


def _translate_tool_call_arguments(internal: str, raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        if not isinstance(parsed, Mapping):
            return raw
        return json.dumps(arguments_out(internal, parsed), ensure_ascii=False)
    if isinstance(raw, Mapping):
        return arguments_out(internal, raw)
    return raw


class PublicToolNames:
    """Translator the runtime applies at the model boundary."""

    def name_in(self, name: str) -> str:
        return internal_name(name)

    def arguments_in(self, internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return arguments_in(internal, arguments)

    def argument_names(self, internal: str) -> frozenset[str] | None:
        return public_argument_names(internal)

    def schemas_out(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, Mapping) else None
            if not isinstance(function, Mapping):
                out.append(schema)
                continue
            internal = str(function.get("name") or "")
            replacement = public_schema(internal)
            new_function = copy.deepcopy(dict(function))
            new_function["name"] = public_name(internal)
            if replacement is not None:
                new_function["description"], new_function["parameters"] = replacement
            out.append({**schema, "function": new_function})
        return out

    def messages_out(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # History is re-encoded every iteration: one poisoned entry must never
        # make the whole conversation unusable, so each item keeps its original
        # form when it cannot be translated.
        for message in messages:
            try:
                out.append(self._message_out(message))
            except Exception:  # noqa: BLE001 - translation is best-effort on stored history
                out.append(message)
        return out

    @staticmethod
    def _message_out(message: Any) -> Any:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            return message
        changed = dict(message)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            new_calls: list[Any] = []
            for call in tool_calls:
                try:
                    function = call.get("function") if isinstance(call, Mapping) else None
                    if not isinstance(function, Mapping):
                        new_calls.append(call)
                        continue
                    internal = str(function.get("name") or "")
                    new_function = {
                        **function,
                        "name": public_name(internal),
                        "arguments": _translate_tool_call_arguments(internal, function.get("arguments")),
                    }
                    new_calls.append({**call, "function": new_function})
                except Exception:  # noqa: BLE001
                    new_calls.append(call)
            changed["tool_calls"] = new_calls
        content = message.get("content")
        if isinstance(content, list):
            new_blocks: list[Any] = []
            for block in content:
                try:
                    if isinstance(block, Mapping) and block.get("type") == "tool_use":
                        internal = str(block.get("name") or "")
                        new_blocks.append({
                            **block,
                            "name": public_name(internal),
                            "input": _translate_tool_call_arguments(internal, block.get("input") or {}),
                        })
                    else:
                        new_blocks.append(block)
                except Exception:  # noqa: BLE001
                    new_blocks.append(block)
            changed["content"] = new_blocks
        return changed

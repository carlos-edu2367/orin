"""Claude Code–compatible public names and argument shapes for Orin's tools.

Orin's tools keep their internal snake_case ids (``read_file``) until W4
rewrites them. What the *model*, permission rules and hooks see can already be
the Claude Code surface (``Read`` with ``file_path``): this module converts
between the two shapes at the model boundary.

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

# public key -> internal key, per internal tool
_RENAMES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path"},
    "write_file": {"file_path": "path"},
    "edit_file": {"file_path": "path", "old_string": "old_text", "new_string": "new_text"},
    "search_files": {"-A": "after_context", "-B": "before_context", "-C": "context_lines", "-i": "ignore_case"},
    "run_command": {"run_in_background": "background"},
    "run_subagent": {"prompt": "task", "resume": "agent_id"},
    "use_skill": {"skill": "skill_id"},
    "search_mcp_tools": {"max_results": "limit"},
    "exit_plan_mode": {"plan": "summary", "allowedPrompts": "allowed_prompts"},
    "read_process_output": {"task_id": "pid"},
    "stop_process": {"task_id": "pid", "shell_id": "pid"},
}

# public keys Orin cannot honour yet (W4 implements them); dropped on the way in
_DROPPED: dict[str, frozenset[str]] = {
    "run_command": frozenset({"dangerouslyDisableSandbox"}),
    "search_files": frozenset({"-n"}),
    "fetch_url": frozenset({"prompt"}),
    "web_search": frozenset({"allowed_domains", "blocked_domains"}),
    "use_skill": frozenset({"args"}),
    "run_subagent": frozenset({"model"}),
}

_SUBAGENT_TYPES_IN = {
    "explore": "explore",
    "plan": "plan",
    "general-purpose": "general",
    "general": "general",
    "fork": "fork",
}
_SUBAGENT_TYPES_OUT = {"explore": "Explore", "plan": "Plan", "general": "general-purpose", "fork": "fork"}

_STRING = {"type": "string"}


def _obj(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required), "additionalProperties": False}


# Descriptions are Orin's own wording; field names follow Claude Code's public interface.
_PUBLIC_SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "read_file": (
        "Read a file from the workspace. Text comes back line-numbered; use offset and limit to page through a long "
        "file, and pages (e.g. '1-5') for a PDF. Read a file before you edit or overwrite it.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file, relative to the workspace or absolute inside it."},
            "offset": {"type": "integer", "minimum": 1, "description": "First line to read (1-based)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 800, "description": "How many lines to read."},
            "pages": {**_STRING, "description": "PDF page range such as '1-5' or '3'."},
        }, ("file_path",)),
    ),
    "write_file": (
        "Create a file, or replace an existing one you have already read in this session.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file to write."},
            "content": {**_STRING, "description": "The complete new content."},
        }, ("file_path", "content")),
    ),
    "edit_file": (
        "Replace an exact string in a file you have read. old_string must occur exactly once unless replace_all is true.",
        _obj({
            "file_path": {**_STRING, "description": "Path of the file to edit."},
            "old_string": {**_STRING, "description": "Exact text to replace, including indentation."},
            "new_string": {**_STRING, "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
        }, ("file_path", "old_string", "new_string")),
    ),
    "notebook_edit": (
        "Replace, insert or delete one cell of a Jupyter notebook.",
        _obj({
            "notebook_path": {**_STRING, "description": "Path of the .ipynb file."},
            "new_source": {**_STRING, "description": "New cell source (replace and insert)."},
            "cell_id": {**_STRING, "description": "Cell id, or 0-based index as a string."},
            "cell_type": {"type": "string", "enum": ["code", "markdown"]},
            "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
        }, ("notebook_path",)),
    ),
    "glob_files": (
        "Find files by glob pattern (e.g. '**/*.py'), newest first.",
        _obj({
            "pattern": {**_STRING, "description": "Glob pattern."},
            "path": {**_STRING, "description": "Directory to search in. Defaults to the workspace root."},
        }, ("pattern",)),
    ),
    "search_files": (
        "Search file contents with a regular expression.",
        _obj({
            "pattern": {**_STRING, "description": "Regular expression to search for."},
            "path": {**_STRING, "description": "File or directory to search."},
            "glob": {**_STRING, "description": "Only search files matching this glob."},
            "type": {**_STRING, "description": "Only search this file type, e.g. 'py'."},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
            "-A": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines after each match."},
            "-B": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines before each match."},
            "-C": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Lines around each match."},
            "-i": {"type": "boolean", "description": "Case-insensitive search."},
            "-n": {"type": "boolean", "description": "Show line numbers (always on)."},
            "multiline": {"type": "boolean", "description": "Let patterns span lines."},
            "head_limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Return at most this many entries."},
            "offset": {"type": "integer", "minimum": 0, "description": "Skip this many entries first."},
        }, ("pattern",)),
    ),
    "run_command": (
        "Run a shell command in the workspace. Prefer the file tools over shell commands for reading, searching and "
        "editing files. Give a short description. Use run_in_background for long-lived processes such as dev servers.",
        _obj({
            "command": {**_STRING, "description": "The command to run."},
            "description": {**_STRING, "description": "What the command does, in 5-10 words."},
            "timeout": {"type": "integer", "minimum": 1000, "maximum": MAX_BASH_TIMEOUT_SECONDS * 1000, "description": "Timeout in milliseconds."},
            "run_in_background": {"type": "boolean", "description": "Start it in the background and return immediately."},
        }, ("command",)),
    ),
    "read_process_output": (
        "Read the output of a background command started with run_in_background.",
        _obj({"task_id": {**_STRING, "description": "Id returned when the command started."}}, ("task_id",)),
    ),
    "stop_process": (
        "Stop a background command.",
        _obj({"task_id": {**_STRING, "description": "Id of the background command."}}, ("task_id",)),
    ),
    "todo_write": (
        "Write the session's todo list. Keep exactly one item in_progress while you work.",
        _obj({
            "todos": {
                "type": "array",
                "maxItems": 40,
                "items": _obj({
                    "content": {**_STRING, "description": "The task, in imperative form."},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    "activeForm": {**_STRING, "description": "Present-continuous label shown while in progress."},
                }, ("content", "status")),
            },
        }, ("todos",)),
    ),
    "task_create": (
        "Create a task in the session task list.",
        _obj({
            "subject": {**_STRING, "description": "Short imperative title."},
            "description": {**_STRING, "description": "What the task involves."},
            "activeForm": {**_STRING, "description": "Present-continuous label shown while in progress."},
            "metadata": {"type": "object", "additionalProperties": True},
        }, ("subject",)),
    ),
    "task_update": (
        "Update a task: status, text, ownership or dependencies.",
        _obj({
            "taskId": _STRING,
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]},
            "subject": _STRING,
            "description": _STRING,
            "activeForm": _STRING,
            "owner": _STRING,
            "addBlocks": {"type": "array", "items": _STRING},
            "addBlockedBy": {"type": "array", "items": _STRING},
            "metadata": {"type": "object", "additionalProperties": True},
        }, ("taskId",)),
    ),
    "task_get": ("Get one task by id.", _obj({"taskId": _STRING}, ("taskId",))),
    "task_list": ("List the session's tasks.", _obj({})),
    "ask_user": (
        "Ask the user 1-4 multiple-choice questions when you are blocked on a decision only they can make, then stop "
        "and wait for the answer.",
        _obj({
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": _obj({
                    "question": {**_STRING, "description": "The full question."},
                    "header": {**_STRING, "maxLength": 12, "description": "Very short label for the question."},
                    "multiSelect": {"type": "boolean", "description": "Allow several answers."},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": _obj({
                            "label": {**_STRING, "description": "Option text (1-5 words)."},
                            "description": {**_STRING, "description": "What choosing it means."},
                        }, ("label",)),
                    },
                }, ("question", "header", "options", "multiSelect")),
            },
        }, ("questions",)),
    ),
    "enter_plan_mode": ("Switch to plan mode: explore read-only and prepare a plan before changing code.", _obj({})),
    "exit_plan_mode": (
        "Present the finished plan to the user for approval and leave plan mode.",
        _obj({
            "plan": {**_STRING, "description": "The plan, in Markdown."},
            "allowedPrompts": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        }),
    ),
    "enter_worktree": (
        "Create (or switch to) an isolated git worktree for this session.",
        _obj({
            "name": {**_STRING, "description": "Optional worktree name."},
            "base_branch": {**_STRING, "description": "Git ref to branch from. Defaults to HEAD."},
        }),
    ),
    "exit_worktree": (
        "Leave the current worktree, keeping or removing it.",
        _obj({
            "action": {"type": "string", "enum": ["keep", "remove"]},
            "force": {"type": "boolean", "description": "Remove even with uncommitted changes."},
        }),
    ),
    "fetch_url": (
        "Fetch a public URL and return its readable content.",
        _obj({
            "url": {**_STRING, "description": "The URL to fetch."},
            "prompt": {**_STRING, "description": "What you want to learn from the page."},
        }, ("url",)),
    ),
    "web_search": (
        "Search the web and return titles, URLs and snippets.",
        _obj({
            "query": {**_STRING, "description": "Search query."},
            "allowed_domains": {"type": "array", "items": _STRING},
            "blocked_domains": {"type": "array", "items": _STRING},
        }, ("query",)),
    ),
    "run_subagent": (
        "Delegate a self-contained task to a subagent. Types: 'Explore' (fast read-only search), 'Plan' (read-only "
        "design), 'general-purpose' (full toolset), 'fork' (inherits this conversation).",
        _obj({
            "description": {**_STRING, "description": "3-5 word summary."},
            "prompt": {**_STRING, "description": "Complete instructions; the subagent does not see this conversation."},
            "subagent_type": {**_STRING, "description": "Explore, Plan, general-purpose or fork."},
            "model": {**_STRING, "description": "Optional model alias."},
            "run_in_background": {"type": "boolean", "description": "Return immediately and notify on completion."},
            "resume": {**_STRING, "description": "Id of a previous subagent to continue."},
        }, ("description", "prompt")),
    ),
    "use_skill": (
        "Load a skill's instructions when the task matches it.",
        _obj({"skill": {**_STRING, "description": "Skill name."}, "args": {**_STRING, "description": "Optional arguments."}}, ("skill",)),
    ),
    "search_mcp_tools": (
        "Find deferred MCP tools by keyword and load their schemas so they can be called.",
        _obj({
            "query": {**_STRING, "description": "Keywords or a tool name."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ("query",)),
    ),
}


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


def public_schema(internal: str) -> tuple[str, dict[str, Any]] | None:
    entry = _PUBLIC_SCHEMAS.get(internal)
    if entry is None:
        return None
    description, schema = entry
    return description, copy.deepcopy(schema)


def public_argument_names(internal: str) -> frozenset[str] | None:
    entry = _PUBLIC_SCHEMAS.get(internal)
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
            label = str(option.get("label") or "").strip()
            detail = str(option.get("description") or "").strip()
            text = f"{label} — {detail}" if label and detail else label
            options.append({"id": f"o{option_index}", "label": text[:240]})
        if not options:
            mode = "text"
        elif bool(raw.get("multiSelect")) or len(options) < 2:
            mode = "checkbox"
        else:
            mode = "single_choice"
        converted.append({"id": f"q{index}", "question": str(raw.get("question") or ""), "mode": mode, "options": options})
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
            "header": str(raw.get("id", ""))[:12],
            "multiSelect": raw.get("mode") == "checkbox",
            "options": [
                {"label": option.get("label", "")}
                for option in _options_list(raw)
                if isinstance(option, Mapping)
            ],
        })
    return {**{k: v for k, v in arguments.items() if k != "questions"}, "questions": out}


def arguments_in(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Public (Claude Code) argument shape -> Orin's internal shape."""
    result: dict[str, Any] = dict(arguments or {})
    for public_key, internal_key in _RENAMES.get(internal, {}).items():
        if public_key in result:
            value = result.pop(public_key)
            result.setdefault(internal_key, value)
    for key in _DROPPED.get(internal, ()):
        result.pop(key, None)
    if internal == "run_command" and "timeout" in result:
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
        if number is not None:
            result["pid"] = number
    elif internal == "run_subagent":
        raw_type = str(result.get("subagent_type") or "").strip().lower()
        result["subagent_type"] = _SUBAGENT_TYPES_IN.get(raw_type, "general")
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
    if internal == "run_command" and "timeout_seconds" in result:
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

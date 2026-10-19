"""Agent-facing tools for the conversational runtime.

These are deliberately separate from ``agentos.tool_runtime``: that catalog is
built so no adapter output ever crosses the provider boundary, which is exactly
the wrong contract for a chat agent that must *read* what its tools returned.
Here the tool result is the point, so each tool returns bounded, sanitized
content back to the model and, in parallel, emits a public activity event that
the UI renders as a human-readable summary.

Everything the model can touch is confined to one conversation workspace; the
network tool is HTTP(S) only and refuses private address literals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
from datetime import UTC, datetime
import difflib
import hashlib
from html.parser import HTMLParser
import itertools
from ipaddress import ip_address
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Collection, Mapping
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from agentos.reading.extract import extract_text
from agentos.reading.render import ImageTooLarge, normalize_image, render_pdf_pages
from agentos.reading.vision import VisionUnavailable
from .diagnostics import STEP_ORDER, detect_recipe, file_diagnostic_command
from .workspace import MAX_LIST_DEPTH, MAX_SEARCH_RESULTS, ConversationWorkspace, WorkspaceError
from .models import MAX_USER_QUESTION_ITEMS
from .file_preview import media_type_for
from .lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from .reminders import system_reminder
from .tools.public_names import (
    PublicToolNames,
    arguments_in as _public_arguments_in,
    arguments_out as _public_arguments_out,
    internal_name as _internal_tool_name,
    public_name as _public_tool_name,
    public_text,
)


from agentos.agentic.tools.files.read import ReadTools
from agentos.agentic.tools.files.write import WriteTools
from agentos.agentic.tools.files.edit import EditTools
from agentos.agentic.tools.files.notebook import NotebookTools
from agentos.agentic.tools.search.glob import GlobTools
from agentos.agentic.tools.search.grep import GrepTools
from agentos.agentic.tools.orin.search_code import CodeSearchTools
from agentos.agentic.tools.shell.bash import BashTools
from agentos.agentic.tools.shell.powershell import PowerShellTools
from agentos.agentic.tools.shell.tasks import ShellTaskTools
from agentos.agentic.tools.orin.verify import VerifyTools
from agentos.agentic.tools.web.fetch import FetchTools
from agentos.agentic.tools.web.search import WebSearchTools
from agentos.agentic.tools.orin.memory import MemoryTools
from agentos.agentic.tools.orin.skills_admin import SkillTools
from agentos.agentic.tools.mcp.admin import McpTools
from agentos.agentic.tools.orin.plugins_admin import PluginTools
from agentos.agentic.tools.orin.projects import ProjectTools
from agentos.agentic.tools.planning.todo import TodoTools
from agentos.agentic.tools.planning.tasks import TaskTools
from agentos.agentic.tools.planning.plan_mode import PlanModeTools
from agentos.agentic.tools.interaction.ask_user import AskUserTools
from agentos.agentic.tools.agents.agent import AgentTools
from agentos.agentic.tools.git.worktree import WorktreeTools
from agentos.agentic.tools.orin.preview import PreviewTools
from agentos.agentic.tools.orin.media import MediaTools
from agentos.agentic.tools.scheduling.cron import CronTools
from agentos.agentic.tools.scheduling.wakeup import WakeupTools
from agentos.agentic.tools.scheduling.monitor import MonitorTools
from agentos.agentic.tools._shared import (  # noqa: F401 - re-exported for callers and tests
    _SENSITIVE_NAME,
    _PRIVATE_KEY_BLOCK,
    _AUTHORIZATION_VALUE,
    _SENSITIVE_ASSIGNMENT,
    _JWT_VALUE,
    _netloc,
    _safe_display_url,
    sanitize_page_text,
    _READ_ORDER,
    ReadFileRecord,
    TOOLKITS,
    VERIFICATION_MODES,
    ContractError,
    parse_contract,
    image_block,
    logger,
    MAX_TOOL_RESULT_CHARS,
    EXTERNAL_CHANGE_HASH_LIMIT_BYTES,
    PERMISSION_DENIAL_CODES,
    _ORIN_AUTHORED_CODES,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    CANCEL_CHECK_SECONDS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_CHARS,
    FETCH_TIMEOUT_SECONDS,
    MAX_FETCH_REDIRECTS,
    _PROCESS_LOG_DIR,
    _PROCESS_MANIFEST_PATH,
    MAX_PROCESS_LOG_TAIL_CHARS,
    MAX_TRACKED_PROCESSES,
    DIAGNOSTIC_TIMEOUT_SECONDS,
    DIAGNOSTIC_DEBOUNCE_SECONDS,
    MAX_FILE_DIAGNOSTIC_CHARS,
    VERIFY_STEP_TIMEOUT_SECONDS,
    MAX_VERIFY_STEP_CHARS,
    SCAFFOLD_RECIPES,
    _BLOCKED_COMMAND,
    _VERIFICATION_CMD_RE,
    is_verification_command,
    _DEV_SERVER_CMD_RE,
    _SERVER_READY_RE,
    _URL_EXTRACT_RE,
    _GLOB_IGNORED_DIRS,
    AgentToolError,
    _HUNK_HEADER_RE,
    _MAX_PATCH_HUNKS,
    _MAX_PATCH_LINES,
    _MAX_PATCH_LINE_CHARS,
    compute_structured_patch,
    _PARAM_ALIASES,
    _broker_arguments,
    coerce_tool_arguments,
    ToolDefinition,
    _safe_public_input,
    ToolOutcome,
    _schema,
    _TEXT,
    _QUESTION_ID,
    _TextExtractor,
    _bounded,
    _bounded_output,
    _public_url,
    _terminate_pid,
    _terminate_process_tree,
    _process_is_running,
    _append_reminder,
)

# The most any single tool result may return when its handler asks for more (Read with allow_large).
LARGE_RESULT_CHARS = 100_000
MAX_HOOK_FIELD_CHARS = 50_000


def _bounded_value(value: Any, depth: int = 0) -> Any:
    """A JSON-like value with long strings cut and lists capped, so one tool result cannot flood a hook."""
    if depth > 6:
        return None
    if isinstance(value, str):
        return value if len(value) <= MAX_HOOK_FIELD_CHARS else value[:MAX_HOOK_FIELD_CHARS] + "\n[truncated]"
    if isinstance(value, Mapping):
        return {str(key): _bounded_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth + 1) for item in list(value)[:500]]
    return value


def _tool_response(outcome: ToolOutcome) -> dict[str, Any]:
    """What PostToolUse hooks receive: Orin's summary plus the tool's Claude Code output when it has one."""
    response: dict[str, Any] = {"status": outcome.status, "summary": outcome.summary, "content": outcome.content[:4000]}
    structured = outcome.data.get("tool_output") if isinstance(outcome.data, Mapping) else None
    if isinstance(structured, Mapping):
        response.update(_bounded_value(structured))
    return response


class AgentToolset(
    ReadTools,
    WriteTools,
    EditTools,
    NotebookTools,
    GlobTools,
    GrepTools,
    CodeSearchTools,
    BashTools,
    PowerShellTools,
    ShellTaskTools,
    VerifyTools,
    FetchTools,
    WebSearchTools,
    MemoryTools,
    SkillTools,
    McpTools,
    PluginTools,
    ProjectTools,
    TodoTools,
    TaskTools,
    PlanModeTools,
    AskUserTools,
    AgentTools,
    WorktreeTools,
    PreviewTools,
    MediaTools,
    CronTools,
    WakeupTools,
    MonitorTools,
):
    """Builds the tool schemas and executes the model's calls.

    ``delegate`` is injected by the runtime because creating and running a
    subagent needs the whole turn loop, which lives one layer above this class.
    """

    def __init__(
        self,
        workspace: ConversationWorkspace,
        *,
        memory=None,
        delegate: Callable[[str, str], ToolOutcome] | None = None,
        delegate_batch: Callable[[list[Mapping[str, Any]]], ToolOutcome] | None = None,
        create_agent: Callable[[str, str, str | None], ToolOutcome] | None = None,
        run_subagent: Callable[..., ToolOutcome] | None = None,
        check_subagent: Callable[..., ToolOutcome] | None = None,
        on_close: Callable[[], None] | None = None,
        on_tool_progress: Callable[[str, str, Mapping[str, Any]], None] | None = None,
        http_client: httpx.Client | None = None,
        search_client: object | None = None,
        retrieval: object | None = None,
        retrieval_reindex: Callable[[list[str]], None] | None = None,
        browser: object | None = None,
        enable_terminal: bool = True,
        skills=None,
        skill_library=None,
        skill_user_id: str | None = None,
        skill_agent_id: str | None = None,
        skill_load_recorder: Callable[[object], None] | None = None,
        policy: object | None = None,
        model_sees_images: bool = False,
        visual_reader: object | None = None,
        browser_capability: str = "interact",
        mcp_provider: object | None = None,
        mcp_service: object | None = None,
        mcp_user_id: str | None = None,
        plugin_service: object | None = None,
        plugin_user_id: str | None = None,
        code_mode_active: bool = False,
        code_mode_permits_push: bool = False,
        code_mode_permits_pr: bool = False,
        code_mode_requires_approval: bool = False,
        code_mode_autonomy: str | None = None,
        code_mode_work_kind: str | None = None,
        permission_mode: str | None = None,
        permission_broker: object | None = None,
        interactive_permissions: bool = False,
        on_permission_request: Callable[[dict[str, Any]], None] | None = None,
        checkpoint_store: object | None = None,
        turn_id: str | None = None,
        conversation_id: str | None = None,
        read_file_state: dict[str, ReadFileRecord] | None = None,
        read_files_state: dict[str, Any] | None = None,
        todos_state: list[dict[str, Any]] | None = None,
        on_todo_update: Callable[[list[dict[str, Any]]], None] | None = None,
        background_shells: dict[str, dict[str, Any]] | None = None,
        read_only_mode: bool = False,
        default_provider: str | None = None,
        default_model_id: str | None = None,
        default_workspace_path: str | None = None,
        project_id: str | None = None,
        project_store: object | None = None,
        chat_store: object | None = None,
        local_workspaces: object | None = None,
        project_user_id: str | None = None,
        media_policy: object | None = None,
        media_client: object | None = None,
        public_tool_names: bool = False,
        lifecycle: LifecycleBus | None = None,
        extra_env: Mapping[str, str] | None = None,
        lifecycle_context: Mapping[str, str] | None = None,
        reminders: object | None = None,
        memory_tracker: object | None = None,
        non_interactive_ask: str = "allow",
        cancel_check: Callable[[], bool] | None = None,
        file_history: object | None = None,
        user_message_id: str | None = None,
        web_answerer: Callable[[str, str], str | None] | None = None,
        scheduler: object | None = None,
        schedule_context: Mapping[str, str] | None = None,
    ) -> None:
        from agentos.code_mode.checkpoints import FileCheckpointStore
        from agentos.code_mode.models import CodePermissionMode, normalize_permission_mode
        from agentos.code_mode.permissions import ToolPermissionBroker

        self.workspace = workspace
        self._tls = threading.local()
        # Asked while a foreground command runs: True stops the command (the turn was interrupted).
        self._cancel_check = cancel_check
        # Per-message file history (W4): what each file held before this user message's turn first changed it.
        self._file_history = file_history
        self._history_message_id = str(user_message_id or turn_id or "") or None
        # WebFetch's prompt is answered by the turn's own model through this callable (W4); None answers nothing.
        self._web_answerer = web_answerer
        # Orin's scheduler and who/what a job made here runs as (W4: CronCreate, ScheduleWakeup, Monitor).
        # Bound by the trusted turn, never by a tool argument; None publishes none of those tools.
        self._scheduler = scheduler
        self._schedule_context: dict[str, str] = {str(k): str(v) for k, v in dict(schedule_context or {}).items() if v}
        self._read_file_lock = threading.Lock()
        self._reported_changes: set[tuple[str, str, str]] = set()
        # (mtime_ns, size) of each path at its last external-change examination; an unchanged stamp is not re-hashed.
        self._external_stamps: dict[str, tuple[int, int]] = {}
        self.turn_id = str(turn_id) if turn_id else None
        self.conversation_id = str(conversation_id) if conversation_id else None
        self._conversation_id = self.conversation_id
        self._default_provider = str(default_provider).strip() or None if default_provider is not None else None
        self._default_model_id = str(default_model_id).strip() or None if default_model_id is not None else None
        self._default_workspace_path = str(default_workspace_path).strip() or None if default_workspace_path is not None else None
        self._project_id = str(project_id).strip() or None if project_id is not None else None
        self._project_store = project_store
        self._chat_store = chat_store
        self._local_workspaces = local_workspaces
        self._project_user_id = str(project_user_id).strip() or None if project_user_id is not None else None
        self._media_policy = media_policy
        self._media_client = media_client
        self.memory = memory
        self.model_sees_images = bool(model_sees_images)
        self._visual_reader = visual_reader
        self._delegate = delegate
        self._delegate_batch = delegate_batch
        self._create_agent = create_agent
        self._run_subagent = run_subagent
        self._check_subagent = check_subagent
        self._on_close = on_close
        self._on_tool_progress = on_tool_progress
        self._http_client = http_client
        self._search_client = search_client
        self._retrieval = retrieval
        self._retrieval_reindex = retrieval_reindex
        self._browser = browser
        self.browser_capability = browser_capability
        self._last_browser_navigation_url: str | None = None
        self._last_browser_navigation_outcome: ToolOutcome | None = None
        self._enable_terminal = enable_terminal and not bool(read_only_mode)
        self._read_only_mode: bool = bool(read_only_mode)
        self.skills = skills
        # The registry is intentionally read-only.  Publishing must go through
        # the application service, with the user identity bound by the trusted
        # turn rather than supplied by a model tool argument.
        self._skill_library = skill_library
        self._skill_user_id = skill_user_id
        self._skill_agent_id = skill_agent_id
        self._skill_load_recorder = skill_load_recorder
        self._policy = policy
        self._loaded_skills: set[tuple[str, str]] = set()
        self._mcp_provider = mcp_provider
        self._mcp_service = mcp_service
        self._mcp_user_id = mcp_user_id
        self._plugin_service = plugin_service
        self._plugin_user_id = plugin_user_id
        self._code_mode_active = bool(code_mode_active)
        self._code_mode_permits_push = bool(code_mode_permits_push)
        self._code_mode_permits_pr = bool(code_mode_permits_pr)
        self._code_mode_requires_approval = bool(code_mode_requires_approval)
        self._code_mode_work_kind: str = str(code_mode_work_kind or "implementation")
        raw_mode = permission_mode or code_mode_autonomy
        if raw_mode:
            resolved_mode = normalize_permission_mode(raw_mode)
        elif self._code_mode_requires_approval:
            resolved_mode = CodePermissionMode.ASK_PERMISSIONS
        elif self._code_mode_permits_push and self._code_mode_permits_pr:
            resolved_mode = CodePermissionMode.FULL_AUTONOMY
        else:
            resolved_mode = CodePermissionMode.ACCEPT_EDITS
        self._permission_mode: CodePermissionMode = resolved_mode
        self._previous_permission_mode: CodePermissionMode = (
            CodePermissionMode.ACCEPT_EDITS if resolved_mode is CodePermissionMode.PLAN_MODE else resolved_mode
        )
        self._plan_mode_active: bool = resolved_mode is CodePermissionMode.PLAN_MODE
        self._permission_broker: ToolPermissionBroker = (
            permission_broker if isinstance(permission_broker, ToolPermissionBroker) else ToolPermissionBroker(self.workspace)
        )
        self._on_permission_request: Callable[[dict[str, Any]], None] | None = on_permission_request
        self._interactive_permissions: bool = bool(interactive_permissions or permission_broker is not None or on_permission_request is not None)
        self._checkpoint_store: FileCheckpointStore = (
            checkpoint_store if isinstance(checkpoint_store, FileCheckpointStore) else FileCheckpointStore(self.workspace)
        )
        resolved_read_state = read_file_state if read_file_state is not None else read_files_state
        self._read_file_state: dict[str, ReadFileRecord] = resolved_read_state if resolved_read_state is not None else {}
        self._read_files_state = self._read_file_state
        self._read_files = self._read_file_state
        self._dedup_after_non_code_edit: bool = bool(code_mode_active or resolved_read_state is not None)
        self._non_code_edited_paths: set[str] = set()
        self._todos: list[dict[str, Any]] = todos_state if todos_state is not None else []
        self._todos_state = self._todos
        self._on_todo_update: Callable[[list[dict[str, Any]]], None] | None = on_todo_update
        self._background_shells: dict[str, dict[str, Any]] = background_shells if background_shells is not None else {}
        self._current_cwd: Path = self.workspace.root
        self._restore_session_cwd()
        self._original_workspace_root: Path = self.workspace.root
        self._active_worktree_name: str | None = None
        self._notified_exited_pids: set[int] = set()
        # Tracked unconditionally (see _observe_mutation_outcome): the general
        # verify-before-responding gate in the runtime applies to every turn,
        # not only Code mode.
        self._changed_paths: set[str] = set()
        self._change_events = 0
        self._tool_calls_count = 0
        # A Code-mode completion is evidence-led. A command string containing
        # "test" is not evidence (``echo test`` used to satisfy this gate).
        self._code_mode_verified = False
        self._code_mode_visual_check = False
        self._code_completion_gate_rejections: int = 0
        self._frontend_verify_attempts: int = 0
        self._frontend_verify_infra_failed: bool = False
        self._diagnosed_at: dict[str, float] = {}
        self._definitions: tuple[ToolDefinition, ...] | None = None
        self._by_name: dict[str, ToolDefinition] = {}
        self._spellings: dict[str, str] = {}
        self._active_mcp_tool_names: set[str] = set()
        # Claude Code-compatible names/arguments at the model boundary
        # (experimental.claudeToolNames). Internal ids stay snake_case.
        self.name_translator: PublicToolNames | None = PublicToolNames() if public_tool_names else None
        self._lifecycle: LifecycleBus | None = lifecycle
        # Fields merged into every lifecycle payload this toolset emits (e.g. the subagent's id).
        self._lifecycle_context: dict[str, str] = {str(k): str(v) for k, v in dict(lifecycle_context or {}).items()}
        # Environment added to run_command (a SessionStart hook's env file).
        self._extra_env: dict[str, str] = {str(k): str(v) for k, v in dict(extra_env or {}).items()}
        # Set when a hook asked to stop the turn; the runtime ends the turn after this tool batch.
        self.stop_requested: str | None = None
        # Shared with the runtime: tools queue <system-reminder> notes for the next model request.
        self.reminders = reminders
        self.memory_tracker = memory_tracker
        # W1-R9: an explicitly non-interactive Code run may not turn an ordinary "ask" into a silent allow.
        self._non_interactive_ask_allowed = str(non_interactive_ask or "allow").strip().lower() != "deny"

    @property
    def active_mcp_tool_names(self) -> frozenset[str]:
        return frozenset(self._active_mcp_tool_names)

    def mcp_server_summaries(self) -> tuple[dict[str, Any], ...]:
        if self._mcp_provider is None:
            return ()
        summaries_fn = getattr(self._mcp_provider, "server_summaries", None)
        if callable(summaries_fn):
            try:
                return tuple(summaries_fn())
            except Exception:
                return ()
        counts: dict[str, int] = {}
        for item in getattr(self._mcp_provider, "definitions", lambda: ())():
            name = getattr(item, "name", "")
            if isinstance(name, str) and name.startswith("mcp__"):
                parts = name.split("__", 2)
                if len(parts) == 3 and parts[1]:
                    counts[parts[1]] = counts.get(parts[1], 0) + 1
        return tuple(
            {"slug": slug, "display_name": slug, "tool_count": count}
            for slug, count in counts.items()
        )

    @property
    def _active_invocation_id(self) -> str | None:
        return getattr(self._tls, "active_invocation_id", None)

    @_active_invocation_id.setter
    def _active_invocation_id(self, value: str | None) -> None:
        self._tls.active_invocation_id = value

    @property
    def _verified_since_last_edit(self) -> bool:
        return self._code_mode_verified

    @_verified_since_last_edit.setter
    def _verified_since_last_edit(self, value: bool) -> None:
        self._code_mode_verified = bool(value)

    # -- definitions ----------------------------------------------------

    def _build_definitions(self) -> tuple[ToolDefinition, ...]:
        from agentos.agentic.tools.registry import default_registry

        items: list[ToolDefinition] = [spec.bind(self) for spec in default_registry().specs() if spec.available(self)]
        if self._mcp_provider is not None:
            # Remote tools come last so a server can never shadow a native tool
            # name, and the namespace prefix already makes collision impossible.
            native = {item.name for item in items}
            items.extend(item for item in self._mcp_provider.definitions() if item.name not in native)
        return tuple(items)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """The tool set is fixed for the lifetime of a turn, so build it once."""
        if self._definitions is None:
            built = self._build_definitions()
            if self._read_only_mode:
                built = tuple(
                    item for item in built
                    if item.read_only and item.name not in {
                        "write_file", "edit_file", "notebook_edit", "run_command", "powershell", "stop_process",
                        "verify_project", "create_agent", "ask_agent", "ask_agents", "run_subagent", "check_subagent",
                        "generate_image", "generate_audio", "generate_video",
                    }
                )
            if self._policy is not None:
                built = tuple(item for item in built if self._policy.allows(item.name, item.policy_tags))
            self._definitions = built
            self._by_name = {item.name: item for item in self._definitions}
            # Canonical names and aliases, exact and lower-case, resolve to the stable key in every mode.
            self._spellings = {}
            for item in self._definitions:
                for spelling in (item.public_name, *item.aliases):
                    if spelling and spelling != item.name:
                        self._spellings.setdefault(spelling, item.name)
                        self._spellings.setdefault(spelling.lower(), item.name)
        return self._definitions

    def key_for(self, name: str) -> str:
        """The stable key of the tool ``name`` spells (key, canonical name or alias, any case); else ``name``."""
        self.definitions()
        if not isinstance(name, str) or not name or name in self._by_name or name.startswith("mcp__"):
            return name
        return self._spellings.get(name) or self._spellings.get(name.lower()) or _internal_tool_name(name)

    def _is_mcp_schema_published(self, item: ToolDefinition) -> bool:
        if item.kind == "mcp" and item.name.startswith("mcp__"):
            return item.name in self._active_mcp_tool_names
        return True

    def schemas(self, allowed: Collection[str] | None = None, kinds: Collection[str] | None = None) -> list[dict[str, Any]]:
        """Provider-facing schemas, optionally narrowed to one phase's tools.

        Remote ``mcp__<slug>__<tool>`` definitions are lazy-loaded: only tools
        activated via ``search_mcp_tools`` / ``inspect_mcp_server`` / ``call_mcp_tool``
        in the current turn are published as individual tool schemas.
        """
        if allowed is None:
            return [item.schema() for item in self.definitions() if self._is_mcp_schema_published(item)]
        names, families = set(allowed), set(kinds or ())
        return [
            item.schema()
            for item in self.definitions()
            if (item.name in names or item.kind in families) and self._is_mcp_schema_published(item)
        ]

    def resolve(self, name: str) -> ToolDefinition:
        key = self.key_for(name)  # builds the definitions first: `_by_name` is rebound there
        definition = self._by_name.get(key)
        if definition is None:
            raise AgentToolError(f"Unknown tool '{name}'.")
        return definition

    def is_read_only(self, name: str) -> bool:
        try:
            return self.resolve(name).read_only
        except AgentToolError:
            return False

    def _cancel_requested(self) -> bool:
        """Whether the person has stopped the turn. A check that raises never stops a healthy command."""
        check = self._cancel_check
        if check is None:
            return False
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - a broken check must not kill the command
            return False

    def is_mutating(self, name: str) -> bool:
        """Whether a failed call could have changed an external effect.

        A tool can be neither read-only nor mutating: planning and user-input
        tools, for example, coordinate the turn without changing a workspace
        or an external system. Their validation failures are safe for the
        model to correct in the same turn.
        """
        try:
            return "mutates" in self.resolve(name).policy_tags
        except AgentToolError:
            return False

    def argument_names(self, name: str) -> frozenset[str] | None:
        """The argument names a tool declares, or None when the tool is unknown."""
        try:
            definition = self.resolve(name)
        except AgentToolError:
            return None
        properties = definition.parameters.get("properties")
        return frozenset(properties) if isinstance(properties, Mapping) else None

    # -- execution ------------------------------------------------------

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a tool by name and return a flat dictionary result."""
        merged_args = dict(arguments or {})
        merged_args.update(kwargs)
        outcome = self.invoke(name, merged_args)
        out: dict[str, Any] = {
            "ok": outcome.status == "succeeded",
            "status": outcome.status,
            "summary": outcome.summary,
            "content": outcome.content,
            "payload": dict(outcome.payload),
        }
        if outcome.error_code:
            out["error_code"] = outcome.error_code
        if outcome.data:
            out.update(outcome.data)
        for key, val in outcome.payload.items():
            out.setdefault(key, val)
        return out

    def _speak_public(self, outcome: ToolOutcome) -> ToolOutcome:
        """With Claude Code names on, Orin's own error text names tools the way the model sees them."""
        if self.name_translator is not None and outcome.status != "succeeded" and outcome.error_code in _ORIN_AUTHORED_CODES:
            outcome.content = public_text(outcome.content)
            outcome.summary = public_text(outcome.summary)
        return outcome

    def _task_hook_block(self, bus: LifecycleBus, internal: str, arguments: Mapping[str, Any]) -> ToolOutcome | None:
        """TaskCreated/TaskCompleted run before the change; a block leaves the task list exactly as it was."""
        if internal == "task_create":
            # Mirrors the tool's refusals: a blank subject or an unknown status creates nothing, so no event.
            subject_text = str(arguments.get("subject") or arguments.get("content") or "").strip()
            clean_status = str(arguments.get("status") or "pending").strip().lower()
            if not subject_text or clean_status not in {"pending", "in_progress", "completed"}:
                return None
            event = HookEvent.TASK_CREATED
            payload = {
                "task_id": self._next_task_id(),
                "task_subject": str(arguments.get("subject") or arguments.get("content") or arguments.get("title") or ""),
                "task_description": str(arguments.get("description") or ""),
            }
        elif internal == "task_update" and str(arguments.get("status") or "").strip().lower() == "completed":
            event = HookEvent.TASK_COMPLETED
            task_id = str(arguments.get("taskId") or arguments.get("task_id") or arguments.get("id") or "")
            found = next(
                (item for item in self._todos if str(item.get("id")) == task_id or (task_id.isdigit() and str(item.get("id")) == f"todo_{task_id}")),
                None,
            )
            if found is None:
                return None  # the tool refuses an unknown id: nothing is completed, so no event
            payload = {
                "task_id": task_id,
                "task_subject": str(found.get("subject") or found.get("content") or ""),
                "task_description": str(found.get("description") or ""),
            }
        else:
            return None
        decision = self._emit_lifecycle(bus, event, **payload)
        if decision is None or not (decision.blocked or decision.permission_decision == "deny"):
            return None
        reason = decision.reason or "blocked by hook"
        return ToolOutcome(
            "failed", "Bloqueado por hook",
            f"A {event.value} hook refused this change: {reason}",
            {"tool_kind": "planning"}, "HOOK_BLOCKED_TASK",
        )

    def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str | None = None,
        invocation_id: str | None = None,
    ) -> ToolOutcome:
        bus = self._lifecycle
        if bus is None:
            return self._speak_public(self._invoke_unobserved(name, arguments, call_id=call_id, invocation_id=invocation_id))
        internal = self.key_for(name)
        try:
            # Idempotent: an Orin-shaped call passes through, a Claude Code-shaped one is mapped, in every mode.
            arguments = _public_arguments_in(internal, dict(arguments or {}))
        except Exception:  # noqa: BLE001 - a model-chosen value must never crash the turn
            return ToolOutcome("failed", "Argumentos inválidos"[:240], f"Invalid arguments for {name}.", {}, "INVALID_ARGUMENTS")
        tool_name = _public_tool_name(internal)
        tool_input = _safe_public_input(internal, arguments)
        tool_use_id = str(call_id or invocation_id or getattr(self, "_active_invocation_id", None) or "")
        kind = self._tool_kind_for_hooks(internal)
        pre = self._emit_lifecycle(bus, HookEvent.PRE_TOOL_USE, tool_name=tool_name, tool_input=tool_input, tool_use_id=tool_use_id)
        reminders: list[str] = []
        if pre is not None:
            reminders.extend(pre.additional_context)
            if pre.stop:
                reason = pre.stop_reason or pre.reason or "sem motivo informado"
                self.stop_requested = pre.stop_reason or pre.reason or "stopped by hook"
                return ToolOutcome("failed", "Interrompido por hook", reason[:2000], {"tool_kind": kind}, "HOOK_STOPPED")
            if pre.blocked or pre.permission_decision == "deny":
                reason = pre.reason or "sem motivo informado"
                self._emit_lifecycle(
                    bus, HookEvent.PERMISSION_DENIED,
                    tool_name=tool_name, tool_input=tool_input, tool_use_id=tool_use_id, reason=reason[:2000],
                )
                return ToolOutcome(
                    "failed", "Bloqueado por hook",
                    f"Um hook PreToolUse bloqueou `{tool_name}`: {reason}",
                    {"tool_kind": kind}, "HOOK_DENIED",
                )
            if isinstance(pre.updated_input, Mapping):
                try:
                    arguments = _public_arguments_in(internal, dict(pre.updated_input))
                    tool_input = _safe_public_input(internal, arguments)
                except Exception:  # noqa: BLE001 - a hook's malformed input must never crash the turn
                    logger.warning("PreToolUse hook returned input that cannot be applied to %s", internal)
        blocked_task = self._task_hook_block(bus, internal, arguments)
        if blocked_task is not None:
            return self._speak_public(blocked_task)
        cwd_before = self._current_cwd
        hook_permission = pre.permission_decision if pre is not None else None
        outcome = self._invoke_unobserved(
            internal, arguments, call_id=call_id, invocation_id=invocation_id, hook_permission=hook_permission,
        )
        common = {"tool_name": tool_name, "tool_input": tool_input, "tool_use_id": tool_use_id}
        if outcome.error_code in PERMISSION_DENIAL_CODES:
            self._emit_lifecycle(bus, HookEvent.PERMISSION_DENIED, **common, reason=outcome.content[:2000])
        elif outcome.status == "succeeded":
            post = self._emit_lifecycle(
                bus,
                HookEvent.POST_TOOL_USE,
                **common,
                tool_response=_tool_response(outcome),
            )
            if post is not None:
                if post.stop:
                    self.stop_requested = post.stop_reason or post.reason or "stopped by hook"
                if post.updated_tool_output is not None and internal.startswith("mcp__"):
                    replaced = post.updated_tool_output
                    if not isinstance(replaced, str):
                        try:
                            replaced = json.dumps(replaced, ensure_ascii=False, default=str)
                        except (TypeError, ValueError):
                            replaced = str(replaced)
                    outcome.content = replaced[:MAX_TOOL_RESULT_CHARS]
                reminders.extend(post.additional_context)
                if post.blocked:
                    reminders.append(f"PostToolUse hook: {post.reason or 'feedback from hook'}")
            _append_reminder(outcome, reminders, MAX_TOOL_RESULT_CHARS)
            self._emit_tool_side_events(bus, internal, dict(arguments or {}), outcome, cwd_before)
        else:
            failure = self._emit_lifecycle(
                bus,
                HookEvent.POST_TOOL_USE_FAILURE,
                **common,
                error=outcome.content[:2000] or outcome.summary,
                error_code=outcome.error_code,
            )
            if failure is not None:
                reminders.extend(failure.additional_context)
            _append_reminder(outcome, reminders, MAX_TOOL_RESULT_CHARS)
        return self._speak_public(outcome)

    def _tool_kind_for_hooks(self, internal: str) -> str:
        try:
            return self.resolve(internal).kind
        except Exception:  # noqa: BLE001 - an unknown tool still needs a payload
            return ""

    def _emit_lifecycle(self, bus: LifecycleBus, event: HookEvent, **payload: Any) -> LifecycleDecision | None:
        """Emit a lifecycle event and return the merged decision.

        A failing payload or subscriber never breaks the tool call: the result is None.
        """
        try:
            return bus.emit(event, **{**self._lifecycle_context, **payload})
        except Exception:  # noqa: BLE001 - lifecycle never breaks a turn
            logger.exception("lifecycle emit failed for %s", getattr(event, "value", event))
            return None

    def _emit_tool_side_events(
        self,
        bus: LifecycleBus,
        internal: str,
        arguments: Mapping[str, Any],
        outcome: ToolOutcome,
        cwd_before: Path,
    ) -> None:
        payload = outcome.payload or {}
        if internal == "enter_worktree":
            self._emit_lifecycle(bus, HookEvent.WORKTREE_CREATE, worktree=dict(payload.get("worktree") or {}))
        elif internal == "exit_worktree" and str(arguments.get("action") or "keep") == "remove":
            # exit_worktree clears the active name before this runs, so the outcome carries it
            self._emit_lifecycle(bus, HookEvent.WORKTREE_REMOVE, worktree_name=str(payload.get("worktree_name") or ""))
        if self._current_cwd != cwd_before:
            self._emit_lifecycle(bus, HookEvent.CWD_CHANGED, old_cwd=str(cwd_before), new_cwd=str(self._current_cwd))

    def _invoke_unobserved(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str | None = None,
        invocation_id: str | None = None,
        hook_permission: str | None = None,
    ) -> ToolOutcome:
        requested_name = name
        name = self.key_for(name)
        try:
            arguments = _public_arguments_in(name, dict(arguments or {}))
        except Exception:  # noqa: BLE001 - a model-chosen value must never crash the turn
            return ToolOutcome("failed", "Argumentos inválidos"[:240], f"Invalid arguments for {requested_name}.", {}, "INVALID_ARGUMENTS")
        self._active_invocation_id = call_id or invocation_id or getattr(self, "_active_invocation_id", None)
        # A model can hallucinate a tool name, so an unknown tool is a result the
        # model reads and corrects, never an exception that kills the turn.
        if self._read_only_mode and name in {
            "write_file", "edit_file", "notebook_edit", "run_command", "powershell", "monitor", "stop_process", "verify_project",
            "generate_image", "generate_audio", "generate_video",
        }:
            return ToolOutcome(
                "failed",
                "Bloqueado em subagente somente-leitura",
                f"Tool '{name}' is not allowed in read-only subagent mode.",
                {"tool_kind": "filesystem"},
                "READ_ONLY_SUBAGENT",
            )
        try:
            definition = self.resolve(name)
        except AgentToolError as error:
            return ToolOutcome("failed", f"Ferramenta desconhecida: {name}"[:240], str(error), {}, "UNKNOWN_TOOL")
        arguments, coercion_note = coerce_tool_arguments(name, arguments)
        broker_tool_name = name
        broker_args: Mapping[str, Any] = arguments
        if name == "notebook_edit":
            broker_args = {**arguments, "path": str(arguments.get("notebook_path") or arguments.get("path") or "")}
        if self._plan_mode_active or self._permission_mode.value == "plan_mode":
            plan_decision = self._permission_broker.evaluate(
                broker_tool_name,
                broker_args,
                self._permission_mode,
                plan_mode_active=True,
            )
            if plan_decision == "deny":
                return ToolOutcome(
                    "failed",
                    "Bloqueado no Modo Plano",
                    "No Modo Plano (plan_mode), apenas ferramentas de leitura e escrita de planos em `.orin/plans/` são permitidas. Use `exit_plan_mode` quando o plano estiver concluído.",
                    {"tool_kind": definition.kind, "code_mode": True, "plan_mode": True},
                    "CODE_PLAN_MODE_READONLY",
                )
        if hook_permission == "ask" or self._code_mode_active or self._interactive_permissions or self._permission_broker.has_rules():
            decision = self._permission_broker.decide(
                broker_tool_name,
                broker_args,
                self._permission_mode,
                plan_mode_active=self._plan_mode_active,
            )
            from agentos.permissions.engine import PermissionDecision

            # A PreToolUse hook's verdict wins over the engine's ask/allow; deny rules still stand.
            # W2-R5: a hook may skip an ordinary prompt, never one that only a person may approve (protected paths, ask rules).
            if hook_permission == "allow" and decision.behavior == "ask" and not decision.requires_human:
                decision = PermissionDecision("allow", "approved by a PreToolUse hook")
            elif hook_permission == "ask" and decision.behavior == "allow":
                decision = PermissionDecision("ask", "a PreToolUse hook asked for confirmation", requires_human=True)
            perm_decision = decision.behavior
            if perm_decision == "deny":
                return ToolOutcome(
                    "failed",
                    "Permissão negada por regra do projeto",
                    f"A execução de `{name}` foi bloqueada pelas regras de permissão (settings).",
                    {"tool_kind": definition.kind, "code_mode": True},
                    "CODE_PERMISSION_DENIED",
                )
            if perm_decision == "ask" and self._lifecycle is not None:
                request = self._emit_lifecycle(
                    self._lifecycle,
                    HookEvent.PERMISSION_REQUEST,
                    tool_name=_public_tool_name(name),
                    tool_input=_safe_public_input(name, arguments),
                    permission_suggestions=[],
                )
                if request is not None:
                    if request.stop:
                        self.stop_requested = request.stop_reason or request.reason or "stopped by hook"
                        return ToolOutcome(
                            "failed", "Permissão interrompida por hook",
                            f"Um hook PermissionRequest interrompeu a execução de `{name}`: {request.reason or request.stop_reason or 'sem motivo informado'}",
                            {"tool_kind": definition.kind, "code_mode": True}, "CODE_PERMISSION_DENIED",
                        )
                    if request.permission_decision == "deny":
                        return ToolOutcome(
                            "failed", "Permissão negada por hook",
                            f"Um hook PermissionRequest negou `{name}`: {request.reason or 'sem motivo informado'}",
                            {"tool_kind": definition.kind, "code_mode": True}, "CODE_PERMISSION_DENIED",
                        )
                    if request.permission_decision == "allow":
                        if isinstance(request.updated_input, Mapping):
                            # W2-R4: a rewritten input is a new request. It is coerced and checked by the plan mode and
                            # the permission engine exactly as the original was; a deny rule always stands.
                            try:
                                candidate, _ = coerce_tool_arguments(name, _public_arguments_in(name, dict(request.updated_input)))
                            except Exception:  # noqa: BLE001 - a hook's malformed input must never crash the turn
                                return ToolOutcome(
                                    "failed", "Argumentos inválidos", f"Invalid arguments for {name}.",
                                    {"tool_kind": definition.kind, "code_mode": True}, "INVALID_ARGUMENTS",
                                )
                            candidate_broker_args = _broker_arguments(name, candidate)
                            if self._plan_mode_active or self._permission_mode.value == "plan_mode":
                                if self._permission_broker.evaluate(
                                    broker_tool_name, candidate_broker_args, self._permission_mode, plan_mode_active=True,
                                ) == "deny":
                                    return ToolOutcome(
                                        "failed",
                                        "Bloqueado no Modo Plano",
                                        "No Modo Plano (plan_mode), apenas ferramentas de leitura e escrita de planos em `.orin/plans/` são permitidas. Use `exit_plan_mode` quando o plano estiver concluído.",
                                        {"tool_kind": definition.kind, "code_mode": True, "plan_mode": True},
                                        "CODE_PLAN_MODE_READONLY",
                                    )
                            recheck = self._permission_broker.decide(
                                broker_tool_name,
                                candidate_broker_args,
                                self._permission_mode,
                                plan_mode_active=self._plan_mode_active,
                            )
                            if recheck.behavior == "deny":
                                return ToolOutcome(
                                    "failed",
                                    "Permissão negada por regra do projeto",
                                    f"A execução de `{name}` foi bloqueada pelas regras de permissão (settings).",
                                    {"tool_kind": definition.kind, "code_mode": True},
                                    "CODE_PERMISSION_DENIED",
                                )
                            arguments = candidate
                            decision = recheck
                        # The hook's allow never overrides a decision that only a person may make.
                        if not decision.requires_human:
                            perm_decision = "allow"
            if perm_decision == "ask":
                if self._interactive_permissions:
                    perm_result = self._permission_broker.request_and_wait(
                        tool_name=name,
                        arguments=arguments,
                        turn_id=self.turn_id,
                        conversation_id=self.conversation_id,
                        permission_mode=self._permission_mode,
                        emit_request=self._on_permission_request,
                    )
                    if perm_result.get("decision") not in {"allow_once", "allow_always"}:
                        reason_text = str(perm_result.get("reason") or "").strip()
                        output_text = (
                            f"O usuário negou a permissão para executar `{name}` e orientou: {reason_text}"
                            if reason_text
                            else f"O usuário negou a permissão para executar `{name}`."
                        )
                        return ToolOutcome(
                            "failed",
                            "Permissão negada pelo usuário",
                            output_text,
                            {
                                "tool_kind": definition.kind,
                                "code_mode": True,
                                "permission_request_id": perm_result.get("request_id"),
                                **({"reason": reason_text} if reason_text else {}),
                            },
                            "CODE_PERMISSION_DENIED",
                        )
                elif decision.requires_human:
                    return ToolOutcome(
                        "failed",
                        "Confirmação necessária",
                        f"`{name}` precisa da confirmação de uma pessoa ({decision.reason}), e esta execução não tem como pedir.",
                        {"tool_kind": definition.kind, "code_mode": True},
                        "CODE_PERMISSION_DENIED",
                    )
                elif self._code_mode_active and not self._non_interactive_ask_allowed:
                    return ToolOutcome(
                        "failed",
                        "Confirmação necessária",
                        f"`{name}` needs a person's confirmation ({decision.reason}) and this run cannot ask. Allow it with a "
                        "permissions.allow rule, or set permissions.nonInteractiveAsk to \"allow\" in your user settings.",
                        {"tool_kind": definition.kind, "code_mode": True},
                        "CODE_PERMISSION_DENIED",
                    )
                elif self._code_mode_requires_approval and name in {"write_file", "edit_file", "notebook_edit", "run_command", "powershell", "monitor", "verify_project"}:
                    path = str(arguments.get("path") or arguments.get("notebook_path") or "")
                    if not (name in {"write_file", "edit_file", "notebook_edit"} and path.replace("\\", "/").lstrip("./").startswith(".orin/plans/")):
                        return ToolOutcome(
                            "failed",
                            "Aguardando aprovação do plano",
                            "No Modo Code, registre o contrato e aguarde a aprovação do plano antes de alterar código ou executar comandos.",
                            {"tool_kind": definition.kind, "code_mode": True},
                            "CODE_PLAN_APPROVAL_REQUIRED",
                        )
        if self._code_mode_active and name in {"run_command", "powershell", "monitor"}:
            command = str(arguments.get("command") or "")
            if re.search(r"\b(deploy|release|publish)\b", command, re.IGNORECASE):
                return ToolOutcome(
                    "failed", "Confirmação necessária para produção",
                    "Deploy, publicação e release em produção sempre exigem confirmação explícita da pessoa.",
                    {"tool_kind": definition.kind, "code_mode": True}, "CODE_DEPLOY_CONFIRMATION_REQUIRED",
                )
            if re.search(r"\bgit\s+push\b", command, re.IGNORECASE) and not self._code_mode_permits_push:
                return ToolOutcome(
                    "failed", "Confirmação necessária para publicar",
                    "Commits locais são permitidos; git push precisa de confirmação explícita ou da preferência Autonomia total.",
                    {"tool_kind": definition.kind, "code_mode": True}, "CODE_PUSH_CONFIRMATION_REQUIRED",
                )
            if re.search(r"\bgh\s+(?:pr|repo)\b", command, re.IGNORECASE) and not self._code_mode_permits_pr:
                return ToolOutcome(
                    "failed", "Confirmação necessária para publicar",
                    "Commits locais são permitidos; pull request precisa de confirmação ou da preferência Autonomia total.",
                    {"tool_kind": definition.kind, "code_mode": True}, "CODE_PUSH_CONFIRMATION_REQUIRED",
                )
        limit = definition.max_result_size_chars
        err_payload: dict[str, Any] = {"tool_kind": definition.kind}
        if name in {"run_command", "powershell", "monitor"}:
            cmd_arg = str(arguments.get("command") or "")[:400]
            if cmd_arg:
                err_payload["command"] = cmd_arg
        try:
            result = definition.handler(**dict(arguments))
        except (AgentToolError, WorkspaceError) as error:
            error_code = getattr(error, "error_code", "TOOL_REFUSED") or "TOOL_REFUSED"
            return ToolOutcome("failed", str(error)[:240], str(error), err_payload, error_code)
        except TypeError as error:
            message = f"Invalid arguments for {name}: {error}"
            if name in {"write_file", "edit_file"} or "'path'" in str(error) or "'content'" in str(error):
                message += (
                    " Hint: Ensure 'path' is provided first; if you are writing a large file, "
                    "your output may have been truncated at max_tokens — write smaller modular "
                    "files or use edit_file in chunks."
                )
            return ToolOutcome("failed", message[:240], message, err_payload, "INVALID_ARGUMENTS")
        except Exception as error:  # noqa: BLE001 - the model must see why a tool failed
            message = f"{type(error).__name__}: {error}"
            return ToolOutcome("failed", f"{name} falhou", message[:limit], err_payload, "TOOL_FAILED")
        if isinstance(result, ToolOutcome):
            outcome = self._finalize_outcome(result, definition.kind, limit=limit, tool_name=name)
        else:
            requested = result.pop("max_result_chars", None) if isinstance(result, dict) else None
            if isinstance(requested, int) and not isinstance(requested, bool) and requested > limit:
                limit = min(requested, LARGE_RESULT_CHARS)
            raw_content = str(result.get("content", ""))
            content, truncated = _bounded(raw_content, limit)
            payload = dict(result.get("payload") or {})
            payload.setdefault("tool_kind", definition.kind)
            if truncated:
                payload["truncated"] = True
                saved_path = self._persist_truncated_output(name, raw_content)
                if saved_path:
                    payload["full_output_path"] = saved_path
                saved_hint = f" Full output saved to {saved_path}." if saved_path else ""
                notice = (
                    f"\n\n[output truncated at {limit} characters —{saved_hint} "
                    "narrow the request instead of repeating it: use read_file with offset/limit, "
                    "search_files with a tighter pattern, or a command that prints less]"
                )
                if self.name_translator is not None:
                    notice = public_text(notice)
                # The notice itself counts against the budget, so make room for it
                # rather than growing content past the tool result limit.
                content, _ = _bounded(raw_content, max(0, limit - len(notice)))
                content += notice
            images = [dict(item) for item in (result.get("images") or ()) if isinstance(item, Mapping)]
            extra_data = {k: v for k, v in result.items() if k not in {"summary", "content", "payload", "images"}}
            outcome = ToolOutcome(
                "succeeded",
                str(result.get("summary", f"{name} concluído"))[:240],
                content,
                payload,
                images=images,
                data=extra_data,
            )
        if coercion_note and outcome.status == "succeeded":
            if len(outcome.content) + len(coercion_note) <= limit:
                outcome.content += coercion_note
        if name in {"write_file", "edit_file"} and outcome.status == "succeeded":
            outcome = self._with_file_diagnostic(outcome, arguments)
        if not (name in {"run_command", "powershell"} and bool(arguments.get("background"))) and name not in {"read_process_output", "stop_process", "list_files"}:
            bg_notice = self._check_exited_background_processes()
            if bg_notice and len(outcome.content) + len(bg_notice) <= limit:
                outcome.content += bg_notice
        self._observe_mutation_outcome(name, arguments, outcome)
        return outcome

    def _persist_truncated_output(self, tool_name: str, full_content: str) -> str | None:
        """Persist full untruncated tool output inside .orin/tool-outputs/ for paged inspection."""
        try:
            rel_dir = ".orin/tool-outputs"
            out_dir = self.workspace.resolve(rel_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", tool_name)[:32] or "tool"
            rel_path = f"{rel_dir}/{safe_name}-{uuid4().hex[:12]}.txt"
            target = self.workspace.resolve(rel_path)
            target.write_text(full_content, encoding="utf-8", errors="replace")
            return rel_path
        except Exception:
            return None

    def _with_file_diagnostic(self, outcome: ToolOutcome, arguments: Mapping[str, Any]) -> ToolOutcome:
        """Append a fast, single-file lint/typecheck result to a write's own output.

        This is the mechanical feedback loop a weak model needs most: it never
        has to think to ask for it, and the error is right there in the same
        result it is already reading. Debounced per path so a file rewritten
        across several ``write_file``/``append`` calls is not re-linted on
        every one of them.
        """
        if not self._enable_terminal:
            # Linters configured by the project (eslint, ruff plugins) are code
            # execution. Without a terminal capability nothing runs at all.
            return outcome
        path = str(outcome.payload.get("path") or arguments.get("path") or "")
        if not path:
            return outcome
        command = file_diagnostic_command(path, project_root=self.workspace.root)
        if command is None:
            return outcome
        now = time.monotonic()
        last = self._diagnosed_at.get(path)
        if last is not None and now - last < DIAGNOSTIC_DEBOUNCE_SECONDS:
            return outcome
        self._diagnosed_at[path] = now
        try:
            completed = subprocess.run(
                command, shell=True, cwd=str(self.workspace.root), capture_output=True, text=True,
                timeout=DIAGNOSTIC_TIMEOUT_SECONDS, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            return outcome
        body = (completed.stdout or "") + (completed.stderr or "")
        if not body.strip():
            if completed.returncode == 0:
                addition = f"\n\n[diagnóstico automático — {command}: nenhum problema encontrado]"
            else:
                return outcome
        else:
            trimmed, _ = _bounded_output(body.strip(), MAX_FILE_DIAGNOSTIC_CHARS)
            status = "sem problemas" if completed.returncode == 0 else "encontrou problemas"
            addition = f"\n\n[diagnóstico automático — {command} ({status})]\n{trimmed}"
        return ToolOutcome(outcome.status, outcome.summary, outcome.content + addition, outcome.payload, outcome.error_code, outcome.images, outcome.data)

    @staticmethod
    def _is_verification_command(command_str: str) -> bool:
        return is_verification_command(command_str)

    def _observe_mutation_outcome(self, name: str, arguments: Mapping[str, Any], outcome: ToolOutcome) -> None:
        """Track workspace changes, and, in Code mode, the checks run against them.

        Change tracking runs unconditionally: a plain chat turn that edits a
        file is subject to the same verify-before-responding gate the runtime
        enforces for every turn (see ``change_events``/``has_unverified_changes``
        in the runtime), not only Code mode. Code mode's stricter "you must
        have run a real check" gate (``code_completion_gate``) stays layered on
        top, active only when Code mode is active.
        """
        if outcome.status != "succeeded":
            return
        self._tool_calls_count += 1
        payload = outcome.payload or {}
        if name in {"write_file", "edit_file", "notebook_edit"}:
            path = str(payload.get("path") or arguments.get("path") or arguments.get("notebook_path") or "").replace("\\", "/")
            if path and not path.startswith(".orin/"):
                self._changed_paths.add(path)
                self._change_events += 1
                self._code_mode_verified = False
            return
        if name == "verify_project":
            if self._code_mode_active and not payload.get("scaffold"):
                steps = payload.get("steps")
                if (
                    payload.get("all_passed") is True
                    and isinstance(steps, list)
                    and any(item.get("ran") for item in steps if isinstance(item, Mapping))
                ) or payload.get("recipe") is None:
                    self._code_mode_verified = True
            # A scaffold can create files but is not a verification result.
            artifacts = payload.get("artifacts")
            if isinstance(artifacts, list) and any(
                isinstance(item, Mapping) and isinstance(item.get("path"), str) and not str(item["path"]).startswith(".orin/")
                for item in artifacts
            ):
                self._change_events += 1
                self._code_mode_verified = False
            return
        if name not in {"run_command", "powershell"} or bool(payload.get("failed")):
            return
        command_str = str(arguments.get("command") or payload.get("command") or "").strip()
        is_verif = self._is_verification_command(command_str)
        artifacts = payload.get("artifacts")
        changed_any = False
        ignored_prefixes = (
            ".orin/",
            "shots/",
            "screenshots/",
            "test-results/",
            "playwright-report/",
            "coverage/",
            "browser-captures/",
        )
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, Mapping) and isinstance(artifact.get("path"), str):
                    path = str(artifact["path"]).replace("\\", "/").lstrip("./")
                    if not path.startswith(ignored_prefixes):
                        self._changed_paths.add(path)
                        changed_any = True
        if is_verif and payload.get("exit_code") == 0:
            self._code_mode_verified = True
        elif changed_any:
            self._change_events += 1
            self._code_mode_verified = False

    def change_events(self) -> int:
        """How many times a workspace-changing tool call has succeeded this turn.

        The runtime compares this to the value recorded the last time
        verification concluded to decide whether a new one is owed before the
        turn may respond.
        """
        return self._change_events

    def code_completion_gate(self) -> tuple[bool, str | None]:
        """Require actual checks after implementation changes before delivery."""
        if not self._code_mode_active:
            return True, None
        if not self._changed_paths:
            if (
                self._tool_calls_count == 0
                and not self._code_mode_requires_approval
                and not self._plan_mode_active
                and self._code_mode_work_kind in {"implementation", "bugfix", "refactor"}
            ):
                self._code_completion_gate_rejections += 1
                return (
                    False,
                    "Você está no Modo Code e ainda não executou nenhuma ferramenta para inspecionar ou alterar o workspace. "
                    "Use as ferramentas disponíveis (como `list_files`, `read_file`, `write_file`, `edit_file`, `verify_project`, `run_command`) antes de concluir.",
                )
            return True, None
        if not self._code_mode_verified:
            self._code_completion_gate_rejections += 1
            return False, "Você alterou código, mas ainda não há evidência estruturada de `verify_project` com etapas executadas e aprovadas. Rode `verify_project` antes de concluir."
        return True, None

    def _finalize_outcome(self, outcome: ToolOutcome, kind: str, *, limit: int = MAX_TOOL_RESULT_CHARS, tool_name: str = "tool") -> ToolOutcome:
        payload = dict(outcome.payload or {})
        payload.setdefault("tool_kind", kind)
        raw_content = str(outcome.content or "")
        content, truncated = _bounded(raw_content, limit)
        if truncated:
            saved_path = self._persist_truncated_output(tool_name, raw_content)
            if saved_path:
                payload["full_output_path"] = saved_path
            saved_hint = f" Full output saved to {saved_path}." if saved_path else ""
            notice = (
                f"\n\n[output truncated at {limit} characters —{saved_hint} "
                "narrow the request instead of repeating it: use read_file with offset/limit, "
                "search_files with a tighter pattern, or a command that prints less]"
            )
            content, _ = _bounded(raw_content, max(0, limit - len(notice)))
            content += notice
            payload["truncated"] = True
        return ToolOutcome(
            outcome.status,
            str(outcome.summary or "tool completed")[:240],
            content,
            payload,
            outcome.error_code,
            list(outcome.images or []),
            dict(outcome.data or {}),
        )

    # -- handlers -------------------------------------------------------

    def _snapshot_before_write(self, path: str) -> None:
        """Keep the file as it is now: in the per-turn checkpoint (today's UI rewind) and the per-message history."""
        self._checkpoint_store.snapshot_before_write(self.turn_id or "turn-default", path)
        history = self._file_history
        if history is None or not self._history_message_id:
            return
        try:
            target = self.workspace.resolve(path)
        except Exception:  # noqa: BLE001 - the edit itself reports a bad path
            return
        history.snapshot_before_write(self._history_message_id, target, turn_id=self.turn_id or "")

    def rewind_turn(self, turn_id: str | None = None) -> dict[str, Any]:
        """Restore workspace files modified during ``turn_id`` back to their pre-turn state."""
        return self._checkpoint_store.rewind_turn(str(turn_id or self.turn_id or "turn-default"))


    def close(self) -> None:
        self._detach_monitors()
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:
                pass
        if self._mcp_provider is not None:
            self._mcp_provider.close()

    # -- skills --------------------------------------------------------

    def _available_tool_names(self) -> tuple[str, ...]:
        """Report actual tools; a Skill declaration never creates a tool."""
        return tuple(item.name for item in self.definitions() if item.kind != "skill")

    def post_compact_enrichment(self) -> str:
        """Build a re-hydration markdown block after context compaction (top-5 recent files, todos, and active plan)."""
        sections: list[str] = []
        with self._read_file_lock:
            read_items = list(self._read_file_state.items())
        if read_items:
            recent = sorted(
                read_items,
                key=lambda item: (item[1].read_seq, item[1].mtime_ns),
                reverse=True,
            )[:5]
            file_blocks: list[str] = []
            for rel_path, _record in recent:
                try:
                    content, _ = self.workspace.read_text(rel_path)
                except Exception:
                    continue
                snippet = content[:3000]
                if len(content) > 3000:
                    snippet += "\n... [truncated for post-compact re-hydration]"
                file_blocks.append(f"#### `{rel_path}`\n```\n{snippet}\n```")
            if file_blocks:
                sections.append("### Recently Accessed Files (Re-hydrated)\n" + "\n\n".join(file_blocks))
        if self._todos:
            todo_lines: list[str] = []
            for item in self._todos:
                status = item.get("status", "pending")
                marker = "[x]" if status == "completed" else "[~]" if status == "in_progress" else "[ ]"
                active = f" ({item['active_form']})" if status == "in_progress" and item.get("active_form") else ""
                todo_lines.append(f"- {marker} {item.get('id', '')}: {item.get('content', '')}{active}")
            sections.append("### Current Todo List\n" + "\n".join(todo_lines))
        try:
            plans_dir = self.workspace.root / ".orin" / "plans"
            plan_files = (
                sorted(f".orin/plans/{p.name}" for p in plans_dir.glob("*.md") if p.is_file())
                if plans_dir.is_dir()
                else []
            )
        except Exception:
            plan_files = []
        if plan_files:
            plan_path = max(plan_files, key=lambda rel: (self.workspace.root / rel).stat().st_mtime_ns)
            try:
                plan_text, _ = self.workspace.read_text(plan_path)
                if plan_text.strip():
                    sections.append(f"### Active Plan (`{plan_path}`)\n{plan_text[:2000]}")
            except Exception:
                pass
        return "\n\n".join(sections)

TRUNCATED_ARGUMENTS_MESSAGE = (
    "Tool arguments were cut off before the call ended — the output limit was reached in the "
    "middle of this call, so the payload is incomplete. Do not resend it unchanged: split it. "
    "Write a long file in parts (write_file for the first part, then write_file with "
    "mode=\"append\" for each following part), and save a long script with write_file before "
    "running it with run_command."
)
MALFORMED_ARGUMENTS_MESSAGE = (
    "Tool arguments were not valid JSON and could not be repaired. Resend the call with every "
    "string properly escaped, or split a long payload into smaller write_file calls "
    "(mode=\"append\" adds to the end of an existing file)."
)


def parse_arguments(raw: object, known_keys: Collection[str] | None = None) -> dict[str, Any]:
    """Read a tool call's arguments, repairing JSON a model wrote incorrectly.

    ``known_keys`` are the argument names the target tool actually declares.
    When a broken payload has more than one reading that accounts for the whole
    text — file content that itself looks like JSON does this — the reading
    whose keys all belong to the tool is the one the model meant.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip() or "{}"
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return _ToolArgumentReader(text, known_keys).parse()
        if not isinstance(value, Mapping):
            raise AgentToolError("Tool arguments must be a JSON object.")
        return dict(value)
    raise AgentToolError("Tool arguments must be a JSON object.")


_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_JSON_WHITESPACE = " \t\r\n"
_JSON_LITERALS = {"true": True, "false": False, "null": None}
_VALUE_STARTS = '"{[-0123456789tfn]'  # ']' covers a trailing comma before it
# A model writing a long CSS/JS payload leaves genuinely ambiguous text, so the
# reader below explores alternatives. The budget counts characters examined, not
# alternatives, so a hostile or hopeless payload cannot turn that search into an
# unbounded amount of work whatever shape it takes. A payload that can be read at
# all is read in a few passes over its own text; anything needing far more than
# that is not going to resolve, and the model is better served by a fast error.
_REPAIR_STEPS_PER_CHARACTER = 20
_MAX_REPAIR_STEPS = 4_000_000
_MAX_REPAIR_DEPTH = 40


class _RepairBudgetExhausted(Exception):
    """The search for a consistent reading of the payload ran too long."""


class _ToolArgumentReader:
    """Read the arguments object a model *meant* to send when its JSON is invalid.

    Providers stream tool arguments as raw text, and a model writing a file full
    of CSS or JavaScript routinely breaks that text: it leaves literal newlines
    inside a string, writes ``"Inter"`` or ``[data-role="tab"]`` without escaping
    the inner quotes, and emits ``\\d`` or ``\\201C``, which JSON rejects as an
    invalid escape. No per-character rule can tell a quote that ends a value
    from a quote that belongs to the content, so this reader does not try: it
    enumerates the possible readings lazily and keeps the first one that
    accounts for every byte of the payload. Where more than one reading does
    that, the tool's own argument names decide (see ``parse``).

    Only strings are treated as ambiguous. Nothing here invents structure: a
    payload that no reading explains is still an error the model must fix.
    """

    def __init__(self, text: str, known_keys: Collection[str] | None = None) -> None:
        self.text = text
        self.known_keys = frozenset(known_keys) if known_keys else None
        self.budget = min(_MAX_REPAIR_STEPS, _REPAIR_STEPS_PER_CHARACTER * len(text) + 50_000)
        self.declared_keys_only = False

    def parse(self) -> dict[str, Any]:
        # First pass: only the argument names the tool declares may end a string
        # value. That is what tells ``"font-family: "Inter", sans-serif"`` apart
        # from a real ``", "content":`` boundary, and it collapses the search on
        # a payload whose content is itself full of JSON-looking text. A model
        # that also invented an argument name gets the second, permissive pass.
        for declared_only in (True, False) if self.known_keys else (False,):
            self.declared_keys_only = declared_only
            self.budget = min(_MAX_REPAIR_STEPS, _REPAIR_STEPS_PER_CHARACTER * len(self.text) + 50_000)
            reading = self._first_complete_reading()
            if reading is not None:
                return reading
        # Text that stops before its closing brace was cut off mid-call; text
        # that reaches it is complete but written wrong. The model can only act
        # on the difference if we tell it which one happened.
        raise AgentToolError(MALFORMED_ARGUMENTS_MESSAGE if self.text.rstrip().endswith("}") else TRUNCATED_ARGUMENTS_MESSAGE)

    def _first_complete_reading(self) -> dict[str, Any] | None:
        index = self._skip(0)
        if index >= len(self.text) or self.text[index] != "{":
            return None
        try:
            for value, end in self._object(index, 0):
                if self._skip(end) < len(self.text):
                    continue
                if not self.declared_keys_only or self.known_keys.issuperset(value):
                    return value
        except _RepairBudgetExhausted:
            return None
        return None

    def _spend(self, cost: int = 1) -> None:
        self.budget -= cost
        if self.budget <= 0:
            raise _RepairBudgetExhausted

    def _skip(self, index: int) -> int:
        while index < len(self.text) and self.text[index] in _JSON_WHITESPACE:
            index += 1
        return index

    def _object(self, index: int, depth: int):
        if depth <= _MAX_REPAIR_DEPTH:
            yield from self._members(index + 1, {}, depth)

    def _members(self, index: int, current: dict[str, Any], depth: int):
        index = self._skip(index)
        if index >= len(self.text):
            return
        character = self.text[index]
        if character == "}":
            self._spend()
            yield dict(current), index + 1
        elif character == ",":
            yield from self._members(index + 1, current, depth)
        elif character == '"':
            for key, after_key in self._string(index, "key"):
                cursor = self._skip(after_key)
                if cursor < len(self.text) and self.text[cursor] == ":":
                    for value, after_value in self._value(cursor + 1, "object", depth):
                        yield from self._members(after_value, {**current, key: value}, depth)

    def _array(self, index: int, depth: int):
        if depth <= _MAX_REPAIR_DEPTH:
            yield from self._items(index + 1, [], depth)

    def _items(self, index: int, current: list[Any], depth: int):
        index = self._skip(index)
        if index >= len(self.text):
            return
        character = self.text[index]
        if character == "]":
            self._spend()
            yield list(current), index + 1
        elif character == ",":
            yield from self._items(index + 1, current, depth)
        else:
            for value, after in self._value(index, "array", depth):
                yield from self._items(after, [*current, value], depth)

    def _value(self, index: int, container: str, depth: int):
        index = self._skip(index)
        if index >= len(self.text):
            return
        character = self.text[index]
        if character == "{":
            yield from self._object(index, depth + 1)
        elif character == "[":
            yield from self._array(index, depth + 1)
        elif character == '"':
            yield from self._string(index, "value" if container == "object" else "item")
        else:
            yield from self._scalar(index)

    def _scalar(self, index: int):
        end = index
        while end < len(self.text) and self.text[end] not in ",}]" and self.text[end] not in _JSON_WHITESPACE:
            end += 1
        token = self.text[index:end]
        if token in _JSON_LITERALS:
            self._spend()
            yield _JSON_LITERALS[token], end
            return
        try:
            number = json.loads(token)
        except ValueError:
            return
        self._spend()
        yield number, end

    def _string(self, index: int, role: str):
        """Yield every reading of the string starting at ``index``, shortest first.

        A quote that could close the string is offered as one reading and then
        kept as content, so the caller decides which one actually parses.
        """
        cursor = index + 1
        parts: list[str] = []
        while cursor < len(self.text):
            self._spend()
            character = self.text[cursor]
            if character == "\\":
                consumed, decoded = self._escape(cursor)
                if consumed == 0:
                    return
                parts.append(decoded)
                cursor += consumed
                continue
            if character == '"' and self._may_terminate(cursor, role):
                # Materializing a candidate costs as much as the text behind it,
                # so charge for it: that is what keeps a payload with thousands
                # of possible boundaries from being explored quadratically.
                self._spend(len(parts))
                yield "".join(parts), cursor + 1
            parts.append(character)
            cursor += 1

    def _escape(self, index: int) -> tuple[int, str]:
        following = self.text[index + 1: index + 2]
        if not following:
            return 0, ""
        if following in _JSON_ESCAPES:
            return 2, _JSON_ESCAPES[following]
        if following == "u":
            raw = self.text[index + 2: index + 6]
            if len(raw) == 4 and all(character in "0123456789abcdefABCDEF" for character in raw):
                code = int(raw, 16)
                if 0xD800 <= code <= 0xDBFF and self.text[index + 6: index + 8] == "\\u":
                    low_raw = self.text[index + 8: index + 12]
                    if len(low_raw) == 4 and all(character in "0123456789abcdefABCDEF" for character in low_raw):
                        low = int(low_raw, 16)
                        if 0xDC00 <= low <= 0xDFFF:
                            return 12, chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00))
                if 0xD800 <= code <= 0xDFFF:
                    # A lone surrogate cannot be encoded as UTF-8 later on.
                    return 6, "�"
                return 6, chr(code)
        # Not a JSON escape at all — a CSS codepoint or a regex like ``\d``.
        # The backslash is content; the character after it is read normally.
        return 1, "\\"

    def _may_terminate(self, index: int, role: str) -> bool:
        after = self._skip(index + 1)
        if after >= len(self.text):
            return True
        character = self.text[after]
        if role == "key":
            return character == ":"
        if role == "value":
            return character == "}" or (character == "," and self._key_ahead(after + 1))
        return character == "]" or (character == "," and self._item_ahead(after + 1))

    def _key_ahead(self, index: int) -> bool:
        index = self._skip(index)
        if index >= len(self.text):
            return False
        if self.text[index] == "}":
            return True  # a trailing comma before the closing brace
        if self.text[index] != '"':
            return False
        cursor = index + 1
        while cursor < len(self.text):
            character = self.text[cursor]
            if character == "\\":
                cursor += 2
                continue
            if character in "\r\n":
                return False
            if character == '"':
                after = self._skip(cursor + 1)
                if after >= len(self.text) or self.text[after] != ":":
                    return False
                return not self.declared_keys_only or self.text[index + 1: cursor] in self.known_keys
            cursor += 1
        return False

    def _item_ahead(self, index: int) -> bool:
        index = self._skip(index)
        return index < len(self.text) and self.text[index] in _VALUE_STARTS


__all__ = [
    "AgentToolError",
    "AgentToolset",
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "MAX_COMMAND_TIMEOUT_SECONDS",
    "MAX_TOOL_RESULT_CHARS",
    "ReadFileRecord",
    "ToolDefinition",
    "ToolOutcome",
    "coerce_tool_arguments",
    "compute_structured_patch",
    "parse_arguments",
    "shlex",
]

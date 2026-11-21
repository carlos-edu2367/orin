"""Module-level helpers shared by AgentToolset and the per-tool mixins (moved from agent_tools.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
from datetime import UTC, datetime
import difflib
import hashlib
from html.parser import HTMLParser
import itertools
import idna
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
from agentos.pathsafety import resolve_contained
from agentos.reading.extract import extract_text
from agentos.reading.render import ImageTooLarge, normalize_image, render_pdf_pages
from agentos.reading.vision import VisionUnavailable
from agentos.agentic.diagnostics import STEP_ORDER, detect_recipe, file_diagnostic_command
from agentos.agentic.workspace import MAX_LIST_DEPTH, MAX_SEARCH_RESULTS, ConversationWorkspace, WorkspaceError
from agentos.agentic.models import MAX_USER_QUESTION_ITEMS
from agentos.agentic.file_preview import media_type_for
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.reminders import system_reminder
from agentos.agentic.tools.public_names import (
    PublicToolNames,
    arguments_in as _public_arguments_in,
    arguments_out as _public_arguments_out,
    internal_name as _internal_tool_name,
    public_name as _public_tool_name,
    public_text,
)


_SENSITIVE_NAME = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization|cookie|credential)"
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.IGNORECASE | re.DOTALL)
_AUTHORIZATION_VALUE = re.compile(rf"(?i)(\bauthorization\b\s*[:=]\s*)(?:bearer\s+)?[^\r\n,.;!?]+")
_SENSITIVE_ASSIGNMENT = re.compile(rf"(?i)([\"']?\b{_SENSITIVE_NAME}\b[\"']?\s*[:=]\s*)[^\r\n,.;!?]+")
_JWT_VALUE = re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})(?![A-Za-z0-9_-])")


def _netloc(parsed) -> str:
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    return host


def _safe_display_url(value: str) -> str:
    """Credential- and query-free URL, safe to show in the UI, activity log or ledger."""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), _netloc(parsed), parsed.path or "/", "", ""))


def sanitize_page_text(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", value)
    value = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return _JWT_VALUE.sub("[REDACTED TOKEN]", value)


# Global read order: a later read always has a larger number, across toolsets sharing one read state.
_READ_ORDER = itertools.count(1)


@dataclass(slots=True)
class ReadFileRecord:
    """Tracks the last observed file state for anti-staleness and read-before-edit checks."""

    mtime_ns: int
    sha256: str
    offset: int = 1
    limit: int = 400
    total_lines: int = 0
    explicit_read: bool = False
    read_seq: int = 0
    read_ranges: tuple[tuple[int, int], ...] = ()


from agentos.agentic.contract import TOOLKITS, VERIFICATION_MODES, ContractError, parse as parse_contract
from agentos.agentic.provider_content import image_block


logger = logging.getLogger("agentos.agentic.agent_tools")

MAX_TOOL_RESULT_CHARS = 12_000
# A file over this size is not hashed by the external-change check: a moved (mtime, size) stamp counts as a change.
EXTERNAL_CHANGE_HASH_LIMIT_BYTES = 8 * 1024 * 1024

# Error codes that mean the call was refused by policy rather than failing.
PERMISSION_DENIAL_CODES = frozenset({
    "CODE_PERMISSION_DENIED",
    "CODE_PLAN_MODE_READONLY",
    "READ_ONLY_SUBAGENT",
    "CODE_PLAN_APPROVAL_REQUIRED",
    "CODE_DEPLOY_CONFIRMATION_REQUIRED",
    "CODE_PUSH_CONFIRMATION_REQUIRED",
})
# Failures whose whole text Orin wrote (no file content, no command output): safe to rewrite tool ids.
_ORIN_AUTHORED_CODES = frozenset({
    "FILE_NOT_READ", "STALE_FILE_READ", "INVALID_ARGUMENTS", "UNKNOWN_TOOL", "CODE_PERMISSION_DENIED",
    "CODE_PLAN_MODE_READONLY", "CODE_PLAN_APPROVAL_REQUIRED", "READ_ONLY_SUBAGENT", "HOOK_DENIED",
    "CODE_DEPLOY_CONFIRMATION_REQUIRED", "CODE_PUSH_CONFIRMATION_REQUIRED",
})
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
# How often a running command asks whether the person stopped the turn; the check reads the database.
CANCEL_CHECK_SECONDS = 0.5
MAX_COMMAND_TIMEOUT_SECONDS = 600
# Leaves headroom under MAX_TOOL_RESULT_CHARS for the "exit=" line and the
# generic truncation notice, so our own head+tail bounding is what applies —
# not the head-only ``_bounded`` every other tool result goes through, which
# would throw away exactly the line a build or install failure ends with.
MAX_COMMAND_OUTPUT_CHARS = 9_000
FETCH_TIMEOUT_SECONDS = 25
MAX_FETCH_REDIRECTS = 5
_PROCESS_LOG_DIR = ".orin/logs"
_PROCESS_MANIFEST_PATH = ".orin/logs/processes.json"
MAX_PROCESS_LOG_TAIL_CHARS = 8_000
MAX_TRACKED_PROCESSES = 50
# A per-file lint/typecheck after a write only pays for itself if it is
# cheap; this bounds how long any single one may run and how often the same
# path is checked again when a long file is written across several calls.
DIAGNOSTIC_TIMEOUT_SECONDS = 20
DIAGNOSTIC_DEBOUNCE_SECONDS = 3.0
MAX_FILE_DIAGNOSTIC_CHARS = 600
VERIFY_STEP_TIMEOUT_SECONDS = 300
MAX_VERIFY_STEP_CHARS = 3_000
# Scaffold recipes deliberately live beside the single tool that executes
# them. They are curated, non-interactive command sequences rather than a
# generic shell escape hatch, and sharing ``verify_project`` keeps the phase
# tool budget at 16.
SCAFFOLD_RECIPES: Mapping[str, tuple[str, ...]] = {
    "vite-react-ts": (
        "npm create vite@latest . -- --template react-ts",
        "npm install",
    ),
    "next-app": (
        'npx --yes create-next-app@latest . --ts --eslint --app --src-dir --use-npm --no-tailwind --import-alias "@/*"',
    ),
    "fastapi-service": (
        "python -m pip install \"fastapi[standard]\"",
    ),
    "express-api": (
        "npm init -y",
        "npm install express",
    ),
}

# Commands that would damage the host rather than the workspace. This is a local
# personal installation, so the goal is to stop an obviously catastrophic action
# without pretending to be a general-purpose sandbox.
_BLOCKED_COMMAND = re.compile(
    r"(?ix)"
    r"(\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+/(\s|$))"        # rm -rf /
    r"|(\b(mkfs|fdisk|diskpart)\b|(?<!-)\bformat(?:\.com|\.exe)?\s+[a-zA-Z]:)"
    r"|(\b(shutdown|reboot|halt|poweroff)\b)"
    r"|(\bdel\b.*\s/[sq]\b.*[a-z]:\\)"                            # del /s /q C:\
    r"|(\brd\b\s+/s\b)"
    r"|(:\(\)\s*\{.*\};\s*:)"                                     # fork bomb
    r"|(\bReg(istry)?\s*(delete|Remove-Item\s+HK))"
    r"|(\b(Stop-Computer|Restart-Computer|Format-Volume|Clear-Disk)\b)"            # PowerShell host cmdlets
    r"|(\b(remove-item|ri|rm|rmdir|rd|del|erase)\b(?=.*\s-r\w*\b)(?=.*[\s'\"][a-z]:[\\/]*['\"]?(\s|$)))"  # recursive delete of a drive root
    r"|(\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|]{0,10000}\|\s*(iex|invoke-expression)\b)"  # download and run
    r"|(\b(iex|invoke-expression)\s*\(\s*(iwr|irm|invoke-webrequest|invoke-restmethod)\b)"
)

_VERIFICATION_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:(?:uv|poetry|pipenv|pdm)\s+run\s+)?(?:pytest|py\.test|(?:[\"']?[^\s;&|]*[/\\])?python(?:\d*(?:\.\d+)?)?(?:\.exe)?[\"']?\s+-m\s+(?:pytest|unittest|ruff|mypy|flake8|py_compile)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+(?:test|build|lint|typecheck|check|verify|audit[\w-]*))|"
    r"npx\s+(?:vitest|jest|tsc|eslint|playwright)|vitest|jest|tsc|ruff\s+check|mypy|flake8|cargo\s+(?:test|check)|go\s+test|"
    r"node\s+(?:--check\b|[^\s;&|]*(?:audit|verify|check|test)[^\s;&|]*))",
    re.IGNORECASE,
)


def is_verification_command(command: str) -> bool:
    """Return True when ``command`` looks like a test, lint, typecheck, build, or audit command."""
    clean = str(command or "").strip()
    return bool(clean and _VERIFICATION_CMD_RE.search(clean))


_DEV_SERVER_CMD_RE = re.compile(
    r"\b(npm\s+run\s+(dev|start|serve)|pnpm\s+(dev|start)|yarn\s+(dev|start)|"
    r"vite(\s|$)|next\s+dev|uvicorn\b|gunicorn\b|hypercorn\b|"
    r"fastapi\s+(?:dev|run)\b|flask\s+run|streamlit\s+run|gradio\b|"
    r"(?:python(?:\d+(?:\.\d+)?)?|py)(?:\.exe)?\s+(?:-(?!m\b)\S++\s++)*+-m\s+(?:uvicorn|gunicorn|hypercorn|fastapi|flask|streamlit|http\.server)\b|"
    r"django-admin\s+runserver|manage\.py\s+runserver)\b",
    re.IGNORECASE,
)
_SERVER_READY_RE = re.compile(
    r"(Local:\s*https?://\S+|Listening on\s*https?://\S+|"
    r"Uvicorn running on\s*https?://\S+|Ready in \d+|"
    r"Serving HTTP on\s+\S+|started server on\s+\S+)",
    re.IGNORECASE,
)
_URL_EXTRACT_RE = re.compile(r"https?://[^\s)\"'>]+", re.IGNORECASE)
_GLOB_IGNORED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".orin"})


def linked_or_outside(path: Path, roots: Collection[Path]) -> bool:
    """True for a link or junction, or an entry whose real location is in none of ``roots`` (W4-R17).

    ``os.walk`` descends into a junction on Windows, and a junction is not a symlink, so a search that
    walks the tree needs this check on every directory and file it visits. ``roots`` are resolved paths.
    """
    if os.path.islink(path) or os.path.isjunction(path):
        return True
    return not any(resolve_contained(path, root) is not None for root in roots)


class AgentToolError(RuntimeError):
    """A tool refused or failed in a way the model should read and react to."""

    def __init__(self, message: str, *, error_code: str = "TOOL_REFUSED") -> None:
        super().__init__(message)
        self.error_code = error_code


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_MAX_PATCH_HUNKS = 20
_MAX_PATCH_LINES = 250
_MAX_PATCH_LINE_CHARS = 500


def compute_structured_patch(old_content: str, new_content: str) -> tuple[list[dict[str, Any]], int, int]:
    """Return ``(hunks, added_lines, removed_lines)`` in Claude Code structuredPatch format."""
    old_lines = old_content.splitlines(keepends=False)
    new_lines = new_content.splitlines(keepends=False)
    diff = list(difflib.unified_diff(old_lines, new_lines, n=3, lineterm=""))
    hunks: list[dict[str, Any]] = []
    added = 0
    removed = 0
    recorded_lines = 0
    current_hunk: dict[str, Any] | None = None
    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            continue
        match = _HUNK_HEADER_RE.match(line)
        if match:
            if current_hunk is not None:
                hunks.append(current_hunk)
            if len(hunks) < _MAX_PATCH_HUNKS and recorded_lines < _MAX_PATCH_LINES:
                current_hunk = {
                    "oldStart": int(match.group(1)),
                    "oldLines": int(match.group(2) or "1"),
                    "newStart": int(match.group(3)),
                    "newLines": int(match.group(4) or "1"),
                    "lines": [],
                }
            else:
                current_hunk = None
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if current_hunk is not None and recorded_lines < _MAX_PATCH_LINES:
            current_hunk["lines"].append(line[:_MAX_PATCH_LINE_CHARS])
            recorded_lines += 1
    if current_hunk is not None and len(hunks) < _MAX_PATCH_HUNKS:
        hunks.append(current_hunk)
    return hunks, added, removed


_PARAM_ALIASES: Mapping[str, Mapping[str, str]] = {
    "read_file": {"file_path": "path", "filepath": "path", "file": "path"},
    "write_file": {
        "file_path": "path",
        "filepath": "path",
        "file": "path",
        "file_text": "content",
        "file_content": "content",
        "new_text": "content",
        "body": "content",
    },
    "edit_file": {
        "file_path": "path",
        "filepath": "path",
        "file": "path",
        "old_string": "old_text",
        "new_string": "new_text",
    },
    "notebook_edit": {
        "path": "notebook_path",
        "file_path": "notebook_path",
        "filepath": "notebook_path",
        "file": "notebook_path",
    },
    "glob_files": {
        "glob": "pattern",
        "file_pattern": "pattern",
        "directory": "path",
    },
    "search_files": {
        "-B": "before_context",
        "-A": "after_context",
        "-C": "context_lines",
        "-i": "ignore_case",
    },
    "run_command": {
        "run_in_background": "background",
        "timeout": "timeout_seconds",
    },
    "powershell": {
        "run_in_background": "background",
        "timeout": "timeout_seconds",
    },
    "bash": {
        "run_in_background": "background",
        "timeout": "timeout_seconds",
    },
    "exit_plan_mode": {
        "allowedPrompts": "allowed_prompts",
        "path": "plan_path",
    },
    "task_create": {
        "activeForm": "active_form",
        "title": "subject",
        "blockedBy": "blocked_by",
    },
    "task_update": {
        "taskId": "task_id",
        "id": "task_id",
        "activeForm": "active_form",
        "addBlocks": "add_blocks",
        "addBlockedBy": "add_blocked_by",
    },
    "task_get": {
        "taskId": "task_id",
        "id": "task_id",
    },
    "task_claim": {
        "taskId": "task_id",
        "id": "task_id",
    },
    "view_file": {"file_path": "path", "filepath": "path", "file": "path"},
    "run_subagent": {
        "prompt": "task",
        "type": "subagent_type",
        "agent": "subagent_type",
        "agent_type": "subagent_type",
        "model": "model_id",
        "role": "description",
        "background": "run_in_background",
        "resume": "agent_id",
        "characterName": "character_name",
    },
    "ask_agent": {"prompt": "task", "agent": "name"},
    "read_project_conversation": {"max_messages": "limit"},
    "generate_image": {"path": "output_path", "file_path": "output_path", "model_id": "model"},
    "generate_audio": {
        "prompt": "text",
        "input": "text",
        "path": "output_path",
        "file_path": "output_path",
        "format": "response_format",
        "model_id": "model",
    },
    "generate_video": {"path": "output_path", "file_path": "output_path", "model_id": "model"},
}


def _broker_arguments(name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The arguments the permission engine judges, mapped the way ``_invoke_unobserved`` maps them."""
    if name == "notebook_edit":
        return {**arguments, "path": str(arguments.get("notebook_path") or arguments.get("path") or "")}
    return arguments


class _CoercedArgumentsResult(tuple):  # type: ignore[type-arg]
    """2-tuple ``(coerced_dict, note)`` that also supports dict-style key lookup on ``coerced_dict``."""

    def __new__(cls, coerced: dict[str, Any], note: str | None) -> _CoercedArgumentsResult:
        return super().__new__(cls, (coerced, note))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, (int, slice)):
            return super().__getitem__(key)
        return super().__getitem__(0)[key]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            return key in super().__getitem__(0)
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return super().__getitem__(0).get(key, default)


def coerce_tool_arguments(tool_name: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Map common model parameter aliases to canonical schema names."""
    aliases = _PARAM_ALIASES.get(tool_name)
    if not aliases:
        return _CoercedArgumentsResult(dict(arguments), None)
    coerced = dict(arguments)
    had_timeout_alias = "timeout" in coerced and "timeout_seconds" not in coerced
    notes: list[str] = []
    for wrong_key, right_key in aliases.items():
        if wrong_key in coerced and right_key not in coerced:
            coerced[right_key] = coerced.pop(wrong_key)
            notes.append(f"parameter '{wrong_key}' was read as '{right_key}'")
    if had_timeout_alias and tool_name in {"run_command", "powershell", "bash"}:
        raw_timeout = coerced.get("timeout_seconds")
        if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool):
            seconds = int(round(float(raw_timeout) / 1000.0)) if raw_timeout >= 100 else int(round(float(raw_timeout)))
            coerced["timeout_seconds"] = max(1, min(seconds, MAX_COMMAND_TIMEOUT_SECONDS))
    if tool_name == "edit_file" and isinstance(coerced.get("edits"), list):
        new_edits: list[Any] = []
        for item in coerced["edits"]:
            if isinstance(item, Mapping):
                edit_item = dict(item)
                if "old_string" in edit_item and "old_text" not in edit_item:
                    edit_item["old_text"] = edit_item.pop("old_string")
                    notes.append("parameter 'edits[].old_string' was read as 'old_text'")
                if "new_string" in edit_item and "new_text" not in edit_item:
                    edit_item["new_text"] = edit_item.pop("new_string")
                    notes.append("parameter 'edits[].new_string' was read as 'new_text'")
                new_edits.append(edit_item)
            else:
                new_edits.append(item)
        coerced["edits"] = new_edits
    if not notes:
        return _CoercedArgumentsResult(coerced, None)
    return _CoercedArgumentsResult(coerced, "\n\n[Note: " + "; ".join(notes) + "]")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[..., dict[str, Any]]
    # Drives the icon/label the UI shows for a grouped activity card.
    kind: str
    # A read-only tool does not mutate the workspace, so several of them may
    # run at once without changing what any of them observes. Network reads
    # remain subject to the public-network policy.
    read_only: bool = False
    # Coarse labels a policy can authorize or refuse as a family.
    policy_tags: tuple[str, ...] = ()
    # Per-tool result budget before truncation and disk persistence.
    max_result_size_chars: int = MAX_TOOL_RESULT_CHARS
    # Registry metadata (W4): the canonical name the model, rules and hooks see, the other spellings that resolve
    # to this tool, its permission family, whether it may run alongside others, and its Claude Code models.
    public_name: str = ""
    aliases: tuple[str, ...] = ()
    permission_category: str = ""
    concurrency_safe: bool = False
    input_model: Any = None
    output_model: Any = None

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": dict(self.parameters)}}


def _safe_public_input(internal: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The public (Claude Code) shape of a call for hook payloads; the raw shape if it cannot be encoded."""
    try:
        return _public_arguments_out(internal, dict(arguments or {}))
    except Exception:  # noqa: BLE001 - payload building is best-effort
        return dict(arguments or {})


@dataclass(slots=True)
class ToolOutcome:
    status: str
    summary: str
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    # Provider-neutral image blocks the runtime appends to the conversation
    # after this tool's result, so a model that sees can look at them.
    images: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def get(self, key: str, default: Any = None) -> Any:
        if key == "ok":
            return self.status == "succeeded"
        if key in {"status", "summary", "content", "payload", "error_code", "images"}:
            return getattr(self, key)
        if key in self.data:
            return self.data[key]
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key == "ok":
            return self.status == "succeeded"
        if key in {"status", "summary", "content", "payload", "error_code", "images"}:
            return getattr(self, key)
        if key in self.data:
            return self.data[key]
        return self.payload[key]

    def __contains__(self, key: object) -> bool:
        if key in {"ok", "status", "summary", "content", "payload", "error_code", "images"}:
            return True
        return key in self.data or key in self.payload


def _schema(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required), "additionalProperties": False}


_TEXT = {"type": "string"}
_QUESTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class _TextExtractor(HTMLParser):
    """Reduce a fetched page to readable text; scripts and styles are dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data.strip()
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        joined = " ".join(self.parts)
        return re.sub(r"[ \t]*\n[ \t]*(\n[ \t]*)+", "\n\n", re.sub(r"[ \t]{2,}", " ", joined)).strip()


def _bounded(value: str, limit: int = MAX_TOOL_RESULT_CHARS) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _bounded_output(text: str, limit: int) -> tuple[str, bool]:
    """Bound long process output while keeping both ends.

    A build or install log's most useful line — the actual compiler error —
    is almost always its last one. The generic ``_bounded`` above truncates
    from the tail and would throw exactly that away; this keeps a head
    (usually enough to see what command ran and how far it got) and a tail
    (where the failure is) instead.
    """
    if len(text) <= limit:
        return text, False
    head_chars = limit // 3
    tail_chars = limit - head_chars
    omitted = len(text) - head_chars - tail_chars
    return f"{text[:head_chars]}\n\n[...{omitted} characters omitted...]\n\n{text[-tail_chars:]}", True


def _public_url(url: str, *, resolve_dns: bool = False, allow_loopback: bool = False) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentToolError("Only absolute http(s) URLs can be fetched.")
    host = parsed.hostname
    if host.lower() in {"localhost", "localhost.localdomain"}:
        if allow_loopback:
            return url.strip()
        raise AgentToolError("Local network addresses cannot be fetched.")
    if host.endswith(".local"):
        raise AgentToolError("Local network addresses cannot be fetched.")
    try:
        address = ip_address(host)
    except ValueError:
        if resolve_dns:
            try:
                records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except OSError as error:
                raise AgentToolError("The URL host could not be resolved safely.") from error
            addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records if record[4]))
            if not addresses or any(not ip_address(item).is_global for item in addresses):
                raise AgentToolError("Private network addresses cannot be fetched.")
        return url.strip()
    if address.is_loopback and allow_loopback:
        return url.strip()
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise AgentToolError("Private network addresses cannot be fetched.")
    return url.strip()


# W4-R15: names WebFetch never connects to, whatever they resolve to.
_REFUSED_NAME_SUFFIXES = (".localhost", ".local", ".internal", ".lan")


@dataclass(frozen=True, slots=True)
class PinnedHop:
    """One validated WebFetch hop: the URL as named, its ASCII host, and the one address it may connect to."""

    url: str
    host: str
    address: str


def _pinned_public_hop(url: str) -> PinnedHop:
    """Validate one WebFetch hop and pin it to an address (W4-R15).

    The name is resolved once, and every answer must be a global address. The connection goes to the address
    returned here, so a second lookup in the transport cannot answer differently. Literal addresses must be global.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentToolError("Only absolute http(s) URLs can be fetched.")
    try:
        # UTS 46 as httpx and the browsers encode it, so one name is one spelling on the wire (W4-R15).
        host = idna.encode(parsed.hostname, uts46=True).decode("ascii").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (idna.IDNAError, UnicodeError, ValueError) as error:
        raise AgentToolError("The URL host is not a valid name.") from error
    try:
        literal = ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise AgentToolError("Private network addresses cannot be fetched.")
        return PinnedHop(url.strip(), host, str(literal))
    if "." not in host or host.endswith(_REFUSED_NAME_SUFFIXES):
        raise AgentToolError("Local network addresses cannot be fetched.")
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise AgentToolError("The URL host could not be resolved safely.") from error
    addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records if record[4]))
    if not addresses or any(not ip_address(item).is_global for item in addresses):
        raise AgentToolError("Private network addresses cannot be fetched.")
    return PinnedHop(url.strip(), host, addresses[0])


def _terminate_pid(pid: int) -> None:
    """Best-effort kill of a process (and, on Windows, its tree) by pid alone.

    Used both for a timed-out foreground command (which still has its
    ``Popen`` object, see ``_terminate_process_tree``) and for stopping a
    background process tracked across tool calls, where only the pid survives.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except (OSError, subprocess.SubprocessError):
            return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def _checked(check: Callable[[], Any] | None) -> bool:
    """A stop check's answer; a check that is missing or raises never stops a healthy command."""
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001
        return False


def _close_stream_quietly(stream: Any, wait_seconds: float = 0.2) -> None:
    """Close a pipe without ever blocking the caller.

    ``BufferedReader.close()`` takes the same lock a ``readline()`` blocked in
    another thread holds, so closing a pipe a reader thread is still waiting on
    (a grandchild keeps its write end open) blocks forever. The close runs in a
    daemon thread and the caller waits at most ``wait_seconds`` for it.
    """
    if stream is None:
        return

    def _close() -> None:
        try:
            stream.close()
        except Exception:
            pass

    closer = threading.Thread(target=_close, name="orin-pipe-close", daemon=True)
    closer.start()
    closer.join(max(0.0, wait_seconds))


def _terminate_process_tree(process: subprocess.Popen[bytes], *, job: Any = None) -> None:
    """Stop a timed-out shell together with any child retaining its output pipes.

    On Windows ``taskkill /T`` follows parent links, and a Git Bash pipeline
    (``find / | head``) leaves its programs with a parent that already exited,
    so they survive it and keep the output pipe open. ``job`` (a Windows Job
    Object the shell was assigned to) reaches them anyway: every descendant is
    in the job, whatever happened to its parent.
    """
    if os.name == "nt":
        if job is not None:
            try:
                job.terminate()
            except Exception:
                pass
        if getattr(process, "pid", None):
            _terminate_pid(process.pid)
    else:
        if process.poll() is not None:
            return
        _terminate_pid(process.pid)
    if process.poll() is None:
        try:
            process.kill()
        except Exception:
            pass
    for stream_attr in ("stdout", "stderr"):
        _close_stream_quietly(getattr(process, stream_attr, None))


def _process_is_running(pid: int) -> bool:
    """Whether ``pid`` is alive, checked without needing the original ``Popen``.

    A background process is tracked in a manifest file that outlives the
    ``AgentToolset`` that started it (a new one is built every turn), so
    liveness has to be answerable from the pid alone.

    On POSIX, ``os.kill(pid, 0)`` alone is not enough: it reports a zombie
    (a child that exited but was never ``wait()``-ed on) as alive, because a
    zombie still occupies a process-table entry. ``run_command(background=True)``
    never waits on the child it starts, so every background process on
    Linux/macOS would otherwise be reported as running forever. Reaping it
    with ``waitpid(pid, WNOHANG)`` first fixes that for the common case
    (we are still the parent); the ``kill(pid, 0)`` fallback covers the case
    where the process was tracked across a restart and we no longer are.
    """
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, errors="replace", timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in (completed.stdout or "")
    try:
        reaped_pid, _ = os.waitpid(pid, 1)  # 1 == os.WNOHANG on Unix systems
        if reaped_pid == pid:
            return False
        if reaped_pid == 0:
            return True
    except ChildProcessError:
        # Not our child -- tracked across a new AgentToolset (a later turn)
        # or a backend restart. waitpid() only works while the original
        # launching process is still the one asking; fall back to a plain
        # existence check.
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """Whether ``pid`` has exited but still waits for its parent to reap it.

    A command killed together with its shell is reparented to PID 1, and
    not every PID 1 reaps promptly (container supervisors often don't), so
    ``kill(pid, 0)`` keeps reporting the dead process as alive. Linux exposes
    the state in ``/proc``; elsewhere the check is skipped.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # The command name is parenthesised and may itself contain spaces or
    # parentheses, so the state is the first field after the last ")".
    fields = stat.rpartition(")")[2].split()
    return bool(fields) and fields[0] in {"Z", "X"}


_BACKGROUND_POPENS_LOCK = threading.Lock()
_BACKGROUND_POPENS: dict[int, Any] = {}
_BACKGROUND_JOBS: dict[int, Any] = {}


def _job_has_active_processes(job: Any) -> bool:
    checker = getattr(job, "has_active_processes", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001
            return False
    return True


def _popen_has_exited(proc: Any) -> bool:
    try:
        return proc.poll() is not None
    except Exception:  # noqa: BLE001
        return True


def register_background_popen(pid: int, process: Any) -> None:
    """Keep a bounded reference to a background ``Popen`` so later turns or the worker observer can read its exit code."""
    if not isinstance(pid, int) or pid <= 0 or process is None or not hasattr(process, "poll"):
        return
    cap = MAX_TRACKED_PROCESSES * 4
    with _BACKGROUND_POPENS_LOCK:
        _BACKGROUND_POPENS[pid] = process
        if len(_BACKGROUND_POPENS) > cap:
            dead_pids = [
                p
                for p, proc in _BACKGROUND_POPENS.items()
                if p != pid
                and _popen_has_exited(proc)
                and not (p in _BACKGROUND_JOBS and _job_has_active_processes(_BACKGROUND_JOBS[p]))
            ]
            for dead_pid in dead_pids:
                if len(_BACKGROUND_POPENS) <= cap:
                    break
                _BACKGROUND_POPENS.pop(dead_pid, None)
        while len(_BACKGROUND_POPENS) > cap:
            oldest = next(iter(_BACKGROUND_POPENS))
            _BACKGROUND_POPENS.pop(oldest, None)


def register_background_job(pid: int, job: Any) -> None:
    """Keep the Windows Job Object for a backgrounded command so ``stop_process`` and session teardown kill its tree."""
    if not isinstance(pid, int) or pid <= 0 or job is None:
        return
    cap = MAX_TRACKED_PROCESSES * 4
    with _BACKGROUND_POPENS_LOCK:
        old = _BACKGROUND_JOBS.get(pid)
        if old is not None and old is not job:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        _BACKGROUND_JOBS[pid] = job
        if len(_BACKGROUND_JOBS) > cap:
            dead_pids = [
                p
                for p, candidate in _BACKGROUND_JOBS.items()
                if p != pid and not _job_has_active_processes(candidate)
            ]
            for dead_pid in dead_pids:
                evicted = _BACKGROUND_JOBS.pop(dead_pid, None)
                if evicted is not None:
                    try:
                        evicted.close()
                    except Exception:  # noqa: BLE001
                        pass
        while len(_BACKGROUND_JOBS) > cap:
            oldest = next(iter(_BACKGROUND_JOBS))
            evicted = _BACKGROUND_JOBS.pop(oldest, None)
            if evicted is not None:
                try:
                    evicted.close()
                except Exception:  # noqa: BLE001
                    pass


def has_background_job(pid: int) -> bool:
    """Return True if ``pid`` currently has a registered Job Object in ``_BACKGROUND_JOBS``."""
    with _BACKGROUND_POPENS_LOCK:
        return pid in _BACKGROUND_JOBS


def terminate_background_job(pid: int) -> bool:
    """Terminate and close the tracked Job Object for ``pid``, if any."""
    with _BACKGROUND_POPENS_LOCK:
        job = _BACKGROUND_JOBS.pop(pid, None)
    if job is None:
        return False
    try:
        job.terminate()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            job.close()
        except Exception:  # noqa: BLE001
            pass
    return True


def cleanup_session_background_processes() -> int:
    """Terminate all tracked background jobs and popens on worker/session teardown."""
    with _BACKGROUND_POPENS_LOCK:
        jobs = list(_BACKGROUND_JOBS.items())
        _BACKGROUND_JOBS.clear()
        popens = list(_BACKGROUND_POPENS.items())
        _BACKGROUND_POPENS.clear()
    cleaned = 0
    seen_pids: set[int] = set()
    for pid, job in jobs:
        seen_pids.add(pid)
        try:
            job.terminate()
            cleaned += 1
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                job.close()
            except Exception:  # noqa: BLE001
                pass
    for pid, proc in popens:
        try:
            if hasattr(proc, "poll") and proc.poll() is None:
                _terminate_process_tree(proc)
                if pid not in seen_pids:
                    cleaned += 1
        except Exception:  # noqa: BLE001
            pass
    return cleaned


def poll_background_popen(pid: int) -> tuple[bool, int | None] | None:
    """Return ``(running, exit_code)`` when ``pid`` has a tracked ``Popen`` in this process, else ``None``."""
    with _BACKGROUND_POPENS_LOCK:
        process = _BACKGROUND_POPENS.get(pid)
    if process is None or not hasattr(process, "poll"):
        return None
    try:
        code = process.poll()
    except Exception:  # noqa: BLE001
        return None
    return (code is None, int(code) if isinstance(code, int) and not isinstance(code, bool) else None)


def _append_reminder(outcome: ToolOutcome, items: list[str], limit: int) -> None:
    """Append hook feedback as a system-reminder block, keeping the whole result under ``limit``."""
    clean = [item for item in items if isinstance(item, str) and item.strip()]
    if not clean:
        return
    block = "\n\n" + system_reminder("\n".join(clean))
    head = outcome.content[: max(0, limit - len(block))]
    outcome.content = head + block


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
                    return 6, "\ufffd"
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

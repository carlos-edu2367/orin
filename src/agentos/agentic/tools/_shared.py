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
MAX_FILE_DIAGNOSTIC_CHARS = 2_000
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
    r"(?:^|[;&|]\s*)(?:pytest|py\.test|(?:[\"']?[^\s;&|]*[/\\])?python(?:\d*(?:\.\d+)?)?(?:\.exe)?[\"']?\s+-m\s+(?:pytest|unittest|ruff|mypy|flake8|py_compile)|"
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
    r"vite(\s|$)|next\s+dev|uvicorn\b|flask\s+run|python\d*\s+-m\s+http\.server|"
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
    "exit_plan_mode": {
        "allowedPrompts": "allowed_prompts",
        "path": "plan_path",
    },
    "task_create": {
        "activeForm": "active_form",
        "title": "subject",
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


def coerce_tool_arguments(tool_name: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Map common model parameter aliases to canonical schema names."""
    aliases = _PARAM_ALIASES.get(tool_name)
    if not aliases:
        return dict(arguments), None
    coerced = dict(arguments)
    notes: list[str] = []
    for wrong_key, right_key in aliases.items():
        if wrong_key in coerced and right_key not in coerced:
            coerced[right_key] = coerced.pop(wrong_key)
            notes.append(f"parameter '{wrong_key}' was read as '{right_key}'")
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
        return coerced, None
    return coerced, "\n\n[Note: " + "; ".join(notes) + "]"


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


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out shell together with any child retaining its output pipes."""
    if process.poll() is not None:
        return
    _terminate_pid(process.pid)
    if process.poll() is None:
        process.kill()


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
                ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in completed.stdout
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


def _append_reminder(outcome: ToolOutcome, items: list[str], limit: int) -> None:
    """Append hook feedback as a system-reminder block, keeping the whole result under ``limit``."""
    clean = [item for item in items if isinstance(item, str) and item.strip()]
    if not clean:
        return
    block = "\n\n" + system_reminder("\n".join(clean))
    head = outcome.content[: max(0, limit - len(block))]
    outcome.content = head + block

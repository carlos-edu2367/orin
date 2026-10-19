"""Granular tool permission broker and workspace rule matcher for Orin Code mode."""
from __future__ import annotations

import difflib
import hashlib
import os
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Callable, Literal, Mapping
from uuid import uuid4

# agentos.permissions.* imports agentos.code_mode.models, so the engine/rules
# imports stay inside the functions that use them (import cycle through code_mode/__init__).
from agentos.settings import load_effective_settings
from agentos.settings.paths import SettingsPaths, orin_user_dir
from agentos.settings.writer import add_permission_rule

from .models import CodeAutonomy, CodePermissionMode, normalize_permission_mode

PermissionEvaluation = Literal["allow", "ask", "deny"]
PermissionReplyDecision = Literal["allow_once", "allow_always", "deny"]

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "view_file",
    "transcribe_pdf",
    "list_files",
    "search_files",
    "search_code",
    "glob_files",
    "grep_files",
    "git_status",
    "git_diff",
    "git_log",
    "todo_write",
    "ask_user",
    "enter_plan_mode",
    "exit_plan_mode",
    "write_contract",
    "report_verification",
    "use_skill",
    "search_skills",
    "recall_memory",
})

FILE_EDIT_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
})

EXECUTION_TOOLS: frozenset[str] = frozenset({
    "run_command",
    "powershell",
    "verify_project",
    "git_commit",
})

_TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "run_command",
    "shell": "run_command",
    "run_command": "run_command",
    "edit": "edit_file",
    "edit_file": "edit_file",
    "write": "write_file",
    "write_file": "write_file",
    "read": "read_file",
    "read_file": "read_file",
    "glob": "glob_files",
    "glob_files": "glob_files",
    "grep": "search_files",
    "grep_files": "search_files",
}

_RULE_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\((.*)\))?\s*$")
_COMPOUND_CMD_PREFIXES: frozenset[str] = frozenset({
    "git",
    "npm",
    "pnpm",
    "yarn",
    "npx",
    "bun",
    "cargo",
    "poetry",
    "uv",
    "pip",
    "python",
    "python3",
    "pytest",
    "go",
    "docker",
})


def _canonical_tool_name(name: str) -> str:
    cleaned = name.strip()
    return _TOOL_NAME_ALIASES.get(cleaned.lower(), cleaned)


def parse_permission_rule(rule: str) -> tuple[str, str | None] | None:
    """Parse ``ToolName`` or ``ToolName(specifier)`` into ``(canonical_tool_name, specifier)``."""
    if not isinstance(rule, str) or not rule.strip():
        return None
    match = _RULE_PATTERN.match(rule.strip())
    if match is None:
        return None
    raw_tool, raw_spec = match.group(1), match.group(2)
    tool_name = _canonical_tool_name(raw_tool)
    if raw_spec is None:
        return tool_name, None
    spec = raw_spec.strip()
    if not spec or spec == "*":
        return tool_name, None
    return tool_name, spec


def _normalize_rel_path(raw_path: str, workspace: Path | None = None) -> str:
    cleaned = str(raw_path or "").strip().replace("\\", "/")
    if not cleaned:
        return ""
    if workspace is not None:
        try:
            candidate = Path(cleaned)
            if candidate.is_absolute():
                resolved = candidate.resolve()
                root_resolved = workspace.resolve()
                cleaned = resolved.relative_to(root_resolved).as_posix()
        except Exception:
            pass
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip("/")


def is_plan_file_path(raw_path: str, workspace: Path | None = None) -> bool:
    """Return True only when ``raw_path`` points inside ``.orin/plans/``."""
    rel = _normalize_rel_path(raw_path, workspace)
    if not rel:
        return False
    parts = PurePosixPath(rel).parts
    if ".." in parts:
        return False
    return rel.startswith(".orin/plans/") and len(parts) >= 3


def _canonical_internal(name: str) -> str:
    from agentos.agentic.tools.public_names import internal_name

    cleaned = str(name or "").strip()
    return _TOOL_NAME_ALIASES.get(cleaned.lower()) or internal_name(cleaned)


def suggest_permission_rule(tool_name: str, arguments: Mapping[str, Any] | None = None, *, workspace: Path | None = None) -> str:
    """Reusable rule in Claude Code syntax for ``allow_always``."""
    from agentos.permissions.rules import MatchContext, suggest_rule
    from agentos.permissions.shell import default_shell

    root = Path(workspace) if workspace is not None else Path.cwd()
    internal = _canonical_internal(tool_name)
    return suggest_rule(internal, dict(arguments or {}), MatchContext(root, Path.home(), default_shell()))


def _compute_preview_structured_patch(
    workspace_root: Path,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Pre-compute unified diff hunks in memory without touching disk."""
    if tool_name not in {"write_file", "edit_file"}:
        return None
    args = dict(arguments or {})
    rel_path = _normalize_rel_path(str(args.get("path") or ""), workspace_root)
    if not rel_path:
        return None
    target = (workspace_root / rel_path).resolve()
    try:
        target.relative_to(workspace_root.resolve())
    except ValueError:
        return None
    before_text = ""
    if target.exists() and target.is_file():
        try:
            before_text = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            before_text = ""
    if tool_name == "write_file":
        after_text = str(args.get("content") or "")
    else:
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        replace_all = bool(args.get("replace_all", False))
        if old_text and old_text in before_text:
            after_text = before_text.replace(old_text, new_text) if replace_all else before_text.replace(old_text, new_text, 1)
        else:
            after_text = new_text

    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
            n=3,
        )
    )
    hunks: list[dict[str, object]] = []
    added = 0
    removed = 0
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    current_hunk: dict[str, object] | None = None
    for line in diff_lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        match = hunk_header_re.match(line)
        if match:
            if current_hunk is not None and len(hunks) < 40:
                hunks.append(current_hunk)
            current_hunk = {
                "oldStart": int(match.group(1)),
                "oldLines": int(match.group(2) or "1"),
                "newStart": int(match.group(3)),
                "newLines": int(match.group(4) or "1"),
                "lines": [],
            }
            continue
        if current_hunk is None:
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        lines_list = current_hunk["lines"]
        if isinstance(lines_list, list) and len(lines_list) < 200:
            lines_list.append(line)
    if current_hunk is not None and len(hunks) < 40:
        hunks.append(current_hunk)
    return {
        "path": rel_path,
        "structured_patch": hunks,
        "added_lines": added,
        "removed_lines": removed,
    }


def _workspace_root(workspace: Path | object) -> Path:
    if isinstance(workspace, (Path, str)):
        return Path(workspace).resolve()
    root = getattr(workspace, "root", workspace)
    return Path(root).resolve()


def load_workspace_permission_rules(workspace: Path | object) -> dict[str, list[str]]:
    """Read ``<workspace>/.orin/settings.json`` and return ``{allow, ask, deny}`` lists."""
    root = _workspace_root(workspace)
    settings_path = root / ".orin" / "settings.json"
    result: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    if not settings_path.exists():
        return result
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return result
    if not isinstance(raw, Mapping):
        return result
    permissions = raw.get("permissions")
    if not isinstance(permissions, Mapping):
        return result
    for bucket in ("allow", "ask", "deny"):
        entries = permissions.get(bucket)
        if isinstance(entries, list):
            result[bucket] = [str(item).strip() for item in entries if isinstance(item, str) and str(item).strip()]
    return result


def save_workspace_permission_rule(
    workspace: Path | object,
    bucket: Literal["allow", "ask", "deny"],
    rule: str,
) -> dict[str, list[str]]:
    """Persist a permission rule into ``<workspace>/.orin/settings.json``."""
    if bucket not in {"allow", "ask", "deny"}:
        raise ValueError(f"Unsupported permission bucket: {bucket}")
    clean_rule = str(rule or "").strip()
    if not clean_rule:
        raise ValueError("Permission rule must not be empty")
    root = _workspace_root(workspace)
    orin_dir = root / ".orin"
    orin_dir.mkdir(parents=True, exist_ok=True)
    settings_path = orin_dir / "settings.json"
    data: dict[str, Any] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                data = dict(loaded)
        except Exception:
            data = {}
    perms_raw = data.get("permissions")
    perms: dict[str, list[str]] = {
        "allow": [],
        "ask": [],
        "deny": [],
    }
    if isinstance(perms_raw, Mapping):
        for key in ("allow", "ask", "deny"):
            items = perms_raw.get(key)
            if isinstance(items, list):
                perms[key] = [str(item).strip() for item in items if isinstance(item, str) and str(item).strip()]
    if clean_rule not in perms[bucket]:
        perms[bucket].append(clean_rule)
    data["permissions"] = perms
    tmp_path = settings_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(settings_path)
    return perms


class ToolPermissionBroker:
    """Evaluates and coordinates granular tool permissions for a workspace.

    Decisions come from ``PermissionEngine`` over the 5-layer settings. Repository
    rules only apply once the workspace is trusted; "always allow" grants are
    written to a user-owned file outside the repository.
    """

    def __init__(
        self,
        workspace: Path | object,
        *,
        trusted: bool = False,
        classifier: Callable[[str, Mapping[str, Any]], PermissionDecision] | None = None,
        cli_settings: Mapping[str, Any] | None = None,
        event_emitter: Callable[[str, str, dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        poll_interval_seconds: float = 0.2,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.workspace_root = _workspace_root(workspace)
        # Kept so the engine sees the workspace's additional working directories as the turn sets them (W4).
        self._workspace = workspace
        self.trusted = bool(trusted)
        self.classifier = classifier
        self.cli_settings = dict(cli_settings) if cli_settings is not None else None
        self.event_emitter = event_emitter
        self.cancelled = cancelled
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    @property
    def permissions_dir(self) -> Path:
        """Pending requests live in the user's directory, never in the repository.

        Anything running inside the workspace (a dev server, a postinstall, a
        watcher) could otherwise write a reply file and mint a grant. Processes
        of the same user outside the workspace remain out of scope.
        """
        digest = hashlib.sha256(os.path.normcase(str(self.workspace_root)).encode("utf-8")).hexdigest()[:16]
        return orin_user_dir() / "permissions" / digest

    @property
    def settings_path(self) -> Path:
        return self.workspace_root / ".orin" / "settings.json"

    def engine(self):
        from agentos.permissions.engine import PermissionEngine

        # Fresh settings on every call, so a new grant applies to the next call.
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, trusted=self.trusted)
        extra = tuple(getattr(self._workspace, "additional_roots", ()) or ())
        return PermissionEngine(self.workspace_root, settings, classifier=self.classifier, additional_roots=extra)

    def has_rules(self) -> bool:
        try:
            return self.engine().has_rules()
        except Exception:  # noqa: BLE001 - unreadable settings mean no rules, never a crash
            return False

    def load_rules(self) -> dict[str, list[str]]:
        settings = load_effective_settings(self.workspace_root, cli=self.cli_settings, trusted=self.trusted)
        return {kind: [rule for rule, _source in settings.permission_rules(kind)] for kind in ("allow", "ask", "deny")}

    def _grants_file(self) -> Path:
        return SettingsPaths(self.workspace_root).writable_file("local")

    def add_allow_rule(self, rule: str) -> dict[str, list[str]]:
        add_permission_rule(self._grants_file(), "allow", rule)
        return self.load_rules()

    def add_ask_rule(self, rule: str) -> dict[str, list[str]]:
        add_permission_rule(self._grants_file(), "ask", rule)
        return self.load_rules()

    def add_deny_rule(self, rule: str) -> dict[str, list[str]]:
        add_permission_rule(self._grants_file(), "deny", rule)
        return self.load_rules()

    def decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        permission_mode: str | CodePermissionMode | CodeAutonomy = CodePermissionMode.ASK_PERMISSIONS,
        *,
        mode: str | CodePermissionMode | CodeAutonomy | None = None,
        plan_mode_active: bool = False,
        extra_allow: tuple = (),
    ) -> PermissionDecision:
        effective = mode if mode is not None else permission_mode
        return self.engine().decide(
            _canonical_internal(tool_name),
            dict(arguments or {}),
            normalize_permission_mode(effective),
            plan_mode_active=plan_mode_active,
            extra_allow=tuple(extra_allow or ()),
        )

    def evaluate(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        permission_mode: str | CodePermissionMode | CodeAutonomy = CodePermissionMode.ASK_PERMISSIONS,
        *,
        mode: str | CodePermissionMode | CodeAutonomy | None = None,
        plan_mode_active: bool = False,
    ) -> PermissionEvaluation:
        """Evaluate ``(tool_name, arguments, permission_mode)`` -> ``'allow' | 'ask' | 'deny'``."""
        return self.decide(tool_name, arguments, permission_mode, mode=mode, plan_mode_active=plan_mode_active).behavior  # type: ignore[return-value]

    def request_path(self, request_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(request_id).strip())
        if not safe_id:
            raise ValueError("Invalid permission request_id")
        return self.permissions_dir / f"{safe_id}.json"

    def create_permission_request(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        conversation_id: str | None = None,
        permission_mode: str | CodePermissionMode | CodeAutonomy = CodePermissionMode.ASK_PERMISSIONS,
        request_id: str | None = None,
        emit_request: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Persist a pending permission request in ``.orin/permissions/<request_id>.json`` and emit an event."""
        canonical = _canonical_tool_name(tool_name)
        args = dict(arguments or {})
        mode = normalize_permission_mode(permission_mode)
        req_id = str(request_id or f"perm_{uuid4().hex[:12]}")
        suggested_rule = suggest_permission_rule(tool_name, args, workspace=self.workspace_root)
        preview = _compute_preview_structured_patch(self.workspace_root, canonical, args)

        command_preview = str(args.get("command") or "") if canonical in {"run_command", "powershell"} else ""
        path_preview = _normalize_rel_path(str(args.get("path") or ""), self.workspace_root)

        record: dict[str, Any] = {
            "request_id": req_id,
            "status": "pending",
            "decision": None,
            "allowed": False,
            "tool_name": canonical,
            "command": command_preview,
            "path": path_preview,
            "suggested_rule": suggested_rule,
            "rule_suggestion": suggested_rule,
            "permission_mode": mode.value,
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "created_at": datetime.now(UTC).isoformat(),
            "resolved_at": None,
        }
        if preview is not None:
            record["structured_patch"] = preview["structured_patch"]
            record["added_lines"] = preview["added_lines"]
            record["removed_lines"] = preview["removed_lines"]

        self.permissions_dir.mkdir(parents=True, exist_ok=True)
        path = self.request_path(req_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

        if callable(self.event_emitter):
            summary = (
                f"Permissão necessária para executar `{command_preview[:80]}`"
                if canonical in {"run_command", "powershell"} and command_preview
                else f"Permissão necessária para editar `{path_preview}`"
                if path_preview
                else f"Permissão necessária para `{canonical}`"
            )
            event_payload: dict[str, Any] = {
                "stage": "waiting_decision",
                "state": "waiting_user",
                "tool_name": canonical,
                "permission_request_id": req_id,
                "suggested_rule": suggested_rule,
                "rule_suggestion": suggested_rule,
                "permission_mode": mode.value,
                "command": command_preview,
                "path": path_preview,
                "permission_request": {
                    "request_id": req_id,
                    "status": "pending",
                    "tool_name": canonical,
                    "command": command_preview,
                    "path": path_preview,
                    "suggested_rule": suggested_rule,
                    "rule_suggestion": suggested_rule,
                    "permission_mode": mode.value,
                    "turn_id": turn_id,
                    "structured_patch": record.get("structured_patch"),
                    "added_lines": record.get("added_lines"),
                    "removed_lines": record.get("removed_lines"),
                },
            }
            if "structured_patch" in record:
                event_payload["structured_patch"] = record["structured_patch"]
                event_payload["added_lines"] = record["added_lines"]
                event_payload["removed_lines"] = record["removed_lines"]
            try:
                self.event_emitter("code_mode.decision_required", summary, event_payload)
            except Exception:
                pass
        if callable(emit_request):
            try:
                emit_request(dict(record))
            except Exception:
                pass
        return record

    create_request = create_permission_request

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        path = self.request_path(request_id)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return dict(loaded) if isinstance(loaded, Mapping) else None
        except Exception:
            return None

    def resolve_request(
        self,
        request_id: str,
        *,
        decision: PermissionReplyDecision,
        reason: str | None = None,
        rule: str | None = None,
    ) -> dict[str, Any]:
        """Record the user's decision and persist ``allow_always`` rules when requested.

        Only the rule the request itself was created with (``suggested_rule``) can
        become a grant. A ``rule`` argument cannot widen it, and a request without a
        suggested rule is treated as ``allow_once``.
        """
        if decision not in {"allow_once", "allow_always", "deny"}:
            raise ValueError(f"Invalid permission decision: {decision}")
        path = self.request_path(request_id)
        if not path.exists():
            raise LookupError(f"Permission request '{request_id}' not found")
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise LookupError(f"Permission request '{request_id}' is corrupted")

        suggested = str(record.get("suggested_rule") or "").strip()
        persisted_rule = None
        if decision == "allow_always" and suggested:
            self.add_allow_rule(suggested)
            persisted_rule = suggested
        record["status"] = "resolved"
        record["decision"] = decision
        record["allowed"] = decision in {"allow_once", "allow_always"}
        record["rule"] = persisted_rule
        record["persisted_rule"] = persisted_rule
        if reason is not None:
            record["reason"] = str(reason)
        record["resolved_at"] = datetime.now(UTC).isoformat()

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return record

    submit_decision = resolve_request

    def request_and_wait(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        conversation_id: str | None = None,
        permission_mode: str | CodePermissionMode | CodeAutonomy = CodePermissionMode.ASK_PERMISSIONS,
        request_id: str | None = None,
        emit_request: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Create a pending request and poll until resolved, cancelled, or timed out."""
        record = self.create_permission_request(
            tool_name=tool_name,
            arguments=arguments,
            turn_id=turn_id,
            conversation_id=conversation_id,
            permission_mode=permission_mode,
            request_id=request_id,
            emit_request=emit_request,
        )
        req_id = str(record["request_id"])
        check_cancelled = cancelled or self.cancelled
        poll_interval = max(0.01, float(poll_interval_seconds)) if poll_interval_seconds is not None else self.poll_interval_seconds
        deadline = time.monotonic() + (float(timeout_seconds) if timeout_seconds is not None else self.timeout_seconds)

        while True:
            if callable(check_cancelled) and check_cancelled():
                record["status"] = "cancelled"
                record["decision"] = "deny"
                record["allowed"] = False
                return record

            current = self.get_request(req_id)
            if current is not None:
                decision = current.get("decision")
                status = current.get("status")
                if decision in {"allow_once", "allow_always", "deny"} and (status == "resolved" or status == "pending"):
                    if decision == "allow_always":
                        # The grant is the rule this request was created with, never one the reply file names.
                        suggested = str(record.get("suggested_rule") or "").strip()
                        if suggested:
                            self.add_allow_rule(suggested)
                    if status != "resolved":
                        current["status"] = "resolved"
                    current["allowed"] = decision in {"allow_once", "allow_always"}
                    return current

            if time.monotonic() >= deadline:
                record["status"] = "timed_out"
                record["decision"] = "deny"
                record["allowed"] = False
                return record

            time.sleep(poll_interval)


__all__ = [
    "EXECUTION_TOOLS",
    "FILE_EDIT_TOOLS",
    "PermissionEvaluation",
    "PermissionReplyDecision",
    "READ_ONLY_TOOLS",
    "ToolPermissionBroker",
    "is_plan_file_path",
    "load_workspace_permission_rules",
    "parse_permission_rule",
    "save_workspace_permission_rule",
    "suggest_permission_rule",
]

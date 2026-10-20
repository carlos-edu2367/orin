"""Settings the person edits from the interface (W7): effective settings with provenance, permission rules per layer,
hooks, agents, output styles, keybindings and trusted folders.

Every route reads or writes files on the host (``~/.orin``, a workspace's ``.orin``), so they exist only where the
instance may touch host folders (the local profile); a shared server answers 404 (per-profile settings are W8). An
``orin code`` token may read but never write here. Values that leave through this API are redacted except where the
person edits them (the raw rules and hooks of a writable layer).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.redaction import redact_value

from .contracts import ApplicationNotFoundError
from .mcp_settings import McpApproval, change_mcp_approval, mcp_report
from .security import AuthenticatedPrincipal, AuthorizationError

WRITABLE_LAYERS = ("user", "project", "local")
RULE_LISTS = ("allow", "ask", "deny", "additionalDirectories")
MAX_KEYBINDINGS_BYTES = 64 * 1024
MAX_AGENT_PROMPT = 40_000
_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOOL_NAME = re.compile(r"^[A-Za-z_][\w.:-]*$")  # an agent file lists tool names, never permission rules


class SettingsRejected(ValueError):
    """A change the API refuses, with the reasons the editor shows."""

    def __init__(self, code: str, problems: list[str]) -> None:
        super().__init__(code)
        self.code = code
        self.problems = problems[:50]


class PermissionRuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(default=None, max_length=200)
    layer: Literal["user", "project", "local"]
    kind: Literal["allow", "ask", "deny", "additionalDirectories"]
    rule: str = Field(min_length=1, max_length=1000)
    op: Literal["add", "remove"]


class HooksChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(default=None, max_length=200)
    layer: Literal["user", "project", "local"]
    hooks: dict[str, Any] = Field(default_factory=dict)


class AgentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(default=None, max_length=200)
    scope: Literal["user", "project"]
    description: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1, max_length=MAX_AGENT_PROMPT)
    tools: list[str] | None = Field(default=None, max_length=128)
    disallowed_tools: list[str] = Field(default_factory=list, max_length=128)
    model: str | None = Field(default=None, max_length=120)
    permission_mode: Literal["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions", "auto"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class OutputStyleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(default=None, max_length=200)
    layer: Literal["user", "local"]
    style: str = Field(min_length=1, max_length=120)


class KeybindingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=MAX_KEYBINDINGS_BYTES)


class TrustedFolderChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


def _rejected(error: SettingsRejected) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "category": "VALIDATION", "message_key": error.code, "retryable": False,
                   "details": {"problems": error.problems}}},
        status_code=422,
    )


def _settings(root: Path | None, trusted: bool):
    from agentos.settings import load_effective_settings

    return load_effective_settings(root, trusted=trusted)


def _rule_entries(settings, key: str) -> list[dict[str, Any]]:
    """Every element of ``permissions.<key>`` with its layer and file, highest precedence first (W0-R18). A rule a
    higher layer already lists is kept, marked ``duplicate``."""
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for layer in reversed(settings.layers):
        values = (layer.data.get("permissions") or {}).get(key) if isinstance(layer.data.get("permissions"), dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            text = value.strip()
            entries.append({"rule": text, "layer": layer.name, "file": str(layer.path) if layer.path else None,
                            "duplicate": text in seen})
            seen.add(text)
    return entries


def _file_report(paths, layer: str) -> list[dict[str, Any]]:
    return [{"path": str(path), "exists": path.is_file()} for path in paths.files_for(layer)]


def effective_report(root: Path | None, trusted: bool) -> dict[str, Any]:
    from agentos.settings.paths import SettingsPaths

    settings = _settings(root, trusted)
    paths = SettingsPaths(root)
    writable: dict[str, str | None] = {}
    for layer in WRITABLE_LAYERS:
        try:
            writable[layer] = str(paths.writable_file(layer))
        except ValueError:
            writable[layer] = None
    return {
        "project_root": str(root) if root is not None else None,
        "trusted": trusted,
        "values": redact_value(dict(settings.data)),
        "provenance": dict(settings.provenance),
        "layers": [{"name": layer.name, "file": str(layer.path) if layer.path else None, "data": redact_value(dict(layer.data))}
                   for layer in settings.layers],
        "files": {layer: _file_report(paths, layer) for layer in ("managed", *WRITABLE_LAYERS)},
        "writable": writable,
        "permissions": {key: _rule_entries(settings, key) for key in RULE_LISTS},
        "errors": [{"file": error.path, "message": error.message} for error in settings.errors],
        "untrusted_ignored": list(settings.untrusted_ignored),
    }


def change_permission_rule(root: Path | None, change: PermissionRuleChange) -> None:
    from agentos.permissions.rules import parse_rule
    from agentos.settings.paths import SettingsPaths
    from agentos.settings.writer import update_settings_file

    rule = change.rule.strip()
    if change.kind == "additionalDirectories":
        if "\n" in rule or "\x00" in rule:
            raise SettingsRejected("invalid_directory", ["a folder is one line"])
    elif change.op == "add" and parse_rule(rule, kind=change.kind, source=change.layer) is None:
        raise SettingsRejected("invalid_rule", [f"'{rule[:200]}' is not a permission rule"])
    try:
        target = SettingsPaths(root).writable_file(change.layer)
    except ValueError as error:
        raise SettingsRejected("project_required", [str(error)]) from error

    def mutate(data: dict[str, Any]) -> None:
        permissions = data.get("permissions")
        if not isinstance(permissions, dict):
            permissions = data["permissions"] = {}
        current = permissions.get(change.kind)
        items = [item for item in current if isinstance(item, str)] if isinstance(current, list) else []
        remaining = [item for item in items if item.strip() != rule]
        if change.op == "remove" and len(remaining) == len(items):
            # W7-R5: the rule lives in another file (a Claude Code file, or another layer); nothing to remove here.
            raise SettingsRejected("rule_not_in_file", [f"'{rule[:200]}' is not in this file"])
        items = remaining
        if change.op == "add":
            items.append(rule)
        if items:
            permissions[change.kind] = items
        else:
            permissions.pop(change.kind, None)
        if not permissions:
            data.pop("permissions", None)

    update_settings_file(target, mutate)


def hooks_report(root: Path | None, trusted: bool) -> dict[str, Any]:
    from agentos.hooks.config import load_hook_specs
    from agentos.settings.paths import SettingsPaths

    settings = _settings(root, trusted)
    specs, errors = load_hook_specs(settings)
    paths = SettingsPaths(root)
    editable: dict[str, Any] = {}
    for layer in WRITABLE_LAYERS:
        try:
            target = paths.writable_file(layer)
        except ValueError:
            continue
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig")) if target.is_file() else {}
        except (OSError, ValueError):
            data = {}
        hooks = data.get("hooks") if isinstance(data, dict) else None
        editable[layer] = {"file": str(target), "hooks": hooks if isinstance(hooks, dict) else {}}
    return {
        "disabled": settings.get("disableAllHooks") is True,
        "effective": [{"event": spec.event.value, "matcher": spec.matcher, "type": spec.type, "source": spec.source,
                       "label": redact_value(spec.label()), "timeout": spec.timeout} for spec in specs],
        "editable": editable,
        "errors": [{"source": getattr(error, "source", ""), "message": str(getattr(error, "message", error))} for error in errors],
    }


def change_hooks(root: Path | None, change: HooksChange) -> None:
    from agentos.hooks.config import parse_hook_mapping
    from agentos.settings.paths import SettingsPaths
    from agentos.settings.writer import update_settings_file

    specs, errors = parse_hook_mapping(change.hooks, change.layer)
    problems = [str(getattr(error, "message", error)) for error in errors]
    if any(spec.type == "callback" for spec in specs):
        problems.append("callback hooks exist only for an SDK host")
    if problems:
        raise SettingsRejected("invalid_hooks", problems)
    try:
        target = SettingsPaths(root).writable_file(change.layer)
    except ValueError as error:
        raise SettingsRejected("project_required", [str(error)]) from error

    def mutate(data: dict[str, Any]) -> None:
        if change.hooks:
            data["hooks"] = change.hooks
        else:
            data.pop("hooks", None)

    update_settings_file(target, mutate)


def _agent_dir(scope: str, root: Path | None) -> Path:
    from agentos.settings.paths import orin_user_dir

    if scope == "user":
        return orin_user_dir() / "agents"
    if root is None:
        raise SettingsRejected("project_required", ["a project agent needs a conversation with a folder"])
    return root / ".orin" / "agents"


def agents_report(root: Path | None, trusted: bool) -> dict[str, Any]:
    from agentos.agentic.agent_definitions import load_agent_definitions

    catalog = load_agent_definitions(workspace_root=root, trusted=trusted)
    editable_dirs = {"user": _agent_dir("user", None)}
    if root is not None:
        editable_dirs["project"] = root / ".orin" / "agents"
    agents = []
    for definition in catalog.agents.values():
        path = definition.path
        editable = path is not None and any(path.parent == folder for folder in editable_dirs.values())
        agents.append({
            "name": definition.name, "description": definition.description, "origin": definition.origin,
            "path": str(path) if path else None, "editable": editable, "prompt": definition.prompt,
            "tools": list(definition.tools) if definition.tools is not None else None,
            "disallowed_tools": list(definition.disallowed_tools), "model": definition.model,
            "permission_mode": definition.permission_mode, "max_turns": definition.max_turns, "effort": definition.effort,
        })
    return {
        "agents": sorted(agents, key=lambda item: (item["origin"] == "builtin", item["name"])),
        "errors": [{"file": file, "message": message} for file, message in catalog.errors],
        "shadowed": [{"name": name, "kept": kept, "ignored": ignored} for name, kept, ignored in catalog.shadowed],
    }


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_agent(root: Path | None, name: str, body: AgentFile) -> Path:
    from agentos.agentic.agent_definitions import RESERVED_NAMES, parse_agent_file

    clean = name.strip().lower()
    if not _AGENT_NAME.fullmatch(clean) or clean in RESERVED_NAMES:
        raise SettingsRejected("invalid_agent_name", ["a name of lowercase letters, digits, '-' and '_' that is not a builtin agent"])
    problems = [f"'{tool[:80]}' is not a tool name" for tool in [*(body.tools or []), *body.disallowed_tools] if not _TOOL_NAME.fullmatch(tool.strip())]
    if problems:
        raise SettingsRejected("invalid_agent", problems)
    lines = ["---", f"name: {clean}", f"description: {_scalar(' '.join(body.description.split()))}"]
    if body.tools is not None:
        lines.append(f"tools: {', '.join(tool.strip() for tool in body.tools) or _scalar('')}")
    if body.disallowed_tools:
        lines.append(f"disallowedTools: {', '.join(tool.strip() for tool in body.disallowed_tools)}")
    for key, value in (("model", body.model), ("permissionMode", body.permission_mode), ("effort", body.effort)):
        if value:
            lines.append(f"{key}: {_scalar(value.strip())}")
    if body.max_turns is not None:
        lines.append(f"maxTurns: {body.max_turns}")
    text = "\n".join([*lines, "---", "", body.prompt.strip(), ""])
    folder = _agent_dir(body.scope, root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{clean}.md"
    temporary = folder / f".{clean}.md.tmp"
    temporary.write_text(text, encoding="utf-8")
    try:
        parse_agent_file(temporary, origin=body.scope)  # what Orin will read back, checked before it replaces anything
    except (OSError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise SettingsRejected("invalid_agent", [str(error)[:300]]) from error
    temporary.replace(target)
    return target


def delete_agent(root: Path | None, name: str, scope: str) -> bool:
    clean = name.strip().lower()
    if not _AGENT_NAME.fullmatch(clean):
        raise SettingsRejected("invalid_agent_name", ["not an agent name"])
    target = _agent_dir(scope, root) / f"{clean}.md"
    if not target.is_file() or target.is_symlink():
        return False
    target.unlink()
    return True


def output_styles_report(root: Path | None, trusted: bool) -> dict[str, Any]:
    from agentos.agentic.output_styles import load_output_styles

    settings = _settings(root, trusted)
    from agentos.settings.paths import orin_user_dir

    # Without a conversation there is no repository: only the built-in and personal styles.
    styles = load_output_styles(root if root is not None else orin_user_dir(), trusted=trusted and root is not None)
    active = settings.get("outputStyle")
    return {
        "styles": [{"key": key, "name": style.name, "description": style.description, "source": style.source}
                   for key, style in sorted(styles.items())],
        "active": active if isinstance(active, str) and active.strip() else "default",
        "active_layer": settings.provenance.get("outputStyle"),
    }


def change_output_style(root: Path | None, trusted: bool, change: OutputStyleChange) -> None:
    from agentos.settings.paths import SettingsPaths
    from agentos.settings.writer import update_settings_file

    key = change.style.strip().lower()
    known = {item["key"] for item in output_styles_report(root, trusted)["styles"]}
    if key not in known:
        raise SettingsRejected("unknown_output_style", [f"no output style named '{change.style[:80]}'"])
    try:
        target = SettingsPaths(root).writable_file(change.layer)
    except ValueError as error:
        raise SettingsRejected("project_required", [str(error)]) from error

    def mutate(data: dict[str, Any]) -> None:
        data["outputStyle"] = key

    update_settings_file(target, mutate)


def keybindings_files() -> dict[str, Path]:
    from agentos.settings.paths import claude_user_dir, orin_user_dir

    return {"orin": orin_user_dir() / "keybindings.json", "claude": claude_user_dir() / "keybindings.json"}


def keybindings_report() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, path in keybindings_files().items():
        entry: dict[str, Any] = {"path": str(path), "text": None, "error": None}
        try:
            if path.is_file():
                if path.stat().st_size > MAX_KEYBINDINGS_BYTES:
                    entry["error"] = f"larger than {MAX_KEYBINDINGS_BYTES} bytes"
                else:
                    entry["text"] = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            entry["error"] = f"unreadable: {type(error).__name__}"
        report[name] = entry
    return report


def write_keybindings(change: KeybindingsChange) -> None:
    text = change.text
    try:
        data = json.loads(text) if text.strip() else []
    except ValueError as error:
        raise SettingsRejected("invalid_keybindings", [f"not JSON: {error}"[:300]]) from error
    bindings = data.get("bindings") if isinstance(data, dict) else data
    if not isinstance(bindings, list):
        raise SettingsRejected("invalid_keybindings", ["a list of bindings, or an object with a 'bindings' list"])
    path = keybindings_files()["orin"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".keybindings.json.tmp")
    temporary.write_text(text if text.strip() else "[]\n", encoding="utf-8")
    temporary.replace(path)


def register_settings_routes(
    app: FastAPI,
    services,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    app.add_exception_handler(SettingsRejected, lambda _request, error: _rejected(error))  # type: ignore[arg-type]

    def owner(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        services.capabilities.require("host_folders")
        current = principal_for(request, mutable=mutable)
        services.security.authorize(current, action="settings.write" if mutable else "settings.read", resource_id=None, purpose=purpose)
        if mutable and current.credential_kind == "cli_token":
            raise AuthorizationError("orin code does not change settings through the interface's API")
        return current

    def folder_of(principal: AuthenticatedPrincipal, conversation_id: str | None) -> tuple[Path | None, bool]:
        from agentos.permissions.trust import WorkspaceTrustStore

        if not conversation_id:
            return None, False
        root = Path(conversation_root(conversation_id, principal))
        return root, WorkspaceTrustStore().is_trusted(principal.user_id, root)

    @app.get("/v1/settings/effective")
    async def effective_settings(request: Request, conversation_id: str | None = None) -> JSONResponse:
        principal = owner(request, mutable=False, purpose="settings.effective")
        root, trusted = folder_of(principal, conversation_id)
        return JSONResponse(await run_in_threadpool(effective_report, root, trusted))

    @app.put("/v1/settings/permissions")
    async def change_permissions(payload: PermissionRuleChange, request: Request) -> JSONResponse:
        principal = owner(request, mutable=True, purpose="settings.permissions")
        root, trusted = folder_of(principal, payload.conversation_id)
        await run_in_threadpool(change_permission_rule, root, payload)
        report = await run_in_threadpool(effective_report, root, trusted)
        return JSONResponse({"permissions": report["permissions"]})

    @app.get("/v1/settings/hooks")
    async def list_hooks(request: Request, conversation_id: str | None = None) -> JSONResponse:
        principal = owner(request, mutable=False, purpose="settings.hooks")
        root, trusted = folder_of(principal, conversation_id)
        return JSONResponse(await run_in_threadpool(hooks_report, root, trusted))

    @app.put("/v1/settings/hooks")
    async def save_hooks(payload: HooksChange, request: Request) -> JSONResponse:
        principal = owner(request, mutable=True, purpose="settings.hooks")
        root, trusted = folder_of(principal, payload.conversation_id)
        await run_in_threadpool(change_hooks, root, payload)
        return JSONResponse(await run_in_threadpool(hooks_report, root, trusted))

    @app.get("/v1/settings/agents")
    async def list_agents(request: Request, conversation_id: str | None = None) -> JSONResponse:
        principal = owner(request, mutable=False, purpose="settings.agents")
        root, trusted = folder_of(principal, conversation_id)
        return JSONResponse(await run_in_threadpool(agents_report, root, trusted))

    @app.put("/v1/settings/agents/{name}")
    async def save_agent(name: str, payload: AgentFile, request: Request) -> JSONResponse:
        principal = owner(request, mutable=True, purpose="settings.agents")
        root, trusted = folder_of(principal, payload.conversation_id)
        path = await run_in_threadpool(write_agent, root, name, payload)
        return JSONResponse({"path": str(path), **await run_in_threadpool(agents_report, root, trusted)})

    @app.delete("/v1/settings/agents/{name}")
    async def remove_agent(name: str, request: Request, scope: Literal["user", "project"] = "user",
                           conversation_id: str | None = None) -> JSONResponse:
        principal = owner(request, mutable=True, purpose="settings.agents")
        root, trusted = folder_of(principal, conversation_id)
        if not await run_in_threadpool(delete_agent, root, name, scope):
            raise ApplicationNotFoundError(name)
        return JSONResponse(await run_in_threadpool(agents_report, root, trusted))

    @app.get("/v1/settings/output-styles")
    async def list_output_styles(request: Request, conversation_id: str | None = None) -> JSONResponse:
        principal = owner(request, mutable=False, purpose="settings.output_styles")
        root, trusted = folder_of(principal, conversation_id)
        return JSONResponse(await run_in_threadpool(output_styles_report, root, trusted))

    @app.put("/v1/settings/output-styles")
    async def choose_output_style(payload: OutputStyleChange, request: Request) -> JSONResponse:
        principal = owner(request, mutable=True, purpose="settings.output_styles")
        root, trusted = folder_of(principal, payload.conversation_id)
        await run_in_threadpool(change_output_style, root, trusted, payload)
        return JSONResponse(await run_in_threadpool(output_styles_report, root, trusted))

    @app.get("/v1/settings/keybindings")
    async def read_keybindings(request: Request) -> JSONResponse:
        owner(request, mutable=False, purpose="settings.keybindings")
        return JSONResponse(await run_in_threadpool(keybindings_report))

    @app.put("/v1/settings/keybindings")
    async def save_keybindings(payload: KeybindingsChange, request: Request) -> JSONResponse:
        owner(request, mutable=True, purpose="settings.keybindings")
        await run_in_threadpool(write_keybindings, payload)
        return JSONResponse(await run_in_threadpool(keybindings_report))

    @app.get("/v1/settings/trusted-folders")
    async def list_trusted_folders(request: Request) -> JSONResponse:
        from agentos.permissions.trust import WorkspaceTrustStore

        principal = owner(request, mutable=False, purpose="settings.trusted_folders")
        folders = await run_in_threadpool(WorkspaceTrustStore().trusted_roots, principal.user_id)
        return JSONResponse({"folders": folders})

    @app.get("/v1/settings/mcp")
    async def mcp_servers(request: Request, conversation_id: str | None = None) -> JSONResponse:
        """W7: the configuration-file MCP servers in force for a folder, per scope, and the project's pending ones."""
        principal = owner(request, mutable=False, purpose="settings.mcp")
        root, trusted = folder_of(principal, conversation_id)
        return JSONResponse(await run_in_threadpool(lambda: mcp_report(root, trusted, personal=services.capabilities.host_folders)))

    @app.put("/v1/settings/mcp/approval")
    async def mcp_approval(payload: McpApproval, request: Request) -> JSONResponse:
        """W7: approve, deny or forget one of the project's servers (the per-folder grants file only)."""
        principal = owner(request, mutable=True, purpose="settings.mcp")
        root, trusted = folder_of(principal, payload.conversation_id)
        if root is None:
            raise ApplicationNotFoundError("conversation folder")
        await run_in_threadpool(change_mcp_approval, root, trusted, payload)
        return JSONResponse(await run_in_threadpool(lambda: mcp_report(root, trusted, personal=services.capabilities.host_folders)))

    @app.post("/v1/settings/trusted-folders/revoke")
    async def revoke_trusted_folder(payload: TrustedFolderChange, request: Request) -> JSONResponse:
        from agentos.permissions.trust import WorkspaceTrustStore

        principal = owner(request, mutable=True, purpose="settings.trusted_folders")
        store = WorkspaceTrustStore()
        if payload.path not in await run_in_threadpool(store.trusted_roots, principal.user_id):
            raise ApplicationNotFoundError("trusted folder")
        await run_in_threadpool(store.revoke_key, principal.user_id, payload.path)
        return JSONResponse({"folders": await run_in_threadpool(store.trusted_roots, principal.user_id)})


__all__ = ["SettingsRejected", "register_settings_routes"]

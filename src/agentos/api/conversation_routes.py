"""Conversation routes the W7 interface adds: background tasks (the panel), the slash command catalog and the status
line. Agents and teammates come from ``/overview``; this module covers what only the workspace knows."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from agentos.redaction import redact_text

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from .security import AuthenticatedPrincipal

MANIFEST = ".orin/logs/processes.json"
LOG_TAIL_BYTES = 4096


def _manifest(root: Path) -> dict[str, dict[str, Any]]:
    path = root / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):
        data = {str(item.get("pid")): item for item in data if isinstance(item, dict) and item.get("pid") is not None}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}


def _log_tail(root: Path, relative: object) -> str:
    if not isinstance(relative, str) or not relative:
        return ""
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())  # a manifest edited to point outside the workspace is not read
        with target.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            return redact_text(handle.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return ""


def shell_tasks(root: Path) -> list[dict[str, Any]]:
    from agentos.process_identity import same_process

    tasks = []
    for key, entry in _manifest(root).items():
        try:
            pid = int(key)
        except ValueError:
            continue
        created = entry.get("created")
        verifiable = created not in (None, "")
        running = same_process(pid, created) if verifiable else False
        tasks.append({
            "task_id": f"shell:{pid}", "kind": "shell", "pid": pid,
            "command": redact_text(str(entry.get("command") or ""))[:400],
            "description": redact_text(str(entry.get("description") or ""))[:200],
            "started_at": entry.get("started_at"), "url": entry.get("detected_url"),
            # A record written before W7 has no creation time: it is shown, never stopped from here (W6-R3).
            "state": "running" if running else ("finished" if verifiable else "unknown"),
            "stoppable": running,
            "log_tail": _log_tail(root, entry.get("log_path")),
        })
    return sorted(tasks, key=lambda item: str(item.get("started_at") or ""), reverse=True)


def stop_shell(root: Path, task_id: str) -> bool:
    from agentos.process_identity import kill_verified

    if not task_id.startswith("shell:"):
        raise ApplicationValidationError("only background shells are stopped here")
    key = task_id.split(":", 1)[1]
    entry = _manifest(root).get(key)
    if entry is None or not key.isdigit():
        raise ApplicationNotFoundError(task_id)
    return kill_verified(int(key), entry.get("created"))


STATUSLINE_TIMEOUT_SECONDS = 5.0
STATUSLINE_MAX_LINES = 5
STATUSLINE_MAX_CHARS = 400
STATUSLINE_CACHE_SECONDS = 1.0
STATUSLINE_MAX_CACHE_ENTRIES = 512
_STATUSLINE_CACHE: dict[str, tuple[float, str, dict[str, Any]]] = {}
_STATUSLINE_LOCKS: dict[str, Any] = {}
_STATUSLINE_GUARD = threading.Lock()


def statusline_command(settings) -> tuple[str | None, str | None]:
    """``(command, None)`` when the status line may run, else ``(None, why not)``. It runs only from a trusted folder
    (the caller checks), never while hooks are disabled, and only from managed policy when that is all it allows."""
    raw = settings.get("statusLine")
    if not isinstance(raw, dict) or raw.get("type") != "command" or not str(raw.get("command") or "").strip():
        return None, "not_configured"
    if settings.get("disableAllHooks") is True:
        return None, "hooks_disabled"
    if settings.get("allowManagedHooksOnly") is True and settings.provenance.get("statusLine") != "managed":
        return None, "managed_hooks_only"
    return str(raw["command"]), None


def statusline_input(conversation: dict[str, Any], root: Path, settings, *, added_dirs: list[str], usage: dict[str, Any]) -> dict[str, Any]:
    """What the status line command reads on stdin: the documented fields Orin knows (others are omitted)."""
    from agentos.version import __version__

    model = str(conversation.get("model_id") or "")
    style = settings.get("outputStyle")
    payload: dict[str, Any] = {
        "session_id": str(conversation.get("conversation_id") or ""),
        "session_name": str(conversation.get("title") or "") or None,
        "cwd": str(root),
        "model": {"id": model, "display_name": model.rsplit("/", 1)[-1] or model},
        "workspace": {"current_dir": str(root), "project_dir": str(root), "added_dirs": added_dirs},
        "version": __version__,
        "output_style": {"name": style if isinstance(style, str) and style else "default"},
    }
    cost = usage.get("estimated_cost_usd")
    if isinstance(cost, (int, float)):
        payload["cost"] = {"total_cost_usd": float(cost)}
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    if isinstance(tokens_in, int) and isinstance(tokens_out, int):
        payload["context_window"] = {"total_input_tokens": tokens_in, "total_output_tokens": tokens_out}
    return payload


def run_statusline(conversation_id: str, command: str, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Run the command with the JSON on stdin; its first lines are the status line (ANSI colours kept)."""
    import os
    import threading
    import time

    from agentos.hooks.processes import run_shell_command

    digest = json.dumps(payload, sort_keys=True) + "\x00" + command
    with _STATUSLINE_GUARD:
        lock = _STATUSLINE_LOCKS.get(conversation_id)
        if lock is None:
            if len(_STATUSLINE_LOCKS) >= STATUSLINE_MAX_CACHE_ENTRIES:
                for stale_cid in list(_STATUSLINE_LOCKS.keys()):
                    if len(_STATUSLINE_LOCKS) < STATUSLINE_MAX_CACHE_ENTRIES:
                        break
                    stale_lock = _STATUSLINE_LOCKS.get(stale_cid)
                    if stale_lock is not None and not stale_lock.locked():
                        _STATUSLINE_LOCKS.pop(stale_cid, None)
            lock = threading.Lock()
            _STATUSLINE_LOCKS[conversation_id] = lock
        else:
            # Refresh insertion order for active conversation lock
            _STATUSLINE_LOCKS.pop(conversation_id, None)
            _STATUSLINE_LOCKS[conversation_id] = lock

    with lock:  # one run per conversation at a time; a repeat within a second reuses the last answer
        with _STATUSLINE_GUARD:
            cached = _STATUSLINE_CACHE.get(conversation_id)
        if cached is not None and cached[1] == digest and time.monotonic() - cached[0] < STATUSLINE_CACHE_SECONDS:
            return cached[2]
        environment = {**os.environ, "COLUMNS": "120", "LINES": "1"}
        result = run_shell_command(command, cwd=str(root), env=environment, stdin_text=json.dumps(payload), timeout_seconds=STATUSLINE_TIMEOUT_SECONDS)
        if result.timed_out or result.error or result.exit_code not in (0, None):
            answer = {"enabled": True, "lines": [], "error": "timed_out" if result.timed_out else "command_failed"}
        else:
            lines = [line[:STATUSLINE_MAX_CHARS] for line in result.stdout.replace("\r\n", "\n").split("\n") if line.strip()]
            answer = {"enabled": True, "lines": lines[:STATUSLINE_MAX_LINES], "error": None}
        with _STATUSLINE_GUARD:
            _STATUSLINE_CACHE.pop(conversation_id, None)
            _STATUSLINE_CACHE[conversation_id] = (time.monotonic(), digest, answer)
            while len(_STATUSLINE_CACHE) > STATUSLINE_MAX_CACHE_ENTRIES:
                oldest_cid = next(iter(_STATUSLINE_CACHE))
                _STATUSLINE_CACHE.pop(oldest_cid, None)
                stale_lock = _STATUSLINE_LOCKS.get(oldest_cid)
                if stale_lock is not None and not stale_lock.locked():
                    _STATUSLINE_LOCKS.pop(oldest_cid, None)
        return answer


def command_catalog(root: Path | None, trusted: bool, *, personal: bool) -> list[dict[str, Any]]:
    """The skills and commands a person can run with ``/name`` in this folder (built-ins live in the interface)."""
    from agentos.skills.disk import load_disk_skills

    catalog = load_disk_skills(workspace_root=root, trusted=trusted, personal=personal)
    return sorted(
        ({"name": skill.name, "description": " ".join(skill.description.split())[:300], "kind": skill.kind,
          "origin": skill.origin, "argument_hint": skill.argument_hint[:120], "context": skill.context}
         for skill in catalog.skills.values() if skill.user_invocable),
        key=lambda item: item["name"],
    )


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before_message_id: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=160)


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)


class ShellRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=4000)


def fork_conversation(services, user_id: str, conversation_id: str, *, before_message_id: str | None = None,
                      title: str | None = None) -> dict[str, Any]:
    """Fork in the store, then give a chat outside a project the same folder (a project's chats share the project's)."""
    result = dict(services.conversation_application.fork(conversation_id, user_id, before_message_id=before_message_id, title=title))
    workspaces = getattr(services, "local_workspaces", None)
    if not result.get("project_id") and workspaces is not None:
        root = workspaces.root_for(conversation_id, user_id)
        if root:
            workspaces.set_root(str(result["conversation_id"]), user_id, root)
    return result


def register_conversation_routes(
    app: FastAPI,
    services,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    @app.get("/v1/conversations/{conversation_id}/tasks")
    async def list_tasks(conversation_id: str, request: Request) -> JSONResponse:
        def _list_tasks() -> dict[str, Any]:
            principal = principal_for(request)
            services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.tasks")
            root = conversation_root(conversation_id, principal)
            return {"tasks": shell_tasks(root)}

        return JSONResponse(await run_in_threadpool(_list_tasks))

    @app.post("/v1/conversations/{conversation_id}/tasks/{task_id}/stop")
    async def stop_task(conversation_id: str, task_id: str, request: Request) -> JSONResponse:
        def _stop_task() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.tasks")
            services.capabilities.require("shell")
            root = conversation_root(conversation_id, principal)
            stopped = stop_shell(root, task_id)
            return {"task_id": task_id, "stopped": stopped, "tasks": shell_tasks(root)}

        return JSONResponse(await run_in_threadpool(_stop_task))

    @app.post("/v1/conversations/{conversation_id}/fork", status_code=201)
    async def fork(conversation_id: str, payload: ForkRequest, request: Request) -> JSONResponse:
        """/branch and /fork (W7): a new conversation with this one's messages (or those before one of them)."""
        def _fork() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.fork")
            return fork_conversation(
                services, principal.user_id, conversation_id, before_message_id=payload.before_message_id, title=payload.title
            )

        return JSONResponse(await run_in_threadpool(_fork), status_code=201)

    @app.patch("/v1/conversations/{conversation_id}/title")
    async def rename(conversation_id: str, payload: RenameRequest, request: Request) -> JSONResponse:
        """/rename (W7)."""
        def _rename() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.rename")
            try:
                return services.conversation_application.rename(conversation_id, principal.user_id, payload.title)
            except ValueError as error:
                raise ApplicationValidationError(str(error)) from error

        return JSONResponse(await run_in_threadpool(_rename))

    @app.delete("/v1/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str, request: Request) -> Response:
        """Delete a conversation and its history (404 if not the person's, 409 while it runs or for the Friend).
        Files on disk stay where they are."""
        def _delete() -> None:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.delete")
            services.conversation_application.delete(conversation_id, principal.user_id)
            with _STATUSLINE_GUARD:
                _STATUSLINE_CACHE.pop(conversation_id, None)
                existing_lock = _STATUSLINE_LOCKS.get(conversation_id)
                if existing_lock is None or not existing_lock.locked():
                    _STATUSLINE_LOCKS.pop(conversation_id, None)

        await run_in_threadpool(_delete)
        return Response(status_code=204)

    @app.post("/v1/conversations/{conversation_id}/shell", status_code=201)
    async def shell(conversation_id: str, payload: ShellRequest, request: Request) -> JSONResponse:
        """`!command` (W7): a turn that runs the person's command in the session's folder, without the model."""
        def _shell() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.shell")
            services.capabilities.require("shell")
            key = request.headers.get("idempotency-key") or ""
            if not key or len(key) > 255:
                raise ApplicationValidationError("an Idempotency-Key header is required")
            try:
                receipt = services.conversation_application.shell(principal.user_id, conversation_id, payload.command, key)
            except ValueError as error:
                raise ApplicationValidationError(str(error)) from error
            return receipt if isinstance(receipt, dict) else {
                "conversation_id": getattr(receipt, "conversation_id", conversation_id), "turn_id": getattr(receipt, "turn_id", None),
                "message_id": getattr(receipt, "message_id", None), "state": getattr(receipt, "state", None),
            }

        return JSONResponse(await run_in_threadpool(_shell), status_code=201)

    def trusted(principal: AuthenticatedPrincipal, root: Path) -> bool:
        from agentos.permissions.trust import WorkspaceTrustStore

        return WorkspaceTrustStore().is_trusted(principal.user_id, root)

    @app.get("/v1/conversations/{conversation_id}/commands")
    async def list_commands(conversation_id: str, request: Request) -> JSONResponse:
        def _list_commands() -> dict[str, Any]:
            principal = principal_for(request)
            services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.commands")
            root = conversation_root(conversation_id, principal)
            is_trusted = trusted(principal, root)
            items = command_catalog(root, is_trusted, personal=services.capabilities.host_folders)
            return {"commands": items, "trusted": is_trusted}

        return JSONResponse(await run_in_threadpool(_list_commands))

    @app.post("/v1/conversations/{conversation_id}/statusline")
    async def statusline(conversation_id: str, request: Request) -> JSONResponse:
        """The status line (settings ``statusLine``): a command that reads the session as JSON and prints a line."""
        from agentos.settings import load_effective_settings

        def _statusline() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.statusline")
            root = conversation_root(conversation_id, principal)  # the conversation must be this person's, whatever else
            if not services.capabilities.shell:
                return {"enabled": False, "lines": [], "error": "shell_unavailable"}
            is_trusted = trusted(principal, root)
            if not is_trusted:  # as in Claude Code: the line stays blank until the folder is trusted
                return {"enabled": False, "lines": [], "error": "workspace_not_trusted"}
            settings = load_effective_settings(root, trusted=True)
            command, why_not = statusline_command(settings)
            if command is None:
                return {"enabled": False, "lines": [], "error": why_not}
            application = services.conversation_application
            conversation = application.get(conversation_id, principal.user_id) if application is not None else {}
            usage: dict[str, Any] = {}
            try:
                overview = application.overview(conversation_id, principal.user_id)
                usage = dict((overview or {}).get("token_usage") or {}) if isinstance(overview, dict) else {}
            except Exception:  # noqa: BLE001 - the line runs without cost figures rather than not at all
                usage = {}
            added: list[str] = []
            directories = services.conversation_directories
            if directories is not None and services.capabilities.host_folders:
                added = [str(path) for path in directories.paths(principal.user_id, conversation_id)]
            payload = statusline_input(dict(conversation or {}), root, settings, added_dirs=added, usage=usage)
            return run_statusline(conversation_id, command, root, payload)

        return JSONResponse(await run_in_threadpool(_statusline))


__all__ = ["fork_conversation", "register_conversation_routes", "shell_tasks", "stop_shell"]

"""Conversation routes the W7 interface adds: background tasks (the panel), the slash command catalog and the status
line. Agents and teammates come from ``/overview``; this module covers what only the workspace knows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

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


def register_conversation_routes(
    app: FastAPI,
    services,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    @app.get("/v1/conversations/{conversation_id}/tasks")
    async def list_tasks(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.tasks")
        root = conversation_root(conversation_id, principal)
        return JSONResponse({"tasks": await run_in_threadpool(shell_tasks, root)})

    @app.post("/v1/conversations/{conversation_id}/tasks/{task_id}/stop")
    async def stop_task(conversation_id: str, task_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.tasks")
        services.capabilities.require("shell")
        root = conversation_root(conversation_id, principal)
        stopped = await run_in_threadpool(stop_shell, root, task_id)
        return JSONResponse({"task_id": task_id, "stopped": stopped, "tasks": await run_in_threadpool(shell_tasks, root)})


__all__ = ["register_conversation_routes", "shell_tasks", "stop_shell"]

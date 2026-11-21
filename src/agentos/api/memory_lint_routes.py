"""HTTP route for on-demand instruction-vs-memory contradiction lint (`POST /v1/memory/lint`, WS-D7)."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from agentos.local_workspace import FolderRejected
from agentos.local_workspace.paths import normalize_path
from agentos.memory.instruction_lint import (
    NO_WORKSPACE_WARNING,
    LintReport,
)
from agentos.persistence.postgres.instruction_lint_runtime import build_instruction_linter
from agentos.persistence.postgres.schema import workspace_roots
from agentos.profile_files.binding import files_root, relative_display, resolve_inside

from .security import AuthenticatedPrincipal, AuthorizationError


class MemoryLintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str | None = Field(default=None, max_length=4096)
    project_root: str | None = Field(default=None, max_length=4096)
    project_id: str | None = Field(default=None, max_length=255)
    dry_run: bool = False


def _resolve_engine(services: Any) -> Any | None:
    for attr in ("projects", "local_workspaces", "context_observations"):
        port = getattr(services, attr, None)
        engine = getattr(port, "_engine", None) if port is not None else None
        if engine is not None:
            return engine
    quota_service = getattr(services, "quota_service", None)
    qstore = getattr(quota_service, "_store", None) if quota_service is not None else None
    engine = getattr(qstore, "_engine", None)
    if engine is not None:
        return engine
    return None


def _norm_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_user_bound_workspace_root(services: Any, user_id: str, candidate: Path) -> bool:
    """Return True if ``candidate`` matches a workspace root bound to ``user_id`` or instance default."""
    target_key = _norm_key(candidate)
    default_root = getattr(services, "workspace_root", None)
    if default_root:
        try:
            if _norm_key(Path(default_root)) == target_key:
                return True
        except OSError:
            pass

    engine = _resolve_engine(services)
    if engine is not None:
        try:
            with engine.connect() as conn:
                bound_paths = conn.execute(
                    select(workspace_roots.c.root_path).where(workspace_roots.c.user_id == str(user_id))
                ).scalars().all()
            for raw in bound_paths:
                if not raw:
                    continue
                try:
                    if _norm_key(Path(str(raw))) == target_key:
                        return True
                except OSError:
                    continue
        except Exception:  # noqa: BLE001 - fail closed if lookup fails
            return False
    return False


def _resolve_workspace_root(
    services: Any,
    principal: AuthenticatedPrincipal,
    body: MemoryLintRequest,
) -> tuple[Path | None, bool, str | None]:
    explicit_root = (body.workspace_root or body.project_root or "").strip()
    project_id = (body.project_id or "").strip() or None
    profile_files_active = bool(getattr(services.capabilities, "profile_files", False))

    if explicit_root:
        if profile_files_active:
            try:
                root_path = resolve_inside(files_root(principal.user_id), explicit_root)
            except FolderRejected as exc:
                raise AuthorizationError(str(exc)) from exc
            from agentos.permissions.trust import WorkspaceTrustStore

            trusted = WorkspaceTrustStore().is_trusted(principal.user_id, root_path)
            return root_path, trusted, project_id

        services.capabilities.require("host_folders")
        try:
            root_path = normalize_path(explicit_root)
        except FolderRejected as exc:
            raise ApplicationValidationError(str(exc)) from exc
        from agentos.permissions.trust import WorkspaceTrustStore

        trusted = WorkspaceTrustStore().is_trusted(principal.user_id, root_path)
        if not trusted and not _is_user_bound_workspace_root(services, principal.user_id, root_path):
            raise AuthorizationError(
                "workspace_root não pertence a um projeto/conversa vinculado ou diretório confiável do usuário."
            )
        return root_path, trusted, project_id

    if project_id:
        projects_port = getattr(services, "projects", None)
        if projects_port is not None and not projects_port.__class__.__name__.startswith("_Unavailable"):
            project = projects_port.get(project_id, principal.user_id)
            if project is None:
                raise ApplicationNotFoundError(project_id)
            workspace_id = (
                project.get("workspace_id")
                if isinstance(project, dict)
                else getattr(project, "workspace_id", None)
            )
            local_ws = getattr(services, "local_workspaces", None)
            if (
                workspace_id
                and local_ws is not None
                and not local_ws.__class__.__name__.startswith("_Unavailable")
                and hasattr(local_ws, "root_for")
            ):
                bound_root = local_ws.root_for(str(workspace_id), principal.user_id)
                if bound_root:
                    if profile_files_active:
                        if relative_display(files_root(principal.user_id), bound_root) is None:
                            raise AuthorizationError("Pasta do projeto está fora da área de arquivos do perfil.")
                    else:
                        services.capabilities.require("host_folders")
                    from agentos.permissions.trust import WorkspaceTrustStore

                    root_path = Path(str(bound_root))
                    trusted = WorkspaceTrustStore().is_trusted(principal.user_id, root_path)
                    return root_path, trusted, project_id

    default_root = getattr(services, "workspace_root", None)
    if default_root:
        from agentos.permissions.trust import WorkspaceTrustStore

        root_path = Path(default_root)
        trusted = WorkspaceTrustStore().is_trusted(principal.user_id, root_path)
        return root_path, trusted, project_id

    return None, False, project_id


def register_memory_lint_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    """Register `POST /v1/memory/lint`."""

    @app.post("/v1/memory/lint")
    async def lint_memory_instructions(
        request: Request,
        body: MemoryLintRequest | None = None,
    ) -> JSONResponse:
        req = body or MemoryLintRequest()

        def _run() -> dict[str, Any]:
            principal = principal_for(request, mutable=True)
            services.security.authorize(
                principal,
                action="memory.list",
                resource_id=req.project_id,
                purpose="memory.lint",
            )
            services.security.check_rate_limit(
                principal,
                action="memory.lint",
                origin=request.headers.get("origin"),
            )
            root_path, trusted, project_id = _resolve_workspace_root(services, principal, req)
            if root_path is None:
                return LintReport(
                    contradictions=[],
                    files_checked=[],
                    memories_checked=0,
                    estimated_calls=0,
                    dry_run=req.dry_run,
                    warning=NO_WORKSPACE_WARNING,
                ).to_dict()

            linter = getattr(services, "instruction_linter", None)
            if linter is None:
                linter = build_instruction_linter(_resolve_engine(services))

            report = linter.run(
                principal.user_id,
                root_path,
                project_id=project_id,
                dry_run=req.dry_run,
                trusted=trusted,
            )
            return report.to_dict()

        payload = await run_in_threadpool(_run)
        return JSONResponse(payload)


__all__ = ["MemoryLintRequest", "register_memory_lint_routes"]

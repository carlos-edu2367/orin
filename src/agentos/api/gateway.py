from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from time import monotonic
from typing import Any, Mapping
from uuid import uuid4

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .contracts import (
    ApplicationConflictError,
    ApplicationIndeterminateError,
    ApplicationNotFoundError,
    ApplicationValidationError,
    ProviderCredentialRejectedError,
    ExecutionApplication,
    ProviderConfigurationApplication,
    ProviderModelCatalogApplication,
    ConversationApplication,
    ResourceApplication,
    SecurityService,
)
from agentos.provider_catalog.models import PROVIDERS_WITH_OPTIONAL_KEY, ProviderCatalogContext
from agentos.provider_catalog.service import ProviderCatalogUnavailable
from .events import CursorError, InMemoryClientEventStream
from .security import AuthenticationError, AuthorizationError, AuthenticatedPrincipal, InMemorySecurityService, RateLimitError
from agentos.agentic.file_preview import media_type_for, open_in_default_application
from agentos.installation import orin_paths, read_installation_status, remove_installed_version, runtime_profile
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError, resolve_workspace
from agentos.local_workspace import FolderRejected, choose_folder, inspect_folder
from agentos.uploads.media import MAX_FILES_PER_MESSAGE, MAX_UPLOAD_BYTES, UploadRejected
from agentos.uploads.promotion import discard_promoted, promote_uploads
from agentos.mcp.catalog import search_catalog
from agentos.mcp.service import McpConnectionFailed, McpServerNotFound, McpServiceError
from agentos.mcp.toolset import discover as _mcp_connect
from agentos.plugins.service import PluginServiceError


# Conversation SSE pacing. The active poll is fast enough to feel like token
# streaming; the idle poll keeps a quiet conversation from busy-looping, and the
# max lifetime bounds a stream that a client abandoned without closing.
_SSE_ACTIVE_POLL_SECONDS = 0.08
_SSE_IDLE_POLL_SECONDS = 0.35
_SSE_HEARTBEAT_SECONDS = 10.0
_SSE_MAX_SECONDS = 900.0


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateExecutionRequest(_RequestModel):
    agent_id: str = Field(min_length=1, max_length=255)
    task_ref: str = Field(min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=255)
    limits: dict[str, int | float | None] = Field(default_factory=dict)
    expected_agent_version: int | None = Field(default=None, ge=1)
    purpose: str = Field(default="execution.create", min_length=1, max_length=128)


class ControlExecutionRequest(_RequestModel):
    action: str = Field(pattern="^(PAUSE|RESUME|CANCEL)$")
    expected_state_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="user_requested", min_length=1, max_length=128)
    purpose: str = Field(default="execution.control", min_length=1, max_length=128)


class ExecutionInputRequest(_RequestModel):
    input_ref: str = Field(min_length=1, max_length=4096)
    expected_state_version: int = Field(ge=1)
    purpose: str = Field(default="execution.input", min_length=1, max_length=128)


class OpenStreamRequest(_RequestModel):
    execution_ids: list[str] = Field(min_length=1, max_length=64)
    event_types: list[str] = Field(default_factory=list, max_length=32)
    purpose: str = Field(default="execution.observe", min_length=1, max_length=128)

    @field_validator("execution_ids")
    @classmethod
    def _unique_ids(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 255 for item in values) or len(set(values)) != len(values):
            raise ValueError("execution_ids must be unique bounded identifiers")
        return values


class ReadStreamRequest(_RequestModel):
    cursor: str = Field(min_length=3, max_length=4096)
    maximum_events: int = Field(default=100, ge=1, le=100)


class ProviderSetupRequest(_RequestModel):
    api_key: SecretStr | None = Field(default=None)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    enabled: bool = True
    purpose: str = Field(default="provider.configure", min_length=1, max_length=128)


class ConversationSelectionRequest(_RequestModel):
    provider: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=512)


class CreateConversationRequest(_RequestModel):
    message: str = Field(default="", max_length=16000)
    selection: ConversationSelectionRequest
    workspace_id: str | None = Field(default=None, min_length=1, max_length=255)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_FILES_PER_MESSAGE)


class ScheduleRecurrenceRequest(_RequestModel):
    kind: str = Field(pattern="^(once|hourly|daily|weekly)$")
    fire_at: datetime | None = None
    time_of_day: str | None = Field(default=None, max_length=5)
    weekday: int | None = Field(default=None, ge=0, le=6)


class CreateScheduledChatRequest(_RequestModel):
    message: str = Field(min_length=1, max_length=16000)
    selection: ConversationSelectionRequest
    timezone: str = Field(min_length=1, max_length=128)
    recurrence: ScheduleRecurrenceRequest
    project_id: str | None = Field(default=None, min_length=1, max_length=255)


class InspectWorkspaceFolderRequest(_RequestModel):
    path: str | None = Field(default=None, max_length=4096)


class AttachWorkspaceFolderRequest(_RequestModel):
    path: str = Field(min_length=1, max_length=4096)
    acknowledged_risk: bool = False


class CreateProjectRequest(_RequestModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class UpdateProjectRequest(_RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class SendConversationMessageRequest(_RequestModel):
    message: str = Field(default="", max_length=16000)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_FILES_PER_MESSAGE)
    selection: ConversationSelectionRequest | None = None


class SkillDependenciesRequest(_RequestModel):
    skills: list[str] = Field(default_factory=list, max_length=24)
    tools: list[str] = Field(default_factory=list, max_length=24)


class CreateSkillRequest(_RequestModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    version: str = Field(default="1.0.0", min_length=5, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=24)
    capabilities: list[str] = Field(default_factory=list, max_length=24)
    when_to_use: list[str] = Field(default_factory=list, max_length=24)
    when_not_to_use: list[str] = Field(default_factory=list, max_length=24)
    requires_tools: list[str] = Field(default_factory=list, max_length=24)
    dependencies: SkillDependenciesRequest = Field(default_factory=SkillDependenciesRequest)
    instructions: str = Field(min_length=1, max_length=32000)


class UpdateSkillRequest(_RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    version: str | None = Field(default=None, min_length=5, max_length=64)
    tags: list[str] | None = Field(default=None, max_length=24)
    capabilities: list[str] | None = Field(default=None, max_length=24)
    when_to_use: list[str] | None = Field(default=None, max_length=24)
    when_not_to_use: list[str] | None = Field(default=None, max_length=24)
    requires_tools: list[str] | None = Field(default=None, max_length=24)
    dependencies: SkillDependenciesRequest | None = None
    instructions: str | None = Field(default=None, min_length=1, max_length=32000)


class AgentSkillsRequest(_RequestModel):
    mode: str = Field(pattern="^(auto|pinned)$")
    skill_ids: list[str] = Field(default_factory=list, max_length=100)


class McpProposeServerRequest(_RequestModel):
    display_name: str = Field(min_length=1, max_length=255)
    catalog_id: str | None = Field(default=None, max_length=64)
    slug: str | None = Field(default=None, max_length=32)
    transport: str | None = Field(default=None, pattern="^(stdio|http)$")
    command: str | None = Field(default=None, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=32)
    url: str | None = Field(default=None, max_length=2048)
    secret_names: list[str] = Field(default_factory=list, max_length=16)


class McpApproveServerRequest(_RequestModel):
    secrets: dict[str, SecretStr] = Field(default_factory=dict)


class McpEnabledRequest(_RequestModel):
    enabled: bool


class PluginReferenceRequest(_RequestModel):
    reference: str = Field(min_length=1, max_length=1024)


class PluginEnabledRequest(_RequestModel):
    enabled: bool


class OmniRouteRuntimeRequest(_RequestModel):
    auto_start: bool


class OmniRouteRuntimeActionRequest(_RequestModel):
    action: str = Field(pattern="^(start|stop|restart)$")


class AgentRuntimeSettingsRequest(_RequestModel):
    max_iterations: int | None = Field(default=None, ge=1)


class VisionModelRequest(_RequestModel):
    provider: str | None = Field(default=None, max_length=32)
    model_id: str | None = Field(default=None, max_length=512)


class ApiServices:
    """Dependency container. All dependencies are application/security ports."""

    def __init__(
        self,
        *,
        security: SecurityService | None = None,
        execution_application: ExecutionApplication | None = None,
        execution_query: ResourceApplication | None = None,
        resource_services: dict[str, ResourceApplication] | None = None,
        provider_configuration: ProviderConfigurationApplication | None = None,
        provider_catalog: ProviderModelCatalogApplication | None = None,
        conversation_application: ConversationApplication | None = None,
        projects: object | None = None,
        skills: object | None = None,
        mcp: object | None = None,
        plugins: object | None = None,
        omniroute_runtime: object | None = None,
        agentic_runtime: object | None = None,
        events: InMemoryClientEventStream | None = None,
        workspace_root: str | Path | None = None,
        local_workspaces: object | None = None,
        uploads: object | None = None,
        vision_model_settings: object | None = None,
        scheduled_chats: object | None = None,
    ) -> None:
        self.security = security or InMemorySecurityService()
        self.execution_application = execution_application
        self.execution_query = execution_query
        self.resource_services = resource_services or {}
        self.provider_configuration = provider_configuration
        self.provider_catalog = provider_catalog
        self.conversation_application = conversation_application
        self.projects = projects
        self.skills = skills
        self.mcp = mcp
        self.plugins = plugins
        self.local_workspaces = local_workspaces
        self.omniroute_runtime = omniroute_runtime
        self.agentic_runtime = agentic_runtime
        self.events = events or InMemoryClientEventStream()
        self.workspace_root = Path(workspace_root) if workspace_root is not None else orin_paths().workspaces
        self.uploads = uploads
        self.vision_model_settings = vision_model_settings
        self.scheduled_chats = scheduled_chats


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(title="AgentOS API", version="1.0")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(422, "VALIDATION", "invalid_request", retryable=False)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(_: Request, __: AuthenticationError) -> JSONResponse:
        return _error(401, "AUTHENTICATION", "authentication_required", retryable=False)

    @app.exception_handler(AuthorizationError)
    async def authorization_error(_: Request, __: Exception) -> JSONResponse:
        return _error(403, "AUTHORIZATION", "access_denied", retryable=False)

    @app.exception_handler(PermissionError)
    async def permission_error(_: Request, __: Exception) -> JSONResponse:
        return _error(403, "AUTHORIZATION", "access_denied", retryable=False)

    @app.exception_handler(RateLimitError)
    async def rate_limit_error(_: Request, __: RateLimitError) -> JSONResponse:
        return _error(429, "RATE_LIMITED", "rate_limited", retryable=True, retry_after=60)

    @app.exception_handler(ApplicationConflictError)
    async def conflict_error(_: Request, __: ApplicationConflictError) -> JSONResponse:
        return _error(409, "CONFLICT", "execution_conflict", retryable=True)

    @app.exception_handler(ApplicationIndeterminateError)
    async def indeterminate_error(_: Request, __: ApplicationIndeterminateError) -> JSONResponse:
        return _error(409, "INDETERMINATE", "execution_indeterminate", retryable=True, retry_after=2)

    @app.exception_handler(ApplicationValidationError)
    async def application_validation_error(_: Request, __: ApplicationValidationError) -> JSONResponse:
        return _error(422, "VALIDATION", "invalid_request", retryable=False)

    @app.exception_handler(ProviderCredentialRejectedError)
    async def provider_credential_rejected(_: Request, __: ProviderCredentialRejectedError) -> JSONResponse:
        return _error(422, "VALIDATION", "provider_credentials_rejected", retryable=False)

    @app.exception_handler(UploadRejected)
    async def upload_rejected(_: Request, __: UploadRejected) -> JSONResponse:
        return _error(422, "VALIDATION", "upload_rejected", retryable=False)

    @app.exception_handler(ApplicationNotFoundError)
    async def not_found_error(_: Request, __: ApplicationNotFoundError) -> JSONResponse:
        return _error(404, "NOT_FOUND", "resource_not_found", retryable=False)

    @app.exception_handler(LookupError)
    async def lookup_error(_: Request, __: LookupError) -> JSONResponse:
        return _error(422, "VALIDATION", "selection_not_available", retryable=False)

    @app.exception_handler(ProviderCatalogUnavailable)
    async def provider_catalog_unavailable(_: Request, __: ProviderCatalogUnavailable) -> JSONResponse:
        return _error(503, "PROVIDER", "provider_catalog_unavailable", retryable=True)

    @app.exception_handler(CursorError)
    async def cursor_error(_: Request, __: CursorError) -> JSONResponse:
        # code "cursor_invalid" is one of the frontend's RESYNC_CODES
        # (frontend/src/api/errors.ts): the client already knows to drop its
        # cursor and open a fresh stream binding on this response.
        return _error(409, "CONFLICT", "cursor_invalid", retryable=True)

    @app.exception_handler(McpServerNotFound)
    async def mcp_server_not_found(_: Request, __: McpServerNotFound) -> JSONResponse:
        return _error(404, "NOT_FOUND", "resource_not_found", retryable=False)

    @app.exception_handler(McpConnectionFailed)
    async def mcp_connection_failed(_: Request, __: McpConnectionFailed) -> JSONResponse:
        return _error(502, "MCP", "mcp_connection_failed", retryable=True)

    @app.exception_handler(McpServiceError)
    async def mcp_service_error(_: Request, __: McpServiceError) -> JSONResponse:
        return _error(422, "VALIDATION", "invalid_request", retryable=False)

    @app.exception_handler(PluginServiceError)
    async def plugin_service_error(_: Request, __: PluginServiceError) -> JSONResponse:
        return _error(409, "CONFLICT", "plugin_operation_rejected", retryable=False)

    @app.exception_handler(Exception)
    async def internal_error(_: Request, __: Exception) -> JSONResponse:
        return _error(500, "INTERNAL", "internal_error", retryable=False)

    def principal_for(request: Request, *, mutable: bool = False) -> AuthenticatedPrincipal:
        if getattr(services.security, "requires_loopback_client", False):
            client_host = request.client.host if request.client is not None else None
            if not _is_loopback_client(client_host):
                raise AuthenticationError("loopback access is required")
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else None
        principal = services.security.authenticate(bearer_token=bearer, session_id=request.cookies.get("agentos_session"))
        if mutable:
            services.security.validate_csrf(principal, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        return principal

    def context(principal: AuthenticatedPrincipal, *, agent_id: str, execution_id: str, workspace_id: str | None, purpose: str) -> dict[str, str | None]:
        # correlation_id is derived deterministically from execution_id (not a fresh
        # value per request): the real persistence adapter scopes every read/write to
        # the exact context an execution record was created with, so create/control/
        # input/get on the same execution must agree on this value or later calls
        # silently miss the record.
        return {
            "user_id": principal.user_id,
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "execution_id": execution_id,
            "correlation_id": f"corr_{execution_id}",
            "purpose": purpose,
            "credential_ref": principal.credential_ref,
        }

    def require_port(port: object | None) -> object:
        if port is None:
            raise RuntimeError("application service is unavailable")
        return port

    @app.post("/v1/executions", status_code=202)
    async def create_execution(payload: CreateExecutionRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="execution.create", origin=request.headers.get("origin"))
        idempotency_key = _idempotency(request)
        # execution_id is derived from (credential, idempotency_key) rather than a
        # fresh random value: a random id assigned before idempotency is even
        # considered means retries of the same create request would each build a
        # *different* execution record instead of resolving to the one already
        # committed, defeating the idempotency contract this endpoint documents.
        execution_id = f"exe_{sha256(f'{principal.credential_ref}|{idempotency_key}'.encode()).hexdigest()}"
        services.security.authorize(principal, action="execution.create", resource_id=payload.agent_id, purpose=payload.purpose)
        command = {
            "operation_id": f"op_{uuid4().hex}",
            "context": context(principal, agent_id=payload.agent_id, execution_id=execution_id, workspace_id=payload.workspace_id, purpose=payload.purpose),
            "task_ref": payload.task_ref,
            "limits": payload.limits,
            "expected_agent_version": payload.expected_agent_version,
            "idempotency_key": idempotency_key,
            "requested_at": datetime.now(UTC),
        }
        result = require_port(services.execution_application).create(command)  # type: ignore[union-attr]
        return JSONResponse(_receipt(result, execution_id), status_code=202)

    @app.post("/v1/uploads", status_code=201)
    async def create_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=None, purpose="conversation.upload")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        staged = require_port(services.uploads).store(principal.user_id, file.filename or "arquivo", data)  # type: ignore[union-attr]
        return JSONResponse({
            "upload_id": staged.upload_id, "filename": staged.filename,
            "media_type": staged.media_type, "kind": staged.kind, "bytes": staged.bytes,
        }, status_code=201)

    @app.delete("/v1/uploads/{upload_id}", status_code=204)
    async def delete_upload(upload_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=None, purpose="conversation.upload")
        if not require_port(services.uploads).discard(principal.user_id, upload_id):  # type: ignore[union-attr]
            raise ApplicationNotFoundError(upload_id)
        return JSONResponse(status_code=204, content=None)

    def workspace_for(workspace_id: str, principal: AuthenticatedPrincipal) -> ConversationWorkspace:
        return resolve_workspace(workspace_id, managed_root=services.workspace_root, local_root=local_root_for(workspace_id, principal))

    def promote(workspace_id: str, principal: AuthenticatedPrincipal, upload_ids: list[str]) -> list[dict[str, object]]:
        if not upload_ids:
            return []
        return promote_uploads(require_port(services.uploads), workspace_for(workspace_id, principal), principal.user_id, upload_ids)

    def require_content(message: str, attachments: list[str]) -> None:
        """A turn needs text, an attachment, or both — never neither.

        The store itself raises a plain ``ValueError`` for this, which no
        exception handler maps to a client error, so the check happens here
        where ``ApplicationValidationError`` is already mapped to a 422.
        """
        if not message.strip() and not attachments:
            raise ApplicationValidationError("message must include text or at least one attachment")

    @app.post("/v1/conversations", status_code=201)
    async def create_conversation(payload: CreateConversationRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        provider = _provider_name(payload.selection.provider)
        services.security.check_rate_limit(principal, action="conversation.create", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="conversation.create", resource_id=provider, purpose="conversation.create")
        require_content(payload.message, payload.attachments)
        application = require_port(services.conversation_application)
        conversation_id = application.allocate_conversation_id()  # type: ignore[union-attr]
        attachments = promote(conversation_id, principal, payload.attachments)
        try:
            result = application.create(  # type: ignore[union-attr]
                ProviderCatalogContext(principal.user_id, "conversation.create"),
                message=payload.message, provider=provider, model_id=payload.selection.model_id,
                workspace_id=payload.workspace_id, idempotency_key=_idempotency(request),
                attachments=attachments, new_conversation_id=conversation_id,
            )
        except Exception:
            discard_promoted(workspace_for(conversation_id, principal), attachments)
            raise
        data = _jsonable(result)
        if not isinstance(data, dict):
            raise ValueError("conversation response is invalid")
        return JSONResponse({key: data.get(key) for key in ("conversation_id", "title", "turn_id", "message_id", "state")}, status_code=201)

    @app.get("/v1/conversations")
    async def list_conversations(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.list", resource_id=None, purpose="conversation.read")
        return JSONResponse(_jsonable(require_port(services.conversation_application).list(principal.user_id)))  # type: ignore[union-attr]

    @app.post("/v1/schedules", status_code=201)
    async def create_scheduled_chat(payload: CreateScheduledChatRequest, request: Request) -> JSONResponse:
        from agentos.scheduler.scheduled_chats import ScheduledChatInput
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="schedule.create", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="schedule.create", resource_id=payload.project_id, purpose="schedule.create")
        provider = _provider_name(payload.selection.provider)
        try:
            result = require_port(services.scheduled_chats).create(  # type: ignore[union-attr]
                principal.user_id,
                ScheduledChatInput(
                    message=payload.message, provider=provider, model_id=payload.selection.model_id,
                    timezone=payload.timezone, recurrence=payload.recurrence.kind,
                    fire_at=payload.recurrence.fire_at, time_of_day=payload.recurrence.time_of_day,
                    weekday=payload.recurrence.weekday, project_id=payload.project_id,
                ),
                idempotency_key=_idempotency(request),
            )
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(_jsonable(result), status_code=201)

    @app.get("/v1/schedules")
    async def list_scheduled_chats(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="schedule.list", resource_id=None, purpose="schedule.read")
        return JSONResponse(_jsonable(require_port(services.scheduled_chats).list(principal.user_id)))  # type: ignore[union-attr]

    @app.delete("/v1/schedules/{schedule_id}", status_code=204)
    async def cancel_scheduled_chat(schedule_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="schedule.cancel", resource_id=schedule_id, purpose="schedule.cancel")
        if not require_port(services.scheduled_chats).cancel(principal.user_id, schedule_id):  # type: ignore[union-attr]
            raise ApplicationNotFoundError(schedule_id)
        return JSONResponse(status_code=204, content=None)

    @app.post("/v1/projects", status_code=201)
    async def create_project(payload: CreateProjectRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="project.create", resource_id=None, purpose="project.create")
        store = require_port(services.projects)
        return JSONResponse(_project_public(store.create(user_id=principal.user_id, name=payload.name, description=payload.description)), status_code=201)

    @app.get("/v1/projects")
    async def list_projects(request: Request) -> JSONResponse:
        principal = principal_for(request)
        store = require_port(services.projects)
        return JSONResponse({"items": [_project_public(item) for item in store.list_active(principal.user_id)]})

    @app.get("/v1/projects/sidebar")
    async def project_sidebar(request: Request) -> JSONResponse:
        principal = principal_for(request)
        store = require_port(services.projects)
        return JSONResponse({"items": [_project_sidebar_public(item) for item in store.sidebar(principal.user_id)]})

    @app.get("/v1/projects/{project_id}")
    async def get_project(project_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        store = require_port(services.projects)
        project = store.get(project_id, principal.user_id)
        if project is None: raise ApplicationNotFoundError(project_id)
        return JSONResponse(_project_public(project))

    @app.patch("/v1/projects/{project_id}")
    async def update_project(project_id: str, payload: UpdateProjectRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        store = require_port(services.projects)
        project = store.update(project_id, principal.user_id, name=payload.name, description=payload.description)
        if project is None: raise ApplicationNotFoundError(project_id)
        return JSONResponse(_project_public(project))

    @app.post("/v1/projects/{project_id}/archive")
    async def archive_project(project_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        if not require_port(services.projects).archive(project_id, principal.user_id): raise ApplicationNotFoundError(project_id)
        return JSONResponse({"project_id": project_id, "archived": True})

    @app.post("/v1/projects/{project_id}/conversations", status_code=201)
    async def create_project_conversation(project_id: str, payload: CreateConversationRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        project = require_port(services.projects).get(project_id, principal.user_id)
        if project is None: raise ApplicationNotFoundError(project_id)
        provider = _provider_name(payload.selection.provider)
        require_content(payload.message, payload.attachments)
        application = require_port(services.conversation_application)
        conversation_id = application.allocate_conversation_id()  # type: ignore[union-attr]
        attachments = promote(project.workspace_id, principal, payload.attachments)
        try:
            result = application.create(  # type: ignore[union-attr]
                ProviderCatalogContext(principal.user_id, "project.conversation.create"),
                message=payload.message, provider=provider, model_id=payload.selection.model_id,
                workspace_id=project.workspace_id, idempotency_key=_idempotency(request), project_id=project_id,
                attachments=attachments, new_conversation_id=conversation_id,
            )
        except Exception:
            discard_promoted(workspace_for(project.workspace_id, principal), attachments)
            raise
        data = _jsonable(result)
        if not isinstance(data, dict): raise ValueError("conversation response is invalid")
        data["project_id"] = project_id
        return JSONResponse(data, status_code=201)

    @app.get("/v1/projects/{project_id}/memories")
    async def list_project_memories(project_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        store = require_port(services.projects)
        if store.get(project_id, principal.user_id) is None: raise ApplicationNotFoundError(project_id)
        return JSONResponse({"items": store.list_memories(project_id, principal.user_id)})

    @app.delete("/v1/projects/{project_id}/memories/{memory_id}", status_code=204)
    async def delete_project_memory(project_id: str, memory_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        store = require_port(services.projects)
        if not store.delete_memory(project_id, principal.user_id, memory_id): raise ApplicationNotFoundError(memory_id)
        return JSONResponse(status_code=204, content=None)

    @app.get("/v1/memories")
    async def list_managed_memories(request: Request, scope: str = "user", project_id: str | None = None, query: str = "", cursor: str | None = None, limit: int = 50) -> JSONResponse:
        if scope not in {"user", "project"} or (scope == "project" and not project_id):
            raise ApplicationValidationError("invalid memory scope")
        principal = principal_for(request)
        services.security.authorize(principal, action="memory.list", resource_id=project_id, purpose="memory.read")
        result = _require_port(services.projects).list_memories(project_id, principal.user_id, scope=scope, query=query, cursor=cursor, limit=min(max(limit, 1), 100))
        return JSONResponse(_jsonable(result))

    @app.delete("/v1/memories/{memory_id}", status_code=204)
    async def delete_managed_memory(memory_id: str, request: Request, scope: str = "user", project_id: str | None = None) -> JSONResponse:
        if scope not in {"user", "project"} or (scope == "project" and not project_id):
            raise ApplicationValidationError("invalid memory scope")
        principal = principal_for(request, mutable=True)
        if not _require_port(services.projects).delete_memory(project_id, principal.user_id, memory_id, scope=scope):
            raise ApplicationNotFoundError(memory_id)
        return JSONResponse(status_code=204, content=None)

    def effective_workspace_id(conversation: dict[str, object], principal: AuthenticatedPrincipal) -> tuple[str, str | None]:
        """The workspace id a chat actually resolves to, plus the project's name.

        A chat inside a project shares the project's workspace, so attaching a
        folder there attaches it for every chat of that project. The name comes
        back so the interface can say so before confirming.
        """
        project_id = conversation.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return str(conversation.get("conversation_id") or ""), None
        project = require_port(services.projects).get(project_id, principal.user_id)
        data = project if isinstance(project, dict) else {"workspace_id": getattr(project, "workspace_id", None), "name": getattr(project, "name", None)}
        workspace_id = data.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ApplicationNotFoundError(str(conversation.get("conversation_id") or ""))
        name = data.get("name")
        return workspace_id, name if isinstance(name, str) else None

    def local_root_for(workspace_id: str, principal: AuthenticatedPrincipal) -> str | None:
        store = services.local_workspaces
        if store is None:
            return None
        return store.root_for(workspace_id, principal.user_id)

    def workspace_state(conversation: dict[str, object], principal: AuthenticatedPrincipal) -> dict[str, object]:
        workspace_id, project_name = effective_workspace_id(conversation, principal)
        root = local_root_for(workspace_id, principal)
        return {
            "kind": "local" if root else "managed",
            "path": root,
            "folder_name": Path(root).name if root else None,
            "scope": "project" if project_name is not None else "chat",
            "project_name": project_name,
        }

    def conversation_record(conversation_id: str, principal: AuthenticatedPrincipal) -> dict[str, object]:
        conversation = _jsonable(require_port(services.conversation_application).get(conversation_id, principal.user_id))
        if not isinstance(conversation, dict):
            raise ApplicationNotFoundError(conversation_id)
        return conversation

    @app.get("/v1/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.read")
        conversation = conversation_record(conversation_id, principal)
        return JSONResponse({**conversation, "workspace": workspace_state(conversation, principal)})

    @app.post("/v1/conversations/{conversation_id}/workspace/inspect")
    async def inspect_conversation_workspace(conversation_id: str, payload: InspectWorkspaceFolderRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.inspect")
        conversation_record(conversation_id, principal)
        chosen = payload.path
        if chosen is None:
            client_host = request.client.host if request.client is not None else None
            if not _is_loopback_client(client_host):
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            result = await run_in_threadpool(choose_folder)
            if not result.available:
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            if result.cancelled or not result.path:
                return JSONResponse({"cancelled": True}, status_code=200)
            chosen = result.path
        try:
            inspection = inspect_folder(chosen, home=Path.home(), orin_data=orin_paths().data)
        except FolderRejected as error:
            raise ApplicationValidationError("invalid workspace folder") from error
        return JSONResponse(asdict(inspection))

    @app.put("/v1/conversations/{conversation_id}/workspace")
    async def attach_conversation_workspace(conversation_id: str, payload: AttachWorkspaceFolderRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.attach")
        conversation = conversation_record(conversation_id, principal)
        try:
            inspection = inspect_folder(payload.path, home=Path.home(), orin_data=orin_paths().data)
        except FolderRejected as error:
            raise ApplicationValidationError("invalid workspace folder") from error
        if not inspection.is_directory:
            raise ApplicationValidationError("workspace folder does not exist")
        if not inspection.writable:
            raise ApplicationValidationError("workspace folder is not writable")
        if inspection.risk != "none" and not payload.acknowledged_risk:
            return _error(409, "CONFLICT", "workspace_risk_acknowledgement_required", retryable=False)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        _require_port(services.local_workspaces).set_root(workspace_id, principal.user_id, inspection.path)
        return JSONResponse(workspace_state(conversation, principal))

    @app.delete("/v1/conversations/{conversation_id}/workspace")
    async def detach_conversation_workspace(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.detach")
        conversation = conversation_record(conversation_id, principal)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        _require_port(services.local_workspaces).clear_root(workspace_id, principal.user_id)
        return JSONResponse(workspace_state(conversation, principal))

    def conversation_workspace(conversation_id: str, principal: AuthenticatedPrincipal) -> ConversationWorkspace:
        conversation = conversation_record(conversation_id, principal)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        return resolve_workspace(workspace_id, managed_root=services.workspace_root, local_root=local_root_for(workspace_id, principal))

    @app.get("/v1/conversations/{conversation_id}/files/{path:path}")
    async def get_conversation_file(conversation_id: str, path: str, request: Request, disposition: str = "inline") -> FileResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.file.read")
        if disposition not in {"inline", "attachment"}: raise ApplicationValidationError("invalid disposition")
        try:
            target = conversation_workspace(conversation_id, principal).resolve(path)
        except WorkspaceError as error:
            raise ApplicationNotFoundError(conversation_id) from error
        if not target.is_file(): raise ApplicationNotFoundError(conversation_id)
        return FileResponse(target, media_type=media_type_for(target), filename=target.name, content_disposition_type=disposition, headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/v1/conversations/{conversation_id}/files/{path:path}/open")
    async def open_conversation_file(conversation_id: str, path: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.open_file", resource_id=conversation_id, purpose="conversation.file.open")
        try:
            target = conversation_workspace(conversation_id, principal).resolve(path)
            if not target.is_file(): raise WorkspaceError("not a file")
            open_in_default_application(target)
        except (OSError, WorkspaceError) as error:
            raise ApplicationNotFoundError(conversation_id) from error
        return JSONResponse({"opened": True})

    @app.post("/v1/conversations/{conversation_id}/messages", status_code=201)
    async def send_conversation_message(conversation_id: str, payload: SendConversationMessageRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.send")
        require_content(payload.message, payload.attachments)
        provider = ""
        model_id = ""
        if payload.selection is not None:
            provider = _provider_name(payload.selection.provider)
            model_id = payload.selection.model_id
            if services.provider_catalog is not None:
                available = _require_port(services.provider_catalog).list(
                    ProviderCatalogContext(principal.user_id, "conversation.send"), provider,
                )
                if not any(_catalog_model_id(item) == model_id for item in available):
                    raise ApplicationValidationError("model is not authorized for this conversation")
        attachments: list[dict[str, object]] = []
        workspace_id = conversation_id
        if payload.attachments:
            workspace_id, _ = effective_workspace_id(conversation_record(conversation_id, principal), principal)
            attachments = promote(workspace_id, principal, payload.attachments)
        try:
            result = require_port(services.conversation_application).send(
                principal.user_id, conversation_id, payload.message, _idempotency(request),
                attachments=attachments, provider=provider, model_id=model_id,
            )  # type: ignore[union-attr]
        except Exception:
            discard_promoted(workspace_for(workspace_id, principal), attachments)
            raise
        return JSONResponse(_jsonable(result), status_code=201)

    @app.post("/v1/conversations/{conversation_id}/cancel", status_code=202)
    async def cancel_conversation(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.cancel", resource_id=conversation_id, purpose="conversation.cancel")
        result = require_port(services.conversation_application).cancel(conversation_id, principal.user_id)  # type: ignore[union-attr]
        return JSONResponse(_jsonable(result), status_code=202)

    @app.get("/v1/conversations/{conversation_id}/overview")
    async def conversation_overview(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.overview")
        return JSONResponse(_jsonable(require_port(services.conversation_application).overview(conversation_id, principal.user_id)))  # type: ignore[union-attr]

    @app.get("/v1/conversations/{conversation_id}/events")
    async def conversation_events(conversation_id: str, request: Request, after: str = "0") -> StreamingResponse:
        principal = principal_for(request)
        if not after or len(after) > 512: raise ValueError("invalid cursor")
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.observe")
        app_service = require_port(services.conversation_application)

        async def body() -> Any:
            # A long-lived stream: the client attaches once per conversation and
            # receives activity as the worker produces it. Polling the durable
            # log here (rather than in the browser) keeps the client free of a
            # reconnect storm while the turn is running.
            cursor = after
            deadline = monotonic() + _SSE_MAX_SECONDS
            idle_heartbeat = 0.0
            while monotonic() < deadline:
                if await request.is_disconnected():
                    return
                try:
                    events, cursor = await run_in_threadpool(app_service.events, conversation_id, principal.user_id, cursor)  # type: ignore[union-attr]
                except CursorError:
                    yield f"event: resync\ndata: {json_dumps({'code': 'cursor_invalid'})}\n\n"
                    return
                for event in events:
                    yield f"id: {event['event_id']}\nevent: {event['event_type']}\ndata: {json_dumps(event)}\n\n"
                if events:
                    idle_heartbeat = 0.0
                    await asyncio.sleep(_SSE_ACTIVE_POLL_SECONDS)
                    continue
                idle_heartbeat += _SSE_IDLE_POLL_SECONDS
                if idle_heartbeat >= _SSE_HEARTBEAT_SECONDS:
                    idle_heartbeat = 0.0
                    yield f"event: heartbeat\ndata: {json_dumps({'cursor': cursor})}\n\n"
                await asyncio.sleep(_SSE_IDLE_POLL_SECONDS)
            yield f"event: heartbeat\ndata: {json_dumps({'cursor': cursor})}\n\n"

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.post("/v1/executions/{execution_id}/control", status_code=202)
    async def control_execution(execution_id: str, payload: ControlExecutionRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="execution.control", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="execution.control", resource_id=execution_id, purpose=payload.purpose)
        command = {"operation_id": f"op_{uuid4().hex}", "context": context(principal, agent_id="unknown", execution_id=execution_id, workspace_id=None, purpose=payload.purpose), "action": payload.action, "expected_state_version": payload.expected_state_version, "reason": payload.reason, "idempotency_key": _idempotency(request), "requested_at": datetime.now(UTC)}
        result = require_port(services.execution_application).control(command)  # type: ignore[union-attr]
        return JSONResponse(_receipt(result, execution_id), status_code=202)

    @app.post("/v1/executions/{execution_id}/input", status_code=202)
    async def provide_execution_input(execution_id: str, payload: ExecutionInputRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="execution.input", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="execution.input", resource_id=execution_id, purpose=payload.purpose)
        command = {"operation_id": f"op_{uuid4().hex}", "context": context(principal, agent_id="unknown", execution_id=execution_id, workspace_id=None, purpose=payload.purpose), "input_ref": payload.input_ref, "expected_state_version": payload.expected_state_version, "idempotency_key": _idempotency(request), "requested_at": datetime.now(UTC)}
        result = require_port(services.execution_application).provide_input(command)  # type: ignore[union-attr]
        return JSONResponse(_receipt(result, execution_id), status_code=202)

    @app.get("/v1/executions/{execution_id}")
    async def get_execution(execution_id: str, request: Request) -> JSONResponse:
        return _get_resource("executions", execution_id, request, services, principal_for)

    @app.get("/v1/executions")
    async def list_executions(request: Request) -> JSONResponse:
        return _list_resource("executions", request, services, principal_for)

    for resource in ("agents", "capabilities", "tools", "workspaces", "artifacts"):
        _register_resource_routes(app, resource, services, principal_for)

    @app.get("/v1/skills")
    async def list_skills(request: Request, query: str = "", source: str | None = None, limit: int = 20, cursor: str | None = None) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="skills.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.list", resource_id=None, purpose="skills.read")
        result = _require_port(services.skills).list({"user_id": principal.user_id, "query": query, "source": source, "limit": min(max(limit, 1), 100), "cursor": cursor, "purpose": "skills.read"})
        return JSONResponse(_jsonable(result))

    @app.get("/v1/skills/{skill_id}")
    async def get_skill(skill_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="skills.get", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.get", resource_id=skill_id, purpose="skills.read")
        result = _require_port(services.skills).get({"user_id": principal.user_id, "skill_id": skill_id, "purpose": "skills.read"})
        return JSONResponse(_jsonable(result))

    @app.post("/v1/skills", status_code=201)
    async def create_skill(payload: CreateSkillRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="skills.create", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.create", resource_id=None, purpose="skills.create")
        try:
            result = _require_port(services.skills).create({"user_id": principal.user_id, **payload.model_dump(), "idempotency_key": _idempotency(request), "purpose": "skills.create"})
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(_jsonable(result), status_code=201)

    @app.put("/v1/skills/{skill_id}")
    async def update_skill(skill_id: str, payload: UpdateSkillRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="skills.update", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.update", resource_id=skill_id, purpose="skills.update")
        fields = payload.model_dump(exclude_none=True)
        try:
            result = _require_port(services.skills).update({"user_id": principal.user_id, "skill_id": skill_id, **fields, "idempotency_key": _idempotency(request), "purpose": "skills.update"})
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(_jsonable(result))

    @app.delete("/v1/skills/{skill_id}/versions/{version}")
    async def remove_skill_version(skill_id: str, version: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="skills.version.remove", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.update", resource_id=f"{skill_id}@{version}", purpose="skills.version.remove")
        try:
            result = _require_port(services.skills).remove_version({
                "user_id": principal.user_id, "skill_id": skill_id, "version": version,
                "idempotency_key": _idempotency(request), "purpose": "skills.version.remove",
            })
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(_jsonable(result))

    @app.get("/v1/skills/{skill_id}/agents")
    async def agents_for_skill(skill_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="skills.agents", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="skills.get", resource_id=skill_id, purpose="skills.read")
        return JSONResponse(_jsonable(_require_port(services.skills).agents_for_skill({"user_id": principal.user_id, "skill_id": skill_id})))

    @app.get("/v1/agents/{agent_id}/skills")
    async def get_agent_skills(agent_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="agents.skills.get", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="agents.get", resource_id=agent_id, purpose="agents.read")
        return JSONResponse(_jsonable(_require_port(services.skills).agent_skills({"user_id": principal.user_id, "agent_id": agent_id})))

    @app.put("/v1/agents/{agent_id}/skills")
    async def set_agent_skills(agent_id: str, payload: AgentSkillsRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="agents.skills.set", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="agents.update", resource_id=agent_id, purpose="agents.update")
        result = _require_port(services.skills).set_agent_skills({"user_id": principal.user_id, "agent_id": agent_id, "mode": payload.mode, "skill_ids": payload.skill_ids, "idempotency_key": _idempotency(request)})
        return JSONResponse(_jsonable(result))

    @app.get("/v1/plugins")
    async def list_plugins(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="plugins.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.list", resource_id=None, purpose="plugins.read")
        return JSONResponse(_jsonable(_require_port(services.plugins).list(principal.user_id)))

    @app.get("/v1/plugins/library")
    async def get_plugin_library(request: Request, refresh: bool = False) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="plugins.library", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.library", resource_id=None, purpose="plugins.read")
        # Web search is blocking I/O just like plugin inspection, so keep it off the event loop.
        result = await run_in_threadpool(_require_port(services.plugins).discover_library, refresh=refresh)
        return JSONResponse(_jsonable(result))

    @app.post("/v1/plugins/inspect")
    async def inspect_plugin(payload: PluginReferenceRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.inspect", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.inspect", resource_id=None, purpose="plugins.inspect")
        # Fetching a plugin package (git clone) is blocking I/O that can take up to
        # PluginFetcher's own timeout; keeping it off the event loop matters here
        # because otherwise it stalls every other request and open SSE stream.
        result = await run_in_threadpool(_require_port(services.plugins).inspect, user_id=principal.user_id, reference=payload.reference)
        return JSONResponse(_jsonable(result))

    @app.post("/v1/plugins/{plugin_id}/approve")
    async def approve_plugin(plugin_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.approve", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.approve", resource_id=plugin_id, purpose="plugins.configure")
        # Activation parses and registers every contribution (and may propose
        # MCP servers), so it is blocking work just like inspection.
        result = await run_in_threadpool(
            _require_port(services.plugins).approve,
            user_id=principal.user_id,
            plugin_id=plugin_id,
        )
        return JSONResponse(_jsonable(result))

    @app.put("/v1/plugins/{plugin_id}/enabled")
    async def set_plugin_enabled(plugin_id: str, payload: PluginEnabledRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.enable", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.enable", resource_id=plugin_id, purpose="plugins.configure")
        return JSONResponse(_jsonable(_require_port(services.plugins).set_enabled(user_id=principal.user_id, plugin_id=plugin_id, enabled=payload.enabled)))

    @app.delete("/v1/plugins/{plugin_id}", status_code=204)
    async def remove_plugin(plugin_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.remove", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.remove", resource_id=plugin_id, purpose="plugins.configure")
        _require_port(services.plugins).remove(user_id=principal.user_id, plugin_id=plugin_id)
        return JSONResponse(None, status_code=204)

    @app.get("/v1/plugins/marketplaces")
    async def list_plugin_marketplaces(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="plugins.marketplaces.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.marketplaces.list", resource_id=None, purpose="plugins.read")
        return JSONResponse(_jsonable(_require_port(services.plugins).list_marketplaces(principal.user_id)))

    @app.post("/v1/plugins/marketplaces", status_code=201)
    async def add_plugin_marketplace(payload: PluginReferenceRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.marketplaces.add", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.marketplaces.add", resource_id=None, purpose="plugins.configure")
        return JSONResponse(_jsonable(_require_port(services.plugins).add_marketplace(user_id=principal.user_id, reference=payload.reference)), status_code=201)

    @app.get("/v1/mcp/catalog")
    async def get_mcp_catalog(request: Request, query: str = "") -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="mcp.catalog", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.catalog", resource_id=None, purpose="mcp.read")
        entries = [{
            "catalog_id": entry.catalog_id, "display_name": entry.display_name, "summary": entry.summary,
            "transport": entry.transport.value, "setup_instructions": entry.setup_instructions,
            "arguments": list(entry.arguments),
            "secrets": [{"name": item.name, "label": item.label, "how_to_obtain": item.how_to_obtain} for item in entry.secrets],
        } for entry in search_catalog(query)]
        return JSONResponse({"entries": entries})

    @app.get("/v1/mcp/servers")
    async def list_mcp_servers(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="mcp.servers.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.list", resource_id=None, purpose="mcp.read")
        return JSONResponse(_jsonable(_require_port(services.mcp).list(principal.user_id)))

    @app.post("/v1/mcp/servers", status_code=201)
    async def propose_mcp_server(payload: McpProposeServerRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.servers.propose", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.propose", resource_id=None, purpose="mcp.configure")
        command = {"user_id": principal.user_id, **payload.model_dump(exclude_none=True)}
        result = _require_port(services.mcp).propose(command)
        return JSONResponse(_jsonable(result), status_code=201)

    @app.get("/v1/mcp/servers/{server_id}")
    async def get_mcp_server(server_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="mcp.servers.get", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.get", resource_id=server_id, purpose="mcp.read")
        return JSONResponse(_jsonable(_require_port(services.mcp).get(principal.user_id, server_id)))

    @app.post("/v1/mcp/servers/{server_id}/approve")
    async def approve_mcp_server(server_id: str, payload: McpApproveServerRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.servers.approve", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.approve", resource_id=server_id, purpose="mcp.configure")
        secrets = {name: value.get_secret_value() for name, value in payload.secrets.items()}
        # Approving a server opens a real MCP connection (subprocess launch or HTTP
        # handshake), which is blocking I/O that can take seconds; run it off the
        # event loop so it doesn't stall every other request and open SSE stream.
        result = await run_in_threadpool(
            _require_port(services.mcp).approve, user_id=principal.user_id, server_id=server_id, secrets=secrets, connect=_mcp_connect,
        )
        return JSONResponse(_jsonable(result))

    @app.post("/v1/mcp/servers/{server_id}/test")
    async def test_mcp_server(server_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.servers.test", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.test", resource_id=server_id, purpose="mcp.configure")
        server = _require_port(services.mcp).get(principal.user_id, server_id)
        result = await run_in_threadpool(_require_port(services.mcp).test, principal.user_id, str(server["slug"]), _mcp_connect)
        return JSONResponse(_jsonable(result))

    @app.put("/v1/mcp/servers/{server_id}/enabled")
    async def set_mcp_server_enabled(server_id: str, payload: McpEnabledRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.servers.enable", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.enable", resource_id=server_id, purpose="mcp.configure")
        result = _require_port(services.mcp).set_enabled(principal.user_id, server_id, enabled=payload.enabled)
        return JSONResponse(_jsonable(result))

    @app.put("/v1/mcp/servers/{server_id}/tools/{tool_name}/enabled")
    async def set_mcp_tool_enabled(server_id: str, tool_name: str, payload: McpEnabledRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.tools.enable", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.tools.enable", resource_id=server_id, purpose="mcp.configure")
        result = _require_port(services.mcp).set_tool_enabled(principal.user_id, server_id, tool_name, enabled=payload.enabled)
        return JSONResponse(_jsonable(result))

    @app.delete("/v1/mcp/servers/{server_id}", status_code=204)
    async def remove_mcp_server(server_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="mcp.servers.remove", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="mcp.servers.remove", resource_id=server_id, purpose="mcp.configure")
        _require_port(services.mcp).remove(principal.user_id, server_id)
        return JSONResponse(status_code=204, content=None)

    @app.put("/v1/providers/{provider}")
    async def configure_provider(provider: str, payload: ProviderSetupRequest, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        if provider_name not in PROVIDERS_WITH_OPTIONAL_KEY and payload.api_key is None:
            raise ValueError("API key is required for this provider")
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.configure", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.configure", resource_id=provider_name, purpose=payload.purpose)
        command = {
            "operation_id": f"op_{uuid4().hex}", "provider": provider_name,
            "user_id": principal.user_id, "credential_ref": principal.credential_ref,
            "purpose": payload.purpose, "enabled": payload.enabled,
            "api_key": payload.api_key.get_secret_value() if payload.api_key is not None else "", "idempotency_key": _idempotency(request),
            "base_url": payload.base_url,
        }
        result = _require_port(services.provider_configuration).configure(command)
        return JSONResponse(_provider_public(result))

    @app.get("/v1/providers/{provider}")
    async def inspect_provider(provider: str, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="provider.inspect", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.inspect", resource_id=provider_name, purpose="provider.inspect")
        result = _require_port(services.provider_configuration).inspect({"provider": provider_name, "user_id": principal.user_id, "purpose": "provider.inspect"})
        return JSONResponse(_provider_public(result))

    @app.delete("/v1/providers/{provider}")
    async def revoke_provider(provider: str, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.revoke", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.revoke", resource_id=provider_name, purpose="provider.revoke")
        result = _require_port(services.provider_configuration).revoke({"operation_id": f"op_{uuid4().hex}", "provider": provider_name, "user_id": principal.user_id, "purpose": "provider.revoke", "idempotency_key": _idempotency(request)})
        return JSONResponse(_provider_public(result))

    @app.post("/v1/providers/omniroute/test")
    async def test_omniroute_connection(payload: ProviderSetupRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.test", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.test", resource_id="omniroute", purpose=payload.purpose)
        _idempotency(request)
        result = _require_port(services.provider_configuration).test_connection({
            "provider": "omniroute", "user_id": principal.user_id, "purpose": payload.purpose,
            "api_key": payload.api_key.get_secret_value() if payload.api_key is not None else "", "base_url": payload.base_url,
        })
        return JSONResponse(_connection_test_public(result))

    @app.post("/v1/providers/ollama/test")
    async def test_ollama_connection(payload: ProviderSetupRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.test", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.test", resource_id="ollama", purpose=payload.purpose)
        _idempotency(request)
        # Reaching a local daemon is blocking I/O; keeping it off the event
        # loop matters more here than for a hosted gateway, because an Ollama
        # that is simply not running takes the full connect timeout to fail.
        result = await run_in_threadpool(
            _require_port(services.provider_configuration).test_connection,
            {"provider": "ollama", "user_id": principal.user_id, "purpose": payload.purpose,
             "api_key": payload.api_key.get_secret_value() if payload.api_key is not None else "",
             "base_url": payload.base_url},
        )
        return JSONResponse(_connection_test_public(result))

    @app.post("/v1/providers/omniroute/install")
    async def install_omniroute(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.install", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.install", resource_id="omniroute", purpose="provider.install")
        _idempotency(request)
        result = await run_in_threadpool(
            _require_port(services.provider_configuration).install,
            {"provider": "omniroute", "user_id": principal.user_id, "purpose": "provider.install"},
        )
        data = _jsonable(result)
        if not isinstance(data, dict) or data.get("installed") is not True or data.get("next_step") != "omniroute":
            raise ValueError("OmniRoute installation response is invalid")
        return JSONResponse({"installed": True, "next_step": "omniroute"})

    @app.get("/v1/providers/omniroute/install")
    async def omniroute_installation_status(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="provider.install.status", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.install.status", resource_id="omniroute", purpose="provider.install.status")
        result = _require_port(services.provider_configuration).installation_status({
            "provider": "omniroute", "user_id": principal.user_id, "purpose": "provider.install.status",
        })
        data = _jsonable(result)
        if not isinstance(data, dict) or not isinstance(data.get("installed"), bool):
            raise ValueError("OmniRoute installation status response is invalid")
        return JSONResponse({"installed": data["installed"]})

    @app.get("/v1/providers/omniroute/runtime")
    async def omniroute_runtime_status(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="provider.inspect", resource_id="omniroute", purpose="provider.inspect")
        manager = _require_port(services.omniroute_runtime)
        return JSONResponse({**manager.status(), "auto_start": manager.auto_start(principal.user_id)})

    @app.put("/v1/providers/omniroute/runtime")
    async def update_omniroute_runtime(payload: OmniRouteRuntimeRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="provider.configure", resource_id="omniroute", purpose="provider.configure")
        manager = _require_port(services.omniroute_runtime)
        return JSONResponse({**manager.status(), **manager.set_auto_start(principal.user_id, payload.auto_start)})

    @app.post("/v1/providers/omniroute/runtime/actions")
    async def control_omniroute_runtime(payload: OmniRouteRuntimeActionRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="provider.configure", resource_id="omniroute", purpose="provider.configure")
        manager = _require_port(services.omniroute_runtime)
        outcome = getattr(manager, payload.action)()
        return JSONResponse({**outcome, "auto_start": manager.auto_start(principal.user_id)})

    @app.get("/v1/runtime/settings")
    async def get_agent_runtime_settings(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="runtime.inspect", resource_id="agentic", purpose="runtime.inspect")
        return JSONResponse(_require_port(services.agentic_runtime).get(principal.user_id))

    @app.put("/v1/runtime/settings")
    async def update_agent_runtime_settings(payload: AgentRuntimeSettingsRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="runtime.configure", resource_id="agentic", purpose="runtime.configure")
        _idempotency(request)
        return JSONResponse(_require_port(services.agentic_runtime).set_max_iterations(principal.user_id, payload.max_iterations))

    @app.get("/v1/installation/status")
    async def get_installation_status(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="installation.inspect", resource_id=None, purpose="installation.inspect")
        status = await run_in_threadpool(read_installation_status, runtime_profile())
        return JSONResponse(status)

    @app.delete("/v1/installation/versions/{version}")
    async def delete_installation_version(version: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="installation.configure", resource_id=version, purpose="installation.version.remove")
        _idempotency(request)
        result = await run_in_threadpool(remove_installed_version, version, runtime_profile())
        return JSONResponse(result)

    @app.get("/v1/settings/vision-model")
    async def get_vision_model_setting(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="settings.vision_model.read", resource_id=None, purpose="settings.vision_model.read")
        selection = _require_port(services.vision_model_settings).get(principal.user_id)
        if selection is None:
            return JSONResponse({"provider": None, "model_id": None, "mode": "automatic"})
        return JSONResponse({"provider": selection.provider, "model_id": selection.model_id, "mode": "manual"})

    @app.put("/v1/settings/vision-model")
    async def set_vision_model_setting(payload: VisionModelRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="settings.vision_model.configure", resource_id=None, purpose="settings.vision_model.configure")
        if (payload.provider is None) != (payload.model_id is None):
            raise ApplicationValidationError("provider and model_id must both be present or both be absent")
        store = _require_port(services.vision_model_settings)
        if payload.provider is None:
            store.clear(principal.user_id)
            return JSONResponse({"provider": None, "model_id": None, "mode": "automatic"})
        store.set(principal.user_id, payload.provider, payload.model_id)
        return JSONResponse({"provider": payload.provider, "model_id": payload.model_id, "mode": "manual"})

    @app.post("/v1/providers/{provider}/models:refresh")
    async def refresh_provider_models(provider: str, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.catalog.refresh", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.catalog.refresh", resource_id=provider_name, purpose="provider.catalog.refresh")
        _idempotency(request)
        result = _require_port(services.provider_catalog).refresh(ProviderCatalogContext(principal.user_id, "provider.catalog.refresh"), provider_name)
        return JSONResponse(_catalog_refresh_public(result))

    @app.get("/v1/providers/{provider}/models")
    async def list_provider_models(provider: str, request: Request, favorites_only: bool = False) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="provider.catalog.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.catalog.list", resource_id=provider_name, purpose="provider.catalog.list")
        items = _require_port(services.provider_catalog).list(ProviderCatalogContext(principal.user_id, "provider.catalog.list"), provider_name, favorites_only)
        public_items = [_catalog_model_public(item) for item in items]
        refreshed_at = public_items[0]["refreshed_at"] if public_items else None
        return JSONResponse({"items": public_items, "refreshed_at": refreshed_at})

    @app.put("/v1/providers/{provider}/favorites/{model_id:path}")
    async def favorite_provider_model(provider: str, model_id: str, request: Request) -> JSONResponse:
        return _set_provider_favorite(provider, model_id, True, request)

    @app.delete("/v1/providers/{provider}/favorites/{model_id:path}")
    async def unfavorite_provider_model(provider: str, model_id: str, request: Request) -> JSONResponse:
        return _set_provider_favorite(provider, model_id, False, request)

    def _set_provider_favorite(provider: str, model_id: str, favorite: bool, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.catalog.favorite", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.catalog.favorite", resource_id=provider_name, purpose="provider.catalog.favorite")
        _idempotency(request)
        result = _require_port(services.provider_catalog).favorite(
            ProviderCatalogContext(principal.user_id, "provider.catalog.favorite"), provider_name, model_id, favorite,
        )
        return JSONResponse(_catalog_model_public(result))

    @app.post("/v1/events/streams", status_code=201)
    async def open_event_stream(payload: OpenStreamRequest, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="events.open", origin=request.headers.get("origin"))
        for execution_id in payload.execution_ids:
            services.security.authorize(principal, action="execution.observe", resource_id=execution_id, purpose=payload.purpose)
        binding, cursor = services.events.open(principal, payload.execution_ids, services.security.revocation_epoch(principal))
        return JSONResponse({"stream_id": binding.stream_id, "cursor": cursor, "stream_binding_digest": binding.digest, "revocation_epoch": binding.epoch}, status_code=201)

    @app.post("/v1/events/streams/{stream_id}/read")
    async def read_event_stream(stream_id: str, payload: ReadStreamRequest, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="events.read", origin=request.headers.get("origin"))
        events, cursor = services.events.read(principal, stream_id, payload.cursor, services.security.revocation_epoch(principal), payload.maximum_events)
        return JSONResponse({"events": [_event_json(event) for event in events], "cursor": cursor})

    @app.get("/v1/events/streams/{stream_id}")
    async def sse(stream_id: str, cursor: str, request: Request) -> StreamingResponse:
        principal = principal_for(request)
        events, next_cursor = services.events.read(principal, stream_id, cursor, services.security.revocation_epoch(principal))

        async def body() -> Any:
            for event in events:
                if not services.events.delivery_permitted(principal, stream_id, services.security.revocation_epoch(principal)):
                    return
                yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {json_dumps(_event_json(event))}\n\n"
            yield f"event: heartbeat\ndata: {json_dumps({'cursor': next_cursor})}\n\n"

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def _register_resource_routes(app: FastAPI, resource: str, services: ApiServices, principal_for: Any) -> None:
    @app.get(f"/v1/{resource}/{{resource_id}}", name=f"get_{resource}")
    async def get_resource(resource_id: str, request: Request) -> JSONResponse:
        return _get_resource(resource, resource_id, request, services, principal_for)

    @app.get(f"/v1/{resource}", name=f"list_{resource}")
    async def list_resource(request: Request) -> JSONResponse:
        return _list_resource(resource, request, services, principal_for)


def _get_resource(resource: str, resource_id: str, request: Request, services: ApiServices, principal_for: Any) -> JSONResponse:
    principal = principal_for(request)
    services.security.check_rate_limit(principal, action=f"{resource}.get", origin=request.headers.get("origin"))
    services.security.authorize(principal, action=f"{resource}.get", resource_id=resource_id, purpose=f"{resource}.read")
    port = services.execution_query if resource == "executions" else services.resource_services.get(resource)
    result = _require_port(port).get({"resource_id": resource_id, "user_id": principal.user_id, "purpose": f"{resource}.read"})
    return JSONResponse(_jsonable(result))


def _list_resource(resource: str, request: Request, services: ApiServices, principal_for: Any) -> JSONResponse:
    principal = principal_for(request)
    services.security.check_rate_limit(principal, action=f"{resource}.list", origin=request.headers.get("origin"))
    services.security.authorize(principal, action=f"{resource}.list", resource_id=None, purpose=f"{resource}.read")
    port = services.execution_query if resource == "executions" else services.resource_services.get(resource)
    result = _require_port(port).list({"user_id": principal.user_id, "purpose": f"{resource}.read", "cursor": request.query_params.get("cursor")})
    return JSONResponse(_jsonable(result))


def _require_port(port: object | None) -> Any:
    if port is None:
        raise RuntimeError("application service is unavailable")
    return port


def _idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key")
    if not value or len(value) > 255:
        raise AuthorizationError("idempotency key is required")
    return value


def _is_loopback_client(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _provider_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"openai", "anthropic", "openrouter", "omniroute", "ollama"}:
        raise ValueError("unsupported provider")
    return normalized


def _catalog_model_id(item: object) -> object:
    if isinstance(item, Mapping):
        return item.get("model_id")
    return getattr(item, "model_id", None)


def _provider_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict):
        raise ValueError("provider configuration response is invalid")
    public: dict[str, object] = {}
    for key, item in data.items():
        key_text = str(key)
        if any(term in key_text.lower() for term in ("api_key", "secret", "token", "password", "credential")):
            continue
        public[key_text] = item.isoformat() if isinstance(item, datetime) else item
    return public


def _catalog_refresh_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict) or not isinstance(data.get("count"), int):
        raise ValueError("provider catalog refresh response is invalid")
    refreshed_at = data.get("refreshed_at")
    return {"count": data["count"], "refreshed_at": refreshed_at.isoformat() if isinstance(refreshed_at, datetime) else None}


def _connection_test_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict) or data.get("connected") is not True:
        raise ValueError("provider connection response is invalid")
    count = data.get("models_available")
    return {
        "connected": True,
        "models_available": count if isinstance(count, int) and count >= 0 else None,
        "base_url": data.get("base_url") if isinstance(data.get("base_url"), str) else None,
    }


def _catalog_model_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict):
        raise ValueError("provider catalog model response is invalid")
    pricing = data.get("pricing")
    public_pricing = None
    if isinstance(pricing, dict):
        public_pricing = {"input_per_million": pricing.get("input_per_million"), "output_per_million": pricing.get("output_per_million")}
    refreshed_at = data.get("refreshed_at")
    return {
        "provider": data.get("provider"), "model_id": data.get("model_id"), "display_name": data.get("display_name"),
        "context_window": data.get("context_window"), "capabilities": data.get("capabilities", []),
        "input_modalities": data.get("input_modalities", []), "output_modalities": data.get("output_modalities", []),
        "pricing": public_pricing, "is_favorite": bool(data.get("is_favorite")),
        "refreshed_at": refreshed_at.isoformat() if isinstance(refreshed_at, datetime) else None,
        "route_kind": data.get("route_kind") if data.get("route_kind") in {"model", "auto"} else "model",
    }


def _receipt(result: object, fallback_execution_id: str) -> dict[str, object]:
    data = _jsonable(result)
    if not isinstance(data, dict):
        data = {"outcome": "accepted"}
    data.setdefault("execution_id", fallback_execution_id)
    data.setdefault("state_version", 1)
    return data


def _project_public(project: object) -> dict[str, object]:
    data = _jsonable(project)
    if not isinstance(data, dict):
        raise ValueError("project response is invalid")
    return {
        "project_id": data.get("project_id"), "name": data.get("name"), "description": data.get("description"),
        "workspace_id": data.get("workspace_id"),
        "created_at": data["created_at"].isoformat() if isinstance(data.get("created_at"), datetime) else None,
        "updated_at": data["updated_at"].isoformat() if isinstance(data.get("updated_at"), datetime) else None,
        "archived_at": data["archived_at"].isoformat() if isinstance(data.get("archived_at"), datetime) else None,
    }


def _project_sidebar_public(item: object) -> dict[str, object]:
    data = _jsonable(item)
    if not isinstance(data, dict):
        raise ValueError("project sidebar response is invalid")
    return {"project_id": data.get("project_id"), "name": data.get("name"), "description": data.get("description"), "chats": [
        {"conversation_id": chat.get("conversation_id"), "title": chat.get("title"), "state": chat.get("state"), "updated_at": chat["updated_at"].isoformat() if isinstance(chat.get("updated_at"), datetime) else None}
        for chat in data.get("chats", []) if isinstance(chat, dict)
    ]}


def _event_json(event: object) -> dict[str, object]:
    return {"event_id": event.event_id, "event_type": event.event_type, "execution_id": event.execution_id, "sequence": event.sequence, "occurred_at": event.occurred_at.isoformat(), "payload": event.payload}


def _jsonable(value: object) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def json_dumps(value: object) -> str:
    import json
    return json.dumps(value, separators=(",", ":"), default=str)


def _error(status: int, category: str, code: str, *, retryable: bool, retry_after: int | None = None) -> JSONResponse:
    correlation_id = f"corr_{uuid4().hex}"
    content = {"error": {"code": code, "category": category, "message_key": code, "correlation_id": correlation_id, "retryable": retryable, "retry_after": retry_after}}
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(content, status_code=status, headers=headers)

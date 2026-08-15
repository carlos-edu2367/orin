"""Fail-closed local composition for the AgentOS HTTP boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import os
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from sqlalchemy.engine import Engine

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticationError, LoopbackSecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.scheduler.scheduled_chats import ScheduledChatService
from agentos.projects import PostgresProjectStore
from agentos.configuration import AgentOSSettings
from agentos.installation import orin_paths
from agentos.persistence.postgres.event_stream import PostgresClientEventStream
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.tool_activity import PostgresToolActivitySink
from agentos.persistence.postgres.tool_invocations import PostgresToolInvocationStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.security import PostgresSecurityService
from agentos.provider_catalog.http import OpenRouterModelCatalogClient
from agentos.provider_catalog.ollama import OllamaCatalogClient
from agentos.provider_catalog.omniroute import OmniRouteCatalogClient
from agentos.provider_catalog.service import ProviderModelCatalogService
from agentos.reading.store import PostgresVisionModelSettingsStore
from agentos.resources.service import ResourceManagerService
from agentos.filesystem.local import LocalFilesystemAdapter, LocalWorkspaceRootResolver
from agentos.filesystem.service import FilesystemService
from agentos.terminal.local import LocalTerminalAdapter
from agentos.terminal.service import TerminalService
from agentos.browser.playwright_adapter import PlaywrightBrowserAdapter
from agentos.browser.service import BrowserService
from agentos.tool_runtime.adapters import BrowserNavigateAtomicTool, FilesystemAtomicTool, TerminalCommandAtomicTool
from agentos.tool_runtime.production import ProductionToolRuntime
from agentos.persistence.postgres.skills import PostgresSkillLibraryService
from agentos.mcp.service import McpServerService
from agentos.omniroute import OmniRouteProcessManager, OmniRouteRuntimeSettingsStore
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.filesystem.models import FilesystemOperationContext, WorkspacePath
from agentos.uploads.staging import UploadStaging
from agentos.persistence.sqlite import create_local_engine


class ProductionSettings(AgentOSSettings):
    """Flat, explicit deployment configuration; secrets remain redacted."""

    # This is the production specialization of AgentOSSettings. Flat aliases
    # are required for deployment tooling while the base model preserves the
    # typed local-secret and provider-catalog validation from RFC 604.
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    DATABASE_URL: str
    AGENTOS_ENV: str = "development"
    LOCALHOST_TRUST_ENABLED: bool = False
    WEB_DIST_DIR: str | None = None
    AGENTOS_ACTIVITY_CURSOR_SECRET: SecretStr | None = None
    OPENROUTER_ENABLED: bool = False
    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_MODEL: str | None = None
    ANTHROPIC_ENABLED: bool = False
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_MODEL: str | None = None
    OPENAI_ENABLED: bool = False
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str | None = None

    @model_validator(mode="after")
    def _validate_enabled_providers(self) -> "ProductionSettings":
        if self.LOCALHOST_TRUST_ENABLED and self.AGENTOS_ENV.strip().lower() not in {"development", "local"}:
            raise ValueError("LOCALHOST_TRUST_ENABLED is allowed only when AGENTOS_ENV is development or local")
        if self.LOCALHOST_TRUST_ENABLED and not self.WEB_DIST_DIR:
            raise ValueError("LOCALHOST_TRUST_ENABLED requires WEB_DIST_DIR with the built frontend")
        for name in ("OPENROUTER", "ANTHROPIC", "OPENAI"):
            if getattr(self, f"{name}_ENABLED") and getattr(self, f"{name}_API_KEY") is None:
                raise ValueError(f"enabled {name} provider requires API key")
        return self


@dataclass(frozen=True, slots=True)
class DependencyProbe:
    postgres: Callable[[], bool]
    redis: Callable[[], bool] | None = None

    @classmethod
    def from_settings(cls, settings: ProductionSettings) -> "DependencyProbe":
        def database_ready() -> bool:
            try:
                from sqlalchemy import text
                engine = create_local_engine(settings.DATABASE_URL)
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                engine.dispose()
                return True
            except Exception:
                return False

        return cls(database_ready)

    def ready(self) -> bool:
        try:
            return bool(self.postgres())
        except Exception:
            return False


class _UnavailableProductionSecurity:
    """Blocks all protected traffic until a real security adapter is composed."""

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> object:
        raise AuthenticationError("production security adapter is unavailable")

    def validate_csrf(self, principal: object, token: str | None, origin: str | None) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def authorize(self, principal: object, *, action: str, resource_id: str | None, purpose: str) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def check_rate_limit(self, principal: object, *, action: str, origin: str | None) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def revocation_epoch(self, principal: object) -> int:
        raise AuthenticationError("production security adapter is unavailable")


class _UnavailableApplicationPort:
    def __getattr__(self, _: str) -> Callable[..., object]:
        def unavailable(*args: object, **kwargs: object) -> object:
            raise RuntimeError("production application adapter is unavailable")
        return unavailable


class _LocalTerminalCwd:
    """Translate terminal context/path values into a canonical local cwd."""

    def __init__(self, resolver: LocalWorkspaceRootResolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _context(context):
        workspace_id = getattr(context, "workspace_id", None) or "workspace-default"
        return FilesystemOperationContext(
            str(getattr(context, "user_id", "system")), workspace_id,
            str(getattr(context, "agent_id", "terminal")), str(getattr(context, "execution_id", "terminal")),
            str(getattr(context, "correlation_id", "terminal")), "terminal.cwd", str(getattr(context, "actor", "system")),
        )

    def resolve(self, context, path: WorkspacePath) -> str | None:
        fs_context = self._context(context)
        root = self.resolver.root_for(fs_context)
        location = self.resolver.location_for(root.workspace_id)
        candidate = location.joinpath(*path.segments)
        try:
            candidate.resolve().relative_to(location)
        except (OSError, RuntimeError, ValueError):
            return None
        return str(candidate)

    def revalidate(self, context, path: WorkspacePath) -> bool:
        fs_context = self._context(context)
        root = self.resolver.root_for(fs_context)
        return self.resolver.revalidate(root, fs_context) and self.resolve(context, path) is not None


def compose_agentic_tool_runtime(*, workspace_root: str | Path | None = None, engine: Engine | None = None) -> tuple[ResourceManagerService, ProductionToolRuntime]:
    """Compose real local filesystem, terminal and browser tool bindings."""
    resource_manager = ResourceManagerService()
    resolver = LocalWorkspaceRootResolver(Path(workspace_root) if workspace_root is not None else orin_paths().workspaces)
    filesystem = FilesystemService(
        LocalFilesystemAdapter(resolver), resolver,
        handle_validator=resource_manager.validate_filesystem_handle,
    )
    resource_manager.bind_filesystem_port(filesystem)
    terminal = TerminalService(
        resource_manager=resource_manager,
        adapter=LocalTerminalAdapter(
            _LocalTerminalCwd(resolver),
            allowed_executables=("echo", "printf", "pwd", "dir", "type", "rg"),
        ),
    )
    browser = BrowserService(
        resource_manager=resource_manager,
        adapter=PlaywrightBrowserAdapter(),
    )
    bindings = {
        "filesystem.read": FilesystemAtomicTool(filesystem, "read"),
        "filesystem.write": FilesystemAtomicTool(filesystem, "write"),
        "terminal.execute": TerminalCommandAtomicTool(terminal),
        "browser.navigate": BrowserNavigateAtomicTool(browser),
    }
    from agentos.tool_runtime.runtime import ToolRuntimeService
    runtime = None
    if engine is not None:
        runtime = ToolRuntimeService(registry=__import__("agentos.tool_runtime.registry", fromlist=["InMemoryToolRegistry"]).InMemoryToolRegistry(), resource_manager=resource_manager, sink=PostgresToolActivitySink(engine), state_store=PostgresToolInvocationStore(engine))
    return resource_manager, ProductionToolRuntime(resource_manager=resource_manager, runtime=runtime, tool_bindings=bindings)


def activity_cursor_fallback(engine: Engine) -> str:
    """A stable cursor key for installations that configured no explicit secret.

    The worker derives the same value from the same database URL, which is what
    lets a cursor minted by one process be read by the other.
    """
    from hashlib import sha256
    return sha256(f"agentos-activity-cursor|{engine.url.render_as_string(hide_password=False)}".encode()).hexdigest()


def unavailable_production_services() -> ApiServices:
    """A fail-closed composition, never an in-memory runtime fallback."""
    unavailable = _UnavailableApplicationPort()
    return ApiServices(
        security=_UnavailableProductionSecurity(),
        execution_application=unavailable,
        execution_query=unavailable,
        resource_services={name: unavailable for name in ("agents", "capabilities", "tools", "workspaces", "artifacts", "memories")},
        skills=unavailable,
        omniroute_runtime=unavailable,
        events=unavailable,  # type: ignore[arg-type]
    )


def compose_production_services(engine: Engine, *, localhost_trust_enabled: bool = False, activity_cursor_secret: str | None = None, multi_agent_coordinator=None) -> ApiServices:
    """Durable composition for the frontend-facing surface (Fase 0/D).

    Executions and their public event stream are backed by the real
    PostgreSQL outbox/persistence; `resource_services` for agents,
    capabilities, tools, workspaces, artifacts and memories stay unavailable
    because Fase 0 does not establish a public DTO/permission for them (see
    docs/frontend/BACKEND_CAPABILITY_MATRIX.md). `provider_configuration` is
    backed by `PostgresProviderConfigurationAdapter` over a dedicated
    `provider_configurations` table (frontend Fase D; see docs/frontend/
    IMPLEMENTATION_PLAN.md, Fase D "Decisões locais").
    """
    unavailable = _UnavailableApplicationPort()
    # Optional Postgres integration tests use a process-scoped ephemeral key;
    # deployed profiles must provide a stable key through the environment.
    provider_cipher = ProviderSecretCipher.from_environment(required=not bool(os.getenv("AGENTOS_TEST_POSTGRES_DSN")))
    # A random per-process cursor key would invalidate every client cursor on
    # restart and force a full resync, so fall back to a value derived from the
    # installation's own database URL rather than to fresh entropy.
    cursor_secret = activity_cursor_secret or activity_cursor_fallback(engine)
    provider_repository = PostgresProviderCatalogRepository(engine, cipher=provider_cipher)
    omniroute_runtime = OmniRouteProcessManager(OmniRouteRuntimeSettingsStore())
    staging = UploadStaging(orin_paths().data / "uploads" / "staging")
    try:
        staging.purge()
    except Exception:
        # Staging cleanup is best-effort: a failure here must never block the
        # API from starting, it just leaves stale uploads for the next purge.
        pass
    return ApiServices(
        security=LoopbackSecurityService() if localhost_trust_enabled else PostgresSecurityService(engine),
        execution_application=ExecutionApplicationAdapter(engine),
        execution_query=ExecutionQueryAdapter(engine),
        resource_services={**{name: unavailable for name in ("agents", "capabilities", "tools", "workspaces", "artifacts", "memories")}, "multi_agent": multi_agent_coordinator or unavailable},
        skills=PostgresSkillLibraryService(engine),
        mcp=McpServerService(engine),
        provider_configuration=PostgresProviderConfigurationAdapter(engine, cipher=provider_cipher),
        provider_catalog=ProviderModelCatalogService(provider_repository, {"openrouter": OpenRouterModelCatalogClient(), "omniroute": OmniRouteCatalogClient(), "ollama": OllamaCatalogClient()}),
        conversation_application=ChatApplication(PostgresChatStore(engine, PostgresAgenticActivityStore(engine, cursor_secret)), ExecutionApplicationAdapter(engine)),
        projects=PostgresProjectStore(engine),
        omniroute_runtime=omniroute_runtime,
        agentic_runtime=AgentRuntimeSettingsStore(),
        events=PostgresClientEventStream(engine),  # type: ignore[arg-type]
        local_workspaces=PostgresLocalWorkspaceStore(engine),
        uploads=staging,
        vision_model_settings=PostgresVisionModelSettingsStore(engine),
        scheduled_chats=ScheduledChatService(engine),
    )


def create_production_app(settings: ProductionSettings, *, services: ApiServices | None = None, probe: DependencyProbe | None = None) -> FastAPI:
    """Create production HTTP surface with real ports supplied by composition.

    Supplying no services creates an explicitly unavailable application; it does
    not silently select any test adapter. The outer bootstrap must inject only
    durable/authorized implementations of the public application ports.
    """
    if services is not None and any(
        component.__class__.__name__.startswith("InMemory")
        or component.__class__.__module__.endswith(".in_memory")
        for component in (services.security, services.events)
    ):
        raise ValueError("production composition cannot use in-memory security or event adapters")
    app = create_app(services or unavailable_production_services())
    dependency_probe = probe or DependencyProbe.from_settings(settings)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        if dependency_probe.ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "unavailable"}, status_code=503)

    if settings.LOCALHOST_TRUST_ENABLED:
        _mount_local_frontend(app, settings.WEB_DIST_DIR)

    def _omniroute_manager() -> object | None:
        manager = getattr(services, "omniroute_runtime", None) if services is not None else None
        if manager is None or isinstance(manager, _UnavailableApplicationPort):
            return None
        return manager

    @app.on_event("startup")
    async def _start_configured_omniroute() -> None:
        manager = _omniroute_manager()
        if manager is None:
            return
        try:
            # OmniRoute is optional and may take tens of seconds to expose its
            # model endpoint on a cold desktop. Starting it synchronously here
            # blocks Uvicorn before /healthz exists, so launch it without making
            # AgentOS readiness depend on that gateway.
            start_in_background = getattr(manager, "start_if_any_enabled_in_background", None)
            if callable(start_in_background):
                start_in_background()
            else:
                await __import__("fastapi.concurrency", fromlist=["run_in_threadpool"]).run_in_threadpool(manager.start_if_any_enabled)
        except Exception:
            # OmniRoute is optional: a failed local gateway must never make
            # AgentOS unavailable.
            return

    @app.on_event("shutdown")
    async def _stop_owned_omniroute() -> None:
        # Only the gateway this process spawned is stopped; `stop()` is a no-op
        # for an external instance. Without this, exiting AgentOS left the child
        # it started running with nothing supervising it.
        manager = _omniroute_manager()
        if manager is None:
            return
        try:
            await __import__("fastapi.concurrency", fromlist=["run_in_threadpool"]).run_in_threadpool(manager.stop)
        except Exception:
            return

    return app


def _mount_local_frontend(app: FastAPI, directory: str | None) -> None:
    """Serve the built SPA only for the explicitly local loopback profile."""
    root = Path(directory or "").resolve()
    index = root / "index.html"
    assets = root / "assets"
    if not index.is_file() or not assets.is_dir():
        raise ValueError("WEB_DIST_DIR must contain a Vite build with index.html and assets/")
    def document() -> str:
        # Read per request rather than once at startup. Rebuilding the client
        # changes the hashed asset names, and a cached document would keep
        # pointing at files that no longer exist until the API is restarted.
        # The cost is one small local file read on a personal installation.
        return index.read_text(encoding="utf-8").replace(
            # The local profile is authenticated by the loopback TCP peer, not a
            # browser cookie. Stamping the HTML at serving time keeps an
            # otherwise-valid static build from locking its own composer behind
            # a CSRF token it can never obtain.
            'name="agentos-auth-mode" content=""',
            'name="agentos-auth-mode" content="loopback"',
            1,
        )

    document()  # Fail fast at boot if the build is unreadable.
    app.mount("/assets", StaticFiles(directory=assets), name="local-web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def local_spa(path: str) -> Response:
        # API routes are registered before this fallback.  Unknown API paths
        # must still return API-style 404 responses rather than HTML.
        if path == "v1" or path.startswith("v1/"):
            return JSONResponse({
                "error": {
                    "code": "resource_not_found", "category": "NOT_FOUND",
                    "message_key": "resource_not_found", "correlation_id": f"corr_{uuid4().hex}",
                    "retryable": False, "retry_after": None,
                },
            }, status_code=404)
        return HTMLResponse(document(), headers={"Cache-Control": "no-store"})

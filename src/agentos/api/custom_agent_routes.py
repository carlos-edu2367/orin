"""``/v1/custom-agents``: the subagents a person makes in the interface (Phase 3 of specialised agents).

They are stored per Orin user, so they work in the chat, without a folder and on a shared server. The server checks
everything the form can get wrong: the name, the tool names, that the provider is enabled for this person and that
each model is in that provider's catalog.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.agent_definitions import MAX_AGENT_MODELS, RESERVED_NAMES, provider_and_models
from agentos.persistence.postgres.user_agents import (
    MAX_USER_AGENTS,
    UserAgentExists,
    UserAgentLimit,
    UserAgentStore,
    catalog_models,
    enabled_providers,
)

from .friend_routes import engine_from_services

MAX_PROMPT = 40_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOOL_NAME = re.compile(r"^[A-Za-z_][\w.:*-]*$")


class CustomAgentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    provider: str | None = Field(default=None, max_length=64)
    models: list[str] = Field(default_factory=list, max_length=MAX_AGENT_MODELS)
    tools: list[str] | None = Field(default=None, max_length=128)
    disallowed_tools: list[str] = Field(default_factory=list, max_length=128)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    permission_mode: Literal["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions", "auto"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    enabled: bool = True


class CustomAgentCreate(CustomAgentFields):
    name: str = Field(min_length=1, max_length=64)


def _rejected(code: str, problems: list[str], status: int = 422) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "category": "VALIDATION", "message_key": code, "retryable": False,
                   "details": {"problems": problems[:50]}}},
        status_code=status,
    )


def _not_found(name: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "agent_not_found", "category": "NOT_FOUND", "message_key": "agent_not_found",
                   "retryable": False, "details": {"problems": [f"no agent named '{name[:64]}'"]}}},
        status_code=404,
    )


def _store_for(services: Any) -> UserAgentStore:
    store = getattr(services, "user_agents", None)
    if store is not None:
        return store
    engine = engine_from_services(services)
    if engine is None:
        raise RuntimeError("user_agents service is unavailable")
    created = UserAgentStore(engine)
    services.user_agents = created
    return created


def clean_name(raw: str) -> str:
    return str(raw or "").strip().lower()


def validate(store: UserAgentStore, user_id: str, name: str, body: CustomAgentFields) -> tuple[dict[str, Any], list[str]]:
    """The values to save, and what is wrong with them (nothing is saved when the list is not empty)."""
    problems: list[str] = []
    if not _NAME.fullmatch(name) or name in RESERVED_NAMES:
        problems.append("the name must be lowercase letters, digits, '-' and '_', and not a builtin agent "
                        f"({', '.join(sorted(RESERVED_NAMES))})")
    for tool in [*(body.tools or []), *body.disallowed_tools]:
        if not _TOOL_NAME.fullmatch(tool.strip()):
            problems.append(f"'{tool[:80]}' is not a tool name")
    if not body.description.strip():
        problems.append("the description says when to use the agent; it cannot be blank")
    if not body.prompt.strip():
        problems.append("the instructions cannot be blank")
    provider: str | None = None
    models: tuple[str, ...] = ()
    try:
        provider, models = provider_and_models({"provider": body.provider, "models": [item.strip() for item in body.models]})
    except ValueError as error:
        problems.append(str(error))
    if models and not provider:
        problems.append("choose the provider these models belong to")
    if provider:
        with store._engine.connect() as connection:
            if provider not in enabled_providers(connection, user_id):
                problems.append(f"the provider {provider} is not enabled for you")
            else:
                known = catalog_models(connection, user_id, provider, models)
                problems.extend(
                    f"'{model}' is not in the catalog of {provider} (refresh its models in Providers)"
                    for model in models if model not in known
                )
    values: dict[str, Any] = {
        "description": " ".join(body.description.split()),
        "prompt": body.prompt.strip(),
        "provider": provider,
        "models": list(models),
        "tools": None if body.tools is None else [tool.strip() for tool in body.tools if tool.strip()],
        "disallowed_tools": [tool.strip() for tool in body.disallowed_tools if tool.strip()],
        "effort": body.effort,
        "permission_mode": body.permission_mode,
        "max_turns": body.max_turns,
        "enabled": body.enabled,
    }
    return values, problems


def register_custom_agent_routes(app: FastAPI, services: Any, principal_for: Any) -> None:
    def principal(request: Request, *, mutable: bool, purpose: str):
        current = principal_for(request, mutable=mutable)
        if mutable:
            services.security.check_rate_limit(current, action="agents.custom.write", origin=request.headers.get("origin"))
        services.security.authorize(
            current, action="agents.custom.write" if mutable else "agents.custom.read", resource_id=None, purpose=purpose,
        )
        return current

    @app.get("/v1/custom-agents")
    async def list_custom_agents(request: Request) -> JSONResponse:
        current = principal(request, mutable=False, purpose="agents.custom.list")
        store = _store_for(services)
        agents = await run_in_threadpool(store.list, current.user_id)
        return JSONResponse({"agents": agents, "limit": MAX_USER_AGENTS})

    @app.post("/v1/custom-agents")
    async def create_custom_agent(request: Request, body: CustomAgentCreate) -> JSONResponse:
        current = principal(request, mutable=True, purpose="agents.custom.create")
        store = _store_for(services)
        name = clean_name(body.name)

        def create() -> JSONResponse:
            values, problems = validate(store, current.user_id, name, body)
            if problems:
                return _rejected("invalid_agent", problems)
            try:
                agent = store.create(current.user_id, name, values)
            except UserAgentExists:
                return _rejected("agent_exists", [f"you already have an agent named '{name}'"], status=409)
            except UserAgentLimit as error:
                return _rejected("agent_limit", [str(error)], status=409)
            return JSONResponse({"agent": agent}, status_code=201)

        return await run_in_threadpool(create)

    @app.put("/v1/custom-agents/{name}")
    async def update_custom_agent(name: str, request: Request, body: CustomAgentFields) -> JSONResponse:
        current = principal(request, mutable=True, purpose="agents.custom.update")
        store = _store_for(services)
        clean = clean_name(name)

        def save() -> JSONResponse:
            if store.get(current.user_id, clean) is None:
                return _not_found(clean)
            values, problems = validate(store, current.user_id, clean, body)
            if problems:
                return _rejected("invalid_agent", problems)
            agent = store.update(current.user_id, clean, values)
            return JSONResponse({"agent": agent}) if agent is not None else _not_found(clean)

        return await run_in_threadpool(save)

    @app.delete("/v1/custom-agents/{name}")
    async def delete_custom_agent(name: str, request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="agents.custom.delete")
        store = _store_for(services)
        clean = clean_name(name)
        deleted = await run_in_threadpool(store.delete, current.user_id, clean)
        return JSONResponse({"deleted": True, "name": clean}) if deleted else _not_found(clean)


__all__ = ["CustomAgentCreate", "CustomAgentFields", "register_custom_agent_routes", "validate"]

"""HTTP route to list direct messages addressed to a subagent and their delivery states (WS-B7, B-RF11)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .security import AuthenticatedPrincipal


def _serialize_message(item: dict[str, object]) -> dict[str, object]:
    out = dict(item)
    for field in ("created_at", "delivered_at"):
        val = out.get(field)
        if isinstance(val, datetime):
            out[field] = val.isoformat()
    return out


def register_agent_message_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
    require_port: Callable[[Any], Any],
    jsonable: Callable[[Any], Any],
) -> None:
    """Register ``GET /v1/conversations/{conversation_id}/agents/{agent_id}/messages``."""

    @app.get("/v1/conversations/{conversation_id}/agents/{agent_id}/messages")
    async def list_conversation_agent_messages(
        conversation_id: str,
        agent_id: str,
        request: Request,
    ) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request)
            services.security.authorize(
                principal,
                action="conversation.read",
                resource_id=conversation_id,
                purpose="conversation.read",
            )
            app_port = require_port(services.conversation_application)
            messages = [
                _serialize_message(dict(item))
                for item in app_port.list_agent_messages(
                    conversation_id,
                    principal.user_id,
                    agent_id,
                )
            ]
            return JSONResponse(
                jsonable(
                    {
                        "conversation_id": conversation_id,
                        "agent_id": agent_id,
                        "messages": messages,
                    }
                ),
                status_code=200,
            )

        return await run_in_threadpool(_sync)


__all__ = ["register_agent_message_routes"]

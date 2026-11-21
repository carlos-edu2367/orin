"""HTTP routes for Friend live-session delivery controls (WS-B8)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from agentos.api.contracts import ApplicationNotFoundError


def register_friend_delivery_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., Any],
) -> None:
    """Register `POST /v1/conversations/{conversation_id}/queue/{queue_id}/defer`."""

    @app.post("/v1/conversations/{conversation_id}/queue/{queue_id}/defer")
    async def defer_conversation_queue_item(
        conversation_id: str,
        queue_id: str,
        request: Request,
    ) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.authorize(
                principal,
                action="conversation.send",
                resource_id=conversation_id,
                purpose="conversation.queue",
            )
            if not queue_id or len(queue_id) > 255:
                raise ApplicationNotFoundError(queue_id)
            chat_app = getattr(services, "conversation_application", None)
            if chat_app is None or chat_app.__class__.__name__.startswith("_Unavailable"):
                raise ApplicationNotFoundError(conversation_id)
            result = chat_app.defer_queued_input_to_next_turn(
                principal.user_id,
                conversation_id,
                queue_id,
            )
            return JSONResponse(result)

        return await run_in_threadpool(_sync)


__all__ = ["register_friend_delivery_routes"]

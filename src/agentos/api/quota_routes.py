"""HTTP and SSE routes for subscription quota visibility (`GET /v1/quota`)."""

from __future__ import annotations

import asyncio
import json
import queue
from typing import Any, Callable

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from .security import AuthenticatedPrincipal

_SSE_HEARTBEAT_SECONDS = 15.0


def _publish_quota_updated(services: Any, user_id: str, provider: str) -> None:
    events = getattr(services, "events", None)
    append = getattr(events, "append", None)
    if not callable(append):
        return
    try:
        append(
            f"quota:{user_id}",
            "quota.updated",
            {"user_id": user_id, "provider": provider},
        )
    except Exception:  # noqa: BLE001 - best-effort event publication
        return


def register_quota_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    """Register `GET /v1/quota` and bind `quota.updated` SSE publication."""
    subscribers: dict[str, set[queue.Queue[dict[str, Any]]]] = {}
    sub_lock = asyncio.Lock()

    quota_service = getattr(services, "quota_service", None)
    subscribe_updates = getattr(quota_service, "subscribe_updates", None)
    if callable(subscribe_updates):
        def _on_quota_updated(user_id: str, provider: str) -> None:
            _publish_quota_updated(services, user_id, provider)
            queues = list(subscribers.get(str(user_id), ()))
            payload = {"event": "quota.updated", "provider": provider, "user_id": str(user_id)}
            for q in queues:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

        subscribe_updates(_on_quota_updated)

    @app.get("/v1/quota")
    async def get_quota(
        request: Request,
        refresh: bool = Query(default=False),
    ) -> JSONResponse:
        def _load() -> dict[str, Any]:
            principal = principal_for(request)
            services.security.check_rate_limit(
                principal,
                action="quota.read",
                origin=request.headers.get("origin"),
            )
            svc = getattr(services, "quota_service", None)
            if svc is None or svc.__class__.__name__.startswith("_Unavailable"):
                return {"subscriptions": []}
            quotas = svc.get_all_quotas(principal.user_id, refresh=refresh)
            return {
                "subscriptions": [
                    item.to_dict() if hasattr(item, "to_dict") else dict(item)
                    for item in quotas
                ]
            }

        payload = await run_in_threadpool(_load)
        return JSONResponse(payload)

    @app.get("/v1/quota/stream")
    async def stream_quota(request: Request) -> StreamingResponse:
        def _auth() -> AuthenticatedPrincipal:
            principal = principal_for(request)
            services.security.check_rate_limit(
                principal,
                action="quota.read",
                origin=request.headers.get("origin"),
            )
            return principal

        principal = await run_in_threadpool(_auth)
        poller = getattr(services, "quota_poller", None)
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)

        async def _body() -> Any:
            user_id = principal.user_id
            async with sub_lock:
                subscribers.setdefault(user_id, set()).add(q)
            if poller is not None and hasattr(poller, "client_connected"):
                poller.client_connected(user_id)
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await run_in_threadpool(q.get, True, 0.5)
                        yield f"event: quota.updated\ndata: {json.dumps(event)}\n\n"
                    except queue.Empty:
                        yield f"event: heartbeat\ndata: {json.dumps({'ok': True})}\n\n"
                        await asyncio.sleep(_SSE_HEARTBEAT_SECONDS)
            finally:
                if poller is not None and hasattr(poller, "client_disconnected"):
                    poller.client_disconnected(user_id)
                async with sub_lock:
                    bucket = subscribers.get(user_id)
                    if bucket is not None:
                        bucket.discard(q)
                        if not bucket:
                            subscribers.pop(user_id, None)

        return StreamingResponse(
            _body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )


__all__ = ["register_quota_routes"]

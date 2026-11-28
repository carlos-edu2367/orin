"""Scheduled chat HTTP routes: create, list, update (edit / pause / resume), cancel and permanent delete."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from .security import AuthenticatedPrincipal


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleSelectionRequest(_RequestModel):
    provider: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=512)


class ScheduleRecurrenceRequest(_RequestModel):
    kind: str = Field(pattern="^(once|hourly|daily|weekly|cron)$")
    fire_at: datetime | None = None
    time_of_day: str | None = Field(default=None, max_length=5)
    weekday: int | None = Field(default=None, ge=0, le=6)
    expression: str | None = Field(default=None, max_length=120)
    cron: str | None = Field(default=None, max_length=120)


class CreateScheduledChatRequest(_RequestModel):
    message: str = Field(min_length=1, max_length=16000)
    selection: ScheduleSelectionRequest
    timezone: str = Field(min_length=1, max_length=128)
    recurrence: ScheduleRecurrenceRequest
    project_id: str | None = Field(default=None, min_length=1, max_length=255)


class UpdateScheduledChatRequest(_RequestModel):
    message: str | None = Field(default=None, min_length=1, max_length=16000)
    selection: ScheduleSelectionRequest | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    recurrence: ScheduleRecurrenceRequest | None = None
    project_id: str | None = Field(default=None, max_length=255)
    state: str | None = Field(default=None, pattern="^(ACTIVE|PAUSED)$")


def register_schedule_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
    require_port: Callable[[Any], Any],
    jsonable: Callable[[Any], Any],
    provider_name: Callable[[str], str],
    idempotency: Callable[[Request], str],
    error_response: Callable[..., JSONResponse],
) -> None:
    @app.post("/v1/schedules", status_code=201)
    def create_scheduled_chat(payload: CreateScheduledChatRequest, request: Request) -> JSONResponse:
        from agentos.scheduler.scheduled_chats import ScheduledChatInput

        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="schedule.create", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="schedule.create", resource_id=payload.project_id, purpose="schedule.create")
        provider = provider_name(payload.selection.provider)
        try:
            result = require_port(services.scheduled_chats).create(
                principal.user_id,
                ScheduledChatInput(
                    message=payload.message,
                    provider=provider,
                    model_id=payload.selection.model_id,
                    timezone=payload.timezone,
                    recurrence=payload.recurrence.kind,
                    fire_at=payload.recurrence.fire_at,
                    time_of_day=payload.recurrence.time_of_day,
                    weekday=payload.recurrence.weekday,
                    cron=payload.recurrence.expression or payload.recurrence.cron,
                    project_id=payload.project_id,
                ),
                idempotency_key=idempotency(request),
            )
        except LookupError as error:
            raise ApplicationNotFoundError(str(error)) from error
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(jsonable(result), status_code=201)

    @app.get("/v1/schedules")
    def list_scheduled_chats(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="schedule.list", resource_id=None, purpose="schedule.read")
        return JSONResponse(jsonable(require_port(services.scheduled_chats).list(principal.user_id)))

    @app.patch("/v1/schedules/{schedule_id}")
    def update_scheduled_chat(schedule_id: str, payload: UpdateScheduledChatRequest, request: Request) -> JSONResponse:
        from agentos.scheduler.scheduled_chats import ScheduledChatUpdate

        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="schedule.update", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="schedule.update", resource_id=schedule_id, purpose="schedule.update")
        provider = provider_name(payload.selection.provider) if payload.selection is not None else None
        model_id = payload.selection.model_id if payload.selection is not None else None
        project_id_set = "project_id" in payload.model_fields_set
        project_id = (payload.project_id or None) if project_id_set else None
        try:
            result = require_port(services.scheduled_chats).update(
                principal.user_id,
                schedule_id,
                ScheduledChatUpdate(
                    message=payload.message,
                    provider=provider,
                    model_id=model_id,
                    timezone=payload.timezone,
                    recurrence=payload.recurrence.kind if payload.recurrence is not None else None,
                    fire_at=payload.recurrence.fire_at if payload.recurrence is not None else None,
                    time_of_day=payload.recurrence.time_of_day if payload.recurrence is not None else None,
                    weekday=payload.recurrence.weekday if payload.recurrence is not None else None,
                    cron=(payload.recurrence.expression or payload.recurrence.cron) if payload.recurrence is not None else None,
                    project_id=project_id,
                    project_id_set=project_id_set,
                    state=payload.state,
                ),
            )
        except LookupError as error:
            raise ApplicationNotFoundError(str(error)) from error
        except ValueError as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse(jsonable(result), status_code=200)

    @app.delete("/v1/schedules/{schedule_id}", status_code=204)
    def cancel_scheduled_chat(schedule_id: str, request: Request, permanent: bool = False) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        action = "schedule.delete" if permanent else "schedule.cancel"
        services.security.authorize(principal, action=action, resource_id=schedule_id, purpose=action)
        port = require_port(services.scheduled_chats)
        if permanent:
            try:
                deleted = port.delete(principal.user_id, schedule_id)
            except RuntimeError as error:
                return error_response(409, "CONFLICT", str(error), retryable=True)
            if not deleted:
                raise ApplicationNotFoundError(schedule_id)
            return JSONResponse(status_code=204, content=None)
        if not port.cancel(principal.user_id, schedule_id):
            raise ApplicationNotFoundError(schedule_id)
        return JSONResponse(status_code=204, content=None)

"""HTTP routes of pipelines and ``/goal`` (WS-C10).

Every route needs a signed-in person and one of the actions ``pipeline.read``, ``pipeline.run`` or ``pipeline.write``.
Routes that touch pipeline files need the ``host_folders`` capability (404 on a shared server), and the ``orin code``
token may run, pause, cancel and resume but never edits or approves pipelines (those are settings of the person).
The answers carry references and summaries, never the output of a model: the output route reads one artifact, up to 256 KB.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.pipelines.goal import GoalError
from agentos.pipelines.service import PipelineService, ServiceError

from .security import AuthenticatedPrincipal, AuthorizationError

_CATEGORY = {400: "VALIDATION", 403: "AUTHORIZATION", 404: "NOT_FOUND", 409: "CONFLICT", 410: "NOT_FOUND", 422: "VALIDATION", 429: "RATE_LIMITED", 503: "UNAVAILABLE"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidateBody(_Body):
    text: str = Field(max_length=300_000)
    source: Literal["user", "project"] = "user"
    conversation_id: str | None = Field(default=None, max_length=255)


class SaveBody(_Body):
    text: str = Field(max_length=300_000)
    scope: Literal["user", "project"] = "user"
    conversation_id: str | None = Field(default=None, max_length=255)
    if_match: str | None = Field(default=None, max_length=128)


class ApprovalBody(_Body):
    definition_hash: str = Field(min_length=64, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=255)


class EstimateBody(_Body):
    inputs: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=255)


class StartBody(_Body):
    pipeline_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=255)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ResumeBody(_Body):
    definition: Literal["original", "new_run"] | None = None
    rerun_gates: bool = False
    budget_override: bool = False


class GoalStartBody(_Body):
    objective: str = Field(min_length=1, max_length=4_000)
    max_tokens: int | None = None


class GoalResumeBody(_Body):
    max_tokens: int | None = None


class GoalClearBody(_Body):
    cancel_turn: bool = False


def failure(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    error = {"code": code, "category": _CATEGORY.get(status, "ERROR"), "message_key": code, "message": message, "retryable": False}
    if details:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status)


def register_pipeline_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    def service_for() -> PipelineService:
        chat = getattr(services, "conversation_application", None)
        if chat is None or getattr(getattr(chat, "store", None), "_engine", None) is None:
            raise ServiceError("PIPELINES_UNAVAILABLE", "Os pipelines não estão disponíveis neste ambiente.", 503)
        return PipelineService.for_store(chat.store, quota_service=getattr(services, "quota_service", None))

    def guard(
        request: Request, *, action: str, purpose: str, mutable: bool = False, resource_id: str | None = None,
        disk: bool = True, rate_limited: bool = False, no_cli_token: bool = False,
    ) -> AuthenticatedPrincipal:
        if disk:
            services.capabilities.require("host_folders")
        principal = principal_for(request, mutable=mutable)
        if no_cli_token and principal.credential_kind == "cli_token":
            raise AuthorizationError("orin code does not edit or approve pipelines")
        if rate_limited:
            services.security.check_rate_limit(principal, action=action, origin=request.headers.get("origin"))
        services.security.authorize(principal, action=action, resource_id=resource_id, purpose=purpose)
        return principal

    def root_of(principal: AuthenticatedPrincipal, conversation_id: str | None) -> Path | None:
        return Path(conversation_root(conversation_id, principal)) if conversation_id else None

    async def answer(work: Callable[[], Any], status: int = 200) -> JSONResponse:
        def guarded() -> JSONResponse:
            try:
                return JSONResponse(work(), status_code=status)
            except (ServiceError, GoalError) as error:
                return failure(error.status, error.code, error.message, getattr(error, "details", None))

        return await run_in_threadpool(guarded)

    # ------------------------------------------------------------------------------ pipelines
    @app.get("/v1/pipelines")
    async def list_pipelines(request: Request, conversation_id: str | None = Query(default=None)) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.list", resource_id=conversation_id)
            return service_for().list_pipelines(principal.user_id, root_of(principal, conversation_id))

        return await answer(work)

    @app.post("/v1/pipelines/validate")
    async def validate_pipeline(body: ValidateBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.validate", mutable=True, resource_id=body.conversation_id)
            return service_for().validate_text(principal.user_id, root_of(principal, body.conversation_id), body.text, source=body.source)

        return await answer(work)

    @app.get("/v1/pipelines/{pipeline_id}")
    async def get_pipeline(pipeline_id: str, request: Request, conversation_id: str | None = Query(default=None)) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.read", resource_id=conversation_id)
            return service_for().get_pipeline(principal.user_id, root_of(principal, conversation_id), pipeline_id)

        return await answer(work)

    @app.put("/v1/pipelines/{pipeline_id}")
    async def save_pipeline(pipeline_id: str, body: SaveBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.write", purpose="pipeline.save", mutable=True, resource_id=body.conversation_id, no_cli_token=True)
            match = request.headers.get("if-match") or body.if_match
            return service_for().save_pipeline(
                principal.user_id, root_of(principal, body.conversation_id), pipeline_id, body.text, scope=body.scope, if_match=match.strip('"') if match else None
            )

        return await answer(work)

    @app.delete("/v1/pipelines/{pipeline_id}")
    async def delete_pipeline(
        pipeline_id: str, request: Request, scope: Literal["user", "project"] = Query(default="user"), conversation_id: str | None = Query(default=None)
    ) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.write", purpose="pipeline.delete", mutable=True, resource_id=conversation_id, no_cli_token=True)
            match = request.headers.get("if-match")
            service_for().delete_pipeline(
                principal.user_id, root_of(principal, conversation_id), pipeline_id, scope=scope, if_match=match.strip('"') if match else None
            )
            return {"deleted": pipeline_id, "scope": scope}

        return await answer(work)

    @app.post("/v1/pipelines/{pipeline_id}/approve")
    async def approve_pipeline(pipeline_id: str, body: ApprovalBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.write", purpose="pipeline.approve", mutable=True, resource_id=body.conversation_id, no_cli_token=True)
            return service_for().approve(principal.user_id, root_of(principal, body.conversation_id), pipeline_id, body.definition_hash)

        return await answer(work)

    @app.post("/v1/pipelines/{pipeline_id}/revoke")
    async def revoke_pipeline(pipeline_id: str, body: ApprovalBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.write", purpose="pipeline.revoke", mutable=True, resource_id=body.conversation_id, no_cli_token=True)
            return service_for().revoke(principal.user_id, root_of(principal, body.conversation_id), pipeline_id, body.definition_hash)

        return await answer(work)

    @app.post("/v1/pipelines/{pipeline_id}/estimate")
    async def estimate_pipeline(pipeline_id: str, body: EstimateBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.estimate", mutable=True, resource_id=body.conversation_id)
            return service_for().estimate(principal.user_id, body.conversation_id, root_of(principal, body.conversation_id), pipeline_id, body.inputs)

        return await answer(work)

    # ---------------------------------------------------------------------------------- runs
    @app.post("/v1/pipeline-runs")
    async def start_run(body: StartBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(
                request, action="pipeline.run", purpose="pipeline.start", mutable=True, resource_id=body.conversation_id, rate_limited=True
            )
            return service_for().start(
                principal.user_id, body.conversation_id, root_of(principal, body.conversation_id), body.pipeline_id, body.inputs
            )

        return await answer(work, status=202)

    @app.get("/v1/pipeline-runs")
    async def list_runs(
        request: Request, conversation_id: str | None = Query(default=None), state: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)
    ) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.runs", resource_id=conversation_id, disk=False)
            if conversation_id:
                root_of(principal, conversation_id)  # also proves the conversation is the person's
            return {"runs": service_for().list_runs(principal.user_id, conversation_id=conversation_id, state=state, limit=limit)}

        return await answer(work)

    @app.get("/v1/pipeline-runs/{run_id}")
    async def get_run(run_id: str, request: Request, conversation_id: str | None = Query(default=None)) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.run.read", resource_id=run_id, disk=False)
            return service_for().get_run(principal.user_id, run_id, conversation_id)

        return await answer(work)

    @app.get("/v1/pipeline-runs/{run_id}/steps/{step_id}/output")
    async def step_output(run_id: str, step_id: str, request: Request, item_key: str = Query(default="", max_length=255), attempt: int | None = Query(default=None, ge=1)) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="pipeline.run.output", resource_id=run_id, disk=False)
            return service_for().step_output(principal.user_id, run_id, step_id, item_key, attempt)

        return await answer(work)

    @app.post("/v1/pipeline-runs/{run_id}/pause", status_code=202)
    async def pause_run(run_id: str, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="pipeline.pause", mutable=True, resource_id=run_id, disk=False)
            return service_for().pause(principal.user_id, run_id)

        return await answer(work, status=202)

    @app.post("/v1/pipeline-runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="pipeline.cancel", mutable=True, resource_id=run_id, disk=False)
            return service_for().cancel(principal.user_id, run_id)

        return await answer(work, status=202)

    @app.post("/v1/pipeline-runs/{run_id}/resume", status_code=202)
    async def resume_run(run_id: str, request: Request, body: ResumeBody | None = None) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="pipeline.resume", mutable=True, resource_id=run_id, rate_limited=True)
            options = body or ResumeBody()
            return service_for().resume(
                principal.user_id, run_id, definition=options.definition, rerun_gates=options.rerun_gates, budget_override=options.budget_override
            )

        return await answer(work, status=202)

    # ---------------------------------------------------------------------------------- /goal
    @app.get("/v1/conversations/{conversation_id}/goal")
    async def goal_status(conversation_id: str, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.read", purpose="goal.status", resource_id=conversation_id, disk=False)
            return service_for().goal_status(principal.user_id, conversation_id, root_of(principal, conversation_id))

        return await answer(work)

    @app.post("/v1/conversations/{conversation_id}/goal", status_code=202)
    async def goal_start(conversation_id: str, body: GoalStartBody, request: Request) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="goal.start", mutable=True, resource_id=conversation_id, disk=False, rate_limited=True)
            return service_for().goal_start(principal.user_id, conversation_id, root_of(principal, conversation_id), body.objective, body.max_tokens)

        return await answer(work, status=202)

    @app.post("/v1/conversations/{conversation_id}/goal/clear")
    async def goal_clear(conversation_id: str, request: Request, body: GoalClearBody | None = None) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="goal.clear", mutable=True, resource_id=conversation_id, disk=False)
            return service_for().goal_clear(
                principal.user_id, conversation_id, root_of(principal, conversation_id), cancel_turn=bool(body.cancel_turn) if body else False
            )

        return await answer(work)

    @app.post("/v1/conversations/{conversation_id}/goal/resume", status_code=202)
    async def goal_resume(conversation_id: str, request: Request, body: GoalResumeBody | None = None) -> JSONResponse:
        def work() -> Any:
            principal = guard(request, action="pipeline.run", purpose="goal.resume", mutable=True, resource_id=conversation_id, disk=False, rate_limited=True)
            return service_for().goal_resume(principal.user_id, conversation_id, root_of(principal, conversation_id), body.max_tokens if body else None)

        return await answer(work, status=202)


__all__ = ["register_pipeline_routes"]

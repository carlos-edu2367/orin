"""HTTP routes for durable subagent runs listing and manual resumption (WS-B4, B-RF4)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select, update

from agentos.agentic.agent_run_persistence import AgentRunRecorder, default_agent_runs_dir
from agentos.persistence.postgres.agent_runs import AgentRunRecord, AgentRunStore
from agentos.persistence.postgres.schema import conversation_agents, conversations

from .security import AuthenticatedPrincipal


def _serialize_run(record: AgentRunRecord, *, live_progress: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "agent_id": record.agent_id,
        "user_id": record.user_id,
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "parent_agent_id": record.parent_agent_id,
        "subagent_type": record.subagent_type,
        "provider": record.provider,
        "model_id": record.model_id,
        "effort": record.effort,
        "state": record.state,
        "task_digest": record.task_digest,
        "history_path": record.history_path,
        "worktree_path": record.worktree_path,
        "pipeline_run_id": record.pipeline_run_id,
        "last_activity_at": record.last_activity_at.isoformat() if isinstance(record.last_activity_at, datetime) else record.last_activity_at,
        "tool_calls": record.tool_calls,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cost_usd": record.cost_usd,
        "result_ref": record.result_ref,
        "created_at": record.created_at.isoformat() if isinstance(record.created_at, datetime) else record.created_at,
        "finished_at": record.finished_at.isoformat() if isinstance(record.finished_at, datetime) else record.finished_at,
        "stalled": record.state == "stalled",
    }
    if live_progress:
        out.update(live_progress)
    return out


def _resolve_engine(app_port: Any) -> Any:
    store = getattr(app_port, "store", None)
    return getattr(store, "_engine", None) or getattr(app_port, "_engine", None)


def _conversation_owned(engine: Any, conversation_id: str, user_id: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            select(conversations.c.conversation_id).where(
                conversations.c.conversation_id == str(conversation_id),
                conversations.c.user_id == str(user_id),
            )
        ).first()
    return row is not None


def _dispatch_resume_turn(
    app_port: Any,
    run: AgentRunRecord,
    *,
    notice: str,
    task_hint: str = "",
) -> dict[str, Any]:
    """Dispatch deterministic background subagent resumption without injecting fake user chat messages (RT-B-05).

    Uses ``app_port.dispatch_agent_resume(run, notice=notice)`` when wired; otherwise relies on
    the durable ``agent_runs.state = 'queued'`` row consumed by ``ChatWorker.resume_queued_subagents()``
    which directly invokes ``TurnSession.resume_interrupted_subagent(agent_id)``.
    """
    del task_hint
    custom_dispatcher = getattr(app_port, "dispatch_agent_resume", None)
    if callable(custom_dispatcher):
        result = custom_dispatcher(run, notice=notice)
        if isinstance(result, dict):
            if result.get("dispatched") is False:
                raise RuntimeError("Custom agent resume dispatcher rejected the request.")
            return dict(result)
        return {"dispatched": True, "mode": "custom"}
    return {"dispatched": True, "mode": "worker_queue", "agent_id": run.agent_id}


def register_agent_run_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
    require_port: Callable[[Any], Any],
    jsonable: Callable[[Any], Any],
) -> None:
    """Register ``GET /v1/conversations/{id}/agent-runs`` and ``POST /v1/conversations/{id}/agents/{agent_id}/resume``."""

    @app.get("/v1/conversations/{conversation_id}/agent-runs")
    async def list_conversation_agent_runs(
        conversation_id: str,
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
            engine = _resolve_engine(app_port)
            if engine is None or not _conversation_owned(engine, conversation_id, principal.user_id):
                return JSONResponse({"detail": "Conversation not found", "error_code": "NOT_FOUND"}, status_code=404)

            store = AgentRunStore(engine)
            rows = store.list_for_conversation(conversation_id, user_id=principal.user_id)
            serialized = [_serialize_run(item) for item in rows]
            return JSONResponse(
                jsonable(
                    {
                        "conversation_id": conversation_id,
                        "runs": serialized,
                    }
                ),
                status_code=200,
            )

        return await run_in_threadpool(_sync)

    @app.post("/v1/conversations/{conversation_id}/agents/{agent_id}/resume", status_code=202)
    async def resume_conversation_agent_run(
        conversation_id: str,
        agent_id: str,
        request: Request,
    ) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.authorize(
                principal,
                action="conversation.send",
                resource_id=conversation_id,
                purpose="conversation.send",
            )
            app_port = require_port(services.conversation_application)
            engine = _resolve_engine(app_port)
            if engine is None or not _conversation_owned(engine, conversation_id, principal.user_id):
                return JSONResponse({"detail": "Conversation not found", "error_code": "NOT_FOUND"}, status_code=404)

            runs_dir = default_agent_runs_dir()
            recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
            run = recorder.get(agent_id)
            if run is None or run.user_id != principal.user_id or run.conversation_id != conversation_id:
                return JSONResponse({"detail": "Agent run not found", "error_code": "NOT_FOUND"}, status_code=404)

            if run.state != "interrupted":
                return JSONResponse(
                    jsonable(
                        {
                            "detail": f"Agent run '{agent_id}' is in state '{run.state}' and cannot be resumed.",
                            "error_code": "AGENT_NOT_RESUMABLE",
                            "agent_id": agent_id,
                            "state": run.state,
                        }
                    ),
                    status_code=409,
                )

            now = datetime.now(UTC)
            # RT-B-13: atomic conditional state transition prevents concurrent double-resume.
            transitioned = recorder.store.transition_state(
                agent_id,
                from_state="interrupted",
                to_state="queued",
                last_activity_at=now,
            )
            if transitioned is None:
                latest = recorder.get(agent_id)
                latest_state = latest.state if latest is not None else run.state
                return JSONResponse(
                    jsonable(
                        {
                            "detail": f"Agent run '{agent_id}' is in state '{latest_state}' and cannot be resumed.",
                            "error_code": "AGENT_NOT_RESUMABLE",
                            "agent_id": agent_id,
                            "state": latest_state,
                        }
                    ),
                    status_code=409,
                )

            restored = recorder.restore_history(agent_id, history_path=run.history_path)
            notice = restored.resume_notice()
            task_hint = str(restored.metadata.get("task") or "")

            with engine.begin() as conn:
                conn.execute(
                    update(conversation_agents)
                    .where(
                        conversation_agents.c.agent_id == agent_id,
                        conversation_agents.c.conversation_id == conversation_id,
                        conversation_agents.c.user_id == principal.user_id,
                    )
                    .values(state="working", updated_at=now)
                )

            try:
                dispatch_info = _dispatch_resume_turn(app_port, transitioned, notice=notice, task_hint=task_hint)
            except Exception as exc:  # noqa: BLE001
                # RT-B-05: revert state back to 'interrupted' on dispatch failure so the run is never stuck in 'queued'.
                rollback_ts = datetime.now(UTC)
                recorder.store.touch(agent_id, state="interrupted", last_activity_at=rollback_ts)
                with engine.begin() as conn:
                    conn.execute(
                        update(conversation_agents)
                        .where(
                            conversation_agents.c.agent_id == agent_id,
                            conversation_agents.c.conversation_id == conversation_id,
                            conversation_agents.c.user_id == principal.user_id,
                        )
                        .values(state="interrupted", updated_at=rollback_ts)
                    )
                return JSONResponse(
                    jsonable(
                        {
                            "detail": f"Failed to dispatch resume for agent '{agent_id}': {exc}",
                            "error_code": "AGENT_RESUME_DISPATCH_FAILED",
                            "agent_id": agent_id,
                            "state": "interrupted",
                        }
                    ),
                    status_code=503,
                )

            return JSONResponse(
                jsonable(
                    {
                        "agent_id": agent_id,
                        "conversation_id": conversation_id,
                        "state": "queued",
                        "resumed": True,
                        "history_messages": len(restored.messages),
                        "history_corrupted": restored.corrupted,
                        "warning": restored.warning,
                        "notice": notice,
                        "dispatch": dispatch_info,
                    }
                ),
                status_code=202,
            )

        return await run_in_threadpool(_sync)


__all__ = ["register_agent_run_routes"]

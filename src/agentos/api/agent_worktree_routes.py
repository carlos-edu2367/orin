"""HTTP routes for inspecting/discarding isolated subagent worktrees (WS-B2 / WS-B9)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.code_mode.agent_worktree import AgentWorktreeManager
from agentos.code_mode.worktree import WorktreeError
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.persistence.postgres.schema import conversation_agents

from .friend_routes import engine_from_services
from .security import AuthenticatedPrincipal


def register_agent_worktree_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    """Register ``POST /v1/conversations/{conversation_id}/worktrees/{agent_id}/discard``."""

    @app.post("/v1/conversations/{conversation_id}/worktrees/{agent_id}/discard")
    async def discard_conversation_agent_worktree(
        conversation_id: str,
        agent_id: str,
        request: Request,
    ) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.authorize(
                principal,
                action="conversation.write",
                resource_id=conversation_id,
                purpose="worktree.discard",
            )
            # Verifies conversation ownership and resolves the conversation's workspace root.
            root = Path(conversation_root(conversation_id, principal)).resolve()
            engine = engine_from_services(services)
            store = AgentWorktreeStore(engine) if engine is not None else None

            # Verify agent ownership & active status before touching any worktree on disk (RT-B-10).
            if engine is not None:
                run = AgentRunStore(engine).get(agent_id)
                if run is not None:
                    if run.conversation_id != conversation_id or run.user_id != principal.user_id:
                        return JSONResponse(
                            {
                                "error": {
                                    "code": "worktree_not_found",
                                    "category": "NOT_FOUND",
                                    "message": "Worktree não encontrada nesta conversa.",
                                }
                            },
                            status_code=404,
                        )
                    if run.state in {"running", "stalled"}:
                        return JSONResponse(
                            {
                                "error": {
                                    "code": "worktree_agent_running",
                                    "category": "CONFLICT",
                                    "message": "Não é possível descartar a worktree enquanto o subagente ainda está em execução.",
                                }
                            },
                            status_code=409,
                        )
                with engine.connect() as conn:
                    agent_row = conn.execute(
                        select(
                            conversation_agents.c.conversation_id,
                            conversation_agents.c.user_id,
                            conversation_agents.c.state,
                        ).where(conversation_agents.c.agent_id == agent_id)
                    ).mappings().first()
                if agent_row is not None:
                    if (
                        str(agent_row["conversation_id"]) != conversation_id
                        or str(agent_row["user_id"]) != principal.user_id
                    ):
                        return JSONResponse(
                            {
                                "error": {
                                    "code": "worktree_not_found",
                                    "category": "NOT_FOUND",
                                    "message": "Worktree não encontrada nesta conversa.",
                                }
                            },
                            status_code=404,
                        )
                    if str(agent_row["state"]) in {"running", "working", "stalled"}:
                        return JSONResponse(
                            {
                                "error": {
                                    "code": "worktree_agent_running",
                                    "category": "CONFLICT",
                                    "message": "Não é possível descartar a worktree enquanto o subagente ainda está em execução.",
                                }
                            },
                            status_code=409,
                        )

            live_bg = BACKGROUND_AGENTS.get(conversation_id, agent_id)
            if live_bg is not None and (live_bg.status in {"running", "stalled"} or not live_bg.future.done()):
                return JSONResponse(
                    {
                        "error": {
                            "code": "worktree_agent_running",
                            "category": "CONFLICT",
                            "message": "Não é possível descartar a worktree enquanto o subagente ainda está em execução.",
                        }
                    },
                    status_code=409,
                )

            if store is not None:
                rec = store.get(agent_id)
                if rec is not None and rec.workspace_root:
                    rec_root = Path(rec.workspace_root).resolve()
                    if rec_root != root:
                        return JSONResponse(
                            {
                                "error": {
                                    "code": "worktree_not_found",
                                    "category": "NOT_FOUND",
                                    "message": "Worktree não encontrada nesta conversa.",
                                }
                            },
                            status_code=404,
                        )

            manager = AgentWorktreeManager(root, store=store)
            try:
                discarded = manager.discard_worktree(agent_id, store=store)
            except WorktreeError as exc:
                return JSONResponse(
                    {
                        "error": {
                            "code": "worktree_discard_failed",
                            "category": "CONFLICT",
                            "message": str(exc),
                        }
                    },
                    status_code=409,
                )
            if not discarded:
                return JSONResponse(
                    {
                        "error": {
                            "code": "worktree_not_found",
                            "category": "NOT_FOUND",
                            "message": "Nenhuma worktree ativa ou registrada encontrada para este agente.",
                        }
                    },
                    status_code=404,
                )
            return JSONResponse(
                {
                    "discarded": True,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "merge_state": "discarded",
                },
                status_code=200,
            )

        return await run_in_threadpool(_sync)


__all__ = ["register_agent_worktree_routes"]

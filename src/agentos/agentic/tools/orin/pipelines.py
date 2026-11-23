"""``RunPipeline``: the one tool that lets the agent start a pipeline (WS-C10, C-RF7, decision G17).

Three locks keep it from spending anything on its own: the tool is not published unless ``pipelines.agentMayStart`` is
on (a user-only key), only pipelines already approved for an *agent* can be named (built-in ones, or user and project
ones whose exact bytes the person approved), and the permission broker asks like for any tool that changes state.
It never runs a pipeline inside the agent's turn: it validates, registers a pending start and answers; the post-turn
hook (``pipelines/after_turn.py``) starts the run when the turn ends well.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


def _settings(toolset: Any) -> Any:
    from agentos.pipelines.settings import PipelineSettings

    return PipelineSettings.from_effective(getattr(toolset, "_effective_settings", None))


def _available(toolset: Any) -> bool:
    """Published only to the lead agent of a turn with a database, and only while the user switched it on."""
    return bool(
        getattr(toolset, "_chat_store", None) is not None
        and getattr(toolset, "turn_id", None)
        and getattr(toolset, "conversation_id", None)
        and getattr(toolset, "_team_sender", None) == "lead"  # a pipeline's own children never start pipelines
        and _settings(toolset).agent_may_start
    )


class PipelineTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def run_pipeline(self, pipeline_id: str | None = None, inputs: dict[str, Any] | None = None, **_ignored: Any) -> dict[str, Any]:
        from agentos.pipelines.service import PipelineService, ServiceError

        if not _settings(self).agent_may_start:
            raise AgentToolError("Iniciar pipelines pelo agente está desligado (`pipelines.agentMayStart`).", error_code="PIPELINES_AGENT_START_DISABLED")
        store = getattr(self, "_chat_store", None)
        user_id = getattr(self, "_project_user_id", None)
        if store is None or getattr(store, "_engine", None) is None or not user_id or not self.turn_id or not self.conversation_id:
            raise AgentToolError("Os pipelines não estão disponíveis neste ambiente.", error_code="PIPELINES_UNAVAILABLE")
        if inputs is not None and not isinstance(inputs, dict):
            raise AgentToolError("`inputs` deve ser um objeto com os valores das entradas.", error_code="INVALID_ARGUMENTS")
        root = Path(self.workspace.root)
        service = PipelineService.for_store(store)
        try:
            if not pipeline_id:
                items = service.agent_startable(user_id, root)
                lines = [f"- {item['id']}: {item['name']} ({item['source']})" for item in items] or ["(nenhum pipeline aprovado para o agente)"]
                return {"summary": f"{len(items)} pipeline(s) disponível(is)", "content": "\n".join(lines), "payload": {"pipelines": items}}
            pending, info = service.request_agent_start(user_id, self.conversation_id, self.turn_id, root, str(pipeline_id), inputs)
        except ServiceError as error:
            problems = "; ".join(str(item.get("message")) for item in (error.details.get("errors") or [])[:3])
            raise AgentToolError(f"{error.message} {problems}".strip(), error_code=error.code) from error
        budget = info["budget"]
        providers = ", ".join(info["providers"]) or "o provedor do turno"
        return {
            "summary": f"Pipeline `{pipeline_id}` agendado para o fim deste turno",
            "content": (
                f"O pipeline `{pipeline_id}` começa quando este turno terminar; nada roda agora. "
                f"Orçamento: {budget.get('max_tokens')} tokens, {budget.get('max_minutes')} min. Provedores: {providers}. "
                "O usuário acompanha e pode cancelar pela tela de execuções."
            ),
            "payload": {"pending_id": pending.pending_id, "pipeline_id": pipeline_id, "inputs": dict(pending.inputs), **info},
        }


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="run_pipeline",
        description=(
            "Start a pre-approved pipeline (a declarative multi-step, multi-agent workflow) AFTER this turn ends; it never runs "
            "inside the turn. Without `pipeline_id` it lists the pipelines you may start. `inputs` holds the values of the "
            "pipeline's inputs; an input that is a whole shell command cannot be set by you (its default is used). The pipeline "
            "spends tokens of the person's subscriptions: start one only when the person asked for that kind of work."
        ),
        legacy_schema=_schema(
            {
                "pipeline_id": {**_TEXT, "description": "Id of an approved pipeline. Omit it to list the available ones."},
                "inputs": {"type": "object", "description": "Values for the pipeline inputs, by name."},
            }
        ),
        handler="run_pipeline",
        kind="orin",
        policy_tags=("mutates",),
        concurrency_safe=False,
        available=_available,
        order=560,
    ),
)


__all__ = ["PipelineTools", "SPECS"]

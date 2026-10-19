"""Agent tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolOutcome,
)


class AgentTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def create_agent(self, name: str, role: str, model_id: str | None = None) -> ToolOutcome:
        if self._create_agent is None:
            raise AgentToolError("Subagents are not available.")
        return self._create_agent(str(name), str(role), str(model_id) if model_id is not None else None)

    def ask_agent(self, name: str, task: str) -> ToolOutcome:
        if self._delegate is None:
            raise AgentToolError("Subagents are not available.")
        return self._delegate(str(name), str(task))

    def ask_agents(self, tasks: list[Mapping[str, Any]]) -> ToolOutcome:
        if self._delegate_batch is None:
            raise AgentToolError("Subagents are not available.")
        return self._delegate_batch(list(tasks))

    def run_subagent(
        self,
        subagent_type: str,
        task: str,
        description: str = "",
        model_id: str | None = None,
        agent_id: str | None = None,
        run_in_background: bool = False,
    ) -> ToolOutcome:
        raw_type = str(subagent_type or "").strip()
        clean_type = raw_type.lower()
        if clean_type == "general-purpose":
            clean_type = "general"
        if clean_type not in {"explore", "plan", "general", "fork"}:
            if raw_type and (model_id is None or not str(model_id).strip()):
                model_id = raw_type
                clean_type = "general"
            else:
                raise AgentToolError(
                    "subagent_type must be one of 'explore', 'plan', 'general', or 'fork' "
                    "(public names: Explore, Plan, general-purpose, fork)."
                )
        if not isinstance(task, str) or not task.strip():
            raise AgentToolError("task must be a non-blank string.")
        clean_agent_id = str(agent_id).strip() if agent_id is not None and str(agent_id).strip() else None
        clean_model_id = str(model_id).strip() if model_id is not None and str(model_id).strip() else None
        if self._run_subagent is not None:
            try:
                return self._run_subagent(
                    clean_type,
                    task.strip(),
                    description=str(description or "").strip(),
                    model_id=clean_model_id,
                    agent_id=clean_agent_id,
                    run_in_background=bool(run_in_background),
                )
            except TypeError:
                return self._run_subagent(
                    clean_type,
                    task.strip(),
                    description=str(description or "").strip(),
                    model_id=clean_model_id,
                )
        if self._delegate is not None:
            return self._delegate(clean_type, task.strip())
        raise AgentToolError("Subagents are not available.")

    def check_subagent(
        self,
        agent_id: str,
        wait_seconds: int = 0,
    ) -> ToolOutcome:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AgentToolError("agent_id must be a non-blank string.")
        if self._check_subagent is None:
            raise AgentToolError("Subagent inspection is not available.")
        try:
            raw_wait = int(wait_seconds or 0)
        except (TypeError, ValueError) as exc:
            raise AgentToolError("wait_seconds must be an integer between 0 and 60.") from exc
        wait = max(0, min(raw_wait, 60))
        return self._check_subagent(agent_id.strip(), wait_seconds=wait)

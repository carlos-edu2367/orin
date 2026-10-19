"""Agent tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


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


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="create_agent",
        description="Create a specialist subagent. An optional model_id must be a favorite model of the current provider; omit it to use the current model.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Short name, e.g. Researcher"},
            "role": {**_TEXT, "description": "One line describing what this agent is responsible for."},
            "model_id": {**_TEXT, "description": "Optional exact ID of a favorite model from the current provider. Defaults to the current model."},
        }, ("name", "role")),
        handler="create_agent",
        kind="agent",
        policy_tags=("mutates",),
        available=lambda self: (self._create_agent is not None),
        order=350,
    ),
    ToolSpec(
        key="ask_agent",
        description="Send a task to a subagent you created (or a builtin specialist: 'explore', 'plan', 'general', 'fork') and wait for its answer.",
        legacy_schema=_schema({"name": _TEXT, "task": {**_TEXT, "description": "The complete instruction; the subagent cannot see this conversation unless name is 'fork'."}}, ("name", "task")),
        handler="ask_agent",
        kind="agent",
        policy_tags=("mutates",),
        available=lambda self: (self._delegate is not None),
        order=360,
    ),
    ToolSpec(
        key="ask_agents",
        description="Send tasks to several subagents at once and wait for all of them. Use this instead of calling ask_agent repeatedly when the tasks do not depend on each other.",
        legacy_schema=_schema({
            "tasks": {
                "type": "array",
                "items": _schema({"name": _TEXT, "task": {**_TEXT, "description": "The complete instruction; the subagent cannot see this conversation."}}, ("name", "task")),
            },
        }, ("tasks",)),
        handler="ask_agents",
        kind="agent",
        policy_tags=("mutates",),
        available=lambda self: (self._delegate_batch is not None),
        order=370,
    ),
    ToolSpec(
        key="run_subagent",
        description="Launch or resume a specialized builtin subagent ('explore' for read-only codebase search, 'plan' for read-only architecture planning, 'general' for multi-step implementation/research, or 'fork' to inherit the full conversation context) and return its report or background handle.",
        legacy_schema=_schema({
            "subagent_type": {
                "type": "string",
                "enum": ["explore", "plan", "general", "fork"],
                "description": "Builtin specialist type: 'explore' (read-only search), 'plan' (read-only architect), 'general' (full toolset), or 'fork' (inherits parent conversation context).",
            },
            "task": {**_TEXT, "description": "Complete task instructions for the subagent."},
            "description": {**_TEXT, "description": "Short 3-5 word summary of what the subagent will do."},
            "model_id": {**_TEXT, "description": "Optional favorite model ID from the current provider."},
            "agent_id": {**_TEXT, "description": "Optional agent_id from a previous run_subagent call to resume that subagent with its full prior context preserved."},
            "run_in_background": {"type": "boolean", "description": "When true, launch the subagent asynchronously in the background and return its agent_id immediately. Use check_subagent to inspect or await its output."},
        }, ("subagent_type", "task")),
        handler="run_subagent",
        kind="agent",
        policy_tags=("mutates",),
        available=lambda self: (self._run_subagent is not None or self._delegate is not None),
        order=380,
    ),
    ToolSpec(
        key="check_subagent",
        description="Inspect or await a background subagent started with run_subagent(run_in_background=true).",
        legacy_schema=_schema({
            "agent_id": {**_TEXT, "description": "The agent_id returned by run_subagent."},
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 60,
                "description": "Optional seconds to wait for completion (0 polls immediately).",
            },
        }, ("agent_id",)),
        handler="check_subagent",
        kind="agent",
        read_only=True,
        available=lambda self: (self._check_subagent is not None or self._run_subagent is not None),
        order=390,
    ),
)

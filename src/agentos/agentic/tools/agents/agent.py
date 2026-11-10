"""Agent tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping, Literal

from pydantic import Field

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput


CHARACTER_NAME_MAX_LEN = 32
CHARACTER_NAME_DESCRIPTION = "Optional character name shown for this subagent in the Cena, e.g. a fun short name"
_SUBAGENT_CHARACTER_NAMES: dict[tuple[str, str], str] = {}


def sanitize_character_name(raw: object) -> str | None:
    """Trim, strip control characters and bound to <= 32 characters."""
    if raw is None or isinstance(raw, bool):
        return None
    cleaned = "".join(
        ch for ch in str(raw) if ord(ch) >= 32 and not (0x7F <= ord(ch) <= 0x9F)
    ).strip()
    if len(cleaned) > CHARACTER_NAME_MAX_LEN:
        cleaned = cleaned[:CHARACTER_NAME_MAX_LEN].rstrip()
    return cleaned or None


def remember_subagent_character_name(
    conversation_id: str,
    agent_id: str,
    record: dict[str, object],
    character_name: object = None,
    prior: object = None,
) -> str | None:
    """Sanitize and persist character_name on the subagent record (preserving prior on resume)."""
    conv_key = (str(conversation_id or ""), str(agent_id or ""))
    resolved = sanitize_character_name(character_name) or sanitize_character_name(
        record.get("character_name") or prior or _SUBAGENT_CHARACTER_NAMES.get(conv_key)
    )
    if resolved:
        record["character_name"] = resolved
        _SUBAGENT_CHARACTER_NAMES[conv_key] = resolved
    return resolved


def resolve_subagent_character_name(
    conversation_id: str,
    main_agent_id: str,
    records: Mapping[str, Any],
    agent_id: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> str | None:
    """Look up the character_name for a subagent event payload."""
    conv_id = str(conversation_id or "")
    candidate_ids: list[str] = []
    if isinstance(payload, Mapping):
        for key in ("to_agent_id", "from_agent_id", "agent_id"):
            raw_val = payload.get(key)
            if isinstance(raw_val, str) and raw_val.strip() and raw_val.strip() != main_agent_id:
                candidate_ids.append(raw_val.strip())
    if agent_id and agent_id != main_agent_id:
        candidate_ids.append(str(agent_id))
    for cid in candidate_ids:
        rec = records.get(cid)
        if isinstance(rec, Mapping):
            cname = sanitize_character_name(rec.get("character_name"))
            if cname:
                return cname
        remembered = _SUBAGENT_CHARACTER_NAMES.get((conv_id, cid))
        if remembered:
            return remembered
    if isinstance(payload, Mapping):
        raw_name = payload.get("agent_name")
        if isinstance(raw_name, str) and raw_name.strip():
            target_name = raw_name.strip()
            for rec in records.values():
                if isinstance(rec, Mapping) and str(rec.get("name") or "") == target_name:
                    cname = sanitize_character_name(rec.get("character_name"))
                    if cname:
                        return cname
    return None


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
        teammate_name: str | None = None,
        team_name: str | None = None,
        character_name: str | None = None,
    ) -> ToolOutcome:
        raw_type = str(subagent_type or "").strip()
        clean_type = raw_type.lower()
        if clean_type == "general-purpose":
            clean_type = "general"
        if clean_type not in {"explore", "plan", "general", "fork"}:
            if raw_type and self._run_subagent is not None:
                clean_type = raw_type  # W5: an agent defined on disk (or a model id); the session decides
            elif raw_type and (model_id is None or not str(model_id).strip()):
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
        clean_char_name = sanitize_character_name(character_name)
        if self._run_subagent is not None:
            # W5: a named teammate of an agent team (only passed when asked, so older callables keep working).
            extra: dict[str, Any] = (
                {"teammate_name": str(teammate_name).strip(), "team_name": str(team_name or "").strip()}
                if teammate_name and str(teammate_name).strip()
                else {}
            )
            if clean_char_name is not None:
                extra["character_name"] = clean_char_name
            try:
                return self._run_subagent(
                    clean_type,
                    task.strip(),
                    description=str(description or "").strip(),
                    model_id=clean_model_id,
                    agent_id=clean_agent_id,
                    run_in_background=bool(run_in_background),
                    **extra,
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


class AgentInput(ToolInput):
    description: str = Field(description="3-5 word summary.")
    prompt: str = Field(description="Complete instructions; the subagent does not see this conversation.")
    subagent_type: str | None = Field(None, description="Explore, Plan, general-purpose or fork.")
    model: str | None = Field(None, description="Optional model. For an agent that lists its own models, one of them (required when it lists several).")
    run_in_background: bool | None = Field(None, description="Return immediately and notify on completion.")
    resume: str | None = Field(None, description="Id of a previous subagent to continue.")
    name: str | None = Field(None, description="Name a teammate of an agent team, so the team can message it with SendMessage.")
    team_name: str | None = Field(None, description="The team the named teammate belongs to.")
    character_name: str | None = Field(None, description=CHARACTER_NAME_DESCRIPTION)


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
                "description": "A builtin specialist: 'explore' (read-only search), 'plan' (read-only architect), 'general' (full toolset) or 'fork' (inherits parent conversation context); or the name of an agent defined here.",
            },
            "task": {**_TEXT, "description": "Complete task instructions for the subagent."},
            "description": {**_TEXT, "description": "Short 3-5 word summary of what the subagent will do."},
            "model_id": {**_TEXT, "description": "Optional. For an agent that lists its own models, one of them (required when it lists several); otherwise a favorite model ID of the current provider."},
            "agent_id": {**_TEXT, "description": "Optional agent_id from a previous run_subagent call to resume that subagent with its full prior context preserved."},
            "run_in_background": {"type": "boolean", "description": "When true, launch the subagent asynchronously in the background and return its agent_id immediately. Use check_subagent to inspect or await its output."},
            "teammate_name": {**_TEXT, "description": "Optional name of a teammate (agent teams); a teammate always runs in the background and can be messaged with send_message."},
            "team_name": {**_TEXT, "description": "Optional team of the named teammate."},
            "character_name": {**_TEXT, "description": CHARACTER_NAME_DESCRIPTION},
        }, ("subagent_type", "task")),
        handler="run_subagent",
        kind="agent",
        input_model=AgentInput,
        public_description="Delegate a self-contained task to a subagent. Types: 'Explore' (fast read-only search), 'Plan' (read-only design), 'general-purpose' (full toolset), 'fork' (inherits this conversation).",
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

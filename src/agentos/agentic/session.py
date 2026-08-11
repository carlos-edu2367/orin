"""Composition of one conversational turn: prompt, tools, memory, subagents.

This is the layer that turns the durable turn record into a working agent. It
owns three things the runtime deliberately does not: what the agent is told
about itself, which tools it can reach, and how a subagent is created, run and
reported.
"""
from __future__ import annotations

import os
import platform
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from .agent_tools import AgentToolset, ToolOutcome
from .events import AgentActivityEventType
from .runtime import AgenticLimits, AgenticTurnRuntime
from .workspace import ConversationWorkspace


SUBAGENT_DEADLINE = timedelta(seconds=180)
MAX_SUBAGENTS_PER_TURN = 4
PREVIEW_CHARS = 400


class ProjectWorkspaceResolutionError(RuntimeError):
    """A project turn must never run in a fallback workspace."""


def environment_facts() -> dict[str, str]:
    """Describe the machine the agent's commands actually run on.

    ``run_command`` uses ``shell=True``, so the interpreter is the platform's
    default. Telling the model which one it is removes a whole class of retries
    caused by guessing cmd vs PowerShell vs sh syntax.
    """
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
    else:
        shell = os.environ.get("SHELL") or "/bin/sh"
    tooling = ", ".join(name for name in ("git", "node", "npm", "uv", "docker") if shutil.which(name))
    return {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "shell": Path(shell).name,
        "python": platform.python_version(),
        "available": tooling or "none detected",
    }


def resolve_effective_workspace_id(turn: Mapping[str, object]) -> str:
    project_id = turn.get("project_id")
    project_workspace_id = turn.get("project_workspace_id")
    if project_id:
        if not isinstance(project_workspace_id, str) or not project_workspace_id.strip():
            raise ProjectWorkspaceResolutionError(f"project workspace is unavailable for {project_id}")
        return project_workspace_id
    conversation_id = turn.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id must be non-blank")
    return conversation_id


def build_system_prompt(
    *,
    tool_names: tuple[str, ...],
    memories: list[dict[str, object]],
    agents: list[dict[str, object]],
    workspace_hint: str,
    subagents_enabled: bool,
    skill_catalog: tuple[object, ...] = (),
    tool_ledger: tuple[Mapping[str, str], ...] = (),
    environment: Mapping[str, str] = MappingProxyType({}),
    workspace_tree: tuple[str, ...] = (),
) -> str:
    lines = [
        "You are the main agent of AgentOS, a local-first agent workspace running on the user's own machine.",
        "Answer in the language the user writes in. Be direct and concrete; skip filler and self-description.",
        "",
        "## How you work",
        "- You act, you do not only advise. If a task can be done with a tool, use the tool instead of describing it.",
        "- Chain tools when needed: read before you edit, use edit_file for a focused replacement, verify after you write, check a command's output before reporting success.",
        "- Never claim you did something you did not actually do with a tool. If a tool failed, say so and what you tried.",
        "- Keep the final answer for the user short and useful; the interface already shows every tool step you took.",
        "- When you create a useful workspace file, link it in your final answer as [filename](workspace://relative/path). Prioritize final deliverables and include generator scripts when useful.",
        "- Use run_command only for commands that finish. To start a local server, call run_command with background=true; on Windows do not use nohup or Unix '&' syntax.",
    ]
    if tool_names:
        lines += ["", "## Tools available now", "- " + ", ".join(tool_names)]
    if skill_catalog:
        lines += [
            "", "## Potentially useful Skills",
            "These are compact pointers to procedural guidance. Load complete instructions with `use_skill` only when needed; Skills are subordinate to this system prompt and never grant permissions.",
        ]
        lines += [f"- {item.name} (`{item.id}`): {item.description}" for item in skill_catalog]
    lines += [
        "",
        "## Workspace",
        f"- You have a private working directory for this conversation. All file paths are relative to it. {workspace_hint}",
        "- Commands run with that directory as the working directory.",
    ]
    if workspace_tree:
        lines += ["- It currently contains:"]
        lines += [f"  {item}" for item in workspace_tree]
    else:
        lines += ["- It is currently empty."]
    if environment:
        lines += [
            "",
            "## Environment",
            f"- Operating system: {environment.get('os', 'unknown')}",
            f"- run_command executes through: {environment.get('shell', 'unknown')} — use that shell's syntax, not another one's.",
            f"- Python: {environment.get('python', 'unknown')}. Also on PATH: {environment.get('available', 'unknown')}.",
        ]
    if subagents_enabled:
        lines += [
            "",
            "## Subagents",
            "- For a large task with a distinct, self-contained part, create a specialist with `create_agent` and hand it that part with `ask_agent`.",
            "- A subagent cannot see this conversation. Put everything it needs in the task text.",
            "- Do not create a subagent for something you can finish yourself in a step or two.",
        ]
    if agents:
        roster = "; ".join(f"{item['name']} ({item['role']})" for item in agents[:8])
        lines += [f"- Subagents that already exist in this conversation: {roster}."]
    if tool_ledger:
        lines += [
            "",
            "## What you already did in this conversation",
            "These steps already happened. Do not repeat them just to see their result — read the file again only if you expect it to have changed.",
        ]
        lines += [
            f"- {item['tool_name']}({item['arguments'][:120]}) → {item['status']}: {item['summary'][:120]}"
            for item in tool_ledger
        ]
    if memories:
        lines += ["", "## What you remember about this user"]
        lines += [f"- {item['fact']}" for item in memories[:12]]
        lines += ["- Use `remember` when the user states a durable preference or fact worth keeping; do not store transient chatter."]
    lines += ["", f"Current date: {datetime.now(UTC).strftime('%Y-%m-%d')} (UTC)."]
    return "\n".join(lines)


class _SubagentStore:
    """Turn-store view for a subagent run.

    A subagent must never write into the main assistant message, so this view
    accumulates its own text and reports its activity under its own agent id.
    """

    def __init__(self, session: "TurnSession", turn: dict[str, object], agent_id: str, name: str, task: str) -> None:
        self._session = session
        self._turn = turn
        self.agent_id = agent_id
        self.name = name
        self._task = task
        self.text: list[str] = []
        self.failed = False
        self.error_code: str | None = None

    def load(self, turn_id: str) -> dict[str, object]:
        return self._turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": self._task}]

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.failed, self.error_code = failed, code

    def record_usage(self, turn: dict[str, object], *, input_tokens: int | None, output_tokens: int | None, total_tokens: int | None) -> None:
        self._session.record_usage(
            turn,
            agent_id=self.agent_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self._session.emit_lifecycle(turn, state, agent_id=self.agent_id, agent_name=self.name, **payload)


class _MainAgentStore:
    """Turn-store view for the main agent.

    It exists so publishing activity is not something the caller has to remember
    to wire: every lifecycle callback from the runtime is turned into a public
    event here, and only then forwarded to the wrapped store for its own
    (technical) projection.
    """

    def __init__(self, session: "TurnSession", inner: object) -> None:
        self._session = session
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self._session.emit_lifecycle(turn, state, **payload)
        forward = getattr(self._inner, "lifecycle", None)
        if callable(forward):
            forward(turn, state, **payload)

    def record_usage(self, turn: dict[str, object], *, input_tokens: int | None, output_tokens: int | None, total_tokens: int | None) -> None:
        self._session.record_usage(
            turn,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


class TurnSession:
    """Builds the runtime for a turn and owns its subagent lifecycle."""

    def __init__(
        self,
        *,
        turn: dict[str, object],
        store,
        agents_store,
        memory_store,
        provider_factory: Callable[[], object],
        workspace_root: Path | str = "data/workspaces",
        cancelled: Callable[[Mapping[str, object]], bool] | None = None,
        limits: AgenticLimits | None = None,
        enable_subagents: bool = True,
        skills=None,
        skill_load_recorder: Callable[[object], None] | None = None,
    ) -> None:
        self.turn = turn
        self.store = store
        self.agents_store = agents_store
        self.memory = memory_store
        self.provider_factory = provider_factory
        self.cancelled = cancelled or (lambda _turn: False)
        self.limits = limits or AgenticLimits(deadline=timedelta(seconds=300), max_iterations=12, max_actions=24)
        self.enable_subagents = enable_subagents
        self.skills = skills
        self.skill_load_recorder = skill_load_recorder
        self.workspace = ConversationWorkspace(workspace_root, resolve_effective_workspace_id(turn))
        self._subagent_runs = 0
        self.main_agent_id = store.main_agent_id(turn) if hasattr(store, "main_agent_id") else str(turn.get("agent_id", "agent:main"))

    # -- activity -------------------------------------------------------

    def _record(self, event_type: AgentActivityEventType, summary: str, payload: dict[str, object] | None = None, *, agent_id: str | None = None, parent_agent_id: str | None = None) -> None:
        recorder = getattr(self.store, "record", None)
        if recorder is None:
            return
        recorder(self.turn, event_type, summary, payload or {}, agent_id=agent_id or self.main_agent_id, parent_agent_id=parent_agent_id)

    def record_usage(self, turn: dict[str, object], *, input_tokens: int | None, output_tokens: int | None, total_tokens: int | None, agent_id: str | None = None) -> None:
        self.agents_store.record_usage(
            agent_id or self.main_agent_id,
            str(turn.get("provider") or ""),
            str(turn.get("model_id") or ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def emit_lifecycle(self, turn: dict[str, object], state: str, *, agent_id: str | None = None, agent_name: str | None = None, **payload: object) -> None:
        """Translate a runtime lifecycle callback into a public activity event."""
        actor = agent_id or self.main_agent_id
        base: dict[str, object] = {"state": state}
        if agent_name:
            base["agent_name"] = agent_name
        if state == "tool_started":
            name = str(payload.get("tool_name") or "tool")
            self._record(AgentActivityEventType.TOOL_STARTED, f"Executando {name}", {
                **base, "tool_name": name, "invocation_id": str(payload.get("invocation_id") or ""),
            }, agent_id=actor)
            return
        if state == "tool_finished":
            name = str(payload.get("tool_name") or "tool")
            status = str(payload.get("status") or "succeeded")
            extra = dict(payload.get("tool_payload") or {})
            summary = str(payload.get("summary") or f"{name} {status}")
            self._record(AgentActivityEventType.TOOL_FINISHED, summary, {
                **base, **extra, "tool_name": name, "status": status,
                "invocation_id": str(payload.get("invocation_id") or ""),
                "error_code": payload.get("error_code"),
            }, agent_id=actor)
            ledger = getattr(self.store, "record_tool_call", None)
            if callable(ledger):
                try:
                    ledger(
                        self.turn, tool_name=name, arguments=dict(payload.get("tool_arguments") or {}),
                        status=status, summary=summary,
                    )
                except Exception:
                    pass
            if status == "succeeded":
                artifacts = extra.get("artifacts")
                if isinstance(artifacts, list):
                    for artifact in artifacts:
                        if not isinstance(artifact, dict):
                            continue
                        path = artifact.get("path")
                        size_bytes = artifact.get("size_bytes")
                        if not isinstance(path, str) or not path or not isinstance(size_bytes, int) or size_bytes < 0:
                            continue
                        self._record(
                            AgentActivityEventType.ARTIFACT_CREATED,
                            f"Criou {path}",
                            {"path": path, "label": path, "size_bytes": size_bytes, "tool_kind": "artifact"},
                            agent_id=actor,
                        )
            return
        if state == "waiting_tool":
            self._record(AgentActivityEventType.TOOL_REQUESTED, "Preparando ferramentas", {**base, "count": payload.get("count")}, agent_id=actor)
            return
        if state == "retrying":
            self._record(AgentActivityEventType.TURN_STARTED, "Tentando novamente", {**base, "attempt": payload.get("attempt")}, agent_id=actor)
            return
        if state == "failed":
            self._record(AgentActivityEventType.TURN_FAILED, "Falhou", {**base, "error_code": payload.get("code")}, agent_id=actor)
            return
        if state == "cancelled":
            self._record(AgentActivityEventType.TURN_FAILED, "Cancelado", {**base, "error_code": "TURN_CANCELLED"}, agent_id=actor)
            return
        if state == "running" and agent_id is not None:
            self._record(AgentActivityEventType.TURN_STARTED, f"{agent_name or 'Subagente'} trabalhando", base, agent_id=actor)
            return

    # -- subagents ------------------------------------------------------

    def _create_agent(self, name: str, role: str) -> ToolOutcome:
        clean = " ".join(str(name).split())[:60]
        if not clean:
            return ToolOutcome("failed", "Nome de agente inválido", "The agent name must be a non-blank string.", {}, "INVALID_ARGUMENTS")
        record = self.agents_store.create(
            clean,
            str(role),
            parent_agent_id=self.main_agent_id,
            provider=str(self.turn.get("provider") or ""),
            model_id=str(self.turn.get("model_id") or ""),
        )
        self._record(
            AgentActivityEventType.AGENT_CREATED,
            f"Criou o agente {clean}",
            {"agent_name": clean, "role": str(role)[:200], "label": clean, "created": bool(record.get("created", True))},
            agent_id=str(record["agent_id"]), parent_agent_id=self.main_agent_id,
        )
        return ToolOutcome(
            "succeeded", f"Criou o agente {clean}",
            f"Subagent '{clean}' is ready. Send it work with ask_agent(name=\"{clean}\", task=...).",
            {"agent_name": clean, "label": clean, "tool_kind": "agent"},
        )

    def _ask_agent(self, name: str, task: str) -> ToolOutcome:
        clean = " ".join(str(name).split())[:60]
        record = self.agents_store.find(clean)
        if record is None:
            return ToolOutcome("failed", f"Agente {clean} não existe", f"No subagent named '{clean}'. Create it first with create_agent.", {"tool_kind": "agent"}, "AGENT_NOT_FOUND")
        if self._subagent_runs >= MAX_SUBAGENTS_PER_TURN:
            return ToolOutcome("failed", "Limite de subagentes atingido", "The subagent budget for this turn is exhausted; finish the work yourself.", {"tool_kind": "agent"}, "SUBAGENT_LIMIT")
        self._subagent_runs += 1
        agent_id, task_text = str(record["agent_id"]), str(task)
        self._record(
            AgentActivityEventType.AGENT_MESSAGE_SENT,
            f"Enviou uma tarefa para {clean}",
            {"agent_name": clean, "recipient_name": clean, "sender_name": "Main", "label": clean,
             "content": task_text[:PREVIEW_CHARS], "to_agent_id": agent_id, "tool_kind": "agent"},
            agent_id=self.main_agent_id,
        )
        self.agents_store.set_state(agent_id, "working")
        sub_store = _SubagentStore(self, self.turn, agent_id, clean, self._subagent_task(record, task_text))
        subagent_tools = self._toolset(subagents=False)
        runtime = AgenticTurnRuntime(
            store=sub_store,
            provider=self.provider_factory(),
            toolset=subagent_tools,
            system_prompt=self._subagent_prompt(record, task_text, subagent_tools),
            limits=AgenticLimits(deadline=SUBAGENT_DEADLINE, max_iterations=self.limits.max_iterations, max_actions=12),
            cancelled=self.cancelled,
        )
        result = runtime.run(str(self.turn["turn_id"]), turn=self.turn)
        answer = "".join(sub_store.text).strip()
        if result.state != "completed" or not answer:
            reason = result.error_code or ("SUBAGENT_EMPTY_RESPONSE" if not answer else "SUBAGENT_FAILED")
            self.agents_store.set_state(agent_id, "failed")
            self._record(
                AgentActivityEventType.DELEGATION_FAILED, f"{clean} não concluiu a tarefa",
                {"agent_name": clean, "sender_name": clean, "recipient_name": "Main", "label": clean, "error_code": reason},
                agent_id=agent_id, parent_agent_id=self.main_agent_id,
            )
            return ToolOutcome("failed", f"{clean} não concluiu", f"Subagent '{clean}' did not finish ({reason}).", {"agent_name": clean, "tool_kind": "agent"}, reason)
        self.agents_store.set_state(agent_id, "completed")
        self._record(
            AgentActivityEventType.AGENT_MESSAGE_RECEIVED,
            f"Recebeu a resposta de {clean}",
            {"agent_name": clean, "sender_name": clean, "recipient_name": "Main", "label": clean,
             "content": answer[:PREVIEW_CHARS], "from_agent_id": agent_id, "tool_kind": "agent"},
            agent_id=agent_id, parent_agent_id=self.main_agent_id,
        )
        payload: dict[str, object] = {"agent_name": clean, "label": clean, "tool_kind": "agent"}
        if result.budget_exhausted:
            payload["budget_exhausted"] = True
            content = (
                f"{clean} ran out of its action budget before finishing and reports the following "
                f"partial, possibly incomplete, answer:\n\n{answer}"
            )
        else:
            content = f"{clean} reported:\n\n{answer}"
        return ToolOutcome("succeeded", f"Recebeu a resposta de {clean}", content, payload)

    def _subagent_task(self, record: Mapping[str, object], task: str) -> str:
        return f"{task}\n\nWhen you are done, reply with the complete result. The main agent only sees your final message."

    def _subagent_prompt(self, record: Mapping[str, object], task: str, toolset: AgentToolset) -> str:
        prompt = (
            f"You are '{record['name']}', a specialist subagent inside AgentOS. Your role: {record['role']}.\n"
            "You were given one task by the main agent and you cannot see the user's conversation.\n"
            "Use your tools to actually do the work, then reply with the finished result and nothing else.\n"
            "Answer in the same language as the task. Be concise and factual."
        )
        catalog = self._skill_catalog(task, toolset)
        if catalog:
            prompt += "\n\nRelevant procedural Skills are available as metadata only. Load one with use_skill only if it helps:"
            prompt += "".join(f"\n- {item.name} ({item.id}): {item.description}" for item in catalog)
        return prompt

    # -- runtime --------------------------------------------------------

    def _toolset(self, *, subagents: bool) -> AgentToolset:
        return AgentToolset(
            self.workspace,
            memory=self.memory,
            create_agent=self._create_agent if subagents else None,
            delegate=self._ask_agent if subagents else None,
            skills=self.skills,
            skill_load_recorder=self.skill_load_recorder,
        )

    def _skill_catalog(self, task: str, toolset: AgentToolset) -> tuple[object, ...]:
        if self.skills is None:
            return ()
        try:
            from agentos.skills.retrieval import RetrievalQuery

            available = tuple(item.name for item in toolset.definitions() if item.kind != "skill")
            return self.skills.retrieve(RetrievalQuery(text=task, available_tools=available, limit=5)).items
        except Exception:
            # Skills enhance the turn but never become a single point of failure.
            return ()

    def build_runtime(self) -> AgenticTurnRuntime:
        toolset = self._toolset(subagents=self.enable_subagents)
        memories = self.memory.recent(limit=12) if self.memory is not None else []
        agents = self.agents_store.list() if self.agents_store is not None else []
        reader = getattr(self.store, "tool_ledger", None)
        if callable(reader):
            try:
                ledger = tuple(reader(self.turn, limit=20))
            except Exception:
                ledger = ()
        else:
            ledger = ()
        try:
            tree = tuple(f"{item['kind'][:1]} {item['path']}" for item in self.workspace.list_entries(depth=3))[:60]
        except Exception:
            # A prompt enrichment must never be the reason a turn cannot start.
            tree = ()
        try:
            environment = environment_facts()
        except Exception:
            # A prompt enrichment must never be the reason a turn cannot start.
            environment = {}
        history = self.store.history_for_turn(self.turn)
        task = next((str(item.get("content") or "") for item in reversed(history) if item.get("role") == "user"), "")
        prompt = build_system_prompt(
            tool_names=tuple(item.name for item in toolset.definitions()),
            memories=memories,
            agents=agents,
            workspace_hint="Files you create there persist for the whole conversation.",
            subagents_enabled=self.enable_subagents,
            skill_catalog=self._skill_catalog(task, toolset),
            tool_ledger=ledger,
            environment=environment,
            workspace_tree=tree,
        )
        # OmniRoute's public OpenAI-compatible response does not guarantee the
        # selected upstream/provider. Record the requested route only; never
        # infer a fallback or selected model from a gateway-side guess.
        if str(self.turn.get("provider")) == "omniroute":
            self._record(
                AgentActivityEventType.MODEL_ROUTING_STARTED,
                "Selecionando rota",
                {"requested_route": str(self.turn.get("model_id") or ""), "provider": "omniroute"},
            )
        return AgenticTurnRuntime(
            store=_MainAgentStore(self, self.store), provider=self.provider_factory(), toolset=toolset,
            system_prompt=prompt, limits=self.limits, cancelled=self.cancelled,
        )


__all__ = ["MAX_SUBAGENTS_PER_TURN", "ProjectWorkspaceResolutionError", "TurnSession", "build_system_prompt", "environment_facts", "resolve_effective_workspace_id"]

# Subagent Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a delegação valer a pena: subagentes deixam de ser truncados em 1024 tokens, passam a rodar em paralelo e recebem no prompt o mesmo contexto de ambiente e workspace que o agente principal.

**Architecture:** `TurnSession` continua sendo o dono do ciclo de vida do subagente. A mudança é de três naturezas: um limite que estava caindo no default errado, um executor concorrente para as delegações independentes, e reaproveitamento das funções de prompt do Plano 3.

**Tech Stack:** Python 3.12, `concurrent.futures.ThreadPoolExecutor`, `threading.Lock`, pytest.

## Global Constraints

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`.
- `MAX_SUBAGENTS_PER_TURN = 4` continua sendo o teto por turn, inclusive somando delegações paralelas.
- Um subagente nunca pode escrever no `assistant_message` do agente principal — o `_SubagentStore` existe para isso e não deve ser contornado.
- Eventos de atividade continuam sendo emitidos por subagente, com `agent_id` próprio e `parent_agent_id` do principal.
- Todo módulo novo/alterado começa com `from __future__ import annotations`.
- Rodar testes com `uv run pytest <caminho> -v`.

**Depende de:** Plano 1 Task 3 e Task 5 (`list_entries(depth=...)` e o rename para `_build_definitions`), Plano 2 Task 5 (`AgenticLimits.max_context_tokens`) e Plano 3 Task 3 (`environment_facts`). Todos os três já devem estar mesclados antes de começar.

---

### Task 1: Corrigir o orçamento de saída do subagente

**Files:**
- Modify: `src/agentos/agentic/session.py:20-22` (constantes) e `:318-325` (construção do runtime do subagente)
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: `AgenticLimits`
- Produces: constantes `SUBAGENT_MAX_OUTPUT_TOKENS = 4096` e `SUBAGENT_MAX_ACTIONS = 12`; o runtime do subagente passa a receber `max_output_tokens` explicitamente.

**Por quê:** `AgenticLimits(deadline=SUBAGENT_DEADLINE, max_iterations=..., max_actions=12)` omite `max_output_tokens`, então o subagente cai no default `1024` de `AgenticLimits`, enquanto o agente principal roda com `4096`. Um subagente encarregado de redigir um documento é cortado no meio e a delegação volta como falha ou resposta pela metade.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_turn_session.py`:

```python
def test_a_subagent_gets_the_same_output_budget_as_the_main_agent() -> None:
    from agentos.agentic.session import SUBAGENT_MAX_OUTPUT_TOKENS

    assert SUBAGENT_MAX_OUTPUT_TOKENS == 4096


def test_the_subagent_runtime_is_built_with_that_budget(monkeypatch) -> None:
    from agentos.agentic import session as session_module

    captured: list[object] = []
    original = session_module.AgenticTurnRuntime

    class Recording(original):
        def __init__(self, **kwargs):
            captured.append(kwargs["limits"])
            super().__init__(**kwargs)

        def run(self, turn_id, *, turn=None):
            from agentos.agentic.runtime import AgenticRunResult

            self.store.delta(turn or {}, "done")
            self.store.finish(turn or {})
            return AgenticRunResult("completed", 1, 0)

    monkeypatch.setattr(session_module, "AgenticTurnRuntime", Recording)

    outcome = _session_with_one_subagent()._ask_agent("Pesquisador", "faça x")

    assert outcome.status == "succeeded"
    assert captured[0].max_output_tokens == 4096
```

Adicionar o helper no mesmo arquivo, junto dos demais helpers de teste:

```python
def _session_with_one_subagent():
    from pathlib import Path
    from tempfile import mkdtemp

    from agentos.agentic.session import TurnSession

    class AgentsStore:
        def __init__(self) -> None:
            self.records = {"Pesquisador": {"agent_id": "agent-sub", "name": "Pesquisador", "role": "pesquisa"}}
            self.states: list[tuple[str, str]] = []

        def create(self, name, role, **kwargs):
            return {"agent_id": "agent-sub", "name": name, "role": role, "created": True}

        def find(self, name):
            return self.records.get(name)

        def list(self):
            return list(self.records.values())

        def set_state(self, agent_id, state):
            self.states.append((agent_id, state))

        def record_usage(self, *args, **kwargs):
            return None

    class Store:
        def main_agent_id(self, turn):
            return "agent-main"

        def history_for_turn(self, turn):
            return [{"role": "user", "content": "faça x"}]

        def record(self, *args, **kwargs):
            return None

    turn = {"turn_id": "turn-1", "conversation_id": "conversation_1", "user_id": "user-1", "provider": "openrouter", "model_id": "m", "execution_id": "execution-1"}
    return TurnSession(
        turn=turn, store=Store(), agents_store=AgentsStore(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=Path(mkdtemp()),
    )
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py -k "output_budget or that_budget" -v`
Expected: FAIL — `ImportError: cannot import name 'SUBAGENT_MAX_OUTPUT_TOKENS'`

- [ ] **Step 3: Implementar**

Em `src/agentos/agentic/session.py`, substituir o bloco de constantes do topo por:

```python
SUBAGENT_DEADLINE = timedelta(seconds=180)
MAX_SUBAGENTS_PER_TURN = 4
PREVIEW_CHARS = 400
# A subagent writes the deliverable the main agent will hand to the user, so it
# needs the same output budget; inheriting the dataclass default silently cut
# every long answer at 1024 tokens.
SUBAGENT_MAX_OUTPUT_TOKENS = 4096
SUBAGENT_MAX_ACTIONS = 12
```

E, em `_ask_agent`, substituir a construção dos limites por:

```python
            limits=AgenticLimits(
                deadline=SUBAGENT_DEADLINE,
                max_iterations=self.limits.max_iterations,
                max_actions=SUBAGENT_MAX_ACTIONS,
                max_output_tokens=SUBAGENT_MAX_OUTPUT_TOKENS,
                max_context_tokens=self.limits.max_context_tokens,
            ),
```

Acrescentar as duas constantes ao `__all__` do módulo.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_turn_session.py
git commit -m "fix(agentic): give subagents the main agent's output budget"
```

---

### Task 2: Delegação paralela

**Files:**
- Modify: `src/agentos/agentic/session.py:169-200` (`__init__`), `:299-345` (`_ask_agent`), `:365-373` (`_toolset`)
- Modify: `src/agentos/agentic/agent_tools.py` (`AgentToolset.__init__`, `definitions`, handler novo)
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: `TurnSession._ask_agent`
- Produces:
  - `TurnSession._ask_agents(requests: list[Mapping[str, str]]) -> ToolOutcome` — executa cada `{name, task}` concorrentemente e devolve um relatório único
  - tool `ask_agents` com argumento `{tasks: [{name, task}]}`; a tool `ask_agent` de tarefa única permanece
  - `TurnSession._subagent_lock: threading.Lock` protege `_subagent_runs`

**Por quê:** `_ask_agent` roda o subagente inline. Quatro delegações são quatro esperas em fila, de até 180s cada — o oposto do motivo de existir um subagente.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_turn_session.py`:

```python
def test_two_delegations_run_at_the_same_time(monkeypatch) -> None:
    import threading

    from agentos.agentic import session as session_module

    barrier = threading.Barrier(2, timeout=5)
    original = session_module.AgenticTurnRuntime

    class Concurrent(original):
        def run(self, turn_id, *, turn=None):
            from agentos.agentic.runtime import AgenticRunResult

            barrier.wait()
            self.store.delta(turn or {}, "resultado")
            self.store.finish(turn or {})
            return AgenticRunResult("completed", 1, 0)

    monkeypatch.setattr(session_module, "AgenticTurnRuntime", Concurrent)

    session = _session_with_two_subagents()
    outcome = session._ask_agents([{"name": "Pesquisador", "task": "a"}, {"name": "Redator", "task": "b"}])

    assert outcome.status == "succeeded"
    assert "Pesquisador" in outcome.content and "Redator" in outcome.content


def test_the_per_turn_subagent_budget_still_applies_to_a_batch() -> None:
    session = _session_with_two_subagents()
    session._subagent_runs = 4

    outcome = session._ask_agents([{"name": "Pesquisador", "task": "a"}])

    assert outcome.status == "failed"
    assert outcome.error_code == "SUBAGENT_LIMIT"
```

Adicionar o helper, ao lado de `_session_with_one_subagent`:

```python
def _session_with_two_subagents():
    session = _session_with_one_subagent()
    session.agents_store.records["Redator"] = {"agent_id": "agent-sub-2", "name": "Redator", "role": "redação"}
    return session
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py -k "same_time or budget_still" -v`
Expected: FAIL — `AttributeError: 'TurnSession' object has no attribute '_ask_agents'`

- [ ] **Step 3: Proteger o contador**

Em `src/agentos/agentic/session.py`, acrescentar aos imports do topo:

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
```

No fim do `__init__` de `TurnSession`, acrescentar:

```python
        self._subagent_lock = Lock()
```

E, em `_ask_agent`, substituir o par de linhas do orçamento:

```python
        if self._subagent_runs >= MAX_SUBAGENTS_PER_TURN:
            return ToolOutcome("failed", "Limite de subagentes atingido", "The subagent budget for this turn is exhausted; finish the work yourself.", {"tool_kind": "agent"}, "SUBAGENT_LIMIT")
        self._subagent_runs += 1
```

por:

```python
        with self._subagent_lock:
            if self._subagent_runs >= MAX_SUBAGENTS_PER_TURN:
                return ToolOutcome("failed", "Limite de subagentes atingido", "The subagent budget for this turn is exhausted; finish the work yourself.", {"tool_kind": "agent"}, "SUBAGENT_LIMIT")
            self._subagent_runs += 1
```

- [ ] **Step 4: Implementar a delegação em lote**

Em `src/agentos/agentic/session.py`, logo depois de `_ask_agent`:

```python
    def _ask_agents(self, requests: list[Mapping[str, str]]) -> ToolOutcome:
        """Run several independent delegations at once.

        Each subagent already has its own store view and its own agent id, so
        the only shared mutable state is the per-turn budget, which the lock in
        ``_ask_agent`` guards.
        """
        if not isinstance(requests, list) or not requests:
            return ToolOutcome("failed", "Nenhuma tarefa informada", "Provide a non-empty tasks array of {name, task} objects.", {"tool_kind": "agent"}, "INVALID_ARGUMENTS")
        pending: list[tuple[str, str]] = []
        for index, item in enumerate(requests):
            if not isinstance(item, Mapping) or not str(item.get("name") or "").strip() or not str(item.get("task") or "").strip():
                return ToolOutcome("failed", "Tarefa inválida", f"tasks[{index}] must be an object with a non-blank name and task.", {"tool_kind": "agent"}, "INVALID_ARGUMENTS")
            pending.append((str(item["name"]), str(item["task"])))
        if len(pending) == 1:
            return self._ask_agent(*pending[0])
        with ThreadPoolExecutor(max_workers=min(len(pending), MAX_SUBAGENTS_PER_TURN)) as pool:
            outcomes = list(pool.map(lambda entry: self._ask_agent(entry[0], entry[1]), pending))
        succeeded = [outcome for outcome in outcomes if outcome.status == "succeeded"]
        body = "\n\n---\n\n".join(f"{name}:\n{outcome.content}" for (name, _), outcome in zip(pending, outcomes))
        status = "succeeded" if succeeded else "failed"
        return ToolOutcome(
            status,
            f"{len(succeeded)}/{len(outcomes)} subagentes concluíram",
            body,
            {"tool_kind": "agent", "label": ", ".join(name for name, _ in pending)[:120], "requested": len(outcomes), "succeeded": len(succeeded)},
            None if succeeded else "SUBAGENT_LIMIT" if all(item.error_code == "SUBAGENT_LIMIT" for item in outcomes) else "SUBAGENT_FAILED",
        )
```

- [ ] **Step 5: Expor a tool**

Em `src/agentos/agentic/agent_tools.py`, acrescentar o parâmetro ao `__init__` de `AgentToolset`, junto de `delegate`:

```python
        delegate_batch: Callable[[list[Mapping[str, Any]]], ToolOutcome] | None = None,
```

e, no corpo:

```python
        self._delegate_batch = delegate_batch
```

Em `_build_definitions`, logo depois do bloco `if self._delegate is not None:`:

```python
        if self._delegate_batch is not None:
            items.append(ToolDefinition(
                "ask_agents",
                "Send tasks to several subagents at once and wait for all of them. Use this instead of calling ask_agent repeatedly when the tasks do not depend on each other.",
                _schema({
                    "tasks": {
                        "type": "array",
                        "items": _schema({"name": _TEXT, "task": {**_TEXT, "description": "The complete instruction; the subagent cannot see this conversation."}}, ("name", "task")),
                    },
                }, ("tasks",)),
                self.ask_agents, "agent",
            ))
```

E o handler, logo depois de `ask_agent`:

```python
    def ask_agents(self, tasks: list[Mapping[str, Any]]) -> ToolOutcome:
        if self._delegate_batch is None:
            raise AgentToolError("Subagents are not available.")
        return self._delegate_batch(list(tasks))
```

Em `src/agentos/agentic/session.py`, em `_toolset`, acrescentar:

```python
            delegate_batch=self._ask_agents if subagents else None,
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 7: Atualizar a orientação do prompt**

Em `build_system_prompt`, no bloco `if subagents_enabled:`, substituir por:

```python
    if subagents_enabled:
        lines += [
            "",
            "## Subagents",
            "- For a large task with a distinct, self-contained part, create a specialist with `create_agent` and hand it that part with `ask_agent`.",
            "- When two or more delegated parts do not depend on each other, send them together with `ask_agents`; they run at the same time.",
            "- A subagent cannot see this conversation. Put everything it needs in the task text.",
            "- Do not create a subagent for something you can finish yourself in a step or two.",
        ]
```

- [ ] **Step 8: Rodar a suíte e commit**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

```bash
git add src/agentos/agentic/session.py src/agentos/agentic/agent_tools.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(agentic): run independent delegations in parallel"
```

---

### Task 3: Contexto de ambiente e workspace para o subagente

**Files:**
- Modify: `src/agentos/agentic/session.py:350-361` (`_subagent_prompt`)
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: `environment_facts()` (Plano 3 Task 3), `ConversationWorkspace.list_entries(depth=...)` (Plano 1 Task 3)
- Produces: `_subagent_prompt` passa a descrever o workspace compartilhado e o shell real

**Por quê:** o subagente recebe um `AgentToolset` apontado para o **mesmo** workspace do agente principal, mas o prompt dele não diz isso nem qual é o shell. Ele escreve caminhos absolutos, erra sintaxe de comando e recria arquivos que já existem.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_turn_session.py`:

```python
def test_the_subagent_prompt_describes_the_shared_workspace_and_shell() -> None:
    session = _session_with_one_subagent()
    session.workspace.write_text("report.md", "draft\n")
    toolset = session._toolset(subagents=False)

    prompt = session._subagent_prompt({"name": "Pesquisador", "role": "pesquisa"}, "escreva o resumo", toolset)

    assert "report.md" in prompt
    assert "run_command executes through" in prompt
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py -k shared_workspace_and_shell -v`
Expected: FAIL — `assert "report.md" in prompt`

- [ ] **Step 3: Implementar**

Em `src/agentos/agentic/session.py`, substituir `_subagent_prompt` por:

```python
    def _subagent_prompt(self, record: Mapping[str, object], task: str, toolset: AgentToolset) -> str:
        prompt = (
            f"You are '{record['name']}', a specialist subagent inside Orin. Your role: {record['role']}.\n"
            "You were given one task by the main agent and you cannot see the user's conversation.\n"
            "Use your tools to actually do the work, then reply with the finished result and nothing else.\n"
            "Answer in the same language as the task. Be concise and factual.\n"
            "Request every independent tool call in the same response instead of one at a time."
        )
        environment = environment_facts()
        prompt += (
            "\n\n## Workspace and environment\n"
            "- You share one working directory with the main agent. All paths are relative to it; do not use absolute paths.\n"
            f"- Operating system: {environment['os']}.\n"
            f"- run_command executes through: {environment['shell']} — use that shell's syntax.\n"
            f"- Python: {environment['python']}. Also on PATH: {environment['available']}."
        )
        try:
            tree = [f"{item['kind'][:1]} {item['path']}" for item in self.workspace.list_entries(depth=3)][:40]
        except Exception:
            # Prompt enrichment must never be why a delegation cannot start.
            tree = []
        if tree:
            prompt += "\n- It currently contains:\n" + "\n".join(f"  {line}" for line in tree)
        else:
            prompt += "\n- It is currently empty."
        catalog = self._skill_catalog(task, toolset)
        if catalog:
            prompt += "\n\nRelevant procedural Skills are available as metadata only. Load one with use_skill only if it helps:"
            prompt += "".join(f"\n- {item.name} ({item.id}): {item.description}" for item in catalog)
        return prompt
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(agentic): give subagents the workspace and environment context"
```

---

## Verificação final do plano

- [ ] `uv run pytest tests/unit/agentic tests/unit/workers -v` — PASS
- [ ] Conferir no diff que `_SubagentStore.delta` continua sendo o único destino do texto do subagente
- [ ] Conferir que `MAX_SUBAGENTS_PER_TURN` continua limitando o total por turn, inclusive em lote

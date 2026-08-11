# Visão geral: modelo e uso de tokens por agente Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir provider, modelo e tokens de cada agente selecionado, além do total de tokens de uma conversa.

**Architecture:** Persistir um snapshot de provider/modelo ao criar subagentes e um agregado de tokens por `conversation_id` e `agent_id`. O runtime encaminha eventos de uso para o armazenamento do agente executor; o endpoint de overview agrega esses dados e o frontend renderiza o resumo e o painel do agente selecionado.

**Tech Stack:** Python 3.12, SQLAlchemy/Alembic, FastAPI, React, TypeScript, Vitest, pytest.

## Global Constraints

- Não alterar ou incluir nos commits mudanças de trabalho já existentes fora deste escopo.
- Exibir `indisponível` para telemetria ausente; nunca converter ausência para `0`.
- Usar contagens confirmadas enviadas pelo provedor; não estimar tokens.
- Preservar a leitura de conversas e agentes existentes, sem backfill obrigatório.
- Manter o agente principal implícito, usando o provider/modelo da conversa como snapshot.

---

### Task 1: Persistir metadados e uso por agente

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py`
- Modify: `src/agentos/persistence/postgres/conversation_agents.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0025_conversation_agent_usage.py`
- Test: `tests/unit/conversations/test_conversation_agent_store.py`

**Interfaces:**
- Produces: `ConversationAgentStore.create(name, role, parent_agent_id, provider, model_id)`.
- Produces: `ConversationAgentStore.record_usage(agent_id, provider, model_id, input_tokens, output_tokens, total_tokens)`.
- Produces: `ConversationAgentStore.usage_by_agent()` returning totals and `usage_reported` keyed by agent id.

- [ ] **Step 1: Write the failing persistence tests**

```python
def test_create_persists_provider_and_model_snapshot(engine):
    store = ConversationAgentStore(engine, conversation_id="chat-1", user_id="user-1")
    agent = store.create("Researcher", "Research", parent_agent_id="agent:chat-1:main", provider="openrouter", model_id="anthropic/claude")
    assert agent["provider"] == "openrouter"
    assert agent["model_id"] == "anthropic/claude"

def test_usage_accumulates_per_agent_and_keeps_unknown_usage_unreported(engine):
    store = ConversationAgentStore(engine, conversation_id="chat-1", user_id="user-1")
    store.record_usage("agent:chat-1:main", "openrouter", "model-a", input_tokens=11, output_tokens=7, total_tokens=18)
    store.record_usage("agent:child", "openrouter", "model-a", input_tokens=None, output_tokens=None, total_tokens=None)
    assert store.usage_by_agent()["agent:chat-1:main"]["total_tokens"] == 18
    assert store.usage_by_agent()["agent:child"]["usage_reported"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/conversations/test_conversation_agent_store.py -q`

Expected: FAIL because the model snapshot and usage methods do not exist.

- [ ] **Step 3: Write minimal implementation**

Add nullable `provider` and `model_id` columns to `conversation_agents`. Add a `conversation_agent_usage` table with `conversation_id`, `agent_id`, `user_id`, provider/model snapshot, nullable input/output/total counters, a non-null `usage_reported` boolean, and `updated_at`. The Alembic revision creates the table and conversation index. In one transaction, the store must add only provided token values and set `usage_reported` only when it receives a numeric token value.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/conversations/test_conversation_agent_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/conversation_agents.py src/agentos/persistence/postgres/migrations/versions/0025_conversation_agent_usage.py tests/unit/conversations/test_conversation_agent_store.py
git commit -m "feat(conversations): persist agent model and usage"
```

### Task 2: Registrar uso pelo runtime e expor o contrato da visão geral

**Files:**
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/conversations/chat.py`
- Test: `tests/unit/agentic/test_turn_session.py`
- Test: `tests/unit/conversations/test_chat_store.py`

**Interfaces:**
- Consumes: `ConversationAgentStore.record_usage` e `usage_by_agent` da Task 1.
- Produces: `ConversationOverview.agents[*].provider`, `.model_id`, `.token_usage`; `ConversationOverview.token_usage`.

- [ ] **Step 1: Write the failing runtime and overview tests**

```python
def test_subagent_usage_is_recorded_for_the_subagent(session_with_usage_reporting_provider):
    session = session_with_usage_reporting_provider
    session._create_agent("Researcher", "Research")
    session._ask_agent("Researcher", "Find the facts")
    usage = session.agents_store.usage_by_agent()[session.agents_store.agent_id_for("Researcher")]
    assert usage["total_tokens"] == 18

def test_overview_returns_total_and_per_agent_usage(chat_store):
    overview = chat_store.overview("chat-1", "user-1")
    child = next(agent for agent in overview["agents"] if agent["name"] == "Researcher")
    assert overview["token_usage"]["total_tokens"] == 31
    assert child["token_usage"]["total_tokens"] == 18
    assert child["provider"] == "openrouter"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py tests/unit/conversations/test_chat_store.py -q`

Expected: FAIL because runtime usage is only local and overview has no per-agent metadata.

- [ ] **Step 3: Write minimal implementation**

When `AgenticTurnRuntime` receives a `StreamKind.USAGE` event, call a storage method that carries input, output, and total tokens. The main and subagent storage adapters forward that report with their own agent IDs. `TurnSession._create_agent` passes the current turn's provider/model to the agent store. `PostgresChatStore.overview` joins usage to the main and child agents, provides unavailable usage for missing records, and sums only known totals.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py tests/unit/conversations/test_chat_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py src/agentos/agentic/session.py src/agentos/conversations/chat.py tests/unit/agentic/test_turn_session.py tests/unit/conversations/test_chat_store.py
git commit -m "feat(overview): expose agent model and token usage"
```

### Task 3: Render total e painel de detalhes no frontend

**Files:**
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/features/conversations/activityTypes.ts`
- Modify: `frontend/src/features/overview/OverviewPanel.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/tests/unit/OverviewPanel.test.tsx`

**Interfaces:**
- Consumes: overview API fields added in Task 2.
- Produces: total de tokens na visão geral; painel acessível para o agente selecionado.

- [ ] **Step 1: Write the failing UI test**

```tsx
it("shows the child model and its tokens only after the child is selected", async () => {
  render(<OverviewPanel conversationId="chat-1" client={client} liveEvents={[]} onClose={vi.fn()} />)
  await screen.findByText("31 tokens")
  expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: /Researcher/ }))
  const details = screen.getByRole("region", { name: /Detalhes de Researcher/ })
  expect(details).toHaveTextContent("anthropic/claude")
  expect(details).toHaveTextContent("18 tokens")
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix frontend -- --run tests/unit/OverviewPanel.test.tsx`

Expected: FAIL because the detail panel and token data are absent.

- [ ] **Step 3: Write minimal implementation**

Add defensive `TokenUsage` API normalization. Project provider, model, and token usage into overview nodes. Add `Tokens gastos` to the overview stats with a formatter that returns `indisponível` for unknown usage. Render a selected-agent region below the map with provider, model, input tokens, output tokens, and total tokens.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test --prefix frontend -- --run tests/unit/OverviewPanel.test.tsx`

Run: `npm test --prefix frontend -- --run`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/conversations.ts frontend/src/features/conversations/activityTypes.ts frontend/src/features/overview/OverviewPanel.tsx frontend/src/styles/agentos.css frontend/tests/unit/OverviewPanel.test.tsx
git commit -m "feat(overview): show selected agent model and tokens"
```

### Task 4: Aplicar migração e verificar integração

**Files:**
- Modify: only files identified by a failing verification in Tasks 1–3.

**Interfaces:**
- Consumes: migrations and overview contract from Tasks 1–3.
- Produces: banco de desenvolvimento atualizado e verificação final registrada.

- [ ] **Step 1: Verify migration configuration and current revision**

Run: `uv run alembic current`

- [ ] **Step 2: Apply the migration**

Run: `uv run alembic upgrade head`

- [ ] **Step 3: Run backend verification**

Run: `uv run pytest tests/unit/conversations/test_conversation_agent_store.py tests/unit/agentic/test_turn_session.py tests/unit/conversations/test_chat_store.py tests/unit/api/test_api_asgi.py -q`

Expected: PASS.

- [ ] **Step 4: Run frontend verification**

Run: `npm run build --prefix frontend`

Run: `npm test --prefix frontend -- --run`

Expected: PASS.

- [ ] **Step 5: Restart only an active AgentOS server**

Identify the exact process listening on the documented AgentOS port. Stop only that process, start it with the repository's documented command, and request its health endpoint. If no server is running, do not start one merely for this task.

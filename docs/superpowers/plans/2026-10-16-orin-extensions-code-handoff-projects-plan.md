# Plano de Implementação: Extensões (Plugins/MCPs/Skills), Handoff Chat → Orin Code e Gestão de Projetos/Workspaces

> **For agentic workers:** REQUIRED: Use `subagent-driven-development` (if subagents available) or `executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o Orin Chat e o Orin Code instalem/configurem plugins, MCPs e skills solicitados pelo usuário; que o Orin Chat sugira iniciar sessões no Orin Code via card interativo (`CodeSessionProposalCard`); e que tanto o usuário (via UI) quanto o agente (via tools) possam enviar a sessão atual para um projeto ou criar um projeto a partir do workspace atual para continuar em outros chats/codes.

**Architecture:**
1. **Extensões em Chat & Code:** Conectar `mcp_service` em `TurnSession` e `AgentToolset`; publicar tools de Skills, MCPs e Plugins em `PhaseController` (`ORIENT`/`EXECUTE`) e em `CODE_MODE_CORE_TOOLS`; renderizar `McpApprovalCard` e `PluginApprovalCard` também na timeline de sessões `code-mode:`.
2. **Handoff Chat → Orin Code (`propose_code_session`):** Nova tool `propose_code_session` exclusiva para conversas `mode="chat"`, emitindo `code_session_proposal` + `wait_for_user=True`, e novo componente `CodeSessionProposalCard.tsx` com edição de pasta, provider/modelo, modo de permissão e prompt, navegando para a nova sessão Code ao aceitar ou retomando o Chat ao negar.
3. **Projetos, Workspaces e Continuidade entre Sessões:** Novos métodos `PostgresChatStore.set_project` e `read_project_conversation`, sincronização automática de `workspace_roots` e migração segura de arquivos do workspace gerenciado (scratch) para o projeto, novos endpoints `PATCH /v1/conversations/{id}/project`, `PUT/DELETE /v1/projects/{id}/workspace` e extensão de `POST /v1/projects`, novas tools de projeto (`list_projects`, `create_project_from_session`, `move_session_to_project`, `read_project_conversation`) e controles de UI no header da conversa (`ChatPage.tsx`), sidebar (`WorkspaceNavigation.tsx`) e página do projeto (`ProjectPage.tsx`).

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy, Pytest, React 19, TypeScript, Vite, Vitest, Testing Library, Playwright.

**Spec de Referência:** `docs/superpowers/specs/2026-10-16-orin-extensions-code-handoff-projects-design.md`

---

## Chunk 1: Funcionalidade 1 — Instalação de Plugins, MCPs e Skills no Chat e no Orin Code

### Task 1.1: Conectar `mcp_service` em `TurnSession` e expor tools de Skill/MCP/Plugin em Chat (`PhaseController`) e Code (`CODE_MODE_CORE_TOOLS`)

**Files:**
- Modify: `src/agentos/agentic/session.py:692-760, 1567-1615`
- Modify: `src/agentos/workers/chat.py:1107-1143`
- Modify: `src/agentos/agentic/phases.py:50-98`
- Modify: `src/agentos/agentic/runtime.py:44-54`
- Modify: `src/agentos/code_mode/prompt.py`
- Test: `tests/unit/agentic/test_extension_tools_exposure.py` (novo)

- [ ] **Step 1 (RED):** Criar `tests/unit/agentic/test_extension_tools_exposure.py` testando:
  1. Que `TurnSession` repassa `mcp_service` e `mcp_user_id` para `AgentToolset`, registrando `list_mcp_catalog`, `list_mcp_servers`, `configure_mcp` e `test_mcp_server`.
  2. Que `PhaseController` em `Phase.ORIENT` e `Phase.EXECUTE` publica `create_skill`, `edit_skill`, `search_skills`, `list_skills`, `read_skill_resource`, `use_skill`, `list_mcp_catalog`, `list_mcp_servers`, `configure_mcp`, `test_mcp_server`, `search_plugin`, `inspect_plugin`, `install_plugin`, `list_plugins`, `uninstall_plugin`.
  3. Que `CODE_MODE_CORE_TOOLS` inclui todas essas tools de Skill, MCP e Plugin no modo Code.
- [ ] **Step 2:** Rodar `uv run pytest tests/unit/agentic/test_extension_tools_exposure.py -v` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Em `src/agentos/agentic/session.py`: adicionar parâmetro `mcp_service=None` em `TurnSession.__init__`, armazenar `self.mcp_service = mcp_service`, e passar `mcp_service=self.mcp_service` e `mcp_user_id=str(self.turn.get("user_id") or "") or None` em `TurnSession._toolset`.
  - Em `src/agentos/workers/chat.py:1139`: passar `mcp_service=mcp_service` ao instanciar `TurnSession`.
  - Em `src/agentos/agentic/phases.py`: definir `_SKILL_TOOLS` e `_EXTENSION_TOOLS`, incluí-las em `PHASE_TOOLS[Phase.ORIENT]` e `PHASE_TOOLS[Phase.EXECUTE]`, e adicionar `"skills": _SKILL_TOOLS` em `TOOLKIT_TOOLS`.
  - Em `src/agentos/agentic/runtime.py`: adicionar todas as tools de skill, MCP e plugin em `CODE_MODE_CORE_TOOLS`.
  - Em `src/agentos/code_mode/prompt.py`: adicionar instrução concisa sobre uso de `configure_mcp`, `install_plugin` e `create_skill`/`edit_skill` quando o usuário pedir instalação/criação de MCP, plugin ou skill.
- [ ] **Step 4:** Rodar `uv run pytest tests/unit/agentic/test_extension_tools_exposure.py -v` e suites existentes (`uv run pytest tests/unit/agentic/ -q`) até passar (GREEN).

### Task 1.2: Renderizar `McpApprovalCard` e `PluginApprovalCard` na timeline do Orin Code (`TurnTimeline.tsx` & `ChatPage.tsx`)

**Files:**
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx:410-445, 704-778`
- Modify: `frontend/src/features/conversations/ChatPage.tsx:705-720`
- Test: `frontend/src/features/conversations/__tests__/TurnTimelineExtensions.test.tsx` (novo)

- [ ] **Step 1 (RED):** Criar `frontend/src/features/conversations/__tests__/TurnTimelineExtensions.test.tsx` verificando que:
  1. Quando um turno em modo `code` emite um evento `tool.finished` com `mcpApproval` (`configure_mcp`) ou `pluginApproval` (`install_plugin`), `TurnTimeline` renderiza `McpApprovalCard` / `PluginApprovalCard` interativos com botões de aprovar/recusar funcionando.
  2. `openQuestionTurnIds` mantém o card ativo enquanto o turno aguarda decisão do usuário.
- [ ] **Step 2:** Rodar `npm --prefix frontend test -- --run src/features/conversations/__tests__/TurnTimelineExtensions.test.tsx` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Em `TurnTimeline.tsx`: garantir que `isCodeToolEvent` retorne `false` quando `event.mcpApproval || event.pluginApproval || event.questions || event.codeSessionProposal` estiver presente; e no loop de `group.id.startsWith('code-mode:')`, quando um evento não for `isCodeToolEvent`, fazer `flushCodeToolBatch()` e renderizar via `renderActivityGroup({ ...group, count: 1, events: [event] }, ...)`.
  - Em `ChatPage.tsx`: incluir `Boolean(event.mcpApproval) || Boolean(event.pluginApproval) || Boolean(event.questions) || Boolean(event.codeSessionProposal)` na condição de `openQuestionTurnIds`.
- [ ] **Step 4:** Rodar `npm --prefix frontend test -- --run src/features/conversations/__tests__/TurnTimelineExtensions.test.tsx` até passar (GREEN).

---

## Chunk 2: Funcionalidade 2 — Orin Chat Sugerindo Iniciar Sessão no Orin Code

### Task 2.1: Tool `propose_code_session` no Backend (`AgentToolset` + `TurnSession`)

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/agentic/prompts.py`
- Test: `tests/unit/agentic/test_propose_code_session_tool.py` (novo)

- [ ] **Step 1 (RED):** Criar `tests/unit/agentic/test_propose_code_session_tool.py` testando:
  1. `propose_code_session` é registrada apenas quando `code_mode_active=False` (modo Chat) e nunca quando `code_mode_active=True`.
  2. Chamar `propose_code_session(title="...", prompt="...", workspace_path="...", provider="...", model_id="...", permission_mode="accept_edits")` valida inputs (rejeita `title` ou `prompt` vazios, valida `permission_mode` em `{"accept_edits", "plan_mode", "ask_permissions"}`), preenche defaults a partir do turno/conversa atual (`provider`, `model_id`, `workspace_root_path`, `project_id`) e retorna `ToolOutcome` com `wait_for_user=True` e `payload["code_session_proposal"]`.
  3. Tentativa de enviar `permission_mode="full_autonomy"` ou inválido em `propose_code_session` faz fallback seguro ou levanta `AgentToolError` amigável (Red-Team: o agente nunca pode sugerir `full_autonomy` sem escolha explícita do usuário).
- [ ] **Step 2:** Rodar `uv run pytest tests/unit/agentic/test_propose_code_session_tool.py -v` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Implementar `propose_code_session` em `AgentToolset` (`src/agentos/agentic/agent_tools.py`), passando `default_provider`, `default_model_id`, `default_workspace_path` e `project_id` a partir de `TurnSession._toolset`.
  - Incluir `"propose_code_session"` em `PHASE_TOOLS[Phase.ORIENT]`, `PHASE_TOOLS[Phase.PLAN]` e `PHASE_TOOLS[Phase.EXECUTE]` em `src/agentos/agentic/phases.py`.
  - Adicionar orientação no `SYSTEM_PROMPT` (`src/agentos/agentic/prompts.py`) instruindo o agente no modo Chat a usar `propose_code_session` quando o usuário pedir alterações de código em múltiplos arquivos, execução de testes ou pedir para abrir uma tarefa no Orin Code.
- [ ] **Step 4:** Rodar `uv run pytest tests/unit/agentic/test_propose_code_session_tool.py -v` até passar (GREEN).

### Task 2.2: Componente `CodeSessionProposalCard.tsx` e Integração em `ChatPage.tsx` / `ActivityStream.tsx`

**Files:**
- Modify: `frontend/src/features/conversations/activityTypes.ts`
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/features/conversations/activitySummary.ts`
- Create: `frontend/src/features/conversations/CodeSessionProposalCard.tsx`
- Modify: `frontend/src/features/conversations/ActivityStream.tsx`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Test: `frontend/src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx` (novo)

- [ ] **Step 1 (RED):** Criar `frontend/src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx` testando:
  1. Parsing de `code_session_proposal` em `parseConversationActivityEvent`.
  2. Renderização do `CodeSessionProposalCard` com título, pasta sugerida (editável), seletor de provider/modelo, seletor de `permission_mode` (`accept_edits`, `plan_mode`, `ask_permissions`) e `textarea` com o prompt editável.
  3. Ao editar o prompt/modelo/pasta e clicar em **"Iniciar no Orin Code"**, chama `onAccept` com os valores editados (`{ title, prompt, workspacePath, provider, modelId, permissionMode }`), cria a conversa em modo `code` e navega para a nova sessão.
  4. Ao clicar em **"Continuar aqui no Chat"** (Negar), chama `onDecline` e envia a mensagem de recusa na conversa atual.
- [ ] **Step 2:** Rodar `npm --prefix frontend test -- --run src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Adicionar `CodeSessionProposalRequest` em `activityTypes.ts` e `parseCodeSessionProposalRequest` em `frontend/src/api/conversations.ts`.
  - Atualizar `groupingKey` em `activitySummary.ts` para isolar `event.codeSessionProposal`.
  - Criar `CodeSessionProposalCard.tsx` com suporte a edição de prompt, escolha/inspeção de pasta, seleção de provider/modelo e modo de permissão, mais estado persistido de sessão já iniciada (`"Sessão iniciada · Abrir →"`).
  - Conectar `onCodeSessionAccept` e `onCodeSessionDecline` em `ActivityStream.tsx`, `TurnTimeline.tsx` e `ChatPage.tsx`.
- [ ] **Step 4:** Rodar `npm --prefix frontend test -- --run src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx` até passar (GREEN).

---

## Chunk 3: Funcionalidade 3 — Enviar Sessão para Projeto ou Criar Projeto no Workspace Atual (Backend + Tools + UI)

### Task 3.1: Backend Store & API para Mover Sessão para Projeto, Criar Projeto a partir da Sessão e Sincronizar/Migrar Workspace

**Files:**
- Modify: `src/agentos/conversations/chat.py`
- Modify: `src/agentos/projects/store.py`
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/projects/test_session_project_handoff.py` (novo)

- [ ] **Step 1 (RED):** Criar `tests/unit/projects/test_session_project_handoff.py` cobrindo:
  1. `GET /v1/conversations/{id}` inclui `project_id` e `project_name`.
  2. `PATCH /v1/conversations/{id}/project` move uma conversa standalone (Chat ou Code) para um projeto existente, atualizando `conversations.project_id`, herdando a pasta local da conversa para o projeto (se o projeto ainda não tiver pasta local) e copiando com segurança arquivos criados no workspace gerenciado (scratch) para o workspace do projeto.
  3. Segurança Red-Team na migração de arquivos: symlinks, `.git`, `.orin`, `.env`, `node_modules` e arquivos que já existem no destino **nunca** são copiados/sobrescritos; tentativa de mover conversa ou projeto de outro `user_id` retorna 404.
  4. `POST /v1/projects` aceita `workspace_path`, `workspace_acknowledged_risk` e `source_conversation_id`, criando o projeto, vinculando a pasta e movendo a conversa de origem em uma única operação.
  5. `PUT /v1/projects/{id}/workspace` e `DELETE /v1/projects/{id}/workspace` vinculam/desvinculam a pasta local do projeto, e `GET /v1/projects/sidebar` retorna `workspace` em cada projeto.
- [ ] **Step 2:** Rodar `uv run pytest tests/unit/projects/test_session_project_handoff.py -v` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Implementar `set_project` e `read_project_conversation` em `PostgresChatStore` (`src/agentos/conversations/chat.py`), além de incluir `project_id` e `project_name` em `PostgresChatStore.get()`.
  - Implementar helper seguro `migrate_managed_workspace_files(source_root: Path, target_root: Path) -> list[str]` para copiar arquivos criados no workspace gerenciado da conversa para o workspace do projeto sem sobrescrever nada e ignorando diretórios sensíveis/symlinks.
  - Adicionar `PATCH /v1/conversations/{conversation_id}/project`, `PUT /v1/projects/{project_id}/workspace`, `DELETE /v1/projects/{project_id}/workspace` e estender `POST /v1/projects` e `GET /v1/projects/sidebar` em `src/agentos/api/gateway.py`.
- [ ] **Step 4:** Rodar `uv run pytest tests/unit/projects/test_session_project_handoff.py -v` até passar (GREEN).

### Task 3.2: Agent Tools de Projeto e Continuidade de Contexto entre Sessões (`AgentToolset` & `TurnSession`)

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/workers/chat.py`
- Test: `tests/unit/agentic/test_project_session_tools.py` (novo)

- [ ] **Step 1 (RED):** Criar `tests/unit/agentic/test_project_session_tools.py` testando:
  1. `list_projects()` retorna os projetos do usuário com suas pastas e sessões.
  2. `create_project_from_session(name="...", description="...")` cria o projeto, vincula o workspace atual da conversa ao projeto e move a conversa atual para o projeto.
  3. `move_session_to_project(project_id="...")` move a conversa atual para o projeto informado e sincroniza o workspace.
  4. `read_project_conversation(conversation_id="...")` retorna o histórico de mensagens de outra conversa do usuário/projeto (e bloqueia acesso a conversas de outros usuários).
  5. Disponibilidade dessas 4 tools tanto no modo Chat (`ORIENT`/`EXECUTE`) quanto no modo Code (`CODE_MODE_CORE_TOOLS`).
- [ ] **Step 2:** Rodar `uv run pytest tests/unit/agentic/test_project_session_tools.py -v` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Implementar `list_projects`, `create_project_from_session`, `move_session_to_project` e `read_project_conversation` em `AgentToolset` (`src/agentos/agentic/agent_tools.py`).
  - Passar `project_store` / callbacks de projeto em `ChatWorker._runtime_for` → `TurnSession` → `AgentToolset`.
  - Injetar resumo das sessões irmãs do projeto no `TurnSession.build_runtime` quando `project_id` estiver presente.
  - Expor as 4 tools em `PHASE_TOOLS` (`phases.py`) e em `CODE_MODE_CORE_TOOLS` (`runtime.py`).
- [ ] **Step 4:** Rodar `uv run pytest tests/unit/agentic/test_project_session_tools.py -v` até passar (GREEN).

### Task 3.3: Frontend UI para Mover Sessão para Projeto, Criar Projeto do Workspace Atual e Evoluir `ProjectPage.tsx`

**Files:**
- Modify: `frontend/src/api/projects.ts`
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/features/projects/ProjectPage.tsx`
- Test: `frontend/src/features/projects/__tests__/ProjectSessionManagement.test.tsx` (novo)

- [ ] **Step 1 (RED):** Criar `frontend/src/features/projects/__tests__/ProjectSessionManagement.test.tsx` testando:
  1. No header da conversa (`ChatPage.tsx`), o botão `"Projeto"` permite mover uma conversa standalone para um projeto existente (`PATCH /v1/conversations/:id/project`) ou criar um novo projeto a partir da conversa/pasta atual (`POST /v1/projects` com `source_conversation_id`).
  2. Quando a conversa já pertence a um projeto, o header exibe o pill do projeto com link para `/projects/:projectId` e opção de trocar/remover do projeto.
  3. Na página do projeto (`ProjectPage.tsx`), exibe a pasta do projeto, permite vincular/alterar pasta local e oferece os botões **"+ Novo Chat neste projeto"** e **`>_ Novo Code neste projeto`**.
- [ ] **Step 2:** Rodar `npm --prefix frontend test -- --run src/features/projects/__tests__/ProjectSessionManagement.test.tsx` e confirmar falha (RED).
- [ ] **Step 3 (GREEN):**
  - Atualizar `frontend/src/api/projects.ts` e `frontend/src/api/conversations.ts` com os novos campos (`project_id`, `project_name`, `workspace`) e funções (`moveConversationToProject`, `createProject` com `workspace_path`/`source_conversation_id`, `attachProjectWorkspace`, `detachProjectWorkspace`).
  - Adicionar o controle de Projeto no header de `ChatPage.tsx` (popover para mover para projeto existente ou criar projeto a partir da sessão atual).
  - Atualizar `WorkspaceNavigation.tsx` (opção de pasta no modal "Novo projeto" e atualização reativa quando a conversa muda de projeto).
  - Atualizar `ProjectPage.tsx` com exibição/configuração da pasta compartilhada, badges `Chat` / `>_ Code` nas sessões e botões **"+ Novo Chat neste projeto"** e **`>_ Novo Code neste projeto`**.
- [ ] **Step 4:** Rodar `npm --prefix frontend test -- --run src/features/projects/__tests__/ProjectSessionManagement.test.tsx` até passar (GREEN).

---

## Chunk 4: Verificação Completa Local & Playwright End-to-End

### Task 4.1: Suite Completa de Testes (Backend + Frontend) e Validação Visual no Navegador (Playwright)

- [ ] **Step 1:** Rodar toda a suíte de testes unitários e de integração afetada no backend (`uv run pytest tests/unit/agentic/ tests/unit/projects/ -v`).
- [ ] **Step 2:** Rodar toda a suíte de testes do frontend (`npm --prefix frontend test -- --run`) e build de produção (`npm --prefix frontend run build`).
- [ ] **Step 3:** Subir o servidor local do Orin e validar visualmente no navegador via **Playwright** os 3 fluxos completos:
  1. Instalação/configuração de MCP/Plugin/Skill em Chat e em Orin Code (verificando a renderização dos cards de aprovação).
  2. Card de sugestão **"Iniciar no Orin Code"** (`CodeSessionProposalCard`) no Chat (editando prompt/modelo/pasta e aceitando para abrir a sessão no Orin Code).
  3. Criação de projeto a partir de uma sessão / envio de sessão existente para dentro de um projeto e continuidade na `ProjectPage`.

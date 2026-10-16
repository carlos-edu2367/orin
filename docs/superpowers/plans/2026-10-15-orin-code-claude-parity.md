# Plano de Implementação: Orin Code (100% Paridade com Claude Code v2.1.300) + Redesign UX/UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar o Orin Code para operar com 100% de paridade funcional e visual com o Claude Code v2.1.300 (invariante estrita `mode = "chat" | "code"` na conversa, modo padrão `accept_edits` com ciclo `Shift+Tab`, Plan Mode real via `enter_plan_mode`/`exit_plan_mode`, ferramentas nativas alinhadas + exclusivas do Orin, blocos compactos de ferramentas com diff colorido inline, auto-backgrounding de dev servers e correção de todos os bugs de UX/UI das screenshots).

**Architecture:**
1. **Persistência & Contratos (`conversations.mode` + `permission_mode`):** A tabela `conversations` passa a armazenar `mode` (`'chat' | 'code'`, imutável após criação) e `permission_mode` (`'accept_edits' | 'plan_mode' | 'ask_permissions' | 'full_autonomy'`). Todo turno criado herda `conversation.mode` (eliminando `detect_code_request` no modo `auto`).
2. **Motor & Ferramentas Claude Code v2.1.300 (`TurnSession` + `agent_tools.py`):** Remove a criação forçada de planos fictícios (`.orin/plans/{turn_id}-plan.md`) em todo turno e o loop burocrático de 5 fases. Em `mode == "code"`, usa o loop direto estilo Claude Code com deduplicação de leitura em `read_file`, diff estruturado em `edit_file`/`write_file`, `glob_files`, `search_files` expandido (`Grep`), `notebook_edit`, auto-background de servidores dev em `run_command` (`Bash`), `todo_write` + `task_create`/`task_update`/`task_get`/`task_list`, e `enter_plan_mode`/`exit_plan_mode` com aprovação interativa.
3. **Frontend UX/UI (`Home.tsx`, `WorkspaceNavigation.tsx`, `ChatPage.tsx`, `Composer.tsx`, `TurnTimeline.tsx`, `MarkdownMessage.tsx`):** Seletor `Chat | Code` na Home, botões `"Novo Chat"` e `"Novo Code"` na Sidebar, blocos compactos `CodeToolCallBlock` com diff inline `+N -M`, card `PlanApprovalCard` para `exit_plan_mode`, barra de tarefas `TodoBar`, pílula `Shift+Tab` no Composer, links `workspace://` como pílulas inline (`InlineWorkspaceFilePill`) sem quebrar listas, limpeza imediata do `<AgentPulse />` ao cancelar/concluir, e checkpoint discreto no cabeçalho do turno.

**Tech Stack:** Python 3.12+ (FastAPI, SQLite/Postgres store), TypeScript / React 18 / Vite / TailwindCSS, Vitest, Pytest.

**Spec de Referência:** `docs/superpowers/specs/2026-10-15-orin-code-claude-parity-design.md`

---

## Fase 1: Invariante de Modo da Conversa (`chat` vs `code`) e `permission_mode` no Backend

### Task 1: Schema, Store e Contratos de Conversa (`mode` imutável + `permission_mode`)

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py`
- Modify: `src/agentos/conversations/chat.py`
- Modify: `src/agentos/api/contracts.py`
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/conversations/test_conversation_mode_invariant.py`

- [ ] **Step 1: Escrever testes falhando para a invariante `mode = 'chat' | 'code'` e `permission_mode` por conversa**
  Criar `tests/unit/conversations/test_conversation_mode_invariant.py` testando:
  1. Criar conversa com `code_mode="code"` grava `conversation.mode == "code"` e `permission_mode == "accept_edits"` (default para Code).
  2. Criar conversa com `code_mode="chat"` (ou default) grava `conversation.mode == "chat"` e **não** ativa modo Code mesmo que o texto contenha palavras de programação (`"crie um componente react em typescript"`).
  3. Enviar mensagens subsequentes (`send`) em uma conversa `mode="code"` mantém todos os turnos em `code_mode="code"`, e em uma conversa `mode="chat"` mantém todos os turnos em `code_mode=None` (imutabilidade de modo).
  4. Atualizar `permission_mode` da conversa via store/serviço (`set_permission_mode`) altera entre `'accept_edits'`, `'plan_mode'`, `'ask_permissions'` e `'full_autonomy'`.

- [ ] **Step 2: Rodar pytest para confirmar a falha inicial**
  Run: `.venv\Scripts\python.exe -m pytest tests/unit/conversations/test_conversation_mode_invariant.py -v`

- [ ] **Step 3: Implementar migração em `schema.py` e persistência em `conversations/chat.py` + `api/gateway.py`**
  - Em `src/agentos/persistence/postgres/schema.py`:
    - Adicionar colunas `mode TEXT NOT NULL DEFAULT 'chat'`, `permission_mode TEXT NOT NULL DEFAULT 'ask_permissions'` e `pre_plan_permission_mode TEXT` na tabela `conversations` (com `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS` no bloco de migração e backfill das conversas que já têm turnos com `code_mode = 'code'`).
  - Em `src/agentos/conversations/chat.py`:
    - Em `PostgresChatStore.create` e `InMemoryChatStore.create`: resolver `resolved_mode = "code" if code_mode == "code" else "chat"` (sem chamar `detect_code_request` para trocar modo automaticamente). Definir `permission_mode = "accept_edits" if resolved_mode == "code" else "ask_permissions"`.
    - Em `PostgresChatStore.send` e `InMemoryChatStore.send`: herdar `resolved_code_mode = "code" if conversation.mode == "code" else None` (ignorando tentativa de mudar o modo da conversa existente).
    - Incluir `mode` e `permission_mode` nos retornos de `get()`, `list()` e `create()`.
    - Adicionar método `set_permission_mode(conversation_id: str, user_id: str, permission_mode: str) -> dict[str, object]`.
  - Em `src/agentos/api/gateway.py`:
    - Adicionar rota `PATCH /v1/conversations/{conversation_id}/permission-mode` recebendo `{"permission_mode": "accept_edits" | "plan_mode" | "ask_permissions" | "full_autonomy"}`.
    - Retornar `mode` e `permission_mode` no payload de `POST /v1/conversations`, `GET /v1/conversations` e `GET /v1/conversations/{conversation_id}`.

- [ ] **Step 4: Rodar os testes unitários e de integração de conversas**
  Run: `.venv\Scripts\python.exe -m pytest tests/unit/conversations/test_conversation_mode_invariant.py tests/unit/conversations/ -v`

---

## Fase 2: Motor Orin Code (Fim do Card Fictício de 5 Fases + Plan Mode Real + Paridade de Tools Claude Code v2.1.300)

### Task 2: Remover Criação Forçada de Plano em Todo Turno e Alinhar `enter_plan_mode` / `exit_plan_mode` + Permissões

**Files:**
- Modify: `src/agentos/workers/chat.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/code_mode/prompt.py`
- Test: `tests/unit/code_mode/test_claude_code_plan_and_permissions.py`

- [ ] **Step 1: Escrever testes para o novo ciclo de vida do Orin Code (sem plano dummy automático, `accept_edits` default, e `enter_plan_mode`/`exit_plan_mode` real)**
  Criar `tests/unit/code_mode/test_claude_code_plan_and_permissions.py` verificando:
  1. Um turno em `mode="code"` com `permission_mode="accept_edits"` **não** cria `.orin/plans/{turn_id}-plan.md` automaticamente no início do turno.
  2. Em `permission_mode="accept_edits"`, chamadas a `edit_file` e `write_file` dentro do workspace são auto-aprovadas sem bloquear por confirmação manual.
  3. Em `permission_mode="plan_mode"`, chamadas a `edit_file`/`write_file` em arquivos de código (fora de `.orin/plans/`) são bloqueadas com instrução para escrever apenas no plano `.orin/plans/` e chamar `exit_plan_mode`.
  4. Chamar `enter_plan_mode` muda a sessão para `plan_mode`; chamar `exit_plan_mode` lê o plano em `.orin/plans/`, emite `plan_approval: True` com `plan_content` e retorna `wait_for_user: True`.

- [ ] **Step 2: Implementar mudanças em `workers/chat.py`, `agentic/session.py`, `agent_tools.py` e `code_mode/prompt.py`**
  - Em `src/agentos/workers/chat.py` e `src/agentos/agentic/session.py`:
    - Remover a chamada automática a `_ensure_code_mode_plan()` no início de cada turno em `_hydrate_code_mode` e `build_runtime`.
    - Usar `conversation.permission_mode` (default `accept_edits` no modo Code) para inicializar a autonomia do turno (`accept_edits` -> `code_autonomy`, `plan_mode` -> `plan_mode`, `ask_permissions` -> `approval_required`, `full_autonomy` -> `full_autonomy`).
  - Em `src/agentos/agentic/agent_tools.py`:
    - Atualizar `_exec_enter_plan_mode`: cria diretório `.orin/plans/`, define arquivo alvo `.orin/plans/plan-{turn_id[:8]}.md`, ativa `plan_mode` no runtime e retorna instruções idênticas ao Claude Code (`chunk_0589.js`).
    - Atualizar `_exec_exit_plan_mode`: aceita `plan` opcional ou lê o arquivo `.orin/plans/*.md` mais recente da sessão, persiste a mudança de estado, emite evento/resultado com `plan_approval: True`, `plan_path`, `plan_content`, e `wait_for_user: True` para que o frontend exiba o `PlanApprovalCard`.
  - Em `src/agentos/code_mode/prompt.py`:
    - Atualizar o System Prompt do Orin Code com as diretrizes reais do Claude Code v2.1.300: concisão extrema, uso obrigatório de `read_file`/`edit_file`/`write_file`/`glob_files`/`search_files` no lugar de `cat`/`sed`/`grep` via Bash, invariante read-before-edit, parâmetro `description` em `run_command`, uso de `todo_write`/`task_create` para tarefas multi-etapas, e links de arquivos sempre inline `[nome](workspace://caminho)`.

- [ ] **Step 3: Rodar os testes de Plan Mode e Permissões**
  Run: `.venv\Scripts\python.exe -m pytest tests/unit/code_mode/test_claude_code_plan_and_permissions.py -v`

---

### Task 3: Paridade de Ferramentas Claude Code v2.1.300 (`read_file` dedup, `glob_files`, `search_files` Grep, `notebook_edit`, `task_*`, `run_command` auto-background)

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/background_processes.py`
- Test: `tests/unit/agentic/test_claude_code_tools_parity.py`

- [ ] **Step 1: Escrever testes cobrindo todas as novas capacidades das ferramentas em `tests/unit/agentic/test_claude_code_tools_parity.py`**
  1. `read_file`: segunda leitura do mesmo `path` + `offset` + `limit` sem alteração de hash retorna `unchanged: True` com aviso conciso; após `edit_file`, nova leitura retorna o conteúdo atualizado.
  2. `edit_file` e `write_file`: retornam `structured_patch` (lista de hunks com `oldStart`, `oldLines`, `newStart`, `newLines`, `lines`), `additions` e `deletions`.
  3. `glob_files`: busca rápida por padrão glob (`**/*.py`, `src/**/*.ts`) ordenada por `mtime`.
  4. `search_files` (`Grep`): suporta `output_mode="files_with_matches"`, `output_mode="count"`, `output_mode="content"`, `before_context`, `after_context`, `context_lines`, `multiline`.
  5. `notebook_edit`: edita (`replace`), insere (`insert`) e remove (`delete`) células em arquivos `.ipynb` válidos.
  6. `todo_write` + `task_create`, `task_update`, `task_get`, `task_list`: `todo_write` gera `id` se omitido e aceita `activeForm`; `task_create`/`task_update`/`task_get`/`task_list` manipulam a mesma lista durável de tarefas e emitem `todo_updated`.
  7. `run_command` (`Bash`): aceita `description` (incluído no resultado e no evento de início da tool) e faz **auto-backgrounding** quando um comando dev server emite linha de prontidão (`Local: http://localhost:5173` / `Uvicorn running on http://127.0.0.1:8000`) sem encerrar o processo, retornando em ~2s com `auto_backgrounded: True`, `process_id` e `detected_url`.

- [ ] **Step 2: Implementar as melhorias em `src/agentos/agentic/agent_tools.py` e `background_processes.py`**
  - Implementar deduplicação de leitura inalterada em `_exec_read_file` usando `(norm_path, offset, limit, sha256)`.
  - Adicionar `glob_files` ao registro de ferramentas (`AGENT_TOOLS`) e executor `_exec_glob_files`.
  - Expandir `search_files` (`_exec_search_files`) com `path`, `output_mode`, `before_context`, `after_context`, `context_lines`, `multiline`, `head_limit`.
  - Adicionar `notebook_edit` (`_exec_notebook_edit`) para manipulação estruturada de `.ipynb`.
  - Adicionar `task_create`, `task_update`, `task_get`, `task_list` e flexibilizar `todo_write` (`id` opcional, `activeForm` camelCase ou snake_case).
  - Em `_exec_run_command`: adicionar parâmetro `description` no schema; detectar comandos de dev server (`npm run dev`, `pnpm dev`, `yarn dev`, `vite`, `next dev`, `uvicorn`, `flask run`, `http.server`) ou saída de prontidão HTTP para promover automaticamente ao `BackgroundProcessManager` sem bloquear por 120s.

- [ ] **Step 3: Rodar a suíte de testes das ferramentas**
  Run: `.venv\Scripts\python.exe -m pytest tests/unit/agentic/test_claude_code_tools_parity.py -v`

---

## Fase 3: Redesign UX/UI Frontend (Home, Sidebar, Timeline Claude Code, Diffs Inline e Correção dos 3 Bugs)

### Task 4: Clientes de API, Tipos e Redutor de Eventos no Frontend (`mode`, `permission_mode`, `structured_patch`, `plan_approval`)

**Files:**
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/api/codeMode.ts`
- Modify: `frontend/src/features/conversations/activityTypes.ts`
- Modify: `frontend/src/features/conversations/activityReducer.ts`

- [ ] **Step 1: Atualizar tipos e funções de API**
  - Em `frontend/src/api/conversations.ts`:
    - Adicionar `mode: 'chat' | 'code'` e `permission_mode: CodePermissionMode` ao tipo `Conversation` e aos itens de `listConversations`.
    - Adicionar `updateConversationPermissionMode(client, conversationId, permissionMode)` chamando `PATCH /v1/conversations/${id}/permission-mode`.
    - Em `createConversation`: enviar `code_mode: input.code_mode ?? 'chat'` (sem `'auto'`).
  - Em `frontend/src/api/codeMode.ts`:
    - Ajustar a ordem de `nextCodePermissionMode` para `['accept_edits', 'plan_mode', 'ask_permissions', 'full_autonomy']` e labels idênticos ao Claude Code (`accept edits on`, `plan mode on`, `ask permissions`, `bypass permissions`).
  - Em `frontend/src/features/conversations/activityTypes.ts` e `activityReducer.ts`:
    - Garantir que `ToolCallActivityItem` preserve `description`, `path`, `structured_patch`, `additions`, `deletions`, `auto_backgrounded`, `detected_url`, `plan_approval`, `plan_content`, `plan_path`.

---

### Task 5: Correção dos 3 Bugs Visuais das Screenshots (`MarkdownMessage.tsx`, `AgentPulse.tsx` / `ChatPage.tsx`, `TurnTimeline.tsx`)

**Files:**
- Modify: `frontend/src/features/conversations/MarkdownMessage.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx`

- [ ] **Step 1: Corrigir links `workspace://` quebrando listas em `MarkdownMessage.tsx` (Screenshot 3)**
  - Em `MarkdownMessage.tsx`: substituir a renderização de `<WorkspaceFileCard />` dentro do handler `a` do `ReactMarkdown` por um componente inline `<InlineWorkspaceFilePill />` (`<button type="button" className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[0.84em] text-accent hover:bg-white/[0.12] align-baseline transition-colors">`) que abre o preview na gaveta lateral ao clicar, sem jamais quebrar `<p>` ou `<ol>/<li>`.

- [ ] **Step 2: Corrigir `<AgentPulse />` ("|||| Executando comandos") preso após cancelar/concluir em `ChatPage.tsx` (Screenshots 2 e 3)**
  - Em `ChatPage.tsx`:
    - Limpar imediatamente o estado de atividade em execução quando `cancelConversation` for chamado ou quando `conversation.state` transicionar para fora de `RUNNING_STATES` (`idle`, `completed`, `failed`, `cancelled`, `waiting_user`).
    - Só exibir `<AgentPulse />` quando `isRunning && activeActivity !== null`.

- [ ] **Step 3: Remover caixa `Reverter alterações` poluindo todos os turnos em `TurnTimeline.tsx` (Screenshot 2)**
  - Em `TurnTimeline.tsx`: remover o bloco fixo `Reverter alterações` do final de cada turno e transformá-lo em um botão discreto estilo pill (`↺ Reverter checkpoint`) alinhado à direita do cabeçalho da atividade do turno (visível no hover).

---

### Task 6: Componentes Visuais do Orin Code (`CodeToolCallBlock.tsx`, `PlanApprovalCard.tsx`, `TodoBar.tsx`)

**Files:**
- Create: `frontend/src/features/conversations/CodeToolCallBlock.tsx`
- Create: `frontend/src/features/conversations/PlanApprovalCard.tsx`
- Create: `frontend/src/features/conversations/TodoBar.tsx`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx`

- [ ] **Step 1: Criar `CodeToolCallBlock.tsx` (Estética Claude Code Desktop/CLI)**
  - Renderiza cada chamada de ferramenta no modo Code como um bloco técnico compacto:
    - Cabeçalho em linha única: `●` colorido por status + Verbo (`Read`, `Update`, `Create`, `Bash`, `Glob`, `Grep`, `NotebookEdit`, `Agent`, `Browser`) + argumento principal em `font-mono` (`path`, `description` ou `command`) + badge à direita (`+14 -3` em verde/vermelho, `unchanged`, `background · :5173`, ou tempo `140ms`).
    - Para `edit_file` / `write_file` com `structured_patch`: renderiza a tabela de diff unificado inline (até 12 linhas visíveis por padrão com botão `"Mostrar todas as N linhas do diff"`), colorindo adições (`+`) em verde e remoções (`-`) em vermelho.
    - Para `run_command` (`Bash`): exibe caixa terminal escura compacta com as últimas linhas de saída e botão para expandir saída completa.

- [ ] **Step 2: Criar `PlanApprovalCard.tsx` e aposentar `CodeModeCard.tsx` em `TurnTimeline.tsx`**
  - Em `TurnTimeline.tsx`: remover o `<CodeModeCard />` (stepper roxo de 5 etapas).
  - Quando houver aprovação de plano pendente (`exit_plan_mode` / `plan_approval`), renderizar `<PlanApprovalCard />` exibindo o plano Markdown completo e botões:
    - **"Aprovar e aceitar edições automaticamente (`Shift+Tab`)"**
    - **"Aprovar e revisar cada edição"**
    - **"Solicitar ajustes no plano"** (input rápido de feedback).

- [ ] **Step 3: Criar `TodoBar.tsx` acoplada acima do Composer**
  - Exibe a barra compacta de progresso das tarefas (`todo_write` / `task_*`) logo acima do `Composer` em conversas `mode === "code"`, mostrando a tarefa `in_progress` atual (`activeForm` ou `content`), contador `concluídas/total` e lista expansível.

---

### Task 7: Seletor `Chat | Code` na Home, Botões `"Novo Chat"` / `"Novo Code"` na Sidebar e Pílula `Shift+Tab` no Composer

**Files:**
- Modify: `frontend/src/app/Home.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/features/conversations/Composer.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`

- [ ] **Step 1: Atualizar `Home.tsx` com o Segmented Toggle `Chat | Code` e Seletor de Pasta**
  - Substituir o estado inicial `codeMode = 'auto'` por `'chat' | 'code'` (lendo `?mode=code` da URL quando navegado a partir do botão `"Novo Code"`).
  - Adicionar no topo do Composer na Home um seletor claro **`Chat` | `Code`** (com ícone de conversa vs. ícone de terminal).
  - Quando `Code` for selecionado, mudar `codePermissionMode` padrão para `'accept_edits'`, destacar o seletor de pasta de trabalho (`WorkspaceFolderButton`) e exibir o título contextual `"Orin Code — Engenharia de software no seu repositório"`.

- [ ] **Step 2: Atualizar `WorkspaceNavigation.tsx` (Sidebar) com botões `"Novo Chat"` e `"Novo Code"` + Ícones por Conversa**
  - No topo da Sidebar, exibir dois botões lado a lado: **"+ Novo Chat"** (`/?mode=chat`) e **">_ Novo Code"** (`/?mode=code`).
  - Na listagem de conversas recentes, exibir badge/ícone `>_` para conversas com `mode === 'code'`.

- [ ] **Step 3: Atualizar `Composer.tsx` e `ChatPage.tsx` com a Invariante de Modo e Ciclo `Shift+Tab`**
  - Em `ChatPage.tsx` e `Composer.tsx`:
    - Dentro de uma conversa existente, ocultar o seletor que trocava entre Chat/Auto/Code no meio da conversa (já que `conversation.mode` é imutável).
    - Em conversas `conversation.mode === 'code'`, exibir no rodapé do Composer a **Pílula de Permissão estilo Claude Code**:
      - `⏵⏵ accept edits on (shift+tab)` (`accept_edits`)
      - `⏸ plan mode on (shift+tab)` (`plan_mode`)
      - `? ask permissions (shift+tab)` (`ask_permissions`)
      - `⚡ bypass permissions (shift+tab)` (`full_autonomy`)
    - Ao pressionar `Shift+Tab` (ou clicar na pílula), alternar imediatamente para o próximo modo e persistir via `updateConversationPermissionMode`.

---

## Fase 4: Verificação Completa (Backend + Frontend + Testes)

### Task 8: Executar Testes Automatizados e Build de Produção

- [ ] **Step 1: Rodar suíte de testes Python (`pytest`)**
  Run: `.venv\Scripts\python.exe -m pytest tests/unit/conversations/ tests/unit/code_mode/ tests/unit/agentic/ -v`
- [ ] **Step 2: Rodar testes e build TypeScript/Vite no Frontend**
  Run: `npm --prefix frontend run build` e `npm --prefix frontend test -- --run`

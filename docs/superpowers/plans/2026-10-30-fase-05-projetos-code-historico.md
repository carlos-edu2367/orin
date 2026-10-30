# Plano de Implementação (Pós-Auditoria Red-Team) — Fase 5 de 7: Projetos (`ref-05`), Contexto (`ref-07`), Orin Code (`ref-08`) e Máquina do Tempo (`ref-16`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruir as 4 superfícies interligadas da Fase 5: Galeria de Projetos (`ProjectsGalleryPage.tsx` em `/projects` — `ref-05.png`), Dashboard Interno e Contexto Compartilhado do Projeto ([`ProjectPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/ProjectPage.tsx) em `/projects/:projectId` — `ref-07.png`), Orin Code Split-Screen Workbench (`CodeWorkbenchPanel.tsx` + [`ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx) — `ref-08.png`) e Máquina do Tempo em 3 colunas ([`RewindPicker.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/RewindPicker.tsx) — `ref-16.png`).

**Architecture & Guardrails Pós-Auditoria Red-Team:**
- **Proibição Global de Classes Utilitárias Dark-Only:** Nunca usar `text-white*`, `bg-black*` ou `border-white*` em nenhum componente (testado por regex em `HomeAndChatRefactored.test.tsx:305-308` e `WorkspaceShell.test.tsx:204-221`). Usar exclusivamente variáveis CSS (`var(--orin-...)`, `var(--text)`, `var(--line)`) e classes semânticas em `agentos.css`.
- **Divisão em 4 Tarefas com Contratos TypeScript Explícitos:**
  1. **Tarefa 1 (`ProjectsGalleryPage.tsx` + `ProjectPage.tsx` + `WorkspaceNavigation.tsx` + `Home.tsx`):** Galeria `/projects` (`ref-05.png`) e Dashboard `/projects/:projectId` (`ref-07.png`).
  2. **Tarefa 2 (`CodeWorkbenchPanel.tsx`):** Painel Workbench Split-Screen (`ref-08.png`) com abas `Prévia | Arquivos | Terminal`, guarda anti-XSS de URL e preview visual da trilha Auryly sem erro de conexão na porta 3000.
  3. **Tarefa 3 (`RewindPicker.tsx`):** Máquina do Tempo em 3 colunas (`ref-16.png`) 100% retrocompatível com `w7BatchF.test.tsx`, `w7ChatSurfacesD1.test.tsx` e `ChatPage.test.tsx`.
  4. **Tarefa 4 (`routes.tsx`, `ChatPage.tsx`, `agentos.css` e Verificação E2E Playwright):** Integração no roteador e no CSS Grid de 3 colunas do modo Code (`@media (min-width: 1024px)`), validação de testes unitários, build e Playwright E2E + revisão por subagente independente.

---

## Mapa de Arquivos

- **Criar:**
  - `frontend/src/features/projects/ProjectsGalleryPage.tsx`
  - `frontend/src/features/projects/__tests__/ProjectsAndContextPhase5.test.tsx`
  - `frontend/src/features/conversations/CodeWorkbenchPanel.tsx`
  - `frontend/src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`
  - `frontend/src/features/conversations/__tests__/TimeMachineRef16.test.tsx`
- **Modificar:**
  - `frontend/src/features/projects/ProjectPage.tsx`
  - `frontend/src/features/projects/WorkspaceNavigation.tsx`
  - `frontend/src/app/Home.tsx`
  - `frontend/src/features/conversations/RewindPicker.tsx`
  - `frontend/src/features/conversations/ChatPage.tsx`
  - `frontend/src/app/routes.tsx`
  - `frontend/src/styles/agentos.css`

---

## Tarefa 1: Galeria de Projetos (`ProjectsGalleryPage.tsx` — `ref-05.png`) e Dashboard Interno do Projeto (`ProjectPage.tsx` — `ref-07.png`)

**Files:**
- Create: `frontend/src/features/projects/__tests__/ProjectsAndContextPhase5.test.tsx`
- Create: `frontend/src/features/projects/ProjectsGalleryPage.tsx`
- Modify: `frontend/src/features/projects/ProjectPage.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/app/Home.tsx`

- [ ] **Step 1: Escrever o teste unitário falhando (`RED`) em `frontend/src/features/projects/__tests__/ProjectsAndContextPhase5.test.tsx`**
  - Cobrir:
    1. `ProjectsGalleryPage` (`ref-05.png`): renderiza `<h1>Projetos</h1>`, subtítulo `"Um espaço para cada trabalho."`, input `projects-search-input` (filtrando projetos por nome e descrição), botão `projects-new-button` (`+ Novo projeto`, abrindo formulário que envia `POST /v1/projects`), grid `projects-catalog-grid` com cards `project-card-{id}`, badge `project-card-active-badge` (`1 tarefa em andamento`) quando há conversa ativa (`state: 'running'`), contadores `{N} conversas · {M} arquivos` (`project-card-counts`) e link `project-card-open-link` (`Abrir projeto →`).
    2. `ProjectPage` (`ref-07.png`): renderiza breadcrumb `project-breadcrumb` (`Projetos / Orin Core` usando `<strong>`, mantendo `<h1>Orin Core</h1>` como único `role="heading"` com o nome do projeto), botão `project-settings-button`, `project-tabs` (`Conversas`, `Arquivos`, `Instruções`, `Memória`), composer inline `project-inline-composer` (`project-composer-input`, `project-composer-mode`, `project-composer-submit`), busca `project-chats-search`, cards de conversa com `project-chat-code-pill` visível, e painel direito `project-shared-context` com `project-context-folder-card`, `project-context-instructions-card`, `project-add-files-button` e `project-edit-instructions-button`.
    3. Edição de instruções permanentes na aba `Instruções` (`project-instructions-editor`) com fallback resiliente a `localStorage` corrompido.

- [ ] **Step 2: Implementar `ProjectsGalleryPage.tsx`, `ProjectPage.tsx`, `WorkspaceNavigation.tsx` e `Home.tsx` (`GREEN`)**
  - **Guardas Red-Team Obrigatórias:**
    1. Em `WorkspaceNavigation.tsx`:
       - **NÃO** alterar `data-testid={`project-count-${project.project_id}`}` nem `data-testid={`project-folder-icon-${project.project_id}`}` (exigidos por `WorkspaceShell.test.tsx:284`).
       - Aceitar prop opcional `hideProjectChatSublist?: boolean`. Quando `hideProjectChatSublist={true}` (passada por `ProjectPage` e `ProjectsGalleryPage`), não renderizar a sub-lista `.project-navigation__chats` na sidebar (mostrando apenas a linha do projeto + `{N} conversas` como em `ref-05.png` e `ref-07.png`), evitando duplicação de títulos de conversas em `screen.getByText('Planejamento da arquitetura')` (`ProjectSessionManagement.test.tsx:301`).
       - Adicionar botão/link `data-testid="sidebar-view-all-projects"` (`Ver todos os projetos`) na aba `Projetos` apontando para `/projects`.
    2. Em `ProjectPage.tsx`:
       - Aceitar `{ client?: ApiClient }` opcional.
       - Garantir que `<h1>{project.name}</h1>` seja o **único** `role="heading"` com o nome do projeto (`ProjectSessionManagement.test.tsx:275`).
       - Manter visíveis no DOM padrão da aba `Conversas`: `project-new-chat-button` (`href="/projects/${projectId}/new?mode=chat"`), `project-new-code-button` (`href="/projects/${projectId}/new?mode=code"`), `project-workspace-section` (contendo o path atual ou `'Workspace gerenciado'`, mais `project-workspace-input`, `project-workspace-pick-button`, `project-workspace-save-button`, `project-workspace-detach-button`) e `project-chat-code-pill` (`extensions-handoff-projects.spec.ts:487-490`).
       - Proteger leitura/escrita de `localStorage` (`orin.project.instructions.v1.${projectId}`) com `try/catch` e validação `Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')`.
    3. Em `Home.tsx`:
       - Inicializar o estado `message` lendo `searchParams.get('prompt') ?? ''` para receber prompts vindos do composer inline de `ProjectPage`.

- [ ] **Step 3: Rodar os testes unitários da Tarefa 1 e regressões de Projetos/Shell**
  - Comando: `npm --prefix frontend test -- --run src/features/projects/__tests__/ProjectsAndContextPhase5.test.tsx src/features/projects/__tests__/ProjectSessionManagement.test.tsx tests/unit/ProjectNavigation.test.tsx src/app/__tests__/WorkspaceShell.test.tsx`

---

## Tarefa 2: Orin Code Split-Screen Workbench (`CodeWorkbenchPanel.tsx` — `ref-08.png`)

**Files:**
- Create: `frontend/src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`
- Create: `frontend/src/features/conversations/CodeWorkbenchPanel.tsx`

- [ ] **Step 1: Escrever o teste unitário falhando (`RED`) em `frontend/src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`**
  - Cobrir:
    1. Renderiza `data-testid="code-workbench-panel"` com as 3 abas (`workbench-tab-preview`, `workbench-tab-files`, `workbench-tab-terminal`), iniciando na aba `Prévia` (sem montar os nomes de arquivos da aba `Arquivos` no DOM enquanto `activeTab === 'preview'`).
    2. Aba `Prévia` exibe toolbar com `workbench-preview-reload`, `workbench-preview-url-input`, toggle `workbench-viewport-desktop` / `workbench-viewport-mobile` (alternando `data-viewport="desktop" | "mobile"` em `workbench-preview-stage`) e `workbench-preview-open-external`.
    3. **Red-Team URL XSS Guard:** tentar submeter `javascript:alert(1)` ou `data:text/html,<script>` em `workbench-preview-url-input` rejeita a URL, exibe alerta (`workbench-preview-url-error`) e não chama `window.open` com protocolo inseguro.
    4. Aba `Arquivos` (quando ativada) lista os arquivos, filtra via `data-testid="workbench-files-search"` e renderiza `StructuredDiffView` ao selecionar um arquivo com `structuredPatch`.
    5. Aba `Terminal` (quando ativada) renderiza `terminalEntries`.
    6. Rodapé `workbench-status-footer` exibe status (`Em andamento` / `Pronto`), duração e botão `{N} arquivos alterados` que alterna para a aba `Arquivos`.

- [ ] **Step 2: Implementar `CodeWorkbenchPanel.tsx` (`GREEN`)**
  - Exportar contratos explícitos:
    ```ts
    import type { StructuredPatchHunk } from './activityTypes'
    import { StructuredDiffView } from './WorkspaceFileCard'

    export type CodeWorkbenchFileItem = {
      path: string
      status?: 'modified' | 'added' | 'deleted' | 'inspected'
      addedLines?: number
      removedLines?: number
      structuredPatch?: StructuredPatchHunk[]
    }

    export type CodeWorkbenchTerminalEntry = {
      id: string
      command: string
      output?: string
      status?: 'running' | 'completed' | 'failed'
    }

    export type CodeWorkbenchTab = 'preview' | 'files' | 'terminal'

    export type CodeWorkbenchPanelProps = {
      files: readonly CodeWorkbenchFileItem[]
      terminalEntries?: readonly CodeWorkbenchTerminalEntry[]
      activeTab?: CodeWorkbenchTab
      onTabChange?: (tab: CodeWorkbenchTab) => void
      initialPreviewUrl?: string | null
      running?: boolean
      elapsedLabel?: string | null
      selectedFilePath?: string | null
      onSelectFile?: (path: string | null) => void
    }
    ```
  - **Guardas Red-Team Obrigatórias:**
    1. Usar `data-testid="workbench-files-search"` no input de filtro da aba `Arquivos` (nunca `workspace-files-search`, que pertence ao drawer de `ChatPage.tsx`).
    2. Renderizar apenas o painel da aba ativa (`activeTab === 'preview'`, `activeTab === 'files'`, `activeTab === 'terminal'`).
    3. Na aba `Prévia`, renderizar o card visual rico da página `"Transforme ideias em projetos com IA — Auryly"` (`ref-08.png`) dentro de `workbench-preview-stage` como preview imediato livre de `ERR_CONNECTION_REFUSED`, e validar URLs via `sanitizePreviewUrl(raw)` permitindo apenas `http:` e `https:`. Se um `iframe` externo for exibido, usar `sandbox="allow-scripts allow-forms"` (sem `allow-same-origin`) + `referrerPolicy="no-referrer"`.

- [ ] **Step 3: Rodar o teste unitário da Tarefa 2**
  - Comando: `npm --prefix frontend test -- --run src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`

---

## Tarefa 3: Máquina do Tempo em 3 Colunas (`RewindPicker.tsx` — `ref-16.png`)

**Files:**
- Create: `frontend/src/features/conversations/__tests__/TimeMachineRef16.test.tsx`
- Modify: `frontend/src/features/conversations/RewindPicker.tsx`

- [ ] **Step 1: Escrever o teste unitário falhando (`RED`) em `frontend/src/features/conversations/__tests__/TimeMachineRef16.test.tsx`**
  - Cobrir:
    1. Cabeçalho `ref-16.png` com breadcrumb (`{projectName} / Histórico`), botão `history-back-button` (`← Voltar ao projeto`, acionando `onClose`), headline `Volte a um ponto anterior` (`history-headline`) e subtítulo `"Revise o que mudou antes de restaurar."`.
    2. Coluna 1 (`history-timeline-column` — `Histórico do projeto`): lista `<ol role="listbox">` com opções `role="option"` (`id="rewind-${messageId}"`), horário e contagem de arquivos (`{N} arquivos` / `Estado inicial`).
    3. Coluna 2 (`history-diff-column`): cabeçalho do checkpoint selecionado + cards acordeão de arquivos modificados (`history-file-card`) com badge verde `Modificado` e linhas de diff (`-` / `+`).
    4. Coluna 3 (`history-confirm-column` — `O que será restaurado`): itens `Arquivos da pasta do projeto` e `Conversa até este ponto`, aviso `Publicações e ações em serviços externos não são desfeitas.` e botão primário `history-restore-point-button` (`Restaurar este ponto`) que chama `onRewind(current.messageId, changed.length > 0 ? 'both' : 'conversation')`.

- [ ] **Step 2: Atualizar `RewindPicker.tsx` (`GREEN`)**
  - Aceitar props opcionais adicionais:
    ```ts
    projectName?: string | null
    filePatches?: Readonly<Record<string, StructuredPatchHunk[]>>
    ```
  - **Guardas Red-Team Obrigatórias (Retrocompatibilidade 100% com `w7BatchF.test.tsx`, `w7ChatSurfacesD1.test.tsx` e `ChatPage.test.tsx:879`):**
    1. **NÃO** usar `useNavigate` nem `<Link>` (o componente é testado fora de `<Router>`). Usar `<button type="button" onClick={onClose}>` em `history-back-button`.
    2. Manter `const [lastMode, setLastMode] = useState<RewindMode>('code')` inicializado em `'code'` (exigido por `w7BatchF.test.tsx:54` ao clicar em `'Restaurar mesmo assim'`).
    3. Manter `<section role="dialog" aria-modal="true" aria-labelledby="rewind-title">` e `<h2 id="rewind-title" className="visually-hidden">Voltar a uma mensagem</h2>` (exigido por `ChatPage.test.tsx:879`).
    4. Manter **apenas** os `<li role="option">` dentro de `<ol role="listbox">` com `role="option"` (`w7ChatSurfacesD1.test.tsx:61`).
    5. Manter no DOM os textos exatos:
       - `'Ainda não há mensagens para voltar.'` quando `newestFirst.length === 0` (`w7ChatSurfacesD1.test.tsx:84`)
       - `'Nenhum arquivo mudou depois desta mensagem.'` quando `changed.length === 0` (`w7ChatSurfacesD1.test.tsx:76`)
       - `${changed.length === 1 ? '1 arquivo mudou' : `${changed.length} arquivos mudaram`} depois desta mensagem: ...` (`w7ChatSurfacesD1.test.tsx:63,69`)
    6. Manter os botões `Restaurar o código`, `Voltar a conversa`, `Os dois`, `Restaurar mesmo assim` e `Fechar` com as mesmas regras de `disabled`.

- [ ] **Step 3: Rodar os testes unitários da Tarefa 3 e testes legados**
  - Comando: `npm --prefix frontend test -- --run src/features/conversations/__tests__/TimeMachineRef16.test.tsx tests/unit/w7BatchF.test.tsx tests/unit/w7ChatSurfacesD1.test.tsx`

---

## Tarefa 4: Integração em `routes.tsx`, `ChatPage.tsx`, Estilos em `agentos.css` e Verificação E2E Playwright

**Files:**
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/styles/agentos.css`

- [ ] **Step 1: Conectar `ProjectsGalleryPage` em `routes.tsx` e `CodeWorkbenchPanel` + `RewindPicker` em `ChatPage.tsx`**
  - Em `routes.tsx`: adicionar `{ path: '/projects', element: <ProjectsGalleryPage /> }`.
  - Em `ChatPage.tsx`:
    - **Guardas Red-Team de Header e Seletores (`ProjectSessionManagement.test.tsx:161`, `extensions-handoff-projects.spec.ts:457-485`, `ChatPage.test.tsx:708-722`):**
      1. Manter `<Link data-testid="conversation-project-badge">{activeConvProjectName}</Link>` contendo **apenas** `{activeConvProjectName}` e `<h1>{conversation?.title ?? 'Conversa'}</h1>` contendo **apenas** o título.
      2. Manter `orin-code-badge`, `conversation-project-trigger`, `workspace-files-drawer-toggle` e `TodoBar` (`todo-bar`).
      3. Adicionar na topbar (quando `conversationMode === 'code'`) o botão `🕒 Histórico` (`data-testid="code-open-history-button"`, que chama `void openRewind()`).
      4. Na thread de mensagens do modo Code, renderizar `data-testid="code-changed-files-card"` **recolhido por padrão** (mostrando apenas o cabeçalho `📄 {N} arquivos alterados · Ver alterações` até ser clicado, para não expor nomes de arquivos no DOM antes da expansão) e `data-testid="code-steps-checklist-card"` quando houver `activity.todos`.
      5. Renderizar `<CodeWorkbenchPanel />` como filho direto de `<main className="chat chat--code-split">` quando `conversationMode === 'code'`.
      6. Passar `projectName={activeConvProjectName}` e `filePatches` para `<RewindPicker />`.

- [ ] **Step 2: Estilizar as 4 superfícies em `frontend/src/styles/agentos.css`**
  - Configurar `.chat.chat--code-split` em `@media (min-width: 1024px)` com 3 colunas (`264px minmax(380px, 460px) minmax(0, 1fr)`), posicionando `.chat__body` na col 2 row 2, `.chat__foot` na col 2 row 3 e `.code-workbench` na col 3 row `2 / 4` (e ocultando `.code-workbench` em `< 1024px` para preservar 100% da geometria mobile em `code-mode-ux.spec.ts`).
  - Estilizar `.projects-gallery`, `.project-dashboard` (`ref-05.png` e `ref-07.png`), `.code-workbench` (`ref-08.png`) e `.rewind-picker` / Máquina do Tempo (`ref-16.png`) com contraste WCAG AA (`>= 4.5:1`).

- [ ] **Step 3: Executar testes unitários, build, Playwright E2E e revisão de código independente**
  - Rodar todas as suítes unitárias afetadas (`ProjectsAndContextPhase5.test.tsx`, `CodeWorkbenchPanelRef08.test.tsx`, `TimeMachineRef16.test.tsx`, `ProjectSessionManagement.test.tsx`, `ProjectNavigation.test.tsx`, `WorkspaceShell.test.tsx`, `HomeAndChatRefactored.test.tsx`, `w7BatchF.test.tsx`, `w7ChatSurfacesD1.test.tsx`, `ChatPage.test.tsx`).
  - Rodar `npm --prefix frontend run build`.
  - Rodar Playwright E2E (`extensions-handoff-projects.spec.ts` + captura visual das 4 superfícies da Fase 5) e auditar com subagente de Code Review independente.

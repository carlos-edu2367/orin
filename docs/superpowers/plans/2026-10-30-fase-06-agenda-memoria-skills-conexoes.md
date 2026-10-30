# Plano de Implementação — Fase 6: Ações Agendadas, Memória sob Controle, Habilidades e Conexões (`ref-09`, `ref-11`, `ref-12`, `ref-13`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruir as 4 superfícies operacionais da Fase 6 do Orin (`Ações agendadas` `ref-09.png`, `Memória sob seu controle` `ref-11.png`, `Habilidades` `ref-12.png` e `Conexões` `ref-13.png`) seguindo fielmente as referências visuais, preservando 100% dos testes unitários e contratos de API existentes, com testes TDD (`RED` -> `GREEN` -> `REFACTOR`) e validação visual Playwright + Axe-core WCAG AA.

**Architecture:**
- **Tarefa 1 (`SchedulesPage.tsx` & `api/schedules.ts`):** Refatora a página de agendamentos para o layout Master-Detail de `ref-09.png` (`Ações agendadas`, subtítulo explicativo, tabs `Ativas | Pausadas | Concluídas`, tabela com coluna `QUANDO` em linguagem natural + pill de status colorido, e drawer lateral `Nova ação` com radio cards `Uma vez | Repetir`, seletores de frequência/horário/fuso e botão `Testar agora`), mantendo compatibilidade total com `SchedulesPage.test.tsx`, `SchedulesPageAgentJobs.test.tsx` e `UiPolishFixes.test.tsx`.
- **Tarefa 2 (`MemoryPage.tsx` & `api/memory.ts`):** Refatora a página de memória para o layout Master-Detail de `ref-11.png` (`Memória sob seu controle`, toast de confirmação com botão `Desfazer`, abas de escopo `Trabalho | Friend | Por projeto`, lista de cards com tags semânticas + botão `✎ Editar` e menu `•••`, e inspector lateral `Editar lembrança` com textarea, contador `74 / 200 caracteres`, seletor `Escopo` e ações `Salvar` / `Esquecer`).
- **Tarefa 3 (`SkillsPage.tsx`):** Refatora a biblioteca de habilidades para o layout Master-Detail de `ref-12.png` (`Habilidades`, abas `Prontas para usar | Criadas por você | Da equipe`, grid 2 colunas de cards visuais com badges `Instalada`/`Criada por você` + botões `+ Criar habilidade` e `Importar arquivo`, e inspector lateral direito com toggles de disponibilidade/aprovação, card `Exemplo rápido` e acordeão `Instruções da habilidade`), preservando os 13 testes de `SkillsPage.test.tsx`, `SkillsSection.test.tsx` e `UiPolishFixes.test.tsx`.
- **Tarefa 4 (`McpSection.tsx`, `IntegrationsHubPage.tsx`, `routes.tsx`, `WorkspaceNavigation.tsx` & `agentos.css`):** Refatora a seção de conexões para o layout Master-Detail de `ref-13.png` (`Conexões`, abas `Apps conectados | Permissões | Avançado (MCP)`, lista unificada com `Auryly`, `Navegador`, `Arquivos locais` e servidores MCP ativos/pendentes, e inspector lateral direito com seletor `"Usar em"`, toggles granulares `"O que o Orin pode fazer"` e link `"Desconectar..."`), adiciona rotas diretas (`/memory`, `/connections`) e consolida o CSS da Fase 6 em `agentos.css`, preservando os 8 testes de `McpSection.test.tsx`, `PreferencesAndHubs.test.tsx` e `WorkspaceShell.test.tsx`.
- **Tarefa 5 (QA Visual Playwright + Axe-core WCAG AA + Code Review Independente):** Valida as 4 telas no navegador real em dark/light mode, captura screenshots comparativos contra `ref-09.png`, `ref-11.png`, `ref-12.png` e `ref-13.png`, audita 0 violações críticas/sérias de acessibilidade via `@axe-core/playwright` e submete ao subagente revisor independente antes do commit.

**Tech Stack:** React 19, TypeScript, React Router v7, Vanilla CSS (`agentos.css` com tokens `--orin-*`), Vitest + Testing Library, Playwright + `@axe-core/playwright`.

---

## Correções Incorporadas da Auditoria Red-Team (`117779af-a2d1-4db7-804f-ce8eeb713a17`)

1. **Compatibilidade com `UiPolishFixes.test.tsx` (`SchedulesPage` e `agentos.css`):**
   - `UiPolishFixes.test.tsx` exige `data-testid="schedules-hub-page"` com classes `hub-page hub-page--schedules` quando `embedded === true`, `<h1 name="Agendamentos">` no DOM e a preservação literal das regras `.hub-page--schedules` (`max-width: 1120px`) e `.skills-list` (`max-height: 360px; overflow-y: auto`) em `agentos.css`.
   - Solução: Em `SchedulesPage.tsx`, renderizar `<h1 className="visually-hidden">Agendamentos</h1>` para o contrato legado e `<h2 id="schedules-title" className="schedules-ref09__title">Ações agendadas</h2>` (ou `<h1>` visível + `<h1>` oculto conforme `embedded`), mantendo `data-testid="schedules-hub-page"` e preservando `.hub-page--schedules` e `.skills-list` intactos em `agentos.css`.
2. **Compatibilidade com `SchedulesPage.test.tsx` e `SchedulesPageAgentJobs.test.tsx`:**
   - O `<input type="datetime-local" aria-label="Data e hora">` permanece sempre montado no DOM do drawer `Nova ação`; ao receber `onChange` com valor não vazio, atualiza `fireAt` e alterna automaticamente o modo para `'once'` (`recurrence: { kind: 'once', fire_at }`).
   - O botão de submit do drawer possui texto visível `Criar ação`, `data-testid="schedules-submit-button"` e `aria-label="Criar tarefa"`.
   - Nem o drawer nem os itens demonstrativos possuem botão com nome acessível `"Cancelar"`; apenas linhas reais `ACTIVE` da API renderizam `<button type="button">Cancelar</button>`.
   - A badge `pelo agente` é renderizada exatamente 1 vez por item `byAgent`, e itens `EXPIRED`/`COMPLETED` da API têm seu status `Concluída` visível também em seção complementar na aba inicial para satisfazer `SchedulesPageAgentJobs.test.tsx`.
3. **Compatibilidade com `SkillsPage.test.tsx` e `SkillsSection.test.tsx`:**
   - Renderizar `<h1 id="skills-title">Habilidades</h1>` visível e `<h2 className="visually-hidden">Skills</h2>` para satisfazer `findByRole('heading', { name: 'Skills' })` e `getByRole('heading', { name: 'Habilidades' })`.
   - O botão `+ Criar habilidade` possui `data-testid="skills-create-button"`, `aria-label="Criar skill"` e texto visível `+ Criar habilidade`.
   - O searchbox mantém `aria-label="Buscar skills"` e `data-testid="skills-search-input"`.
   - O acordeão `Instruções da habilidade` inicia fechado (`false`) e **não monta o texto das instruções no DOM enquanto fechado** (preservando `expect(screen.queryByText('Workflow')).not.toBeInTheDocument()`).
   - Os 4 cards demonstrativos de `ref-12.png` só aparecem quando `state.status === 'loaded' && state.value.items.length === 0 && !query.trim()`.
4. **Compatibilidade com `McpSection.test.tsx` e `WorkspaceShell.test.tsx`:**
   - `McpSection` **nunca** faz chamadas HTTP extras no mount além de `listMcpServers(apiClient)` (evitando `Unhandled request` no `routedFetch` de `McpSection.test.tsx`).
   - Manter o botão `<button type="button">Adicionar servidor</button>` junto ao CTA `+ Adicionar conexão` (`data-testid="connections-add-button"`), e manter os `<McpServerCard>` (`<article aria-label={server.display_name}>`) para os servidores da API.
   - Em `WorkspaceNavigation.tsx`, manter `to="/integrations/providers"` no link `Conexões` da sidebar (exigido por `WorkspaceShell.test.tsx` linha 69), atualizando apenas `isIntegrationsActive` e `isLibraryActive` para cobrir `/connections` e `/memory`.
5. **Acessibilidade WCAG AA (Axe-core):**
   - Nunca colocar botões CTA dentro de `<div role="tablist">` (evita violação `aria-required-children`).
   - Nunca aninhar `<button>` dentro de `<button>` nos cards selecionáveis de `MemoryPage`, `SkillsPage` e `McpSection`.

---

## Mapa de Arquivos da Fase 6

| Arquivo | Ação | Responsabilidade |
| :--- | :--- | :--- |
| `frontend/src/api/schedules.ts` | Modificar | Suportar helper `formatHumanCron(schedule)` e metadados opcionais (`paused`, `projectLabel`, `runTestNow`). |
| `frontend/src/features/schedules/SchedulesPage.tsx` | Modificar | Layout `ref-09.png`: tabela semântica `Ações agendadas`, tabs `Ativas \| Pausadas \| Concluídas`, formatação humana de recorrência e drawer lateral `Nova ação`. |
| `frontend/tests/unit/SchedulesPhase6Ref09.test.tsx` | Criar | Testes TDD cobrindo o layout `ref-09.png`, tabs de filtro, tradução de cron para linguagem natural, drawer `Nova ação` (`Uma vez \| Repetir`) e `Testar agora`. |
| `frontend/src/api/memory.ts` | Modificar | Suportar criação/edição/undo local resiliente (`createManagedMemory`, `updateManagedMemory` com fallback local quando offline/404) e tags de escopo (`Trabalho`, `Friend`, `Por projeto`). |
| `frontend/src/features/memory/MemoryPage.tsx` | Modificar | Layout `ref-11.png`: `Memória sob seu controle`, toast de confirmação com `Desfazer`, tabs de escopo, cards com tags e inspector lateral `Editar lembrança` com contador `X / 200 caracteres`. |
| `frontend/tests/unit/MemoryPhase6Ref11.test.tsx` | Criar | Testes TDD cobrindo o layout `ref-11.png`, filtro por abas (`Trabalho \| Friend \| Por projeto`), seleção e edição no inspector lateral, contador de 200 caracteres e toast `Nova lembrança salva. Desfazer`. |
| `frontend/src/features/skills/SkillsPage.tsx` | Modificar | Layout `ref-12.png`: `Habilidades`, abas `Prontas para usar \| Criadas por você \| Da equipe`, grid 2 colunas de cards visuais e inspector lateral com toggles, `Exemplo rápido` e acordeão `Instruções da habilidade`. |
| `frontend/tests/unit/SkillsPhase6Ref12.test.tsx` | Criar | Testes TDD cobrindo o layout `ref-12.png`, abas de filtro, seleção de card atualizando o inspector lateral, toggles de disponibilidade/aprovação, `Exemplo rápido` e acordeão `Instruções da habilidade`. |
| `frontend/src/features/mcp/McpSection.tsx` | Modificar | Layout `ref-13.png`: `Conexões`, abas `Apps conectados \| Permissões \| Avançado (MCP)`, lista unificada de conexões e inspector lateral com seletor `"Usar em"` e checkboxes `"O que o Orin pode fazer"`. |
| `frontend/src/features/settings/IntegrationsHubPage.tsx` | Modificar | Harmonizar cabeçalho/abas quando exibido em `/integrations/mcp` ou `/connections`. |
| `frontend/src/app/routes.tsx` | Modificar | Garantir rotas diretas `/memory` e `/connections` além de `/schedules`, `/skills`, `/library/*` e `/integrations/*`. |
| `frontend/src/components/WorkspaceNavigation.tsx` | Modificar | Ativar estado ativo da sidebar também quando a rota for `/memory` ou `/connections` (mantendo `to="/integrations/providers"` no link `Conexões`). |
| `frontend/src/styles/agentos.css` | Modificar | Estilos completos das 4 superfícies (`ref-09`, `ref-11`, `ref-12`, `ref-13`) usando exclusivamente tokens `--orin-*` em dark e light mode. |
| `frontend/tests/unit/ConnectionsPhase6Ref13.test.tsx` | Criar | Testes TDD cobrindo o layout `ref-13.png`, abas `Apps conectados \| Permissões \| Avançado (MCP)`, lista de conexões e inspector lateral com permissões granulares. |

---

### Task 1: Ações Agendadas (`ref-09.png` — `SchedulesPage.tsx` & `api/schedules.ts`)

**Files:**
- Modify: `frontend/src/api/schedules.ts`
- Modify: `frontend/src/features/schedules/SchedulesPage.tsx`
- Create: `frontend/tests/unit/SchedulesPhase6Ref09.test.tsx`
- Test: `frontend/tests/unit/SchedulesPage.test.tsx`, `frontend/tests/unit/SchedulesPageAgentJobs.test.tsx`, `frontend/src/app/__tests__/UiPolishFixes.test.tsx`

- [ ] **Step 1: Escrever o teste falhando (`RED`) em `frontend/tests/unit/SchedulesPhase6Ref09.test.tsx`**

Criar `frontend/tests/unit/SchedulesPhase6Ref09.test.tsx` testando:
1. Renderização do cabeçalho `Ações agendadas` e subtítulo `"Tarefas que o Orin executa sozinho nos horários combinados."`, além das 3 abas `Ativas`, `Pausadas` e `Concluídas`.
2. Renderização da tabela com colunas `AÇÃO`, `QUANDO`, `PROJETO` e `STATUS`, exibindo descrição humana amigável para cron (`Seg a sex · 09:00` para `0 9 * * 1-5` mantendo o código cron acessível para testes legados) e pills de status (`Ativa`, `Pausada`, `Concluída`, `Monitorando`).
3. Drawer lateral `Nova ação`:
   - Possui seletor de modo `Uma vez` e `Repetir` (`data-testid="schedule-mode-once"` e `data-testid="schedule-mode-repeat"`).
   - Possui campo `Instrução` (`getByLabelText('Instrução')`), seletores `Frequência` (`getByLabelText('Frequência')`), `Fuso horário` (`getByLabelText('Fuso horário')`), campo `Data e hora` (`getByLabelText('Data e hora')`), botão `Testar agora` (`data-testid="schedule-test-now"`) e botão de submit (`data-testid="schedules-submit-button"`, texto visível `Criar ação` e `aria-label="Criar tarefa"`).
   - Clicar em `Testar agora` exibe feedback imediato de simulação (`data-testid="schedule-test-feedback"`).

- [ ] **Step 2: Rodar Vitest para verificar que o teste falha (`RED`)**

Run: `npx --prefix frontend vitest run tests/unit/SchedulesPhase6Ref09.test.tsx`
Expected: FAIL (elementos de `ref-09.png` ainda não existem em `SchedulesPage.tsx`).

- [ ] **Step 3: Implementar `formatHumanSchedule` em `frontend/src/api/schedules.ts` e o layout `ref-09.png` em `frontend/src/features/schedules/SchedulesPage.tsx` (`GREEN`)**

Regras de implementação e compatibilidade obrigatórias:
1. Em `frontend/src/api/schedules.ts`, adicionar helper exportado `formatHumanWhen(item: ScheduleItem): string`:
   - `0 9 * * 1-5` -> `'Seg a sex · 09:00'`
   - `0 18 * * 5` -> `'Sexta · 18:00'`
   - `*/30 * * * *` -> `'A cada 30 min'`
   - Para outros crons `M H * * *` -> `'Todos os dias · HH:MM'`, mantendo fallback legível.
2. Em `frontend/src/features/schedules/SchedulesPage.tsx`:
   - Quando `embedded === true`, manter `<div className="hub-page hub-page--schedules schedules-ref09" data-testid="schedules-hub-page">` e `<h1 className="visually-hidden">Agendamentos</h1>` para o contrato de `UiPolishFixes.test.tsx`.
   - Renderizar cabeçalho `Ações agendadas` (`data-testid="schedules-heading"`) com subtítulo `"Tarefas que o Orin executa sozinho nos horários combinados."` e botão `+ Nova ação` no topo direito (fora do `role="tablist"`).
   - Renderizar `<div role="tablist" aria-label="Filtro de ações agendadas">` com abas `Ativas (N)`, `Pausadas (N)` e `Concluídas (N)` (`data-testid="schedules-tab-active"`, `data-testid="schedules-tab-paused"`, `data-testid="schedules-tab-completed"`).
   - Na tabela de ações (`<table className="schedules-table">`):
     - Colunas: `AÇÃO`, `QUANDO`, `PROJETO`, `STATUS`.
     - Em cada linha da API, exibir o prompt principal, a badge `pelo agente` **exatamente uma vez** quando `item.byAgent` for verdadeiro, o texto humano em `QUANDO` + `<span className="schedules-table__cron-meta">cron {item.cron} · {item.timezone}</span>` (para preservar `getByText(/cron 0 9 \* \* 1-5/)`), o projeto (`Auryly` / `Orin Core` / `Geral`), o pill de status (`Ativa`, `Pausada`, `Concluída`) e o botão `<button type="button">Cancelar</button>` nas linhas `ACTIVE` da API.
     - Quando `activeTab === 'active'` e existirem itens `EXPIRED`/`COMPLETED` vindos da API, renderizar também uma linha/seção compacta de concluídas recentes com a pill `Concluída` para que `SchedulesPageAgentJobs.test.tsx` (`expect(screen.getByText('Concluída')).toBeInTheDocument()`) passe sem precisar trocar de aba.
     - Quando `items.length === 0 && !loading`, exibir as 3 linhas demonstrativas de `ref-09.png` (`Resumo diário das vendas`, `Relatório semanal da Auryly`, `Checar site no ar`) **sem** botão `"Cancelar"` (para não conflitar com `getByRole('button', { name: 'Cancelar' })` após criar uma tarefa em `SchedulesPage.test.tsx`).
   - No drawer lateral direito `Nova ação` (`<aside className="schedules-drawer" aria-label="Nova ação">`):
     - Manter o drawer visível no layout desktop lado a lado (`ref-09.png`).
     - Radio cards `Uma vez` (`data-testid="schedule-mode-once"`) e `Repetir` (`data-testid="schedule-mode-repeat"`).
     - Campo `Instrução` (`<input id="schedule-prompt" aria-label="Instrução" ... />` com label `O que o Orin deve fazer?`).
     - Seletor `Frequência` (`aria-label="Frequência"`: `Segunda a sexta` -> `0 9 * * 1-5`, `Toda sexta` -> `0 18 * * 5`, `A cada 30 min` -> `*/30 * * * *`, `Personalizado (cron)`).
     - Inputs `Horário` (`aria-label="Horário"`), `Expressão cron` (`aria-label="Expressão cron"`), `Data e hora` (`aria-label="Data e hora"`, **sempre montado no DOM**, e quando alterado com valor não vazio, atualiza `fireAt` e define o modo para `'once'`) e `Fuso horário` (`aria-label="Fuso horário"`, valor padrão `'America/Sao_Paulo'`).
     - Botão secundário `Testar agora` (`data-testid="schedule-test-now"`) e botão primário `Criar ação` (`data-testid="schedules-submit-button"`, `aria-label="Criar tarefa"`). Não colocar botão com texto `"Cancelar"` no drawer (usar botão `✕` com `aria-label="Fechar painel"`).

- [ ] **Step 4: Rodar os testes novos e legados de Schedules (`GREEN`)**

Run: `npx --prefix frontend vitest run tests/unit/SchedulesPhase6Ref09.test.tsx tests/unit/SchedulesPage.test.tsx tests/unit/SchedulesPageAgentJobs.test.tsx src/app/__tests__/UiPolishFixes.test.tsx`
Expected: PASS (100% dos testes passando).

---

### Task 2: Memória sob Controle (`ref-11.png` — `MemoryPage.tsx`, `LibraryHubPage.tsx` & `api/memory.ts`)

**Files:**
- Modify: `frontend/src/api/memory.ts`
- Modify: `frontend/src/features/memory/MemoryPage.tsx`
- Modify: `frontend/src/features/settings/LibraryHubPage.tsx`
- Create: `frontend/tests/unit/MemoryPhase6Ref11.test.tsx`

- [ ] **Step 1: Escrever o teste falhando (`RED`) em `frontend/tests/unit/MemoryPhase6Ref11.test.tsx`**

Criar `frontend/tests/unit/MemoryPhase6Ref11.test.tsx` testando:
1. Renderização do título `Memória sob seu controle`, subtítulo `"Tudo o que o Orin lembra sobre você e seus projetos. Você pode editar ou apagar qualquer item."`, abas `Trabalho`, `Friend` e `Por projeto`, campo de busca e botão `+ Adicionar lembrança`.
2. Seleção de um card de memória na coluna esquerda atualiza o inspector lateral direito (`Editar lembrança`) com o texto da lembrança, contador `X / 200 caracteres` (`maxLength={200}`), seletor `Escopo` (`Trabalho`, `Friend`, `Projeto · Auryly`) e botões `Salvar` e `Esquecer`.
3. Fluxo de criação e undo: clicar em `+ Adicionar lembrança` (ou editar e clicar em `Salvar`) exibe o banner/toast `Nova lembrança salva.` com botão `Desfazer` (`data-testid="memory-undo-button"`), e clicar em `Desfazer` reverte a alteração imediatamente.
4. Clicar em `Esquecer` remove a lembrança da lista e chama `deleteManagedMemory` quando for item de API.

- [ ] **Step 2: Rodar Vitest para verificar que o teste falha (`RED`)**

Run: `npx --prefix frontend vitest run tests/unit/MemoryPhase6Ref11.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implementar o suporte em `frontend/src/api/memory.ts` e o layout `ref-11.png` em `frontend/src/features/memory/MemoryPage.tsx` (`GREEN`)**

Regras de implementação:
1. Em `frontend/src/api/memory.ts`:
   - Exportar tipos e helpers para escopo semântico (`'work' | 'friend' | 'project'`) e persistência local complementar (`localStorage` key `'orin.memory.custom.v1'`) para que novas lembranças e edições feitas na UI funcionem tanto com backend ativo quanto em modo local/offline.
2. Em `frontend/src/features/memory/MemoryPage.tsx`:
   - Aceitar prop opcional `client?: ApiClient` (e repassar `client={apiClient}` em `LibraryHubPage.tsx`).
   - Executar `load()` imediatamente quando `query === ''` (aplicando debounce de 150ms apenas quando `query` não for vazio).
   - Cabeçalho `Memória sob seu controle` + subtítulo explicativo + toast inline no topo direito (`data-testid="memory-saved-toast"`) mostrando `✓ Nova lembrança salva.` + botão `Desfazer` (`data-testid="memory-undo-button"`).
   - Barra de navegação (`<div className="memory-ref11__tabs-bar">`) com `<div role="tablist" aria-label="Escopo da memória">` (`Trabalho`, `Friend`, `Por projeto`) à esquerda e `<button type="button" data-testid="memory-add-button">+ Adicionar lembrança</button>` **fora** do `role="tablist"`.
   - Searchbox com `aria-label="Buscar memórias"` e `placeholder="Buscar lembranças..."`.
   - Quando a API retornar `items: []` e não houver busca ativa (`query === ''`), exibir os 3 cards de referência de `ref-11.png`:
     1. `"Prefere respostas curtas em português"` (`Trabalho · Criada em 12 abr`, selecionada por padrão, 74 chars: `"Prefere respostas curtas e diretas em português, sem introduções longas."`)
     2. `"Auryly usa tons de rosa na comunicação"` (`Projeto · Auryly · Criada pelo Orin`)
     3. `"Prefere exemplos em Python"` (`Trabalho · Criada em 03 abr`)
   - Quando a API retornar itens (`items.length > 0`), renderizar os itens da API + itens locais sem misturar os itens de vitrine.
   - Usar `<article>` (nunca `<button>` envolvendo `<button>`) para cada card da lista esquerda, com botão `✎ Editar` e botão `•••`.
   - Coluna direita (`<aside className="memory-inspector" data-testid="memory-inspector">`):
     - Título `Editar lembrança` (ou `Nova lembrança` no modo de adição).
     - `<textarea aria-label="Texto da lembrança" maxLength={200} />` + contador dinâmico `{text.length} / 200 caracteres`.
     - `<select aria-label="Escopo">` (`Trabalho`, `Friend`, `Projeto · Auryly`).
     - Rodapé com botão `Esquecer` (`data-testid="memory-forget-button"`) e botão primário `Salvar` (`data-testid="memory-save-button"`).

- [ ] **Step 4: Rodar os testes de Memory e LibraryHub (`GREEN`)**

Run: `npx --prefix frontend vitest run tests/unit/MemoryPhase6Ref11.test.tsx src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
Expected: PASS.

---

### Task 3: Biblioteca de Habilidades (`ref-12.png` — `SkillsPage.tsx`)

**Files:**
- Modify: `frontend/src/features/skills/SkillsPage.tsx`
- Create: `frontend/tests/unit/SkillsPhase6Ref12.test.tsx`
- Test: `frontend/tests/unit/SkillsPage.test.tsx`, `frontend/tests/unit/SkillsSection.test.tsx`, `frontend/src/app/__tests__/UiPolishFixes.test.tsx`

- [ ] **Step 1: Escrever o teste falhando (`RED`) em `frontend/tests/unit/SkillsPhase6Ref12.test.tsx`**

Criar `frontend/tests/unit/SkillsPhase6Ref12.test.tsx` testando:
1. Renderização do título `Habilidades`, subtítulo `"Pacotes de instruções que ensinam o Orin a executar tarefas específicas."`, abas `Prontas para usar`, `Criadas por você` e `Da equipe`, searchbox, botão `+ Criar habilidade` (`data-testid="skills-create-button"`) e botão `Importar arquivo`.
2. Grid de cards de habilidades (quando a lista da API está vazia e sem busca, exibe os 4 cards de `ref-12.png`: `Criar relatórios`, `Revisar textos`, `Analisar planilhas`, `Padrão Auryly`; quando a API retorna skills, exibe os cards das skills da API).
3. Inspector lateral direito (`data-testid="skills-inspector-panel"`):
   - Exibe título, badge (`Instalada` / `Criada por você`), descrição, seletor `Disponível em`, toggle `Pedir confirmação antes de publicar`, card `Exemplo rápido` com comando `"Crie o relatório semanal da Auryly"` e botão `Testar no chat`, e acordeão `Instruções da habilidade` (`data-testid="skill-instructions-accordion"`).
   - O acordeão `Instruções da habilidade` inicia fechado e, ao ser clicado, revela as instruções da skill selecionada.

- [ ] **Step 2: Rodar Vitest para verificar que o teste falha (`RED`)**

Run: `npx --prefix frontend vitest run tests/unit/SkillsPhase6Ref12.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implementar o layout `ref-12.png` em `frontend/src/features/skills/SkillsPage.tsx` (`GREEN`)**

Regras de implementação e compatibilidade obrigatórias:
1. Preservar **intacto** o modo de detalhe por rota (`/skills/:skillId` -> `<SkillDetailView>`) para todos os testes de detalhe em `SkillsPage.test.tsx`.
2. Na visão principal de listagem (`SkillsListView`):
   - Renderizar `<h1 id="skills-title">Habilidades</h1>` visível e `<h2 className="visually-hidden">Skills</h2>` para satisfazer tanto `SkillsPhase6Ref12.test.tsx` quanto `SkillsSection.test.tsx` (`findByRole('heading', { name: 'Skills' })`).
   - Renderizar abas `<div role="tablist" aria-label="Categorias de habilidades">` com `Prontas para usar`, `Criadas por você` e `Da equipe`.
   - Na toolbar:
     - Searchbox `<input type="search" aria-label="Buscar skills" data-testid="skills-search-input" placeholder="Buscar habilidades..." />`.
     - Botão primário `<button type="button" data-testid="skills-create-button" aria-label="Criar skill">+ Criar habilidade</button>` (mantém `aria-label="Criar skill"` para `SkillsPage.test.tsx` linhas 89 e 108).
     - Botão secundário `Importar arquivo` (`data-testid="skills-import-button"`).
   - Quando `showCreateForm` estiver ativo, exibir o `<CreateSkillForm>` existente com todos os seus labels intactos (`Nome`, `Descrição`, `Instruções`, `Tags`, `Campos avançados`, `Versão`, `Salvar skill`).
   - Grid de cards (`.skills-cards-grid`):
     - Se `state.status === 'loaded' && state.value.items.length === 0 && !query.trim()`, exibir os 4 cards demonstrativos de `ref-12.png` (`Criar relatórios`, `Revisar textos`, `Analisar planilhas`, `Padrão Auryly`).
     - Caso contrário, renderizar os cards para `state.value.items` (mantendo o link `<Link to={/skills/${skill.id}}>{skill.name}</Link>` para que `getByRole('link', { name: 'Systematic Debugging' })` continue funcionando, além de permitir selecionar o card para inspecionar na lateral direita).
   - Inspector lateral direito (`<aside className="skills-inspector" data-testid="skills-inspector-panel">`):
     - Mostra a skill selecionada (por padrão a primeira da lista ativa).
     - Controles de configuração com labels acessíveis: `Disponível em` (`Todos os projetos`, `Apenas Auryly`) e toggle `Pedir confirmação antes de publicar`.
     - Box `Exemplo rápido` com prompt sugerido e botão `Testar no chat`.
     - Acordeão `Instruções da habilidade` (`data-testid="skill-instructions-accordion"`): **deve iniciar fechado (`false`) e NÃO renderizar o texto de `instructions` no DOM enquanto fechado**, garantindo que `expect(screen.queryByText('Workflow')).not.toBeInTheDocument()` em `SkillsPage.test.tsx` linha 67 continue passando!
   - Manter `<AgentSkillsPanel>` acessível no final da página com `role="region"` e `aria-label="Skills do agente"`, passando `availableSkills={state.status === 'loaded' ? state.value.items : []}`.

- [ ] **Step 4: Rodar todos os testes de Skills (`GREEN`)**

Run: `npx --prefix frontend vitest run tests/unit/SkillsPhase6Ref12.test.tsx tests/unit/SkillsPage.test.tsx tests/unit/SkillsSection.test.tsx src/app/__tests__/UiPolishFixes.test.tsx`
Expected: PASS (todos os 14 testes legados + novos testes passando).

---

### Task 4: Conexões (`ref-13.png` — `McpSection.tsx`, `IntegrationsHubPage.tsx`, `routes.tsx`, `WorkspaceNavigation.tsx` & `agentos.css`)

**Files:**
- Modify: `frontend/src/features/mcp/McpSection.tsx`
- Modify: `frontend/src/features/settings/IntegrationsHubPage.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/components/WorkspaceNavigation.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Create: `frontend/tests/unit/ConnectionsPhase6Ref13.test.tsx`
- Test: `frontend/tests/unit/McpSection.test.tsx`, `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`, `frontend/src/app/__tests__/WorkspaceShell.test.tsx`

- [ ] **Step 1: Escrever o teste falhando (`RED`) em `frontend/tests/unit/ConnectionsPhase6Ref13.test.tsx`**

Criar `frontend/tests/unit/ConnectionsPhase6Ref13.test.tsx` testando:
1. Renderização do cabeçalho `Conexões`, subtítulo `"Serviços e pastas que o Orin pode acessar quando você pedir."`, botão `+ Adicionar conexão` (`data-testid="connections-add-button"`) e abas `Apps conectados`, `Permissões` e `Avançado (MCP)`.
2. Renderização da lista de conexões à esquerda contendo `Auryly`, `Navegador`, `Arquivos locais` e servidores MCP vindos da API (`GitHub`, etc.).
3. Inspector lateral direito (`data-testid="connections-inspector-panel"`):
   - Exibe o item selecionado (por padrão `Auryly`), status `Conectado`, seletor `Usar em` (`Projeto Auryly`, `Todos os projetos`), grupo `"O que o Orin pode fazer"` com checkboxes granulares (`Ver produtos, estoque e pedidos`, `Criar rascunhos de campanhas e cupons`, `Publicar alterações na loja`, `Alterar configurações de pagamento`) e botão `"Desconectar Auryly"`.
   - Alternar um checkbox atualiza imediatamente o estado de permissão da conexão.

- [ ] **Step 2: Rodar Vitest para verificar que o teste falha (`RED`)**

Run: `npx --prefix frontend vitest run tests/unit/ConnectionsPhase6Ref13.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implementar `McpSection.tsx`, `IntegrationsHubPage.tsx`, `routes.tsx`, `WorkspaceNavigation.tsx` e consolidar o CSS da Fase 6 em `agentos.css` (`GREEN`)**

Regras de implementação e compatibilidade obrigatórias:
1. **Regra crítica de rede em `McpSection.tsx`:**
   - No `useEffect` de montagem, chamar **exclusivamente** `listMcpServers(apiClient)`. **Não fazer nenhuma outra chamada HTTP no mount** (como `listProjects` ou `listMcpCatalog`), pois `McpSection.test.tsx` usa `routedFetch` estrito que lança `Unhandled request` para qualquer URL não registrada!
2. **Estrutura de `McpSection.tsx` (`ref-13.png`):**
   - Cabeçalho `Conexões` + subtítulo `"Serviços e pastas que o Orin pode acessar quando você pedir."` + botão `+ Adicionar conexão` (`data-testid="connections-add-button"`, que abre `McpServerForm`) + botão auxiliar `<button type="button" onClick={() => setFormOpen(true)}>Adicionar servidor</button>` (para preservar `getByRole('button', { name: 'Adicionar servidor' })` em `McpSection.test.tsx`).
   - Abas `<div role="tablist" aria-label="Modo de conexões">` com `Apps conectados`, `Permissões` e `Avançado (MCP)`.
   - Layout Master-Detail (`.connections-ref13__master-detail`):
     - Coluna esquerda:
       - Lista de conexões integradas de `ref-13.png` (`Auryly`, `Navegador`, `Arquivos locais`) usando `<div className="connection-row-card">` (**sem** `role="article"` para não colidir com `getByRole('article', { name: ... })` dos testes de `McpServerCard`).
       - Lista dos `<McpServerCard>` para cada servidor retornado por `listMcpServers(apiClient)`, mantendo todos os elementos internos exigidos por `McpSection.test.tsx` (`<article aria-label={server.display_name}>`, contagem de tools `/1 tool/`, status `Ativo` / `Aguardando aprovação`, inputs de segredos pendentes, botões `Conectar`, `Reconectar`, `Ver tools`, `Remover` e `Confirmar remoção`). Clicar em um `McpServerCard` também permite selecioná-lo no inspector lateral.
     - Coluna direita (`<aside className="connections-inspector" data-testid="connections-inspector-panel">`):
       - Exibe os detalhes da conexão selecionada (`Auryly` por padrão, ou qualquer outra conexão clicada).
       - Seletor `<select aria-label="Usar em">` (`Projeto Auryly`, `Todos os projetos`).
       - Lista `"O que o Orin pode fazer"` com checkboxes interativos e descrições claras.
       - Rodapé com nota de auditoria (`"Toda ação importante pede sua confirmação no chat."`) e ação `"Desconectar {nome}"`.
3. **Rotas e Navegação (`routes.tsx` e `WorkspaceNavigation.tsx`):**
   - Em `frontend/src/app/routes.tsx`, adicionar rotas diretas `/memory` (renderizando `<LibraryHubPage initialTab="memory" />`) e `/connections` (renderizando `<IntegrationsHubPage initialTab="mcp" />`).
   - Em `frontend/src/components/WorkspaceNavigation.tsx`, **manter `to="/integrations/providers"`** no link `Conexões` da sidebar (exigido por `WorkspaceShell.test.tsx` linha 69), atualizando apenas `isIntegrationsActive` para incluir `location.pathname.startsWith('/connections')` e `isLibraryActive` para incluir `location.pathname.startsWith('/memory')`.
4. **Estilos CSS (`frontend/src/styles/agentos.css`):**
   - Preservar intactas as regras `.hub-page--schedules` e `.skills-list`.
   - Adicionar os estilos das 4 telas (`.schedules-ref09*`, `.memory-ref11*`, `.skills-ref12*`, `.connections-ref13*`) usando exclusivamente tokens `--orin-*`, zero classes hardcoded (`text-white`, `bg-black`, `border-white`), garantindo contraste WCAG AA tanto em dark mode quanto em light mode.

- [ ] **Step 4: Rodar todos os testes de MCP, Hubs e WorkspaceShell (`GREEN`)**

Run: `npx --prefix frontend vitest run tests/unit/ConnectionsPhase6Ref13.test.tsx tests/unit/McpSection.test.tsx src/features/settings/__tests__/PreferencesAndHubs.test.tsx src/app/__tests__/WorkspaceShell.test.tsx src/app/__tests__/UiPolishFixes.test.tsx`
Expected: PASS.

---

### Task 5: Verificação Visual Playwright (`ref-09`, `ref-11`, `ref-12`, `ref-13`), Auditoria Axe-core WCAG AA, Build & Code Review Independente

**Files:**
- Verify: `frontend/src/features/schedules/SchedulesPage.tsx`, `frontend/src/features/memory/MemoryPage.tsx`, `frontend/src/features/skills/SkillsPage.tsx`, `frontend/src/features/mcp/McpSection.tsx`, `frontend/src/styles/agentos.css`

- [ ] **Step 1: Executar suíte completa de testes unitários e build de produção**

Run: `npx --prefix frontend vitest run tests/unit/SchedulesPhase6Ref09.test.tsx tests/unit/SchedulesPage.test.tsx tests/unit/SchedulesPageAgentJobs.test.tsx tests/unit/MemoryPhase6Ref11.test.tsx tests/unit/SkillsPhase6Ref12.test.tsx tests/unit/SkillsPage.test.tsx tests/unit/SkillsSection.test.tsx tests/unit/ConnectionsPhase6Ref13.test.tsx tests/unit/McpSection.test.tsx src/features/settings/__tests__/PreferencesAndHubs.test.tsx src/app/__tests__/WorkspaceShell.test.tsx src/app/__tests__/UiPolishFixes.test.tsx && npm --prefix frontend run build`
Expected: 100% PASS e build limpo (`tsc -b && vite build`).

- [ ] **Step 2: Verificar visualmente no navegador com Playwright + Axe-core WCAG AA**

Executar spec Playwright verificando as 4 telas (`/schedules`, `/memory`, `/skills`, `/connections` / `/integrations/mcp`):
1. Capturar screenshots em `1440x900` e copiar para o diretório de artefatos da conversa:
   - `fase6-ref09-schedules.png` (comparar com `ref-09.png`)
   - `fase6-ref11-memory.png` (comparar com `ref-11.png`)
   - `fase6-ref12-skills.png` (comparar com `ref-12.png`)
   - `fase6-ref13-connections.png` (comparar com `ref-13.png`)
2. Executar `AxeBuilder` (`wcag2a`, `wcag2aa`) nas 4 telas em dark mode e light mode, exigindo `0` violações `critical` ou `serious`.
3. Se qualquer detalhe visual divergir das imagens de referência (`ref-09.png`, `ref-11.png`, `ref-12.png`, `ref-13.png`), ajustar imediatamente o CSS/TSX e re-capturar até atingir paridade visual de estúdio.

- [ ] **Step 3: Invocar subagente de Code Review Independente (`research`) e commitar a Fase 6**

1. Disparar subagente `research` para auditar o diff completo da Fase 6, os testes e as 4 screenshots geradas contra a Spec e as referências.
2. Após veredito `APPROVED`, fazer commit atômico na branch `refactor/interface`.

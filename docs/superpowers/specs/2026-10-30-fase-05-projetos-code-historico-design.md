# Especificação de Design — Fase 5 de 7: Espaço de Projetos (`ref-05`), Contexto de Projeto (`ref-07`), Orin Code Split Workbench (`ref-08`) e Máquina do Tempo (`ref-16`)

> [!IMPORTANT]
> **Escopo Arquitetural (Fase 5 de 7 do Roadmap Mestre):**
> Esta especificação cobre a reconstrução das 4 superfícies interligadas de **Projetos, Orin Code e Histórico/Restauração**, alinhadas às referências visuais `ref-05.png`, `ref-07.png`, `ref-08.png` e `ref-16.png` e ao [Relatório Especializado 04](file:///C:/Users/User/.gemini/antigravity/brain/f9309199-920e-4114-94d6-10ab7d5212cf/relatorio-04-projetos-code-historico.md), preservando 100% dos contratos de API e `data-testid` existentes.

---

## 1. Visão Geral das 4 Superfícies Visuais

````carousel
![Galeria de Projetos (ref-05.png)](C:/Users/User/.gemini/antigravity/brain/3b3d2ee8-ba1f-4504-9638-a5bb14471222/ref-05.png)
<!-- slide -->
![Dashboard e Contexto do Projeto (ref-07.png)](C:/Users/User/.gemini/antigravity/brain/3b3d2ee8-ba1f-4504-9638-a5bb14471222/ref-07.png)
<!-- slide -->
![Orin Code Split-Screen Workbench (ref-08.png)](C:/Users/User/.gemini/antigravity/brain/3b3d2ee8-ba1f-4504-9638-a5bb14471222/ref-08.png)
<!-- slide -->
![Máquina do Tempo: Histórico e Restauração (ref-16.png)](C:/Users/User/.gemini/antigravity/brain/3b3d2ee8-ba1f-4504-9638-a5bb14471222/ref-16.png)
````

---

## 2. Superfície 1 — Galeria de Projetos (`ProjectsGalleryPage.tsx` em `/projects` — `ref-05.png`)

### 2.1. Rota e Integração ao `WorkspaceShell`
- **Nova rota `/projects`** em [`routes.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/app/routes.tsx) renderizando `<ProjectsGalleryPage />` integrado à [`WorkspaceNavigation`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx) (`initialView="projects"`).
- Na [`WorkspaceNavigation.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx), cada item na aba `Projetos` exibe o ícone de pasta à esquerda, o nome do projeto e o contador `{N} conversas` alinhado à direita (`data-testid="sidebar-project-chat-count"`), além de um botão/link `"Ver todos os projetos"` (`data-testid="sidebar-view-all-projects"`) que navega para `/projects`.

### 2.2. Cabeçalho e Barra de Ações
- **Topbar Global (`56px`):** botão pill `"Conversar por voz"` + avatar `<OrinMascot size={30} variant="flat" statusDot="online" />` à direita.
- **Hero da Página:**
  - `<h1>Projetos</h1>` + subtítulo `"Um espaço para cada trabalho."`.
  - Input de busca instantânea `🔍 Buscar projetos...` (`data-testid="projects-search-input"`, filtra por `name` e `description` de forma case-insensitive).
  - Botão primário lavanda `+ Novo projeto` (`data-testid="projects-new-button"`), que abre o modal/formulário de criação conectado a `createProject(client, input)`.

### 2.3. Grid de Cards de Projetos (`data-testid="projects-catalog-grid"`)
- Layout em CSS Grid de 3 colunas em desktop (`grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 20px`).
- Cada card (`data-testid="project-card-${project.project_id}"`) apresenta:
  1. **Topo do Card:**
     - Container quadrado arredondado (`44×44px`, `border-radius: 12px`) com ícone de pasta colorido por paleta determinística (`purple` `#B4A4EF`, `blue` `#60A5FA`, `emerald` `#34D399`, `amber` `#FBBF24`).
     - **Badge de Tarefa Ativa (`data-testid="project-card-active-badge"`):** quando o projeto possuir conversas em execução (`state === 'running' | 'working' | 'streaming' | 'in_progress'`), exibe a pill `● {N} tarefa(s) em andamento`.
     - Botão de menu rápido `•••` no canto superior direito.
  2. **Corpo do Card:**
     - Título `<h2>{project.name}</h2>` (`20px`, bold) e descrição curta (`project.description || 'Produto e conteúdo'`).
     - Linha de metadados com ícones de documento e anexo: `{chatsCount} conversas · {filesCount} arquivos` (`data-testid="project-card-counts"`).
  3. **Rodapé do Card:**
     - Separador horizontal (`1px solid rgba(255, 255, 255, 0.07)`).
     - Timestamp relativo com ícone de relógio (`Atualizado há 20 min` / `Atualizado recentemente`).
     - Link em lavanda `Abrir projeto →` (`to="/projects/${project.project_id}"`, `data-testid="project-card-open-link"`).

---

## 3. Superfície 2 — Dashboard Interno e Contexto Compartilhado ([`ProjectPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/ProjectPage.tsx) — `ref-07.png`)

### 3.1. Estrutura e Topbar Contextual
- Envolve [`ProjectPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/ProjectPage.tsx) com a sidebar [`WorkspaceNavigation`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx) à esquerda (`initialView="projects"`).
- **Topbar (`56px`):**
  - Breadcrumb à esquerda (`data-testid="project-breadcrumb"`): link `Projetos` (`/projects`) + `/` + `<strong>{project.name}</strong>`.
  - Botão `"Conversar por voz"` + `<OrinMascot size={30} variant="flat" statusDot="online" />` à direita.

### 3.2. Hero do Projeto e 4 Abas Internas
- **Cabeçalho do Projeto:**
  - Ícone grande de pasta lavanda (`48×48px`) ao lado de `<h1>{project.name}</h1>` e subtítulo `{project.description || 'Produto, conteúdo e automações.'}`.
  - Botão à direita `⚙️ Configurações do projeto` (`data-testid="project-settings-button"`), que alterna/foca a edição da pasta local do projeto.
- **Barra de Abas (`role="tablist"`, `data-testid="project-tabs"`):**
  - `Conversas` (`data-testid="project-tab-conversations"`, ativa por padrão)
  - `Arquivos` (`data-testid="project-tab-files"`)
  - `Instruções` (`data-testid="project-tab-instructions"`)
  - `Memória` (`data-testid="project-tab-memory"`)

### 3.3. Layout de 2 Colunas (`Coluna Central` + `Contexto compartilhado`)
1. **Coluna Central:**
   - **Composer Inline do Projeto (`data-testid="project-inline-composer"`):**
     - Textarea `"Nova conversa neste projeto..."` (`data-testid="project-composer-input"`).
     - Barra inferior com botão de anexo (`📎`), seletor pill de modo (`Automático ▾` com opções `Automático`, `Chat`, `Code`, `data-testid="project-composer-mode"`), botão de microfone (`🎙️`) e botão circular lavanda de envio (`data-testid="project-composer-submit"`).
     - Ao submeter um texto, navega para `/projects/${encodeURIComponent(projectId)}/new?mode=${selectedMode === 'code' ? 'code' : 'chat'}&prompt=${encodeURIComponent(prompt.trim())}`.
   - **Seção `Conversas recentes`:**
     - Cabeçalho com `<h2>Conversas recentes</h2>` e input `🔍 Buscar conversas` (`data-testid="project-chats-search"`).
     - Lista de cards ricos (`data-testid="project-chat-list"`): cada conversa exibe ícone semântico (documento para Code/criação, lupa para pesquisa, balão para conversa), título em destaque, resumo contextual de 1 linha, pill `◉ Em andamento` quando ativa, tempo relativo e chevron `›`.
   - **Conteúdo das Abas `Arquivos`, `Instruções` e `Memória`:**
     - Na aba `Arquivos`: exibe a pasta vinculada e a lista de arquivos de contexto do projeto.
     - Na aba `Instruções`: permite visualizar, adicionar e remover instruções permanentes do projeto (`data-testid="project-instructions-editor"`), persistidas com chave `orin.project.instructions.v1.${projectId}`.
     - Na aba `Memória`: lista as memórias de `listProjectMemories` e link para gerenciamento completo.
2. **Coluna Direita — Card `Contexto compartilhado` (`340px`, `data-testid="project-shared-context"`):**
   - Título `<h3>Contexto compartilhado</h3>` e texto `"Todas as conversas deste projeto usam este contexto."`.
   - **Card de Pasta Local (`data-testid="project-context-folder-card"`):** exibe o caminho vinculado (`project.workspace?.path || 'C:/Projetos/' + slug`) e subtexto `{filesCount} arquivos` + seta `›` (clicar abre a aba `Arquivos` ou o seletor de pasta).
   - **Card de Instruções (`data-testid="project-context-instructions-card"`):** exibe `{instructionsCount} instruções` + seta `›` (clicar abre a aba `Instruções`).
   - **Ações Rápidas:**
     - Botão `⬆️ Adicionar arquivos` (`data-testid="project-add-files-button"`)
     - Botão `📄 Editar instruções` (`data-testid="project-edit-instructions-button"`)
3. **Preservação Estrita de Contratos Legados ([`ProjectSessionManagement.test.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/__tests__/ProjectSessionManagement.test.tsx)):**
   - Manter presentes e funcionais no DOM de `ProjectPage.tsx`:
     - `data-testid="project-new-chat-button"` (`href="/projects/${projectId}/new?mode=chat"`)
     - `data-testid="project-new-code-button"` (`href="/projects/${projectId}/new?mode=code"`)
     - `data-testid="project-workspace-section"` (exibindo o `path` atual ou `'Workspace gerenciado'`), `data-testid="project-workspace-input"`, `data-testid="project-workspace-pick-button"`, `data-testid="project-workspace-save-button"`, `data-testid="project-workspace-detach-button"` e `data-testid="project-chat-code-pill"`.

---

## 4. Superfície 3 — Orin Code Split-Screen Workbench (`CodeWorkbenchPanel.tsx` + [`ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx) — `ref-08.png`)

### 4.1. Topbar do Orin Code e Acionador da Máquina do Tempo
- Quando `conversationMode === 'code'`, a topbar de [`ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx) exibe:
  - Título/Breadcrumb à esquerda: `{activeConvProjectName ? `${activeConvProjectName} / ${conversation?.title}` : conversation?.title}`.
  - Botão pill `🕒 Histórico` (`data-testid="code-open-history-button"`) ao lado de `Conversar por voz`, que aciona `openRewind()` para abrir a superfície de Histórico e Restauração (`ref-16.png`).

### 4.2. Coluna Esquerda (Conversa Limpa, Resumo de Alterações e Checklist)
- **Card Acordeão de Arquivos Alterados (`data-testid="code-changed-files-card"`):**
  - Exibe `📄 {changedFiles.length} arquivos alterados · Ver alterações` com chevron `▾`.
  - Ao clicar em `Ver alterações` (`data-testid="code-view-changes-button"`), seleciona automaticamente a aba `Arquivos` no `CodeWorkbenchPanel` (e expande/recolhe a lista rápida de arquivos).
- **Card de Checklist de Etapas em Tempo Real (`data-testid="code-steps-checklist-card"`):**
  - Quando houver `activity.todos` (ou etapas de construção), renderiza card visual limpo com ícones circulares lavanda `✓` para etapas concluídas e `○` com rótulo `Em andamento` à direita para a etapa ativa.
- **Composer no Modo Code:**
  - Placeholder `"Peça um ajuste..."` quando `conversationMode === 'code'` e já houver mensagens na conversa.

### 4.3. Coluna Direita — `CodeWorkbenchPanel.tsx` (`data-testid="code-workbench-panel"`)
- Renderizado lado a lado com `.chat__scroll` dentro de `.chat__body` quando `conversationMode === 'code'` (podendo ser recolhido/expandido sem quebrar `workspace-files-drawer`).
- **Barra de 3 Abas (`role="tablist"`, `data-testid="code-workbench-tabs"`):**
  - `Prévia` (`data-testid="workbench-tab-preview"`, ativa por padrão)
  - `Arquivos` (`data-testid="workbench-tab-files"`)
  - `Terminal` (`data-testid="workbench-tab-terminal"`)
- **Aba `Prévia` (Live Preview):**
  - **Toolbar de Navegador:**
    - Botões `←`, `→` e `↻` (`data-testid="workbench-preview-reload"`).
    - Input de URL (`data-testid="workbench-preview-url-input"`): preenchido automaticamente com a última `detectedUrl` dos eventos/tasks da sessão ou `http://localhost:3000/trilhas/ferramentas-do-hype` como endereço de demonstração/edição.
    - Toggle de Viewport (`data-testid="workbench-viewport-toggle"`): botões `Desktop` (`data-testid="workbench-viewport-desktop"`) e `Mobile` (`data-testid="workbench-viewport-mobile"`, que ajusta o container de preview para `390px` centralizado).
    - Botão `↗` (`data-testid="workbench-preview-open-external"`): abre a URL validada (`http:` / `https:`) em nova aba com `noopener,noreferrer`.
  - **Palco de Renderização (`data-testid="workbench-preview-stage"`):**
    - Se houver um servidor local ativo ou URL configurada, exibe o preview (com card de prévia visual rico estilo Auryly como fallback imediato em ambiente sem servidor externo na porta 3000 para evitar tela de erro de conexão em branco, mantendo o `iframe` ou alternância para o `iframe` real).
- **Aba `Arquivos` (`data-testid="workbench-files-view"`):**
  - Lista de arquivos modificados/do workspace com filtro (`workspace-files-search` compatível) e visualização de diff colorido via [`StructuredDiffView`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/WorkspaceFileCard.tsx#L328) ao selecionar um arquivo.
- **Aba `Terminal` (`data-testid="workbench-terminal-view"`):**
  - Consolida os comandos executados (`run_command` / `bash`) e seus outputs em estilo terminal escuro.
- **Rodapé de Status do Workbench (`data-testid="workbench-status-footer"`):**
  - À esquerda: `● Em andamento · 2 min` (quando `running`) ou `● Pronto` (quando concluído).
  - À direita: botão `📄 {changedFiles.length} arquivos alterados ▾` (que alterna para a aba `Arquivos`).

---

## 5. Superfície 4 — Máquina do Tempo: Histórico e Restauração ([`RewindPicker.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/RewindPicker.tsx) — `ref-16.png`)

### 5.1. Layout Full-Canvas em 3 Colunas Integrado ao `WorkspaceShell`
- [`RewindPicker.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/RewindPicker.tsx) passa a ocupar a área à direita da sidebar (`left: var(--orin-sidebar-width, 264px)` em desktop, `inset: 0` em `< 960px`), mantendo `role="dialog"`, `aria-modal="true"` e `aria-labelledby="rewind-title"`.
- **Topbar e Cabeçalho (`ref-16.png`):**
  - Breadcrumb `{projectName || 'Projeto'} / Histórico` + link/botão `← Voltar ao projeto` (`data-testid="history-back-button"`, aciona `onClose`).
  - Título principal `Volte a um ponto anterior` (`data-testid="history-headline"`) e subtítulo `"Revise o que mudou antes de restaurar."` (preservando `<h2 id="rewind-title" className="visually-hidden">Voltar a uma mensagem</h2>` para testes legados).

### 5.2. As 3 Colunas da Máquina do Tempo
1. **Coluna 1 (Esquerda, `300px`) — `Histórico do projeto` (`data-testid="history-timeline-column"`):**
   - Mantém `<ol role="listbox" aria-label="Suas mensagens">` com os itens `role="option"` (`id="rewind-${item.messageId}"`).
   - Renderiza cada checkpoint na linha do tempo vertical com nó circular (`◉` quando selecionado, `○` nos demais), horário (`Agora`, `Hoje, 14:20`, `Hoje, 14:05`), título amigável da mensagem e subtexto `{N} arquivos` (ou `Estado inicial`).
2. **Coluna 2 (Centro, `1fr`) — Inspeção de Diffs do Ponto Selecionado (`data-testid="history-diff-column"`):**
   - Cabeçalho com o título do ponto selecionado e metadados `{timestamp} · {changed.length} arquivos neste ponto`.
   - Lista de cards acordeão para cada arquivo alterado (`data-testid="history-file-card"`):
     - Cabeçalho do arquivo com ícone `📄`, caminho (`src/App.tsx`, etc.), badge verde `Modificado` e chevron `^`.
     - Corpo com diff colorido em linhas numeradas (`-` vermelho escuro para remoção, `+` verde escuro para adição) usando os hunks reais de `structuredPatch` do turno ou um diff resumido sintetizado a partir do arquivo alterado.
3. **Coluna 3 (Direita, `320px`) — `O que será restaurado` (`data-testid="history-confirm-column"`):**
   - Card explicativo com 2 itens marcados com check circular lavanda (`✓`):
     - **Arquivos da pasta do projeto:** *"Todos os arquivos serão restaurados para o estado deste ponto."*
     - **Conversa até este ponto:** *"O histórico da conversa também será restaurado."*
   - Nota de segurança: `ℹ️ Publicações e ações em serviços externos não são desfeitas.`
   - Alerta `unbackable` preservado quando aplicável (com botão `Restaurar mesmo assim`).
   - **Rodapé de Ação:**
     - Botão secundário `Cancelar` (`onClose`) e botão primário lavanda `Restaurar este ponto` (`data-testid="history-restore-point-button"`, que dispara `onRewind(current.messageId, changed.length > 0 ? 'both' : 'conversation')`).
     - Mantém no DOM os botões granulares `Restaurar o código`, `Voltar a conversa`, `Os dois` e `Fechar` para 100% de compatibilidade com a suíte de testes existente de `RewindPicker`.

---

## 6. Mentalidade Adversária (Red-Team) e Casos de Borda Cobertos

1. **Prevenção de XSS na Barra de URL do Live Preview (`CodeWorkbenchPanel.tsx`):**
   - Qualquer URL digitada pelo usuário ou vinda de `detectedUrl` deve ser validada via `new URL(raw)` aceitando **exclusivamente** protocolos `http:` e `https:`. URLs `javascript:`, `data:`, `file:` ou malformadas são rejeitadas e nunca passadas para `iframe.src` nem `window.open`.
2. **Retrocompatibilidade Total com `ProjectSessionManagement.test.tsx` e `RewindPicker`:**
   - Todos os `data-testid`, `role`, `aria-label` e textos esperados pelos testes existentes de `ProjectPage`, `WorkspaceNavigation`, `ChatPage` e `RewindPicker` são preservados.
3. **Estado Vazio / Sem Servidor Local no Preview:**
   - Se o projeto ainda não tiver servidor HTTP local ativo ou se a conversa não tiver `structuredPatch` nos checkpoints antigos, o Workbench e a Máquina do Tempo renderizam estados visuais completos e informativos (nunca telas quebradas ou listas vazias sem explicação).
4. **Isolamento da Sidebar Global (`264px`):**
   - Assim como na Fase 4, tanto `/projects`, `/projects/:projectId`, o modo Code Split-Screen quanto a Máquina do Tempo mantêm a sidebar esquerda `WorkspaceNavigation` visível e alinhada em desktop (`>= 960px`).

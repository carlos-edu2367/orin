# Spec: Remoção do Navegador Interno (Playwright MCP Only) & Redesign dos Cards Interativos (`ask_user` / `CodeSessionProposalCard` / Aprovações)

**Data:** 2026-10-17  
**Escopo:** Arquitetural / Multi-componente (Backend Python + Catálogo/Serviço MCP + Frontend React/CSS)

---

## 1. Contexto e Motivação

1. **Remoção do Navegador Interno em favor do Playwright MCP:**
   - O subsistema de navegador interno do Orin (`IsolatedConversationBrowser`, instalador próprio do Chromium de ~150 MB, `PlaywrightBrowserAdapter`, e as 13 tools nativas `browse_page` / `browser_*` / `verify_frontend`) adiciona alta complexidade de manutenção, processos filhos extras por conversa e problemas de dependências de sistema, sendo que o servidor oficial **Playwright MCP** (`@playwright/mcp`) resolve a automação de navegador de forma padronizada via MCP.
   - O objetivo é remover completamente o navegador interno do Orin e, na seção **Configurações > Navegador**, oferecer exclusivamente a instalação/ativação e gerenciamento do **Playwright MCP**.

2. **Melhoria de UI e Correção de Bugs nos Cards Interativos (`ask_user`, `CodeSessionProposalCard`, Aprovações):**
   - A inspeção visual da imagem enviada pelo Carlos e a auditoria do código revelaram **3 bugs funcionais graves** e **problemas visuais claros** nos cards de decisão do usuário (`UserQuestionCard`, `CodeSessionProposalCard`, `McpApprovalCard`, `PluginApprovalCard`, `ToolPermissionCard`):
     - **Bug 1 (`[REDACTED]` no Prompt do `CodeSessionProposalCard`):** Em `src/agentos/agentic/models.py`, `_SENSITIVE_KEY_PARTS` inclui `"prompt"` e `"code_session_proposal"` não está em `_LARGE_BUDGET_KEYS`. Como resultado, `_bounded()` substitui `code_session_proposal["prompt"]` por `"[REDACTED]"` (e rejeitaria prompts > 512 caracteres).
     - **Bug 2 (`Provider: openai` / `Modelo: default` e ausência do botão "Escolher pasta…" no `CodeSessionProposalCard`):**
       - Modelos menores preenchem `provider="openai"` e `model_id="default"` ao chamar `propose_code_session`, sobrescrevendo o `default_provider` (`openrouter`) e `default_model_id` (`Qwen3.7 Flash`) da sessão atual.
       - Além disso, `ChatPage.tsx` -> `TurnTimeline.tsx` -> `ActivityStream.tsx` não repassava `client`, `defaultProvider`, `defaultModelId` nem `models` para `CodeSessionProposalCard`, ocultando o botão `"Escolher pasta…"` e a lista real de modelos do provider.
     - **Bug 3 Colateral (`McpServerService.propose` via UI com `catalog_id`):** Em `src/agentos/mcp/service.py`, `propose()` não hidratava `transport`, `command`, `args`, `url` e `secret_names` a partir de `find_catalog_entry(catalog_id)` quando chamado pela rota HTTP `POST /v1/mcp/servers` (apenas a tool `configure_mcp` do agente fazia isso).
     - **Problemas de UI/CSS:**
       - `.approval-card__field` estiliza apenas `input`, deixando `<select>` e `<textarea>` com aparência nativa crua do navegador (fundo cinza `#3b3b3b`, sem padding, selects colados uns nos outros).
       - Dupla borda verde aninhada (`cards-inside-cards`): `.approval-card__form` e `.user-question-card__step` criam uma segunda caixa com borda verde dentro do `.approval-card` / `.user-question-card`.
       - No `UserQuestionCard` (`ask_user`), uma `<textarea>` de observação adicional fica sempre aberta ocupando espaço vertical mesmo em perguntas simples de múltipla escolha.

---

## 2. Arquitetura e Mudanças Propostas

### Parte A — Remoção Completa do Navegador Interno e Catálogo Playwright MCP

1. **Catálogo e Serviço MCP (`src/agentos/mcp/catalog.py` e `src/agentos/mcp/service.py`):**
   - Adicionar entrada oficial **`playwright`** ao `CATALOG`:
     - `catalog_id="playwright"`
     - `display_name="Playwright"`
     - `summary="Navegador automatizado via Playwright MCP para abrir páginas dinâmicas, clicar, preencher formulários e capturar telas."`
     - `transport=McpTransport.STDIO`
     - `command="npx"`
     - `args=("-y", "@playwright/mcp@latest")`
     - `setup_instructions="Requer Node.js (npx) disponível no ambiente. Nenhuma chave ou credencial é necessária."`
     - `keywords=("playwright", "browser", "navegador", "web", "chromium", "automacao", "pagina")`
   - Em `McpServerService.propose()` (`src/agentos/mcp/service.py`):
     - Quando `catalog_id` for informado e `transport` não estiver preenchido no payload, buscar `find_catalog_entry(catalog_id)` e preencher automaticamente `transport`, `command`, `args`, `url` e `secret_names` (mantendo compatibilidade com overrides explícitos).

2. **Remoção das Tools Nativas de Navegador e Workers Internos:**
   - **`src/agentos/agentic/agent_tools.py`:**
     - Remover registro de `browse_page`, `browser_observe`, `browser_click`, `browser_fill`, `browser_press`, `browser_select`, `browser_check`, `browser_screenshot`, `browser_back`, `browser_wait_for`, `browser_scroll`, `browser_submit` e `verify_frontend`.
     - Remover `verify_frontend` do `code_completion_gate` (mantendo `verify_project` e comandos de teste/build).
     - Em `preview_start`, iniciar apenas o servidor local e aguardar a porta abrir, sem tentar navegar um browser interno.
   - **`src/agentos/agentic/phases.py`, `contract.py`, `runtime.py`, `session.py`, `workers/chat.py`:**
     - Remover `"browser"` de `TOOLKITS` / `TOOLKIT_TOOLS` e das exigências de contrato (`_require_mechanical_acceptance` e `synthesize` em `contract.py`).
     - Remover `browser_*` de `PHASE_TOOLS[Phase.VERIFY]` e de `runtime.py`.
     - Em `session.py`, substituir o bloco `## Browser` do system prompt por uma orientação enxuta: quando a pessoa precisar de automação de navegador, utilizar as tools do servidor MCP `playwright` (via `search_mcp_tools` / `call_mcp_tool`) ou propor sua ativação com `configure_mcp(catalog_id="playwright", display_name="Playwright")`.
     - Em `workers/chat.py`, remover `ConversationBrowserRegistry` e aquisição/liberação de browser por turno.
   - **Remoção de Módulos e Rotas do Motor Interno:**
     - Remover endpoints `GET /v1/runtime/browser` e `POST /v1/runtime/browser/install` de `src/agentos/api/gateway.py`.
     - Remover subcomando `orin browser` de `src/agentos/launcher/cli.py` e o arquivo `src/agentos/launcher/browser.py`.
     - Remover `src/agentos/agentic/browser_tools.py`, `src/agentos/browser/engine.py`, `src/agentos/browser/conversation_worker.py` e `src/agentos/browser/playwright_adapter.py`.
     - Remover `frontend/src/api/browserEngine.ts` e `frontend/src/features/conversations/BrowserActivityCard.tsx`.

3. **Nova Seção `Configurações > Navegador` (`frontend/src/features/settings/BrowserSection.tsx`):**
   - A seção de Navegador passa a gerenciar exclusivamente o **Playwright MCP** (`@playwright/mcp`):
     - Consulta `listMcpServers(client)` procurando servidor com `catalog_id === 'playwright'` ou `slug === 'playwright'`.
     - **Estado Não Instalado:** Exibe descrição clara do Playwright MCP (`npx -y @playwright/mcp@latest`) e botão primário **"Instalar Playwright MCP"**. Ao clicar, chama `createMcpServer(client, { catalog_id: 'playwright', display_name: 'Playwright' })` seguido imediatamente de `approveMcpServer(client, created.server_id, {})` (já que não requer credenciais), ativando-o em 1 clique.
     - **Estado `pending_approval`:** Exibe botão **"Conectar e ativar Playwright MCP"** que chama `approveMcpServer`.
     - **Estado Instalado (`active`, `disabled`, `error`):** Exibe badge de status (`Ativo · N tools`, `Desativado`, `Erro`), motivo de erro se houver, e ações rápidas: **Testar conexão**, **Ativar / Desativar** e **Remover**.
     - Se `capabilities.mcp_stdio` for `false` (instância remota sem suporte a processos locais `stdio`), exibe aviso claro informando que o Playwright MCP requer execução local (`stdio`).

---

### Parte B — Correção e Redesign dos Cards Interativos (`UserQuestionCard`, `CodeSessionProposalCard`, `ApprovalCard`)

1. **Desbloqueio do `prompt` em `code_session_proposal` (`src/agentos/agentic/models.py`):**
   - Adicionar `"code_session_proposal"` em `_LARGE_BUDGET_KEYS` com limite de texto compatível com especificações de código (`MAX_CODE_PROPOSAL_TEXT = 16_000`).
   - Permitir que a chave `"prompt"` dentro do objeto estruturado `code_session_proposal` não seja mascarada por `_sensitive_key`, mantendo a sanitização de segredos em padrões como `api_key=...`.

2. **Preservação do Provider/Modelo da Sessão Atual e Props Completas no `CodeSessionProposalCard`:**
   - Em `src/agentos/agentic/agent_tools.py` (`propose_code_session`):
     - Ignorar valores placeholders genéricos enviados por modelos (como `model_id="default"`, `"auto"`, `"current"`) e priorizar `self._default_provider` e `self._default_model_id` da conversa atual quando o modelo chutar um provider diferente sem necessidade.
   - Em `ChatPage.tsx`, `TurnTimeline.tsx` e `ActivityStream.tsx`:
     - Repassar `client`, `defaultProvider`, `defaultModelId` e `models` para `CodeSessionProposalCard`, garantindo que o botão **"Escolher pasta…"** apareça e que o seletor de modelos mostre os modelos reais configurados pelo usuário.

3. **Redesign Visual dos Cards (`.approval-card`, `.user-question-card`, `CodeSessionProposalCard`, `UserQuestionCard`):**
   - **Fim do `cards-inside-cards`:** Remover a caixa interna com borda duplicada em `.approval-card__form` e `.user-question-card__step`. Usar um único card elevado com superfície limpa (`var(--surface-raised)`), cabeçalho com ícone em badge sutil e separador horizontal (`border-bottom: 1px solid var(--line)`).
   - **Inputs, Selects e Textareas Consistentes:**
     - Estilizar `.approval-card__field select`, `.approval-card__field textarea` e `.approval-card__field input` com a mesma linguagem visual do Orin (`background: var(--bg-subtle)` / `var(--raised)`, `border: 1px solid var(--line-strong)`, `border-radius: 8px`, `padding: 9px 12px`, `font-size: 12.5px`, foco com `border-color: var(--signal)` e anel sutil).
     - Em `CodeSessionProposalCard`, exibir nomes amigáveis para os providers (`OpenAI`, `Anthropic`, `OpenRouter`, `OmniRoute`, `Ollama`) e espaçamento `gap-3` entre as colunas de Provider, Modelo e Modo de permissão.
   - **UX Refinada no `UserQuestionCard` (`ask_user`):**
     - Opções de escolha (`single_choice` / `checkbox`) com cartões clicáveis bem espaçados, indicador visual claro de seleção e suporte a foco/hover.
     - Em perguntas de escolha (`single_choice` / `checkbox`), substituir a `<textarea>` permanentemente aberta por um botão discreto **"+ Adicionar observação (opcional)"** (ou exibi-la aberta se já houver texto digitado), mantendo o card enxuto e focado na pergunta.

---

## 3. Tratamento de Erros, Segurança e Testes Adversariais

- **Segurança no `models.py`:** A isenção da chave `"prompt"` aplica-se **exclusivamente** quando o dicionário pai imediato é `code_session_proposal` no nível raiz do payload de atividade, e ainda passa por `sanitize_summary` para mascarar eventuais chaves coladas (`api_key=...`). Qualquer outra chave `"prompt"` ou `"raw_prompt"` em payloads arbitrários continua sendo redigida para `[REDACTED]`.
- **Falha de `npx` ao instalar Playwright MCP:** Se o ambiente não tiver Node/`npx` ou a conexão falhar durante a aprovação em `BrowserSection`, o card trata o erro sem quebrar a página, exibe a mensagem de diagnóstico retornada pelo backend e permite tentar novamente ou remover.
- **Testes Automatizados e Verificação Visual:**
  - Testes unitários de backend (pytest) para `catalog.py`, `McpServerService.propose` com `catalog_id="playwright"`, `models.py` garantindo que `code_session_proposal.prompt` não sofre `[REDACTED]` (e que outros `prompt` continuam redigidos), e ausência das tools `browse_page`/`browser_*`.
  - Testes unitários de frontend (Vitest) para `BrowserSection.tsx`, `CodeSessionProposalCard.tsx` e `UserQuestionCard.tsx`.
  - Verificação visual real no navegador com Playwright (desktop e mobile) da seção **Configurações > Navegador** e dos cards **`CodeSessionProposalCard` / `UserQuestionCard`**.

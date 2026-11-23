# Plano de Implementação — `/btw` (Pergunta Paralela Efêmera)

- **Data:** 2026-11-23
- **Escopo:** Backend + Frontend (Chat e Orin Code)
- **Decisões de produto aprovadas:**
  1. **Superfície UI:** Card ancorado acima do `Composer` (largura `760px`, mesma coluna do `QueuePanel`, sem conflitar com a Bancada `CodeWorkbenchPanel`), com controles de minimizar em pill compacto, expandir/compactar altura e fechar.
  2. **Interação:** Single-shot (1 pergunta -> 1 resposta). Novo `/btw <pergunta>` cancela o anterior (se em streaming) e substitui o card.
  3. **Modelo e execução:** Mesmo provider e modelo ativo da conversa, sem ferramentas (`tools=[]`, `tool_choice="none"`, `max_turns=1`), com esforço leve automático (`low` ou menor nível aceito pelo modelo), executando em paralelo sem interromper o turno principal nem gravar no histórico.
  4. **Ações pós-resposta:** Copiar, Inserir no composer e Continuar em `/fork`.

---

## 1. Arquitetura de Backend

### 1.1 Transport factory compartilhado — `src/agentos/agentic/transport_factory.py`
Extrair a lógica de construção de transport (`_transport_for`) de `src/agentos/workers/chat.py` para um helper reutilizável por `ChatWorker` e `BtwService`:
- APIs HTTP com rotação de chaves (`MultiKeyProviderStreamTransport` / `HTTPProviderStreamTransport`): Anthropic, OpenAI, OpenRouter, Ollama (com `num_ctx`), OmniRoute, Nvidia, OpenCode.
- Subscription CLIs (`ClaudeCliProvider` via `cli_adapter_for`): Claude CLI (`anthropic` bridge / `anthropic-cli`), Codex CLI (`openai-codex`), Antigravity CLI (`google-antigravity`).

### 1.2 Serviço efêmero `/btw` — `src/agentos/conversations/btw.py`
- **Snapshot somente leitura (`build_btw_context`):**
  - Lê `conversation_compact_boundaries`, `conversation_messages` (filtrando `BLOCKED_BY_HOOK_STATUS` e `COMMAND_STATUS`, preservando expansões de comando e referências de anexos), `conversation_turns` e `conversation_turn_steps` dos turnos recentes e do turno ativo em andamento.
  - **Projeção textual segura de tool steps:** nunca injeta blocos crus `tool_use`/`tool_result` com `tools=[]` (evita HTTP 400 na Anthropic API e tool calls incompletas durante turno ativo). Projeta passos relevantes como resumo textual estruturado preenchido do mais recente ao mais antigo, dentro de um teto seguro (`min(55% context_window, 100_000 chars)` para compatibilidade com CLIs de stdin limitado como `agy`).
  - System prompt conciso informando o contexto mínimo da sessão, explicitando ausência de ferramentas e orientando resposta direta.
  - Zero escrita nas tabelas de conversa (`conversations`, `conversation_messages`, `conversation_turns`, `conversation_turn_steps`, `conversation_activity_events`).
- **Esforço leve automático (`resolve_btw_effort`):**
  - Seleciona `low` (ou menor nível em `supported_efforts`); `None` quando o modelo não suporta esforço.
- **Execução concorrente e cancelamento (`stream_btw_events`):**
  - Usa `conversation_id=f"btw:{conversation_id}"` no `CliSlotQueue` para não penalizar o turno principal.
  - Itera o transport síncrono em thread dedicada publicando em `asyncio.Queue`, ligado a `threading.Event` de cancelamento (`request.is_disconnected()` -> `cancel_check()` -> encerra socket HTTP ou mata subprocesso CLI com `kill_process_tree`).
  - Timeouts defensivos (idle 60s, wall-clock 180s) e sanitização via `new_assistant_text_stream`.

### 1.3 Endpoint SSE — `src/agentos/api/conversation_routes.py`
- `POST /v1/conversations/{conversation_id}/btw`
  - Payload (`BtwRequest`, `extra="forbid"`): `question` (1..8000 chars), `provider` (opcional), `model_id` (opcional — fallback no último turno da conversa).
  - Segurança: `principal_for(request, mutable=True)`, `check_rate_limit`, `authorize(..., action="conversation.read", purpose="conversation.btw")`, ownership verificado antes de abrir o `StreamingResponse` (404 para outro perfil).
  - Classificado em `OWNED` em `tests/integration/api/test_profile_isolation.py`.
  - Eventos SSE: `start`, `waiting`, `thinking`, `delta`, `done`, `error`, `heartbeat`.

---

## 2. Arquitetura de Frontend e UX

### 2.1 Entrada, Composer e Atalhos
- `frontend/src/features/commands/builtins.ts`: atualizar `/btw` com `argumentHint: '<pergunta>'` e descrição real.
- `frontend/src/features/commands/moreCommands.ts`: remover stub de `NOT_IN_ORIN`.
- `frontend/src/features/commands/useChatCommands.ts`: ligar handler `btw` (`askBtw` nos deps).
- `frontend/src/features/keybindings/catalog.ts` + `CommandInfoDialog.tsx`: adicionar `chat:btw` (`alt+b`).
- `frontend/src/features/conversations/Composer.tsx`:
  - Corrigir `sendTyped` (`W7-D11`): quando `running === true` e o texto começa com `/`, chamar `onSubmit()` em vez de `onQueue()`.
  - Detectar `btwMode` (`/^\s*\/btw(?:\s|$)/i`): classe `composer--btw`, badge explicativo no topo do input e botão **Enviar `/btw`** ao lado do botão **Parar** durante `running === true`.
  - Atalho `chat:btw` alterna prefixo `/btw ` e foca o composer (ou reabre card minimizado).
  - **Proteção da tecla `Escape`:** quando o card `/btw` está aberto (não minimizado), o primeiro `Escape` fecha/cancela apenas o `/btw` sem chamar `onStop()` do turno principal nem abrir Rewind.

### 2.2 Streaming e Estado — `client.ts`, `sessionTools.ts`, `useBtwSession.ts`
- `frontend/src/api/client.ts`: estender `ApiClient.stream()` para suportar `method`, `body` e `X-CSRF-Token`.
- `frontend/src/api/sessionTools.ts`: implementar `streamBtwQuestion(...)` com parser incremental de SSE e `AbortSignal`.
- `frontend/src/features/conversations/useBtwSession.ts`: hook em memória por `conversationId`, single-shot, abortando o stream anterior ao trocar de conversa ou enviar novo `/btw`.

### 2.3 Card Efêmero — `BtwCard.tsx` + `btwCard.css` + `ChatPage.tsx`
- Renderizado em `ChatPage.tsx` dentro de `footer.chat__foot`, logo acima de `QueuePanel` e `Composer`.
- Cabeçalho: badge `BTW`, indicador de contexto (`Fora do histórico` / `Inclui turno em andamento`), metadados (`modelo · duração · tokens`), botões **Expandir/Compactar**, **Minimizar** (pill de 1 linha) e **Fechar**.
- Corpo: pergunta compacta + resposta em `MarkdownMessage` com auto-scroll inteligente (pausa quando o usuário rola para cima), estados claros (`waiting`, `thinking`, `streaming`, `error`, `cancelled`) e thinking opcional colapsável.
- Rodapé de ações: **Parar** (durante streaming), **Copiar**, **Inserir no composer**, **Continuar em `/fork`** (cria fork, grava rascunho no fork via `writeDraft(conversationDraftKey(forkId), ...)` e navega para `/chats/${forkId}`), além de **Tentar novamente** em erro/cancelamento.

---

## 3. Workstreams e Verificação

1. **WS1:** `src/agentos/agentic/transport_factory.py`, `src/agentos/conversations/btw.py`, refactor em `src/agentos/workers/chat.py`, testes `tests/unit/conversations/test_btw.py`.
2. **WS2:** `src/agentos/api/conversation_routes.py`, `tests/integration/api/test_profile_isolation.py`, testes `tests/integration/api/test_btw_route.py`.
3. **WS3:** `frontend/src/api/client.ts`, `frontend/src/api/sessionTools.ts`, `frontend/src/features/conversations/useBtwSession.ts`, `builtins.ts`, `moreCommands.ts`, `useChatCommands.ts`, `catalog.ts`, `CommandInfoDialog.tsx`, testes `useBtwSession.test.ts` e `btwCommand.test.ts`.
4. **WS4:** `BtwCard.tsx`, `btwCard.css`, `Composer.tsx`, `ChatPage.tsx`, `docs/ORIN_CODE.md`, testes `BtwCard.test.tsx` e `Composer`/`ChatPage`, mais validação completa (`pytest`, `vitest`, `tsc -b`, `eslint`, `vite build` e inspeção visual no navegador).

# Auditoria Forense (`chat_abd2d448718b4c9a9660403da16e9bb8`) e Plano de Correção — Orin Code v0.7.6

**Data:** 2026-10-16  
**Alvo auditado:** `C:\Users\User\AppData\Local\Orin\data\orin.db` + `workspaces\chat_abd2d448718b4c9a9660403da16e9bb8` + `logs\worker.log`  
**Volume analisado:** 8 turnos (`id` 36 a 43), 16 mensagens, 2.616 eventos de atividade (`conversation_activity_events`), 1.536 eventos SSE (`conversation_events`), 319 steps (`conversation_turn_steps`).

---

## 1. Diagnóstico dos 3 Problemas Relatados pelo Usuário

### Problema 1 — "Quando o modelo está pensando, parece que travou, não mostra um pensando animado, e às vezes nem mostra o pensando"

#### Evidências no Banco (`orin.db`)
- **0 eventos de raciocínio (`thought` / `reasoning` / `thinking`)** entre os 2.616 eventos de `conversation_activity_events`.
- **Padrão A — Silêncio absoluto antes do 1º token (`context.updated → assistant.delta` / `tool.requested`):**
  - No Turno 1 (`qwen/qwen3.8-max-0902`), ocorreram **75 hiatos ≥ 10s** sem um único evento emitido, chegando a **85,8s** no início do turno, **104,2s** na iteração 58, **161,3s (2m41s)** na iteração 59 e **128,4s** na iteração 80.
  - No Turno 3 (`qwen/qwen3.8-max-0902`), ocorreu um hiato inicial de **65,5s** e um hiato final de **72,2s** após `context.updated`, levando o usuário a cancelar o turno por parecer congelado.
- **Padrão B — Silêncio durante a geração do JSON de Tool Calls (`assistant.delta → tool.requested`):**
  - O modelo emite uma frase curta (ex.: `"Vou atualizar o arquivo..."` em 0,5s) e passa **60s a 134,7s** gerando silenciosamente os argumentos JSON de `write_file` / `edit_file` sem emitir nenhum evento intermediário.

#### Causas-Raiz no Código
1. **Descarte de tokens de raciocínio no parser SSE ([`src/agentos/agentic/provider_stream.py:193-243`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/provider_stream.py#L193-L243)):** `normalize_sse` lê apenas `delta.content` e `delta.tool_calls`, ignorando `delta.reasoning_content`, `delta.reasoning`, `delta.reasoning_details` (OpenRouter), tags `<think>...</think>` e `thinking_delta` (Anthropic).
2. **Descarte de `event.thinking` e silêncio em `StreamKind.TOOL_CALL` ([`src/agentos/agentic/runtime.py:404-420`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/runtime.py#L404-L420)):** `thinking_parts` nunca é emitido para `self._life(...)` e chunks `StreamKind.TOOL_CALL` são acumulados em memória até o `FINISH`, sem emitir progresso (`generating_tool_call`).
3. **Fallback estático `<p>Trabalhando…</p>` e modo `'responding'` preso ([`ChatPage.tsx:1257-1261`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx#L1257-L1261) e [`AgentPulse.tsx:183-210`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/AgentPulse.tsx#L183-L210)):** Enquanto a timeline está vazia, a bolha renderiza `"Trabalhando…"` estático; quando o modelo emite texto curto e passa 130s gerando JSON de tool call, o último evento continua sendo `assistant.delta` (`"Respondendo"` estático). Além disso, `modeFromEvents` não filtra `activity.events` pelo `turnId` ativo.
4. **`prefers-reduced-motion: reduce` no Windows congela 100% da animação ([`AgentPulse.tsx:80`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/AgentPulse.tsx#L80) e [`agentos.css:1251-1254`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/styles/agentos.css#L1251-L1254)):** Quando o Windows desativa animações de janela, o Chromium ativa `prefers-reduced-motion: reduce`, substituindo o `PulseFigure` por um quadrado estático e matando todas as animações CSS, sem contador de segundos ao vivo (`1s, 2s, 3s...`) e sem shimmer.

---

### Problema 2 — "O principal (`qwen 3.8 max`) não conseguiu chamar o subagente `luna`, mesmo estando marcado como favorito"

#### Evidências no Banco (`orin.db`) e `worker.log`
- Em `provider_model_favorites`, o modelo Luna estava favoritado como **`~openai/gpt-luna-latest`** (`id=3`, `display_name='OpenAI: GPT Luna Latest'`).
- **Tentativa 1 (`step id=628`, `13:47:01`):** O Qwen viu `- Modelos favoritos disponíveis para subagentes: ~openai/gpt-luna-latest, ...` no prompt, interpretou o `~` inicial como pontuação e enviou `run_subagent(subagent_type="general", model_id="openai/gpt-luna-latest")`.
  - O backend gravou `model_id="openai/gpt-luna-latest"` na tabela `conversation_agents` **antes** de validar o modelo e depois retornou `CHILD_MODEL_NOT_FAVORITE` porque `"openai/gpt-luna-latest" != "~openai/gpt-luna-latest"`.
- **Tentativas 2, 3 e 4 (`step id=631, 668`):** O Qwen desistiu do Luna e chamou `run_subagent` com `model_id="anthropic/claude-haiku-5.5:batch"`. A OpenRouter respondeu **`HTTP/1.1 404 Not Found`** (modelos `:batch` não atendem em `/api/v1/chat/completions` síncrono), falhando com `PROVIDER_STREAM_FAILED`.

#### Causas-Raiz no Código
1. **Comparação exata (`==`) sem normalização de `~`, slug, alias (`"luna"`) ou `display_name` ([`workers/chat.py:960-980`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/chat.py#L960-L980) e [`session.py:905-935`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py#L905-L935)).**
2. **DB Poisoning em `conversation_agents` ([`session.py:1028-1044`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py#L1028-L1044)):** `_ensure_builtin_subagent_record` persiste o `model_id` inválido no banco **antes** de `_child_model_allowed`.
3. **Fallback para o modelo pai nunca dispara em erro HTTP de stream ([`session.py:1051-1074, 1128`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py#L1051-L1074)):** O `try/except` envolve apenas `_child_provider_factory` (que só instancia o objeto em memória). Quando `runtime.run()` falha na 1ª iteração com `actions == 0` e `PROVIDER_STREAM_FAILED` (como o 404 do `:batch`), não há fallback para `current_model_id`.
4. **Vazamento de `turn.failed` do subagente para o turno pai ([`session.py:898-916`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py#L898-L916) e [`activityReducer.ts:57`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/activityReducer.ts#L57)):** Quando um subagente falha, emite `turn.failed` no `turn_id` do pai e `terminalOutcomeFor` no frontend não ignora eventos com `parentAgentId`, marcando o turno pai como `failed` prematuramente.
5. **Modelos `:batch` sugeridos como candidatos de chat síncrono e ausência de suporte a favoritos cross-provider.**

---

### Problema 3 — "O fluxo de pedir permissão não tem uma boa UX, não é como no Claude Desktop no modo Code (modalzinho fixo acima do input com boa UI e animações)"

#### Evidências no Banco (`orin.db`)
- **0 eventos `code_mode.decision_required`** em todo o `orin.db`!
- No Turno 3 (`turn_94625f8da96340018a3b70e0a1e09248`, modo `approval_required` / `ask_permissions`), quando o agente chamou `run_command` (`where cloudflared...`), em vez de pausar e abrir o card interativo de permissão, o `AgentToolset` lançou erro imediato `CODE_PLAN_APPROVAL_REQUIRED` (`"Aguardando aprovação do plano"`), forçando o modelo para `enter_plan_mode`.
- No Turno 5 (`turn_ab9ad067dcfe42298b119c8bf280b600`), o agente chamou `exit_plan_mode` (`id=17803`) e **15 segundos depois já estava editando arquivos sem ação do usuário**, enquanto o frontend renderizava um `PlanApprovalCard` órfão no meio da timeline.

#### Causas-Raiz no Código
1. **`interactive_permissions` é sempre `False` em produção ([`session.py:1509`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py#L1509) e [`workers/chat.py:1070-1156`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/chat.py#L1070-L1156)):** Nem `claim()` nem `_hydrate_code_mode()` definem `turn["interactive_permissions"] = True`. Com isso, `ToolPermissionBroker.request_and_wait()` em [`agent_tools.py:1508-1541`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/agent_tools.py#L1508-L1541) era código morto!
2. **Divergência `suggested_rule` vs `rule_suggestion` ([`permissions.py:526`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/code_mode/permissions.py#L526) vs [`conversations.ts:826`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/api/conversations.ts#L826)).**
3. **Ausência do `CodePermissionDock` fixo acima do `Composer` em `.chat__foot` ([`ChatPage.tsx:1469`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx#L1469)):** Os cards eram renderizados soltos dentro do scroll da conversa, sem animações `motion/react` (spring), sem badges semânticos (`Bash`, `Edit`, `Write`, `Plan`), sem atalhos de teclado (`1`/`Enter`, `2`, `3`/`Esc`), sem opção **"Negar e orientar..."** e sem recibo compacto de 1 linha (`perm-receipt`) na timeline.

---

## 2. Problemas Adicionais Descobertos na Auditoria Forense do Chat

| ID | Severidade | Problema Encontrado no `chat_abd2d448718b4c9a9660403da16e9bb8` | Causa-Raiz Exata |
|---|---|---|---|
| **P0-1** | 🔴 Crítico | **`sqlite3.IntegrityError` em chamadas paralelas de `read_file` / `view_file` / `search_files` (ocorreu 8 vezes em 5 turnos!)**, vazando SQL cru para o LLM e bloqueando retries com `DUPLICATE_TOOL_CALL`. | `runtime.py:1482` roda tools read-only em `ThreadPoolExecutor`; cada thread chama `_checkpoint()` (`workers/chat.py:659-660`) sem lock, lendo o mesmo `MAX(sequence) + 1` em `execution_checkpoints`. |
| **P0-2** | 🔴 Crítico | **Perda real de resposta no Turno 6:** o modelo escreveu 15 linhas explicando as correções e o link do túnel Cloudflare, mas o gate `code_validation_required` disparou `message.reset` e **apagou toda a explicação do banco**. | 1) `_observe_mutation_outcome` (`agent_tools.py:1735-1754`) viu novos PNGs em `shots/` gerados pelo próprio `node scripts/audit.mjs` e marcou `_code_mode_verified = False` mesmo com `exit_code == 0`! 2) `runtime.py:646, 672` chama `store.reset_assistant_message(turn)` apagando o texto já transmitido. |
| **P0-4** | 🔴 Crítico | **Falso-positivo em `_BLOCKED_COMMAND` recusando `Format-Table` (PowerShell) e `ngrok --log-format` como dano ao sistema host (`TOOL_REFUSED`).** | `agent_tools.py:119` usa `\b(mkfs|fdisk|diskpart|format)\b`; como `-` é fronteira de palavra `\b`, casa com `Format-Table` e `--log-format`. |
| **P1-1** | 🟠 Alto | **Textos do assistente entre iterações de tools ficam grudados sem espaço (`"pública.O servidor"`, `"avatar.Preciso ler"`) e perdem a intercalação após F5.** | `store.delta` concatena texto de iterações diferentes sem `\n\n`, e `_query_activity_window` (`chat.py:925-939`) descarta todos os `assistant.delta` de turnos concluídos em vez de compactá-los por segmento. |
| **P1-3** | 🟠 Alto | **Imagens base64 de `view_file` contam ~70k–165k tokens por imagem (`len(json) // 4`), disparando compactação forçada no Turno 1 (302k tokens) e fazendo o Qwen mudar do português para o inglês.** | `_estimated_tokens` (`runtime.py:1088-1094`) divide o JSON base64 por 4 em vez de usar custo de imagem (~1.200 tokens), e `_age_tool_results` não reconhece mensagens sintéticas de imagem. |
| **P1-4** | 🟠 Alto | **Turno 2 (`"pode prosseguir"`) ficou 51 minutos em `queued` e foi cancelado junto com o Turno 1.** | `request_cancel` (`chat.py:1460-1470`) cancela todos os turnos `queued` junto com o `running`. |
| **P1-5** | 🟠 Alto | **`PreviewServerManager` ignora auto-incremento de porta do Vite (`5173` → `5175`), reportando `ready: false` e salvando porta errada.** | `preview_server.py:333-351` testa apenas a porta inicial inferida sem ler a URL real emitida no log do processo. |
| **P2-1..4** | 🟡 Médio | 1) `edit_file` emite `"Criou <path>"` em vez de `"Editou <path>"` (`session.py:834`).<br>2) Todo `run_command` emite `"Executando validação"` (`session.py:767`).<br>3) `EVENT_LIMIT = 500` em `activityReducer.ts` descarta eventos estruturais de tools.<br>4) `edit_file` rejeita `"old_text": ""` junto com `"edits": [...]` ou `"edits": []` junto com `old_text` (`agent_tools.py:2468`). | Pequenos defeitos de contrato em `session.py`, `agent_tools.py` e `activityReducer.ts`. |

---

## 3. Plano de Execução Dividido em 4 Frentes Especialistas (TDD + Verificação Local)

### Frente 1 — Backend Concurrency, Guardrails, Context & Validation Integrity (`P0-1`, `P0-2`, `P0-4`, `P1-1`, `P1-3`, `P1-4`, `P1-5`, `P2-1`, `P2-2`, `P2-4`)
- **Arquivos:** `src/agentos/workers/chat.py`, `src/agentos/persistence/postgres/execution_journal.py`, `src/agentos/agentic/runtime.py`, `src/agentos/agentic/agent_tools.py`, `src/agentos/conversations/chat.py`, `src/agentos/code_mode/preview_server.py`.
- **Entregas:**
  1. Lock thread-safe em `_checkpoint()` + sequência atômica/retry em `execution_journal.py` + sanitização de exceções inesperadas em `runtime.py` sem envenenar `_failed_signatures`.
  2. Fim do `reset_assistant_message` destrutivo nos gates de validação (`runtime.py:646, 672`) + correção de `_observe_mutation_outcome` (`agent_tools.py:1735-1754`) para que comandos de verificação com `exit_code == 0` que geram screenshots em `shots/`/`test-results/` marquem `_code_mode_verified = True`.
  3. Correção da regex `_BLOCKED_COMMAND` (`agent_tools.py:119`) para não bloquear `Format-Table`, `--log-format` ou `npm run format`.
  4. Separação automática com `\n\n` entre blocos de texto de iterações distintas + compactação por segmento de `assistant.delta` em `_compact_snapshot_activities` (`chat.py`) para manter a intercalação texto/tools após recarregar.
  5. Estimativa realista de tokens para `image_url` (~1.200 tokens) + aging de imagens antigas de `view_file` + preservação explícita do idioma do usuário em `_compact_history`.
  6. Detecção da porta real no log do `PreviewServerManager` (`5173` → `5175`), aceitação de `old_text=""` / `edits=[]` em `edit_file`, e rótulos corretos `"Editou <path>"` / validação apenas em comandos de verificação.

### Frente 2 — Subagentes, Modelos Favoritos & Permissions Wiring no Backend (`Problema 2` + `Problema 3 Backend` + `P0-3`)
- **Arquivos:** `src/agentos/agentic/session.py`, `src/agentos/workers/chat.py`, `src/agentos/code_mode/permissions.py`, `src/agentos/code_mode/prompt.py`, `src/agentos/agentic/agent_tools.py`.
- **Entregas:**
  1. Resolvedor inteligente `resolve_favorite_child_model` (normaliza prefixo `~`, case-insensitive, slug sem vendor, alias curto como `"luna"`/`"haiku"`, `display_name` e suporte cross-provider habilitado; exclui `:batch` de sugestões síncronas).
  2. Validação de `_child_model_allowed` **antes** de `_ensure_builtin_subagent_record` (fim do DB poisoning em `conversation_agents`) e isolamento de `agent_id`/estado por invocação.
  3. Fallback automático para o modelo pai quando o modelo favorito falha na 1ª requisição de stream (`PROVIDER_STREAM_FAILED` com `actions == 0`).
  4. Não emitir `TURN_STARTED` / `TURN_FAILED` no `turn_id` pai para falhas de subagente.
  5. Ativar `interactive_permissions = True` em `_hydrate_code_mode` / `TurnSession.build_runtime`, emitir `"suggested_rule"` e `"rule_suggestion"` no `ToolPermissionBroker`, e pausar `exit_plan_mode` quando o agente estiver saindo de `ToolPermissionMode.PLAN` para aprovação do usuário.

### Frente 3 — Streaming de Raciocínio ("Pensando..." Animado) e Progresso de Tool Calls (`Problema 1`)
- **Arquivos:** `src/agentos/agentic/provider_stream.py`, `src/agentos/agentic/events.py`, `src/agentos/agentic/runtime.py`, `frontend/src/features/conversations/activityTypes.ts`, `frontend/src/features/conversations/activityReducer.ts`, `frontend/src/features/conversations/turnTimelineFold.ts`, `frontend/src/features/conversations/TurnTimeline.tsx`, `frontend/src/features/conversations/AgentPulse.tsx`, `frontend/src/styles/agentos.css`.
- **Entregas:**
  1. `StreamKind.THINKING` em `provider_stream.py` capturando `reasoning_content`, `reasoning`, `reasoning_details`, tags `<think>...</think>` e Anthropic `thinking_delta`, emitindo `agent.thought_delta` com throttle no `runtime.py`.
  2. Heartbeat imediato `tool.progressed` (`phase="generating_arguments"`, `bytes_received`) assim que o primeiro chunk de `StreamKind.TOOL_CALL` chega, eliminando os congelamentos de 60–135s ao gerar arquivos grandes.
  3. Novo `ThoughtBlock` na `TurnTimeline.tsx` (aberto/animado com shimmer e timer ao vivo enquanto pensa; recolhido em `"▸ Pensou por Xs"` quando concluído).
  4. Upgrade do `AgentPulse.tsx`: filtro por `activeTurnId`, cronômetro ao vivo (`1s, 2s, 15s...`), shimmer fluido no rótulo `"Pensando..."`, sub-status contextual (`"Preparando write_file (4.2 KB)..."`, `"Na fila..."`), ancorado no final da bolha ativa sem `<p>Trabalhando…</p>` estático duplicado e funcional mesmo com `prefers-reduced-motion: reduce` no Windows.

### Frente 4 — `CodePermissionDock` Acima do Input (Estilo Claude Desktop) & Timeline Polish (`Problema 3 Frontend` + `P2-3`)
- **Arquivos:** `frontend/src/features/conversations/CodePermissionDock.tsx` (novo), `frontend/src/features/conversations/ToolPermissionCard.tsx`, `frontend/src/features/conversations/PlanApprovalCard.tsx`, `frontend/src/features/conversations/TurnTimeline.tsx`, `frontend/src/features/conversations/ChatPage.tsx`, `frontend/src/api/conversations.ts`, `frontend/src/styles/agentos.css`.
- **Entregas:**
  1. Novo componente `CodePermissionDock` fixo em `.chat__foot` logo acima do `<Composer />`, com animação `AnimatePresence` + `motion.div` (`spring: stiffness 380, damping 30`), suporte a fila (`Também aguardando: N`) e promoção animada.
  2. Badges semânticos (`Bash`, `Edit`, `Write`, `Git`, `MCP`, `Plan`), detector de risco (`high` vs `normal`), preview monoespaçado copiável para comandos e mini-diff colapsável (`+N -M`) para arquivos.
  3. Atalhos de teclado diretos (`1`/`Enter` Permitir uma vez, `2` Permitir sempre no projeto, `3`/`Esc` Negar, `n` abrir input inline **"Negar e orientar o Orin..."** enviando `reason` para o agente ajustar a rota no mesmo turno).
  4. Recibo inline compacto de 1 linha (`perm-receipt`) na `TurnTimeline` (`✓ Permissão concedida · Bash(...)` / `✕ Permissão negada`) e proteção do `EVENT_LIMIT` em `activityReducer.ts` para que `assistant.delta` não descarte eventos estruturais de ferramentas.

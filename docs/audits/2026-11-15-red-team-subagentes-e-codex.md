antigravity-11 reported:

# Relatório de Auditoria Red Team — Subagentes Paralelos & Codex Arquivos/Imagens

**Escopo auditado:** Workstreams `WS-A` a `WS-G` contra `docs/superpowers/plans/2026-11-15-subagentes-paralelos-e-codex-arquivos.md` (36 arquivos modificados + 12 arquivos novos; `src/agentos/providers/antigravity_cli/catalog.py` preservado intocado).  
**Metodologia:** Inspeção linha a linha + execução de provas de conceito em memória no Windows 10 (`./.venv/Scripts/python.exe`, `Node.js v26.13.0`, `Vitest` e `codex-cli 0.167.0`). Nenhum arquivo do repositório foi alterado.

---

## Achados por Severidade

### 1. `[CRITICAL]` Envenenamento Permanente de Cache de Leitura (`_succeeded_reads`) + Inversão Causal `Read` → `Agent` na Mesma Mensagem (`WS-A`)
- **Onde:** `src/agentos/agentic/runtime.py:2830-2835`, `src/agentos/agentic/runtime.py:2866-2871` e `src/agentos/agentic/native_turn.py:508-513`
- **Cenário de reprodução / Prova técnica:**
  1. Em `ToolBatchCollector.submit` (`src/agentos/agentic/native_turn.py:508-513`), `effective_lane == "agent"` despacha `_run_gated([entry], msg_key, "agent")` imediatamente **sem fechar `self._open_batches[msg_key]` nem aguardar `self._in_flight_reads[msg_key]`** (ao contrário de `effective_lane == "write"` nas linhas `515-535`). Quando o modelo emite `[Read("t.txt"), Agent(prompt="edite t.txt")]` na mesma resposta, `Read` fica retido na janela de coleta de 100 ms enquanto o subagente mutável (`general`) inicia **antes** do `Read`.
  2. Pior: como as faixas `read` e `agent` compartilham `_LaneGate.acquire_shared`, se `Read("t.txt")` lê `OLD_CONTENT` do disco enquanto um subagente `general` paralelo edita `t.txt` para `NEW_SUBAGENT_CONTENT` e termina primeiro (incrementando `self._writes` para `2` em `runtime.py:2867`), quando `Read` finaliza logo em seguida ele executa `runtime.py:2871`:
     ```python
     self._succeeded_reads[self._signature(name, arguments)] = (outcome.content, self._writes)
     ```
     gravando `(OLD_CONTENT, 2)` com o `self._writes` **do fim** da execução (pós-mutação do subagente), e não do início (`1`). Na rodada seguinte (`m2`), quando o agente pai chama `Read("t.txt")` para ver o que o subagente mudou, `cached_read` (`runtime.py:2624`) vê `remembered[1] == self._writes` (`2 == 2`) e **serve `OLD_CONTENT` obsoleto do cache** pelo resto do turno.
  - **Evidência executada em Python:**
    ```text
    PoC2 order: ['agent_start', 'read_start', 'agent_wrote', 'read_end']
    PoC2 disk: NEW_SUBAGENT_CONTENT
    PoC2 round2 Read: '     1\tOLD_CONTENT\n\n[resultado da chamada anterior a Read neste mesmo turno; nada foi escrito desde então, então o conteúdo não mudou]'
    PoC2 CACHE POISONED?: True
    ```
- **Correção exata recomendada:**
  1. Em `src/agentos/agentic/runtime.py:2823-2835`, capturar `writes_at_start = self._writes` dentro de `with checkpoint_lock:` antes de `self.toolset.invoke(...)`. Em `src/agentos/agentic/runtime.py:2870-2871`, só popular `self._succeeded_reads` se nenhuma escrita começou ou terminou durante a leitura:
     ```python
     if read_only and self._writes == writes_at_start and outcome.status == "succeeded" and not outcome.images and not outcome.payload.get("unchanged"):
         self._succeeded_reads[self._signature(name, arguments)] = (outcome.content, writes_at_start)
     ```
  2. Em `src/agentos/agentic/native_turn.py:508-513`, quando `effective_lane == "agent"` e `not entry.read_only` (`run_subagent`, `ask_agent`, `ask_agents`), fechar eventual `self._open_batches.pop(msg_key, None)` e aguardar `prior_entries = list(self._in_flight_reads.get(msg_key, ()))` da mesma `msg_key` antes de chamar `self._run_gated([entry], msg_key, "agent")` (sem bloquear outros `agent` entre si).

---

### 2. `[CRITICAL]` Loop Infinito de Auto-Refresh sem Backoff em `ProviderModelCatalogService.list("openai-codex")` Quando `models_cache.json` Contém Qualquer Modelo Somente-Texto (`WS-D`)
- **Onde:** `src/agentos/provider_catalog/service.py:371-379`, `src/agentos/provider_catalog/service.py:402-404`, `src/agentos/provider_catalog/service.py:485-495` e `src/agentos/providers/codex_cli/catalog.py:26-45, 210-212`
- **Cenário de reprodução / Prova técnica:**
  - `discover_codex_models` (`codex_cli/catalog.py:210-212`) chama `codex_input_modalities(slug, entry.get("input_modalities"))` passando o `raw` lido de `~/.codex/models_cache.json`. Se **qualquer** modelo visível em `models_cache.json` declarar `"input_modalities": ["text"]` (ex.: `o3-mini`), o banco recebe `input_modalities=("text",)`, `is_custom=False`, `output_modalities=("text",)`.
  - Já `_codex_modalities_are_stale` (`service.py:491-495`) chama `codex_input_modalities(item.model_id)` **sem o segundo argumento `raw`**, que retorna `("text", "image")` para todo `model_id != "codex-image"`.
  - Logo, `set(("text", "image")) - set(("text",)) == {"image"}` avalia como **`True` imediatamente após cada `refresh()`**! E como `self.refresh()` sucede sem lançar exceção — inclusive fazendo `self._auto_refresh_backoff.pop(...)` em `service.py:305` e `service.py:378` — **toda chamada a `service.list(ctx, "openai-codex")` dispara um `refresh()` completo (I/O de disco + DELETE/INSERT SQL) em loop perpétuo sem backoff**.
  - **Evidência executada em Python:**
    ```text
    PoC1 stale after fresh refresh: True [('gpt-6.1-sol', ('text', 'image')), ('o3-mini', ('text',))]
    ```
  - *(Nota: quando `codex` não está instalado e `models_cache.json` não existe, `discover_codex_models` retorna `FALLBACK_CODEX_MODELS`, todos com `("text", "image")`, então esse caso não entra em loop; o loop ocorre sempre que `models_cache.json` existir com ≥1 modelo `["text"]`).*
- **Correção exata recomendada:**
  - Em `_codex_modalities_are_stale` (`src/agentos/provider_catalog/service.py:485-495`), comparar contra as modalidades efetivas descobertas (ou considerar o catálogo `openai-codex` obsoleto apenas quando modelos conhecidos com visão em `FALLBACK_CODEX_MODELS` estiverem gravados sem `"image"`, ou quando **nenhum** modelo de chat não-custom tiver `"image"`), **e** em `_try_auto_refresh` (`service.py:377-379`), se após `self.refresh(...)` o catálogo continuar acusado como stale, registrar `self._auto_refresh_backoff[key] = time.monotonic() + AUTO_REFRESH_BACKOFF_SECONDS` para impedir re-execução a cada `list()`.

---

### 3. `[HIGH]` Bypass de `runtime.withheld_tools` (`--disallowedTools`) e `runtime.offered_tools` (`--tools`) via `deferred_tool_names` e `ToolSearch` (`WS-E` / `native_turn.py` / `admin.py`)
- **Onde:** `src/agentos/agentic/native_turn.py:728-737, 1014-1029`, `src/agentos/agentic/agent_tools.py:435-450` e `src/agentos/agentic/tools/mcp/admin.py:198, 230-250, 298-300`
- **Cenário de reprodução / Prova técnica:**
  - `AgenticTurnRuntime._tool_schemas` (`runtime.py:1642-1644`) restringe os schemas publicados usando `self.offered_tools` e `self.withheld_tools` (configurados por `--tools` / `--disallowedTools` em `session.py:6726`).
  - Porém `AgentToolset.deferred_tool_names` e `AgentToolset.discover` (`agent_tools.py:435-450`) desconhecem `offered_tools` e `withheld_tools`.
  - Com isso:
    1. `RuntimeToolExecutor.call_tool` (`native_turn.py:1018-1024`) aceita e **executa** qualquer ferramenta diferida (`internal_name in deferred`) mesmo quando explicitamente bloqueada em `runtime.withheld_tools`.
    2. `ToolSearch(query="select:Write")` / `ToolSearch(query="select:NotebookEdit")` em `admin.py:230-250, 299, 308-314` devolve o schema completo (`already_available: true` ou `activated`) de ferramentas proibidas por `withheld_tools` / fora de `offered_tools`.
  - **Evidência executada em Python (`rt.withheld_tools = {"notebook_edit"}`):**
    ```text
    PoC5 withheld NotebookEdit bypassed Unknown Tool?: True | text: "Notebook not found: 'a.ipynb'."
    ```
- **Correção exata recomendada:**
  - Em `RuntimeToolExecutor.is_known_tool` (`native_turn.py:722-737`) e `RuntimeToolExecutor.call_tool` (`native_turn.py:1018-1029`), bloquear imediatamente se `internal_name in self.runtime.withheld_tools` ou `(self.runtime.offered_tools is not None and internal_name not in self.runtime.offered_tools and internal_name not in active_mcp)`.
  - Sincronizar `offered_tools` e `withheld_tools` no `AgentToolset` (ex.: filtrando em `deferred_tool_names`, `discover()` e `available_defs` em `admin.py:198`).

---

### 4. `[HIGH]` `RuntimeToolExecutor._execute_batch` Rouba `claim_queued_inputs` Enquanto Outros Subagentes Foreground Seguem Ativos, Quebrando a Promoção Conjunta da Onda (`P10`, `WS-A` × `WS-B`)
- **Onde:** `src/agentos/agentic/native_turn.py:1518-1522` e `src/agentos/agentic/session.py:3264-3281`
- **Cenário de reprodução / Prova técnica:**
  - Dois subagentes foreground (`slow` e `fast`) rodam em paralelo (`self._active_batches == 2`). Se o usuário enviar uma mensagem na fila no instante em que `fast` (ou um `Read`/`CheckSubagent` paralelo) termina (dentro da janela de 50 ms do loop de `_run_foreground_subagent_watching_queue`), `_execute_batch` (`native_turn.py:1519`) chama `self._turn_notes(claim_queue=not had_ask_user_wait)` **enquanto `self._active_batches == 2`**.
  - `RuntimeToolExecutor._claim_queued_texts()` drena a fila no banco **sem incrementar `TurnSession._fg_promotion_seq`**.
  - Como resultado, o subagente `slow` nunca detecta `_has_queued_user_input()` nem `_fg_promotion_seq != start_seq`: **não é promovido a background** e mantém o CLI pai travado esperando o fim completo do subagente lento.
  - **Evidência executada em Python:**
    ```text
    PoC6 fast stole queue: True | slow stayed BLOCKED instead of promoting?: True | slow text: 'slow finished unpromoted!'
    ```
- **Correção exata recomendada:**
  - Em `src/agentos/agentic/native_turn.py:1518-1521`, não drenar a fila de mensagens do usuário enquanto ainda houver outros lotes ativos (`claim_queue=(not had_ask_user_wait and self._active_batches <= 1)`), **e** registrar um hook/callback da `TurnSession` para que qualquer `claim_queued_inputs` realizado por `RuntimeToolExecutor._claim_queued_texts()` incremente `session._fg_promotion_seq` sob `session._subagent_lock`, promovendo imediatamente quaisquer subagentes foreground ainda em voo.

---

### 5. `[HIGH]` Frontend (`TurnTimeline.tsx`): `ThoughtBlock` é Silenciosamente Apagado do DOM Entre Subagentes e Ondas Distintas São Fundidas (`WS-G`)
- **Onde:** `frontend/src/features/conversations/TurnTimeline.tsx:1180-1182` e `frontend/src/features/conversations/TurnTimeline.tsx:1267-1288`
- **Cenário de reprodução / Prova técnica:**
  - `WS-G` alterou `isInterstitialSubagentWaveItem` (`TurnTimeline.tsx:1180-1182`) para retornar `true` para **qualquer** `candidate.kind === 'thought'` (inclusive pensamento do agente principal).
  - Porém, no laço `while (lookahead < resolvedItems.length)` (`TurnTimeline.tsx:1267-1288`), quando `isInterstitialSubagentWaveItem(candidate)` é `true` e existe outro `subagentActivityGroup` adiante, o código **só preserva `candidate` em `interstitialAgentNodes` quando `candidate.kind === 'activity' && candidate.group.kind === 'agent'`** (`linhas 1275-1284`), executando `lookahead += 1; continue` e descartando o restante!
  - Efeitos reais:
    1. Todo `ThoughtBlock` do agente principal (ou de subagentes) emitido entre dois subagentes **desaparece completamente do DOM** (nunca entra em `timelineNodes`).
    2. Se o agente principal aguardar a Onda 1 (`CheckSubagent`), pensar (`ThoughtBlock`) sem emitir texto visível e disparar a Onda 2, o pensamento entre as ondas é apagado e Onda 1 + Onda 2 são fundidas num único `ParallelSubagentsGroup`.
- **Correção exata recomendada:**
  - Em `frontend/src/features/conversations/TurnTimeline.tsx:1275-1286`, quando `candidate.kind === 'thought'`, renderizar `<div key={candidate.id} className="turn-timeline__thought"><ThoughtBlock thought={candidate.thought} turnCompleted={turnCompleted} /></div>` em vez de descartá-lo no `lookahead += 1` (e restringir `isInterstitialSubagentWaveItem` para pensamentos do principal apenas enquanto ainda houver subagentes em execução na onda atual, de modo que uma onda já 100% concluída seguida de novo raciocínio + novos spawns inicie uma 2ª onda separada).

---

### 6. `[HIGH]` Frontend (`activityReducer.ts`): Qualquer Evento Tardio de Subagente Após `turn.completed`/`turn.cancelled` do Pai Ressuscita `state.status` para `'running'` Permanentemente (`WS-G`)
- **Onde:** `frontend/src/features/conversations/activityReducer.ts:148-159, 218-223, 253-265, 284-296`
- **Cenário de reprodução / Prova técnica:**
  - `WS-G` mudou `outcome` para `const outcome = isSubagent ? null : resolveTurnOutcome(...)`, mas manteve a chamada a `derivePhaseAndStatus(..., event, outcome)` nas linhas `223`, `263` e `294`.
  - Em `derivePhaseAndStatus` (`linhas 154-158`), quando `resolvedOutcome` é `null`, a função retorna incondicionalmente `{ activePhase: nextPhase ?? null, status: 'running' }`.
  - Assim, se um subagente em background (ex.: `keep_subagents=true`, subagente promovido, ou `delegation.failed` emitido no `finally` da thread do subagente logo após `turn.cancelled`/`turn.completed` do pai) emitir **qualquer** evento — inclusive `turn.completed` do próprio subagente — `isSubagent` é `true`, `outcome` é `null`, e `state.status` volta de `'completed'`/`'cancelled'` para **`'running'`** tanto em SSE ao vivo quanto no reload da página (`snapshot`), ficando preso em `'running'` para sempre.
  - **Evidência executada em Node 26:**
    ```text
    FE-PoC-B: { before: 'completed', afterSubDone: 'running', snapStatus: 'running' }
    ```
- **Correção exata recomendada:**
  - Em `applyActivityEvent`, `snapshot` e `reconcile_snapshot` (`activityReducer.ts:223, 263, 294`), pular `derivePhaseAndStatus` quando `isSubagent` for `true` (mantendo `activePhase` e `status` do turno principal inalterados).

---

### 7. `[MEDIUM]` `ACTION_LIMIT` Não Reserva Ações em Voo e Rodadas Paralelas no Codex Inflam `tool_rounds` e `iteration` N× (`WS-A`)
- **Onde:** `src/agentos/agentic/native_turn.py:1286-1290, 1311-1318, 1368-1379, 1770`
- **Cenário de reprodução / Prova técnica:**
  1. Em `_execute_batch` (`linha 1287`), `self.action_count + len(entries) > max_actions` consulta `self.action_count`, que só é incrementado na linha `1368` após `self.runtime._run_toolset` retornar. Com `max_actions=1`, 4 chamadas `Agent` paralelas passam todas na checagem e executam 4 subagentes completos (`PoC3 max_actions=1 -> ran: 4 action_count: 4`).
  2. No Codex (`self._adapter_message_ids == False`, pois `on_tool_use_complete` retorna cedo na linha `1964`), cada `Agent` paralelo de um único `Promise.all` recebe um `batch_key` distinto (`batch:1..batch:4`) e incrementa `self.tool_rounds`, `runtime._note_tool_round()` (`linhas 1370-1378`) e `_runner.iteration` (`linha 1770`) **4 vezes** numa única rodada do modelo (`tool_rounds: 4`), antecipando indevidamente `SAFETY_TOOL_ROUNDS` e lembretes periódicos.
- **Correção exata recomendada:**
  - Reservar `self._reserved_actions += len(entries)` sob `self._commit_lock` antes de `_run_toolset` (testando `self.action_count + self._reserved_actions + len(entries) > max_actions`) e liberar no `finally`.
  - Quando `not self._adapter_message_ids`, contabilizar apenas 1 incremento de `tool_rounds` / `iteration` por onda concorrente (ex.: apenas quando `self._active_batches == 1` no início da onda).

---

### 8. `[MEDIUM]` Frontend (`activitySummary.ts` + `TurnTimeline.tsx`): Tool Interna Abortada de Subagente Prende `ParallelSubagentsGroup` em `"Em execução"`, Falha sem `delegation.failed` Prende Placeholder, e Texto `"Iniciando subagente…"` Persiste Após Desfecho Sem Tools (`WS-G`)
- **Onde:** `frontend/src/features/conversations/activitySummary.ts:181-196, 230-277, 728-767` e `frontend/src/features/conversations/TurnTimeline.tsx:508, 918-920, 1298-1302`
- **Cenário de reprodução / Prova técnica:**
  1. **Tool abortada no subagente (`FE-PoC-C`)**: Em `computeSettledEventIds` (`activitySummary.ts:748`), `delegation.failed` / `agent.message_received` só liquida `isWaitingModelEvent(openEvent)`, deixando qualquer `tool.started` aberto do subagente não-liquidado (`openToolSettled: false`), e a linha `758` ignora `TERMINAL_TURN_TYPES` quando `event.parentAgentId` está definido. Enquanto o turno pai segue rodando (`turnCompleted === false`), `ParallelSubagentsGroup` (`TurnTimeline.tsx:1298-1302`) checa `resolveVisualStatus(ev) === 'running'` e permanece preso exibindo **`"Em execução"`** mesmo depois que todos os subagentes da onda já terminaram/falharam.
  2. **Falha apenas em `tool.finished` do `Agent` pai (`FE-PoC-D`)**: `computeSubagentDelegationStatus` (`activitySummary.ts:181-196`) ignora eventos `tool.finished` (`status === 'failed'`) de `run_subagent`/`check_subagent` com `payload.agent_id`. Se `delegation.failed` não for gravado (ex.: erro de persistência engolido em `session.py:3885-3892` ou corte por `EVENT_LIMIT`), o card fica preso em `status: 'running'` até o fim do turno pai. *(Nota: erros de validação pré-spawn como `INVALID_ARGUMENTS` e `SUBAGENT_DEPTH_LIMIT` retornam antes de `session.py:3566` e não emitem `agent.message_sent`, portanto não geram card fantasma).*
  3. **Resumo congelado (`FE-PoC-D`)**: Quando o subagente falha ou conclui sem chamar ferramentas (`0 ações`), `createSubagentSpawnPlaceholderEvent` (`activitySummary.ts:275`) muda `state`/`status` para `'failed'`/`'completed'`, mas mantém `summary: 'Iniciando subagente…'` fixo dentro do card aberto (`useState(running)` inicializado como `true` no spawn em `TurnTimeline.tsx:508`).
  - **Evidência executada em Node 26:**
    ```text
    FE-PoC-C openToolSettled: false
    FE-PoC-D: { d1Status: 'running', dfStatus: 'failed', dfSummary: 'Iniciando subagente…' }
    ```
- **Correção exata recomendada:**
  - Em `computeSettledEventIds` (`activitySummary.ts:747-766`), liquidar todos os eventos abertos de `scopeAgentId` ao receber `delegation.failed`, `delegation.completed`, `agent.message_received`, ou `TERMINAL_TURN_TYPES` daquele `agentId`.
  - Em `computeSubagentDelegationStatus` (`activitySummary.ts:181-196`), registrar `'failed'`/`'completed'` também a partir de `tool.finished` quando `event.payload?.agent_id` estiver presente.
  - Em `createSubagentSpawnPlaceholderEvent` (`activitySummary.ts:275`), usar resumo coerente quando `isSettled` for verdadeiro (ex.: `'Subagente não concluiu a tarefa'` para `'failed'` e `'Concluído'` para `'completed'`).

---

### 9. `[MEDIUM]` `NativeTurnRunner` Perde `user_image_paths` (`--image` no Codex) em Retry Transitório da 1ª Invocação (`WS-F`)
- **Onde:** `src/agentos/agentic/native_turn.py:2669-2673` e `src/agentos/agentic/native_turn.py:3170-3173`
- **Cenário de reprodução:**
  - Na linha `2671`, `user_image_paths` só é enviado se `process_count == 1 and provider_retries == 0`. Se o 1º processo `codex exec` falhar transitoriamente antes de qualquer saída (`recovery == "retry"`, linhas `3170-3173`), a retentativa roda com `process_count == 2`, `provider_retries == 1` e `resume_session_id == None`, reenviando a mensagem inicial **sem `--image`** e perdendo os anexos visuais do usuário.
- **Correção exata recomendada:**
  - Substituir `(process_count == 1 and provider_retries == 0)` por uma flag booleana `initial_user_images_pending = True`, zerada apenas quando a 1ª execução concluir sua entrega inicial (transição para continuação pós-barreira, `verify_gate`, `stop_hook` ou desvio pós-trabalho), mantendo `initial_user_image_paths` em retries transitórios da chamada inicial.

---

### 10. `[LOW]` Assimetria de Payload em `CheckSubagent(agent_ids=["unico_id"])` e Espera de até 60 s Sem Checar Cancelamento (`WS-B`)
- **Onde:** `src/agentos/agentic/tools/agents/agent.py:260-263` e `src/agentos/agentic/session.py:4233, 4289, 4369`
- **Cenário de reprodução:**
  1. `CheckSubagent(agent_ids=["id1"])` cai em `if len(ids) == 1: return self._check_subagent(ids[0], wait_seconds=wait)` (`agent.py:260`), retornando o formato unitário (sem `payload["agents"]` e sem seções `## ...`) apesar de o chamador ter pedido `agent_ids=[...]`.
  2. `wait_futures(live_futures, timeout=float(wait_seconds))` (`session.py:4369`) e `future.result(timeout=float(wait_seconds))` (`session.py:4289`) bloqueiam por até 60 s sem checar `self.cancelled(self.turn)` nem `call_abandoned`. Em `session.py:4233`, `report_path.read_text(encoding="utf-8")` também carece de `errors="replace"` / `try-except OSError`.
- **Correção exata recomendada:**
  - Se `agent_ids is not None`, chamar sempre `self._check_subagent(..., agent_ids=ids)` mesmo para 1 item; aguardar as futures em fatias de `0.25s` abortando se `self.cancelled(self.turn)` ou `call_abandoned()`; ler `report_path` com `errors="replace"` sob `try/except OSError`.

---

### 11. `[LOW]` `_attachment_marker` Ainda Instrui o Codex a Chamar `ViewFile` para Imagens Já Injetadas via `--image` (`WS-F`)
- **Onde:** `src/agentos/conversations/chat.py:158-168`
- **Cenário de reprodução:**
  - Em turnos `openai-codex` com imagem anexada, apesar de `codex exec --image` já entregar a imagem inline na 1ª mensagem, `_attachment_marker` anexa `"Use view_file(path=\"…\") para ler o conteúdo de um anexo visual"`, incentivando uma tool call `ViewFile` redundante.
- **Correção exata recomendada:**
  - Para `provider == "openai-codex"` quando `"image" in codex_input_modalities(model_id)` e o anexo for `kind == "image"` com extensão suportada por `ALLOWED_CODEX_IMAGE_EXTENSIONS`, ajustar a nota indicando que a imagem já acompanha a mensagem (reservando `ViewFile` para PDF/documentos/áudio/vídeo).

---

## Pontos Auditados e Confirmados 100% Sólidos

- **`_LaneGate`, Locks e Balanceamento de Contadores (`Vetor 1`):** `_LaneGate` (`native_turn.py:300-371`) protege `_exclusive_waiting`, `_exclusive_active` e `_shared_active` com `try/finally` e `notify_all()`, sem vazamento em timeout/abandono; `_read_mutex` é liberado se `acquire_shared` abortar (`linha 437`); `_RUNNING_ENTRIES` (`ContextVar`), `_running`, `_in_flight` e `_active_batches` são sempre restaurados em `finally`. Nenhuma inversão de ordem de locks existe entre `_commit_lock` → `_toolset_state_lock`, `_state_lock`, `_corr_lock`, `_subagent_lock`, `_slot_cond` e `CliSlotQueue._cond`.
- **Histórico `self.messages`, `compaction` e `resume` com Pares Separados (`Vetor 1`):** O commit atômico de `(STEP_ASSISTANT_TOOL_CALL, STEP_TOOL_RESULT)` sob `_commit_lock` + esvaziamento único de `consume_round_buffers()` gera unidades `[assistant(call_i), tool(res_i)]` válidas e autossuficientes em `runtime._message_units`, `compaction.py`, `transcript.units`/`within_budget`, `conversations/resume.py` e `TranscriptState.blocks_for`, sem `tool_result` órfão nem duplicação de texto.
- **`releases_cli_slot(request)` Antes da Tradução Pública (`Vetor 1`):** Embora invocado em `_SlotReleasingExecutor.call_tool` (`claude_cli/native_loop.py:438`) antes de `PublicToolNames.arguments_in`, `releases_cli_slot` (`native_turn.py:788-815`) trata ambos os dialetos: traduz `Agent`/`CheckSubagent`/`AskUserQuestion`/`ExitPlanMode`, verifica `args.get("teammate_name") or args.get("name")`, `run_in_background` e `wait_seconds > 0`; e `_before_tool_call(release=...)` / `_after_tool_call()` mantém `_active_tool_calls` e `_holds_slot` íntegros sob chamadas mistas concorrentes.
- **`CheckSubagent` via MCP, `todos_state` e Subagentes Read-Only (`Vetor 2`):** Testado diretamente em Python (`PoC 4`): `TurnToolServer` e `PublicToolNames.schemas_out` publicam `CheckSubagent` com `agent_ids` (`maxItems: 16`), `agent_id` opcional (`required: []`) e `readOnlyHint: true`, aceitando `CheckSubagent(agent_ids=["a", "b"], wait_seconds=5)` sem exigir `agent_id`. `todos_state=[]` isola `fork` e `resume` em `session.py`, `CODE_MODE_STAGE_CHANGED` de `todo_write` filtra `actor == self.main_agent_id`, e `Explore`/`Plan` (`_read_only_mode=True`) removem ferramentas mutáveis e impedem ativação de MCP externo.
- **Sandbox de Imagens no Codex, `attachments_for_turn` e `VisionReader` (`Vetor 3`):** `attachments_for_turn` (`conversations/chat.py:3276-3292` via `_MainAgentStore.__getattr__` → `_RuntimeStore.attachments_for_turn`) retorna `[{"path": ".orin/uploads/<nome>", "kind": "image", ...}]` relativo a `workspace.root`, funcionando tanto em pastas locais quanto no workspace gerenciado do `Chat Mode`. `validated_codex_images` (`codex_cli/attachments.py`) resolve `strict=True` + `is_relative_to`, barrando symlinks/junctions para fora, nomes 8.3 externos, UNC, ADS (`:`), flags `-`, vírgulas, `.svg` e arquivos > 20 MB. Validado contra o binário real `codex-cli 0.167.0`: tanto `codex exec` quanto `codex exec resume <ID>` aceitam múltiplos `--image <FILE>` antes de `--json` sem engolir o `-` final. `VisionReader` bloqueia `google-antigravity`/`openai-codex` e detecta marcadores de mídia omitida lançando `VisionUnavailable`.
- **Idempotência de Chaves no Reload do Frontend (`Vetor 4`):** Em `snapshot` e `reconcile_snapshot`, o placeholder de spawn e as `tool.*` subsequentes compartilham a chave `tool:${agentId}:${turnId}` em `turnTimelineFold.ts` e `activitySummary.ts`, reutilizando o mesmo `SubagentTaskContainer` sem duplicar cards.
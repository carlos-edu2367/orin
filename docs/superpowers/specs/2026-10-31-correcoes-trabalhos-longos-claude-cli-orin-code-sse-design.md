# Especificação de Design: Correção Completa de Trabalhos Longos, Assinatura CLI do Claude Code, Orin Code, Subagentes e SSE

- **Data:** 2026-10-31
- **Status:** Aguardando aprovação do Carlos
- **Escopo:** `Architectural` — Resolução integral de todos os problemas identificados (P0.1–P0.6, P1.1–P1.7, P2.1–P2.5 e P3.1–P3.2) no relatório de diagnóstico da sessão real (`Ashy`).

---

## 1. Visão Geral da Arquitetura de Correção

As correções foram estruturadas em **4 blocos coesos e independentes**, cobrindo desde a corrupção de eventos no SQLite/SSE até a ponte de subagentes e ferramentas da `Assinatura CLI`, o motor de código/shell no Windows e a sincronização de estado da interface React:

1. **Bloco 1 — Saneamento de Eventos, Redaction Segura, Persistência e Reconexão SSE (`P0.1`, `P0.2`, `P2.5`)**
2. **Bloco 2 — Ponte `Assinatura CLI` do Claude Code, Streaming, Tool Bridge, Visão e Contexto (`P0.3`, `P0.4`, `P0.5`, `P0.6`, `P1.5`, `P1.6`, `P1.7`, `P3.1`)**
3. **Bloco 3 — Orin Code: Retrieval (`project_map`), Git Change Tracking e Execução PowerShell/Bash no Windows (`P1.1`, `P1.2`, `P1.3`, `P1.4`)**
4. **Bloco 4 — Ciclo de Vida de Subagentes, `Equipe em atividade`, `Bancada` e Estabilidade de Workers/Contexto (`P1.7`, `P2.1`, `P2.2`, `P2.3`, `P2.4`, `P3.2`)**

---

## 2. Bloco 1 — Saneamento de Eventos, Redaction Segura, Persistência e Reconexão SSE (`P0.1`, `P0.2`, `P2.5`)

### 2.1 Causa Raiz Consolidada
1. Em `src/agentos/agentic/models.py:125-132` (`_bounded`), o limite de tamanho (`text_limit`, `2000` chars para `output_tail` e `512` para campos normais) é verificado **antes** de `redact_text(value)`. Quando `redact_text` substitui padrões curtos como `"requiresApiKey": false` por `"requiresApiKey": [REDACTED]`, o texto expande para além de `2000` chars (ex.: `2032`–`2040` chars nas linhas `3990–3993` do `orin.db`) e é gravado no banco.
2. Quando o endpoint SSE (`GET /v1/conversations/{id}/events`, `src/agentos/api/gateway.py:1781`) chama `activity_store.replay()` -> `_event_from_row()` (`src/agentos/persistence/postgres/agentic_activity.py:68-84, 165, 235`), `AgentActivityEvent.__post_init__` executa `_bounded()` uma segunda vez sobre o texto já expandido e levanta `ValueError: output_tail text exceeds limit`, derrubando o stream SSE em loop infinito.
3. Em `src/agentos/conversations/chat.py:499-512` (`PostgresChatStore._activity`), qualquer `ValueError` de limite de itens (`MAX_ACTION_ITEMS = 64`) ou texto descartava o evento `TOOL_FINISHED` silenciosamente (`except ValueError: return None`).
4. Em `src/agentos/conversations/chat.py:1550, 1589, 1608, 1623, 1847-1862`:
   - `generating_tool_call` cria eventos `tool.progressed` (`phase="generating_arguments"`) com `"call_id"` mas sem `"invocation_id"`, e `_compact_snapshot_activities` mantém esses eventos em `settled_turns`.
   - `_compact_snapshot_activities` sobrescreve `"cursor"` de cada atividade com `str(row["sequence"])` (inteiro não assinado) em vez do cursor HMAC assinado (`a.<base64>.<sig>`).
5. Em `frontend/src/features/conversations/ChatPage.tsx:251-267, 395-412, 2422-2437` e `turnTimelineFold.ts:277-282`:
   - `loadSnapshot(false)` atualiza `conversation` (`Trabalhou por 6m 2s`), mas ignora `next.activities` e `next.activity_cursor`, mantendo `activity.events` e `cursorRef.current` congelados no evento anterior à falha do SSE.
   - Quando `timeline.length > 0` mas o evento `turn.completed` foi perdido no SSE, `ChatPage.tsx` renderiza `<TurnTimeline />` e **esconde `item.content`** (ocultando a resposta final de 10.747 chars salva no banco), além de manter os cards de tool com status `running` (bolinha amarela).

### 2.2 Design da Solução
1. **Redaction Antes do Truncamento e Bounding Defensivo (`src/agentos/agentic/models.py`)**:
   - Em `_bounded(key, value, *, depth=0, redact=True)`:
     - Se `isinstance(value, str)`: primeiro aplicar `redact_text(value)` quando `redact=True`, e em seguida truncar/validar em `text_limit` (para chaves em `PUBLIC_PAYLOAD_KEYS` ou truncamento seguro `value[:text_limit]` quando vindo de sanitização de saída, mantendo rejeição apenas onde o contrato exigir antes de redaction se explicitamente solicitado, ou truncando após `redact_text` para que `redact_text` **jamais** aumente uma string válida acima de `text_limit`).
   - Adicionar um helper `coerce_public_mapping(payload)` usado na desserialização de linhas já persistidas no banco (`_event_from_row` em `agentic_activity.py`) que trunca strings acima de `text_limit` e listas/dicionários acima de `MAX_ACTION_ITEMS` **sem lançar `ValueError`**, recuperando automaticamente as linhas `3990–3993`, `4098`, `4211`, `4224–4227` já existentes no `orin.db` do usuário.
2. **Proteção do Stream SSE e Cursores Assinados (`src/agentos/api/gateway.py`, `src/agentos/persistence/postgres/agentic_activity.py`, `src/agentos/conversations/chat.py`)**:
   - Em `PostgresAgenticActivityStore.replay()`, isolar a desserialização de cada linha com fallback seguro para que uma linha malformada no banco nunca derrube a listagem inteira.
   - Em `PostgresChatStore._activity` (`chat.py:499-512`), truncar previamente listas grandes (como `artifacts`) para caber no orçamento de `MAX_ACTION_ITEMS` e logar warning caso ocorra erro, garantindo que `TOOL_FINISHED` nunca seja descartado silenciosamente.
   - Em `generating_tool_call` (`chat.py:1847-1862`), incluir `"invocation_id": call_id` no `payload`, e em `_compact_snapshot_activities` (`chat.py:1550`), descartar todos os `tool.progressed` em `settled_turns` (inclusive `generating_arguments`).
   - Em `_compact_snapshot_activities` (`chat.py:1589, 1608, 1623`), codificar o cursor de cada item usando `self.activity_store._encode_cursor(user_id, conversation_id, int(row["sequence"]))` em vez de `str(row["sequence"])`.
3. **Reconciliação de Snapshot, Exibição Garantida da Resposta Final e Liquidação de Tools no Frontend (`ChatPage.tsx`, `turnTimelineFold.ts`, `TurnTimeline.tsx`, `activitySummary.ts`, `activityReducer.ts`)**:
   - Em `ChatPage.tsx`:
     - Em `loadSnapshot(resetActivity)`: mesmo quando `resetActivity === false`, se a conversa carregada tiver `next.state !== 'working'` (turno já concluído/cancelado/falho) ou se `activity.connection === 'degraded'`, sincronizar os eventos faltantes de `next.activities`, avançar `cursorRef.current` para `next.activity_cursor` (quando maior/válido) e disparar a liquidação dos eventos abertos do turno concluído.
     - Ao receber `onCursor` ou `onEvent` em `streamConversationEvents`, atualizar `activity.connection` para `'live'` caso esteja `'connecting'` ou `'degraded'`.
     - Na renderização das mensagens do assistente (`ChatPage.tsx:2422-2437`), **sempre** renderizar o bloco de resposta final `item.content` abaixo da `<TurnTimeline />` quando `item.content.trim()` existir e o `TurnTimeline` não contiver um bloco de texto final equivalente (evitando que uma timeline incompleta esconda a resposta final gravada no banco).
   - Em `turnTimelineFold.ts` (`buildTurnTimeline`) e `activitySummary.ts` (`computeSettledEventIds`):
     - Aceitar `turnCompleted: boolean` em `computeSettledEventIds(events, turnCompleted)`: quando `turnCompleted === true`, marcar todos os eventos abertos (`tool.called`, `tool.started`, `tool.progressed`, `agent.thought_delta`, `turn.started`) daquele turno como `settled`!
     - Em `applyEventToGroup` (`activitySummary.ts:202-224`) e `settleTurnEvents` (`activityReducer.ts:94-118`), preservar o `label`/`description` original da tool sem deixar o prefixo transitório `"Executando: "` como título permanente após o término do turno.

---

## 3. Bloco 2 — Ponte `Assinatura CLI` do Claude Code (`claude_code.py`, `opencode.py`, `provider_stream.py`, `runtime.py`, `chat.py`) (`P0.3`–`P0.6`, `P1.5`–`P1.7`, `P3.1`)

### 3.1 Causa Raiz Consolidada
1. **Timeouts de Wall-Clock matam streams ativos (`P0.3`)**:
   - `stream_claude_code_bridge` (`claude_code.py:746, 891, 994`) aplica `timeout_seconds = 180.0` como tempo total desde o `Popen`, e `AgenticTurnRuntime` (`runtime.py:74, 821, 953`) aplica `PROVIDER_CALL_TIMEOUT_SECONDS = 90.0` desde o início da chamada sem renovar o `deadline` quando chegam `deltas`.
   - Quando ocorre timeout ou falha após qualquer delta de `thinking`, `ProviderTransportError` (subclasse de `ProviderKeyRejected`) não é reconhecido como `ProviderError(TIMEOUT)` em `runtime.py:1109-1122`, travando o turno em `PAUSED` (`reconciliation_required`) sem retry.
2. **Escrita síncrona em `proc.stdin` antes de iniciar threads leitoras (`P0.6`)**:
   - Em `claude_code.py:840-878`, `proc.stdin.write(user_envelope)` roda antes de `stdout_thread.start()` e `stderr_thread.start()`, sujeito a deadlock de pipe no Windows em históricos grandes.
3. **Ponte `<orin_tool_call>` perde chamadas por divergência de nomes, truncamento e auto-referência (`P0.4`)**:
   - `_resolve_tool_name` (`opencode.py:554-579`) falha ao resolver `Read`/`Write`/`Edit` no Chat Mode (`allowed_names` em snake_case) e falha ao resolver aliases snake_case (`bash`, `read_file`, `grep`) no Code Mode (`allowed_names` em PascalCase).
   - `_feed_streaming_text` (`claude_code.py:935`) e `_ORIN_TOOL_CALL_RE` (`opencode.py:62`) quebram se o argumento JSON contiver a string literal `</orin_tool_call>`.
   - `FinishReason.LENGTH` (`max_tokens`) é ignorado (`claude_code.py:1066, 1269`), desativando `_recover_from_output_limit` (`runtime.py:608`).
4. **Perda de Visão (`image`) e `thinking` + Desperdício de Contexto (`P0.5`, `P1.6`)**:
   - `_format_bridge_conversation_transcript` (`opencode.py:456-552`) descarta blocos `"image"` (embora `ClaudeCodeModelCatalogClient` anuncie `vision: True`) e duplica mensagens `system` intermediárias.
   - `runtime.py:1522` desativa `_age_tool_results` para `"anthropic"` mesmo quando usando a CLI bridge sem cache breakpoints, e `_SUBSCRIPTION_MODELS` anuncia modelos especulativos (`claude-opus-5-5`, `fable-5`) com `1_000_000` de janela, atrasando a autocompactação para ~795k tokens.
5. **Subagentes desviados da `Assinatura CLI`, falta de `cancel_check` e `cwd` contaminado (`P1.5`, `P1.7`, `P3.1`)**:
   - `_child_provider_for_turn` (`workers/chat.py:1289-1302`) desvia subagentes da `Assinatura CLI` para outros providers se o modelo estiver favoritado neles; `_provider_transport` (`chat.py:1162-1184`) envolve a CLI bridge em `MultiKeyProviderStreamTransport` se houver chaves antigas no banco; `provider_stream.py:886-894` não passa `cancel_check`; e `Popen` (`claude_code.py:819`) herda o `cwd` do repositório do Orin.

### 3.2 Design da Solução
1. **Stall/Idle Timeout Renovável e Retry Limpo (`claude_code.py`, `runtime.py`)**:
   - Em `stream_claude_code_bridge` (`claude_code.py`): usar `idle_timeout_seconds = 180.0` (renovado a cada linha/evento recebido de `claude.exe`) combinado com um `max_wall_clock_seconds = 1800.0` (30 min).
   - Em `AgenticTurnRuntime` (`runtime.py:821, 953`): renovar `deadline = time.monotonic() + self.provider_timeout_seconds` a cada `delta` recebido no stream.
   - Em `runtime.py:1109-1122`: tratar `ProviderTransportError` com código de timeout/rate-limit/stream-interrupted onde nenhum `text_parts` nem `calls` completo foi emitido (apenas `thinking` ou vazio) como `is_keepalive_stall = True`, permitindo retry automático em vez de travar em `reconciliation_required`.
2. **Escrita Assíncrona / Ordenada no `stdin` (`claude_code.py:840-878`)**:
   - Iniciar `stdout_thread` e `stderr_thread` **antes** de escrever em `proc.stdin` (ou realizar a escrita de `proc.stdin` em uma daemon thread dedicada com `try/except OSError`), eliminando qualquer possibilidade de deadlock de pipe no Windows.
3. **Parser `<orin_tool_call>` Balanceado, Resolução Bidirecional de Tools e `FinishReason.LENGTH` (`opencode.py`, `claude_code.py`)**:
   - Em `opencode.py` (`_TOOL_NAME_ALIASES` e `_resolve_tool_name`): mapear tanto PascalCase (`Read`, `Write`, `Edit`, `Bash`, `PowerShell`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `TodoWrite`, `Agent`) quanto snake_case (`read_file`, `write_file`, `edit_file`, `run_command`, `powershell`, `list_files`, `search_files`, `web_fetch`, `web_search`, `todo_write`, `spawn_agent`) para o nome exato presente em `allowed_names`, e normalizar chaves de argumentos (`file_path <-> path`, `old_string <-> old_text`, `new_string <-> new_text`) conforme o modo da sessão.
   - Substituir o corte ingênuo por `find("</orin_tool_call>")` por um extrator que respeita strings JSON entre aspas ao procurar a tag de fechamento `</orin_tool_call>` correspondente ao objeto JSON `{...}`.
   - Em `claude_code.py:1066, 1269`: rastrear `stop_reason` dos eventos `message_delta` / `result`; se `stop_reason == "max_tokens"` (ou se houver `<orin_tool_call>` aberto não fechado ao fim do stream), emitir `FinishReason.LENGTH` para acionar a recuperação automática `_recover_from_output_limit`.
   - Atualizar `_build_bridge_system_prompt` (`opencode.py:395-415`) para permitir múltiplas blocos `<orin_tool_call>` em paralelo na mesma resposta quando as chamadas forem independentes.
4. **Suporte a Imagens (Vision), Envelhecimento de Tool Results e Janela de Contexto Realista na `Assinatura CLI` (`claude_code.py`, `opencode.py`, `runtime.py`, `chat.py`, `session.py`)**:
   - Em `_format_bridge_conversation_transcript` (`opencode.py:456-552`):
     - Não duplicar mensagens `role == "system"` que já foram adicionadas em `system_parts` (ou colocá-las apenas em `turns_text` como `[System]` quando forem instruções mid-turn, sem reescrever `system_parts`).
     - Para blocos `"image"` (com `source.type == "base64"` ou `file_path`), incluir blocos de conteúdo multimodal compatíveis no `message.content` do envelope `stream-json` enviado ao `claude -p` (lista de blocos `[{"type": "image", ...}, {"type": "text", ...}]` suportada pela especificação Anthropic/Claude CLI) **e**, em `session.py`, habilitar o fallback do `_LazyVisionReader` quando a imagem vier de `view_file` se necessário, garantindo que o modelo sempre enxergue as imagens anexadas.
   - Em `runtime.py:1522-1531` (`_prefix_caching`): retornar `False` quando `is_claude_cli_bridge_url(base_url)` for `True`, reativando `_age_tool_results()` para limpar tool results antigos.
   - Em `claude_code.py:92-111, 558-561`: limpar a lista `_SUBSCRIPTION_MODELS` mantendo os modelos reais suportados pelo Claude Code (`claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5-20251001`), mantendo o mapeamento de compatibilidade em `_normalize_claude_model_id` para conversas existentes que tinham `claude-opus-5-5` salvo, e definir o orçamento de autocompactação padrão da CLI bridge em `200_000` tokens (salvo sufixo explícito `[1m]`).
5. **Propagação de `cancel_check`, Herança de Subagentes, Semáforo de Concorrência e Isolamento Windows (`provider_stream.py`, `chat.py`, `claude_code.py`, `agent_definitions.py`)**:
   - Em `HTTPProviderStreamTransport.stream` (`provider_stream.py:886-894`): repassar `cancel_check=cancel_check` para `stream_claude_code_bridge`.
   - Em `ChatWorker._provider_transport` (`chat.py:1162-1184`): quando `is_claude_cli_bridge_url(base_url)` for `True`, ignorar chaves salvas no cofre e retornar diretamente `HTTPProviderStreamTransport` sem `MultiKeyProviderStreamTransport`.
   - Em `ChatWorker._child_provider_for_turn` (`chat.py:1289-1302`) e `resolve_agent_model` (`agent_definitions.py:258-269`): quando o turno pai usar `anthropic` (`Assinatura CLI`), manter os subagentes no provider `anthropic` e mapear nativamente os aliases `haiku` -> `claude-haiku-4-5-20251001`, `sonnet` -> `claude-sonnet-4-6`, `opus` -> `claude-opus-4-7`.
   - Em `claude_code.py`:
     - Extrair `retry_after_seconds` de `resetsAt` em `rate_limit_event` e respeitar `retry_after_seconds` em `runtime.py:_retry_wait` mesmo quando `retry_base_delay_seconds == 0.0`.
     - Limitar chamadas simultâneas `claude.exe -p` via semáforo (`_CLAUDE_CLI_SEMAPHORE = threading.BoundedSemaphore(3)`) para evitar corrida de refresh OAuth e tempestade de processos nos subagentes.
     - Passar um diretório temporário isolado como `cwd` no `subprocess.Popen` do `claude.exe` (evitando carregar o `CLAUDE.md` do próprio Orin) e resolver wrappers `.cmd`/`.ps1` no Windows para o executável real.

---

## 4. Bloco 3 — Orin Code: Retrieval (`project_map`), Git Change Tracking e Execução PowerShell/Bash no Windows (`P1.1`, `P1.2`, `P1.3`, `P1.4`)

### 4.1 Causa Raiz Consolidada
1. `RetrievalService.project_map()` (`src/agentos/retrieval/service.py:108-118`) chama `self._indexer.scan()` -> `_embed_pending()` (`src/agentos/retrieval/indexer.py:55, 138-152`) de forma síncrona. Quando o Ollama local não tem o modelo de embedding (`404 Not Found` em `http://localhost:11434/api/embed`), `project_map` trava por 5m 24s e continua rodando órfão mesmo após o turno ser cancelado.
2. Em `src/agentos/agentic/workspace.py:444-473`, `begin_change_tracking()` retorna `None` em repositórios `.git`, fazendo `end_change_tracking(None)` reportar todos os arquivos untracked/modificados pré-existentes (inclusive `.orin/*` e `uploads/*`) como criados por cada comando shell (55 eventos `artifact.created` falsos no Turno 2). E `src/agentos/agentic/agent_tools.py:1205-1220` não ignora `uploads/`, forçando o gate `verify_project` sem nenhum código ter sido alterado.
3. A tool `powershell` (`src/agentos/agentic/tools/shell/powershell.py:72`) não está mapeada nos componentes de terminal do frontend (`CodeToolCallBlock.tsx`, `TurnTimeline.tsx`, `activitySummary.ts`, `conversations.ts`), omitindo `stdout`/`stderr`/`exitCode` e exibindo `powershell(Executando: ...)`.
4. Em `src/agentos/agentic/tools/shell/bash.py:335-368` e `_shared.py:374-385, 658-665`: processos filhos no Windows que herdam `stdout`/`stderr` travam o loop até o timeout máximo; `_terminate_process_tree` retorna sem matar os processos netos se o pai já saiu; e `timeout` em ms não é convertido para segundos quando `public_tool_names=False`.

### 4.2 Design da Solução
1. **`project_map` Sem Bloqueio de Embedding e Fast-Fail em `/api/embed` (`src/agentos/retrieval/service.py`, `src/agentos/retrieval/indexer.py`)**:
   - Em `WorkspaceIndexer.scan()`, adicionar parâmetro `embed: bool = True`.
   - Em `RetrievalService.project_map()`, chamar `self._refresh_if_stale(embed=False)` — pois `project_map` usa exclusivamente símbolos léxicos/AST e grafo de imports, nunca vetores!
   - Em `WorkspaceIndexer._embed_pending()`, se `self._embedding.embed()` falhar ou retornar vazio na primeira tentativa do batch (ex.: Ollama 404 ou offline), interromper o loop de embedding imediatamente (circuit-breaker por scan) em vez de tentar centenas de chunks sequencialmente.
2. **Baseline Real no Git Change Tracking e Exclusão de `uploads/` e `.orin/` (`src/agentos/agentic/workspace.py`, `src/agentos/agentic/agent_tools.py`)**:
   - Em `workspace.py`:
     - Em `begin_change_tracking()`, quando `(self.root / ".git").exists()`, capturar um snapshot leve dos arquivos atualmente listados em `_git_changed_files()` com seu par `(mtime_ns, size)` (e ignorar sempre prefixos `.orin/`, `.git/` e `uploads/`).
     - Em `end_change_tracking(baseline)`, comparar o novo `_git_changed_files()` contra o `baseline`: emitir artefatos **apenas** para arquivos que não estavam no baseline ou cujo `(mtime_ns, size)` mudou durante a execução do comando!
   - Em `agent_tools.py:1205-1220` (`_record_changed_paths`): adicionar `"uploads/"` em `ignored_prefixes`.
3. **Paridade Completa de `powershell` no Backend e Frontend (`session.py`, `CodeToolCallBlock.tsx`, `TurnTimeline.tsx`, `activitySummary.ts`, `conversations.ts`)**:
   - Em `src/agentos/agentic/session.py`:
     - Linha `1159` (`_started_summary`): tratar `name in {"run_command", "bash", "powershell"}`.
     - Linhas `1319-1337` (`tool_progressed`): incluir `"description": str(payload.get("description") or "")[:200]` no payload do evento.
   - No Frontend:
     - Em `CodeToolCallBlock.tsx`: adicionar `'powershell'` junto a `'run_command'` e `'bash'` nas linhas `145`, `171`, `278`, `320`, `408` e `495` (usando rótulo `PowerShell` ou `Bash` limpo, exibindo `description` / `command`, `stdout`/`stderr` e badge `exit N`).
     - Em `TurnTimeline.tsx`: adicionar `'powershell'` em `CODE_TOOL_NAMES` (`linhas 67-89`) e em `summarizeCodeToolBatch` (`linha 367`).
     - Em `activitySummary.ts:501` (`COMMAND_TOOLS`) e `conversations.ts:775` (`parseBashOutput`): incluir `'powershell'`.
4. **Drenagem Segura de Pipes e `taskkill /T /F` no Windows + Conversão de `timeout` (`bash.py`, `_shared.py`)**:
   - Em `src/agentos/agentic/tools/shell/bash.py:335-368`:
     - Quando `process.poll() is not None` (processo principal já terminou), aguardar no máximo `0.25s` para `t_out` e `t_err` finalizarem; se continuarem vivas (porque um processo filho em background herdou o pipe), fechar `process.stdout` e `process.stderr` e sair do loop imediatamente.
     - Remover `process.communicate(timeout=0.5)` concorrente com as threads `t_out`/`t_err`; fazer `t_out.join(timeout=0.5)` e `t_err.join(timeout=0.5)` após fechar os streams.
   - Em `src/agentos/agentic/tools/_shared.py:658-665` (`_terminate_process_tree`): no Windows (`os.name == "nt"`), chamar `_terminate_pid(process.pid)` (`taskkill /PID <pid> /T /F`) mesmo se `process.poll() is not None` quando acionado por timeout/cancelamento, garantindo o encerramento de processos netos na árvore.
   - Em `src/agentos/agentic/tools/_shared.py:374-385`: se o argumento vier da chave `"timeout"` (ou se `timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS` e `>= 1000`), converter de milissegundos para segundos (`val / 1000.0`) antes de aplicar o clamp `[1, 600]`.

---

## 5. Bloco 4 — Ciclo de Vida de Subagentes, `Equipe em atividade`, `Bancada` e Estabilidade de Workers/Contexto (`P1.7`, `P2.1`–`P2.4`, `P3.2`)

### 5.1 Causa Raiz Consolidada
1. Em `src/agentos/agentic/session.py:1215-1217, 1511-1513, 2243-2412, 2820-2830`:
   - `self.agents_store.set_state(agent_id, "completed")` não está em um bloco `try / finally`, deixando subagentes com `state="working"` para sempre no banco se ocorrer exceção.
   - `_SubagentStore.lifecycle` omite `parent_agent_id` e suprime o evento de conclusão do subagente (`if is_subagent: return`), deixando o último evento do subagente como `working`.
   - `close_background_subagents()` não sinaliza `entry["cancel"].set()` quando o turno pai falha ou é cancelado.
2. Em `frontend/src/features/conversations/ChatPage.tsx:1871-1951, 2079-2104, 2572-2626`, `TeamActivityView.tsx` e `CodeWorkbenchPanel.tsx:226-229`:
   - `codeWorkbenchFiles` trata `toolKind === 'filesystem'` (que inclui `search_files` / `search_code`) como arquivo, usando `event.label` (a regex de busca) como `filePath`, e rotula arquivos apenas lidos (`status === 'inspected'`) como `"21 arquivos alterados · Ver alterações"`.
   - O botão `Equipe em atividade` e o ponto verde ficam acesos mesmo sem subagentes ativos ou com a conversa concluída, e `TeamActivityView.tsx` inclui chamadas de tool do agente principal (`agent:<conv_id>:main`) como subagentes.
3. Em `src/agentos/conversations/chat.py:2694-2721`, `recover_stale` ignora `lease_renewed_at`; em `runtime.py:67`, `MICROCOMPACT_KEEP_RECENT = 6` apaga leituras paralelas muito cedo; e em `checkpoints.py:121`, `FileCheckpointStore` lê arquivos sem limite de bytes.

### 5.2 Design da Solução
1. **Ciclo de Vida Determinístico de Subagentes (`src/agentos/agentic/session.py`)**:
   - Envolver `_execute_subagent_run_unlocked` em `try / finally` garantindo que `self.agents_store.set_state(agent_id, final_state)` (`"completed"`, `"failed"` ou `"cancelled"`) seja sempre persistido.
   - Em `_SubagentStore`, incluir `parent_agent_id=self.main_agent_id` nos eventos emitidos e emitir o evento de finalização do subagente quando ele encerrar.
   - Em `close_background_subagents()`, sinalizar `entry["cancel"].set()` para todos os subagentes pendentes quando o turno pai falhar ou for cancelado e aguardar o shutdown com timeout finito (`timeout=5.0`).
2. **Contagem Precisa na `Bancada` e no Card `"arquivos alterados"` (`ChatPage.tsx`, `CodeWorkbenchPanel.tsx`)**:
   - Em `ChatPage.tsx` (`codeWorkbenchFiles`):
     - Ignorar ferramentas de busca (`search_files`, `search_code`, `grep`, `glob`, `list_files`, `project_map`) e nunca usar `event.label` como fallback de `filePath`; exigir um caminho real (`payload.path` / `payload.file_path` / `event.filePath`) e normalizar barras (`\` -> `/`, removendo `./` inicial).
     - Separar `changedWorkbenchFiles = codeWorkbenchFiles.filter(f => f.status === 'modified' || f.status === 'added')`.
     - Renderizar o card `"N arquivos alterados · Ver alterações"` (`ChatPage.tsx:2572`) **somente** quando `changedWorkbenchFiles.length > 0`, exibindo `changedWorkbenchFiles.length` no título e listando os arquivos efetivamente modificados/adicionados.
     - No badge do botão `Bancada` (`ChatPage.tsx:2080`), exibir `changedWorkbenchFiles.length > 0 ? changedWorkbenchFiles.length : codeWorkbenchFiles.length` com semântica clara, e em `CodeWorkbenchPanel.tsx:226-229` retornar `modifiedOrAdded.length` sem o fallback `: files.length`.
3. **Estado Real do Botão `Equipe em atividade` e `TeamActivityView.tsx` (`ChatPage.tsx`, `TeamActivityView.tsx`)**:
   - Em `ChatPage.tsx`:
     - Em `subagentRoster`, se `!running` (ou `conversation.state !== 'working'`), marcar quaisquer agentes remanescentes em `'working'` como `'completed'`.
     - No botão do topo (`data-testid="chat-team-view-toggle"`), exibir o ponto verde pulsante e o texto `"Equipe em atividade"` quando `running && activeSubagentCount > 0` (ou manter o texto `"Equipe em atividade"` compatível com seletores de teste mas com classe `.is-idle` sem ponto pulsante quando `!running || activeSubagentCount === 0`).
   - Em `TeamActivityView.tsx`:
     - Em `deriveAgentsFromEvents`, filtrar eventos do agente principal (`cleanId === 'main' || cleanId === 'assistant' || cleanId.endsWith(':main')`) e não incluir `ev.kind === 'tool'` quando o evento pertencer ao agente principal.
     - Usar a prop `running`: quando `running === false`, nenhum subagente ou stepper deve permanecer com status `'running'` / `'Em andamento'`.
4. **Hardening de Workers, Microcompactação e Checkpoints (`chat.py`, `runtime.py`, `checkpoints.py`)**:
   - Em `PostgresChatStore.recover_stale` (`src/agentos/conversations/chat.py:2702`), usar `func.coalesce(conversation_dispatches.c.lease_renewed_at, conversation_dispatches.c.acquired_at) < cutoff`.
   - Em `src/agentos/agentic/runtime.py:67`, elevar `MICROCOMPACT_KEEP_RECENT` de `6` para `16`.
   - Em `src/agentos/code_mode/checkpoints.py:121`, adicionar limite `MAX_CHECKPOINT_FILE_BYTES = 10 * 1024 * 1024` (10 MB) antes de `target.read_bytes()`.

---

## 6. Estratégia de Testes (TDD + Testes Adversariais + Verificação Visual)

1. **Testes Unitários e Adversariais (RED -> GREEN -> REFACTOR)**:
   - **Bloco 1**: Teste adversarial em `models.py` e `agentic_activity.py` com string de `2000` chars contendo múltiplas ocorrências de `"requiresApiKey": false` verificando que `redact_text` + `_bounded` nunca excede `2000` chars e que linhas legadas com `2040` chars no SQLite são lidas por `replay()` sem levantar `ValueError`.
   - **Bloco 2**: Testes unitários para `claude_code.py` e `opencode.py` cobrindo: stall timeout renovável por delta, escrita em `stdin` com threads leitoras já ativas, extração de `<orin_tool_call>` contendo `</orin_tool_call>` dentro de strings JSON, resolução de tools em Chat Mode (`Read` -> `read_file`) e Code Mode (`bash` -> `Bash`), propagação de `FinishReason.LENGTH`, repasse de `cancel_check`, `_prefix_caching == False` na CLI bridge e preservação do provider `anthropic` nos subagentes.
   - **Bloco 3**: Testes unitários para `RetrievalService.project_map()` sem chamar `_embed_pending()`, `workspace.py` (`begin_change_tracking` / `end_change_tracking` em repo `.git` ignorando arquivos pré-existentes, `.orin/` e `uploads/`), e `_run_shell` no Windows liberando pipes quando processos filhos ficam vivos.
   - **Bloco 4**: Testes frontend/backend para `codeWorkbenchFiles` (ignorando queries de `search_files` e separando `inspected` de `modified`), `powershell` no `CodeToolCallBlock`, reconciliação de `loadSnapshot(false)` exibindo `item.content` final e `recover_stale` respeitando `lease_renewed_at`.
2. **Verificação no Banco Real e no Navegador (Playwright)**:
   - Validar que a conversa real da **Ashy** (`chat_1ed5df59af2d4376ad8e57f93bf5c833`) carrega o stream `/events` com HTTP 200 sem nenhum `ValueError`, exibe a resposta completa de 10.747 caracteres, liquida o card `powershell` e não mostra `"Atualizações em tempo real indisponíveis; tentando reconectar."` nem `"21 arquivos alterados"`.

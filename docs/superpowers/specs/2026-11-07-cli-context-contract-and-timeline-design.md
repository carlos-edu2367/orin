# Design Spec: Correção de Contexto Multi-Turno em CLIs, Retomada de Contrato, Medidor de Contexto e Timeline ("Preparando...")

**Data:** 2026-11-07  
**Branch / Worktree:** `fix/cli-context-and-timeline` (`.orin/worktrees/fix-cli-context-and-timeline`)  
**Status:** Aguardando aprovação do Carlos

---

## 1. Contexto e Diagnóstico (Cruzamento Banco Real + Logs do `agy` + Auditoria de Código)

A investigação na instância em execução (`C:\Users\carlos\AppData\Local\Orin\data\orin.db`, conversa `chat_4a7b93cedcae4705bf24c189645f135d`) e nos logs brutos do `antigravity-cli` (`~/.gemini/antigravity-cli/brain/*/transcript_full.jsonl`), combinada com a análise estática do código levantada pelo Claude, identificou **6 problemas reais** (sendo 1 a causa imediata do corte total das mensagens nos turnos 2 e 3 no `agy`, e 5 bugs estruturais confirmados no código que agravam ou causam sintomas idênticos):

### 1.1. Causa imediata da repetição nos Turnos 2 e 3 (`native_turn.py` + limite de 190 KB do `agy`)
1. No Turno 1 (`turn_f058ea0bbd264a0c8db9167a2d02df6a`), o agente executou 65 tool calls cujos `tool_result` somaram **737.201 bytes** em `conversation_turn_steps`.
2. Como o modelo `gemini-4-argon-high` tem janela de 1.000.000 de tokens, `REHYDRATION_BUDGET_FRACTION = 0.40` liberou 400.000 tokens (~1,6 MB) para `_rehydrated_steps`, reidratando todos os 65 `tool_result` sem compressão.
3. Diferente do loop clássico (`runtime.py:796`), o `NativeTurnRunner._run` (`native_turn.py:1622`) **não chama `_age_tool_results` nem `_maybe_compact`**.
4. O `_build_initial_user_content` (`native_turn.py:1466-1548`) serializou todo o histórico em um único `USER_INPUT` enviado ao `agy`, colocando o Turno 1 no início e a mensagem nova do usuário no final (totalizando **744 KB** no Turno 2 e **1,03 MB** no Turno 3).
5. O binário `agy` possui um limite interno de **190.791 bytes** para `USER_INPUT` e aplica **corte no final (`tail truncation`)**:
   - Turno 2 (`7f093c6e-624e-4919-b82e-5ca035951a39`): `<truncated 553483 bytes>` na linha 186 de `Dashboard.tsx` (16ª ação do Turno 1).
   - Turno 3 (`d9bf5dbb-d3d8-48d8-94ca-dbb2559f10ba`): `<truncated 846236 bytes>` no mesmo ponto.
   - Tudo o que vinha depois (o fim do Turno 1, a resposta do assistente do Turno 1, a skill `/superpowers:writing-plans` e as mensagens do usuário dos Turnos 2 e 3) foi descartado pelo `agy` antes de chegar ao modelo.

### 1.2. Ausência de rótulo `[User]` na mensagem atual e descarte do resumo de compactação no `NativeTurnRunner` (`native_turn.py:1483-1548`)
- `prior_messages` recebe rótulos `[User]`, `[Assistant]`, `[Tool Use]`, `[Tool Result]`, seguido por `<system-reminder>...</system-reminder>`, mas `cur_text` (a mensagem atual) é anexada no final **sem o rótulo `[User]`**, ficando colada no fechamento do `<system-reminder>`.
- Além disso, `[m for m in source_msgs[:pinned] if m.get("role") != "system"]` descarta todas as mensagens `role == "system"`, o que apaga o resumo de compactação (`compaction_message` em `chat.py:2469`) e o aviso de mensagens omitidas (`runtime.py:1946`) para todas as CLIs (`antigravity`, `claude`, `codex`).

### 1.3. Retomada cega de `write_contract` em conversas no modo Chat (`chat.py:704`, `session.py:5213`, `phases.py:169`)
- `ChatStore.latest_contract` busca o último `write_contract` da conversa inteira sem checar se o turno que o criou já terminou (`state == "completed"`).
- Em turnos seguintes do modo Chat, esse contrato antigo vira `runtime.contract` e força `PhaseController(resumed_contract=True)` a pular a fase `ORIENT` direto para `EXECUTE` sob os critérios de aceite do turno anterior.

### 1.4. Pedido do usuário escondido no fim da expansão de `/skill` (`chat.py:156-168`, `runtime.py:742`) e corte head-first em `_compact_source` (`runtime.py:1768`)
- `_skill_body` posiciona `Pedido da pessoa: {arguments}` apenas no final das instruções da skill (que podem ter dezenas de KB). Como `_first_request_text` lê apenas os primeiros `2.000` caracteres (`runtime.py:742`), ele captura apenas o cabeçalho da skill e perde o pedido real do usuário.
- `_compact_source` itera `sorted(indices)` do início para o fim parando em `48.000` caracteres; se o início da conversa tiver `tool_result` grandes, o summarizer nunca vê as respostas finais nem os pedidos recentes.

### 1.5. Card `"Preparando <tool>..."` infinito e deslocamento de IDs de correlação (`antigravity_cli/stream.py:368-434`, `native_turn.py:575-588`, `turnTimelineFold.ts:307`)
- O `agy` executa a ferramenta no servidor MCP do Orin **antes** de emitir `step_update` no stdout.
- Quando o `step_update` chega com `state == "DONE"`, `AntigravityStreamTranslator.feed_line` emite `TOOL_USE_START` e `TOOL_USE_END` com ID `{conv_id}:{step_idx}`.
- Isso faz o `_RunnerSink` emitir um evento `generating_tool_call` (`Preparando <tool>...`) para uma ferramenta que já terminou e empilha `{conv_id}:{step_idx}` em `_pending_tool_uses`. A chamada seguinte da mesma ferramenta consome o ID atrasado da chamada anterior, deslocando os IDs e deixando a última chamada de cada ferramenta presa em `"Preparando..."`.
- No frontend, `buildTurnTimeline` (`turnTimelineFold.ts:307`) chama `computeSettledEventIds(turnEvents)` antes de calcular `isTurnCompleted` e sem repassar essa flag.

### 1.6. Medidor de contexto exibindo soma acumulada do turno (`2.699.518 / 1.000.000`) (`antigravity_cli/stream.py:166-200`, `native_turn.py:1340-1417`)
- `_compute_turn_usage` soma `input_tokens` e `cache_read_tokens` de todos os `step_update` do turno. Como cada passo reenvia a janela, 40–65 passos acumulam ~2,7M de tokens faturados.
- `_RunnerSink.on_assistant_usage` usa `usage.context_tokens` (essa soma acumulada) como `used_tokens` da janela de contexto atual no evento `context_updated`, estourando visualmente `1.000.000 (100%)` quando a ocupação real da janela era ~282k tokens.

---

## 2. Arquitetura das Correções Propostas

### Componente 1: Envelhecimento de histórico e orçamento de `USER_INPUT` no `NativeTurnRunner`
- **Arquivos:** `src/agentos/agentic/native_turn.py`
- **Mudanças:**
  1. No início de `NativeTurnRunner._run`, antes de `window = self.runtime._request_messages(self.messages)`, chamar `self.runtime._age_tool_results(self.messages, keep_recent=0)` para que todos os `tool_result` reidratados de turnos anteriores já concluídos sejam comprimidos a `AGED_TOOL_RESULT_CHARS` (`1.600` caracteres).
  2. Em `_build_initial_user_content`:
     - Em vez de descartar cegamente todo `m.get("role") == "system"`, descartar apenas as mensagens de preâmbulo que já são injetadas separadamente (`self.runtime.system_prompt`, `self.runtime.volatile_prompt` e `self.runtime.contract.render()`), preservando resumos de compactação (`compaction_message`) e marcadores de omissão (`[N mensagens anteriores foram omitidas...]`).
     - Limitar o tamanho total de `prior_messages` serializados a `MAX_NATIVE_PRIOR_HISTORY_CHARS = 120_000` caracteres: se os blocos de `prior_messages` excederem esse teto, remover unidades de `tool_use`/`tool_result` mais antigas (preservando mensagens `[User]`, `[Assistant]` e `[System]` de resumo) até caber com folga abaixo do limite de 190 KB do `agy`.
     - Quando houver `prior_messages` ou `volatile_parts` / `reminder_body`, prefixar `cur_text` com `[User]\n` para delimitar inequivocamente a mensagem atual do usuário após o `<system-reminder>`.

### Componente 2: Retomada condicional de `write_contract`
- **Arquivos:** `src/agentos/conversations/chat.py`, `src/agentos/agentic/session.py`
- **Mudanças:**
  - Em `ChatStore.latest_contract(conversation_id)`: fazer join/lookup com `conversation_turns` pelo `turn_id` do passo `write_contract` mais recente. Se o turno daquele contrato tiver `state == "completed"`, retornar `None` (não retomar automaticamente como contrato ativo de um turno já concluído). Retomar apenas quando o turno que escreveu o contrato terminou incompleto (`state != "completed"`, como `cancelled`, `failed`, `waiting_user`, etc.).

### Componente 3: Pedido do usuário no topo de `_skill_body` e preservação do fim da conversa em `_compact_source`
- **Arquivos:** `src/agentos/conversations/chat.py`, `src/agentos/agentic/runtime.py`
- **Mudanças:**
  1. Em `_skill_body(detail, arguments)` (`chat.py:156-168`): quando `arguments` não for vazio, formatar `Pedido da pessoa: {arguments}\n\n` **antes** do bloco `<agentos-skill-instructions authority="subordinate">` (e manter a referência ao final), garantindo que `runtime._first_request_text` (`[:2_000]`) capture o pedido real do usuário e que o modelo o leia antes das instruções da skill.
  2. Em `AgenticTurnRuntime._compact_source(messages, indices)` (`runtime.py:1768-1784`):
     - Ao montar o texto de cada mensagem para o summarizer, comprimir mensagens de `tool_result` com `self._compress` (`AGED_TOOL_RESULT_CHARS = 1600`).
     - Coletar de trás para frente (`reversed(sorted(indices))`) dentro do orçamento de `48_000` caracteres e depois inverter para a ordem cronológica, garantindo que as mensagens mais recentes do usuário e a última resposta do assistente nunca sejam cortadas.

### Componente 4: Fim do `"Preparando <tool>..."` infinito no Antigravity CLI e no Frontend
- **Arquivos:** `src/agentos/providers/antigravity_cli/stream.py`, `frontend/src/features/conversations/turnTimelineFold.ts`
- **Mudanças:**
  1. Em `AntigravityStreamTranslator.feed_line` (`stream.py:368-434`):
     - Quando `canonical_name is None` (ferramenta nativa não autorizada do `agy`), continuar retornando `CliEvent(kind=CliEventKind.TOOL_USE_START, ...)` imediatamente para que o `NativeTurnHandle` dispare `CliSecurityAbort`.
     - Quando `canonical_name is not None` (ferramenta legítima do servidor MCP `orin`), a execução real e seus eventos (`tool.started`, `tool.finished`) já ocorreram síncronamente via `RuntimeToolExecutor.call_tool`. Portanto, ignorar o `step_update` retroativo (`return []`) em vez de emitir `TOOL_USE_START`/`TOOL_USE_END` atrasados que criavam cards `"Preparando..."` fantasmas e poluíam `executor._pending_tool_uses`.
  2. Em `buildTurnTimeline` (`frontend/src/features/conversations/turnTimelineFold.ts:307-313`):
     - Mover o cálculo de `hasTerminalTurnEvent` e `isTurnCompleted` para **antes** de `computeSettledEventIds` e passar `computeSettledEventIds(turnEvents, isTurnCompleted)`.

### Componente 5: Medidor de Contexto preciso no Antigravity CLI
- **Arquivos:** `src/agentos/providers/claude_cli/stream.py`, `src/agentos/providers/antigravity_cli/stream.py`, `src/agentos/agentic/native_turn.py`
- **Mudanças:**
  1. Adicionar campo opcional `context_window_used_tokens: int | None = None` em `CliUsage` (`src/agentos/providers/claude_cli/stream.py`). Quando `None`, `CliUsage.context_tokens` mantém o comportamento atual (`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`).
  2. Em `AntigravityStreamTranslator._compute_turn_usage` (`src/agentos/providers/antigravity_cli/stream.py`):
     - Continuar calculando `raw_in`, `out_tok`, `cache_read`, `think_tok` somando `_turn_step_usages` para contabilização fiel do consumo total do turno (`note_usage` / métricas / custo).
     - Definir `context_window_used_tokens` no `CliUsage` retornado com o `input_tokens` bruto (`input_tokens` do último `step_idx` em `_turn_step_usages`, que no `agy` já representa o tamanho total do prompt daquele passo), e emitir também `CliEvent(kind=CliEventKind.USAGE, message_id=f"{conv_id}:step:{step_idx}", usage=step_usage)` a cada `step_update` de `agent_response` que trouxer `usage`!
  3. Em `_RunnerSink.on_assistant_usage` (`src/agentos/agentic/native_turn.py:1340-1417`):
     - Para `context_updated`, usar `ctx_used = usage.context_window_used_tokens if usage.context_window_used_tokens is not None else usage.context_tokens`, garantindo que o medidor da UI mostre a ocupação real da janela (ex.: ~282k / 1M) e não a soma acumulada de todas as rodadas do turno (2,69M / 1M).
     - Se `message_id` for o pacote `result` final (sem `message_id`) e os passos já tiverem sido contabilizados em `_seen_usage_message_ids`, apenas atualizar `context_updated` sem duplicar `self.total_tokens` / `counters.note_usage`.

---

## 3. Estratégia de Testes e Verificação

1. **Testes Unitários Backend (Pytest + TDD):**
   - `tests/unit/agentic/test_native_turn.py`:
     - Testar que `NativeTurnRunner` envelhece `tool_result` de turnos anteriores (`keep_recent=0`), preserva `compaction_message` (`role="system"`), rotula a mensagem atual com `[User]` após `<system-reminder>` e trunca `prior_messages` pela frente se passar de `120_000` chars mantendo a mensagem atual intacta no final.
     - Testar que `_RunnerSink.on_assistant_usage` usa `context_window_used_tokens` no evento `context_updated`.
   - `tests/unit/agentic/test_task_contract.py`:
     - Testar que `latest_contract` retorna `None` quando o turno que escreveu o contrato está `completed`, e retorna o contrato quando o turno anterior terminou `cancelled` / `failed` / `waiting_user`.
   - `tests/unit/agentic/test_structured_compaction.py` e `tests/unit/conversations/test_commands.py`:
     - Testar que `_skill_body` coloca `Pedido da pessoa: ...` no início e que `_compact_source` preserva as mensagens finais do usuário/assistente mesmo quando há `tool_result` gigantes no início do histórico.
   - `tests/unit/providers/test_antigravity_stream.py` (ou `test_cli_registry.py`):
     - Testar que `AntigravityStreamTranslator` não emite `TOOL_USE_START` / `TOOL_USE_END` atrasados para ferramentas `mcp_orin_*` já `DONE`, continua emitindo `TOOL_USE_START` para ferramentas nativas proibidas, e popula `context_window_used_tokens` com o último passo em vez da soma acumulada.
2. **Testes Unitários Frontend (Vitest + TDD):**
   - `frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.ts`:
     - Testar que `buildTurnTimeline` repassa `isTurnCompleted` para `computeSettledEventIds`, liquidando qualquer `tool.progressed` (`Preparando...`) pendente quando o turno termina.

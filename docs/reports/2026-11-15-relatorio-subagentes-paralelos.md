# Relatório Técnico: Diagnóstico Profundo e Arquitetura para Execução Paralela de Subagentes no Orin

**Data:** 15 de novembro de 2026  
**Escopo:** Runtime Agêntico (`native_turn.py`, `runtime.py`, `session.py`), Servidor MCP por Turno (`cli_tools_mcp/server.py`), Fila de Processos CLI (`cli_queue.py`, `native_loop.py`), Adaptadores CLI (`claude`, `codex`, `antigravity`), Prompts de Sistema e Frontend (`TurnTimeline.tsx`, `turnTimelineFold.ts`, `activitySummary.ts`)  
**Status:** Investigação Concluída (Somente Leitura / Sem Alteração de Código)

---

## 1. Resumo Executivo

Durante o uso do Orin Code com provedores baseados em CLI (Claude Code CLI `claude.exe` v2.1.325, OpenAI Codex CLI `codex.exe` v0.167.0 e Google Antigravity CLI `agy.exe` v1.5.9), observou-se que, mesmo quando o agente principal anuncia explicitamente que está disparando múltiplos subagentes em paralelo (como evidenciado na captura de tela do usuário, onde o líder declarou *"Disparando em paralelo: (1) geração das 3 locuções..., (2) construção dos scripts..., e (3) implementação do motor 3D..."*), a execução real ocorre de forma **100% serializada** (`Task ( antigravity-5 ) · 24 ações · Concluído` rodando sozinho do início ao fim antes de qualquer outro subagente iniciar).

A investigação no código-fonte do Orin, combinada com engenharia reversa dos binários locais dos três CLIs instalados na máquina Windows 10 e análise do código-fonte oficial do OpenAI Codex e da especificação MCP (`2025-03-26`), revelou que **o problema não tem uma única causa isolada, mas sim 5 barreiras cumulativas de serialização** distribuídas entre o protocolo MCP, os clientes CLI e o coletor de lotes do próprio Orin:

1. **Barreira de Metadados MCP (`read_only=False` $\rightarrow$ `annotations.readOnlyHint: false`)**:
   - Em `src/agentos/agentic/tools/agents/agent.py:210-259`, as ferramentas `run_subagent` (`Agent`), `ask_agent` e `ask_agents` são definidas com `policy_tags=("mutates",)` e `read_only=False`.
   - O servidor MCP de turno (`src/agentos/providers/cli_tools_mcp/server.py:270`) publica `"annotations": {"readOnlyHint": bool(spec.read_only)}`. Assim, `Agent` é anunciado aos CLIs com `"readOnlyHint": false`.
2. **Barreira de Despacho nos Clientes CLI (`claude`, `codex`, `agy`)**:
   - **Claude CLI (`claude.exe` v2.1.325)**: A inspeção binária confirmou que para ferramentas MCP o método `isConcurrencySafe()` é avaliado como `return Fe.mayOverlap || (ne.annotations?.readOnlyHint ?? false)`. Como `readOnlyHint` é `false`, o `claude.exe` trata `mcp__orin__Agent` como uma **barreira serial estrita** e aguarda o retorno HTTP do 1º subagente antes de enviar o `POST /mcp` do 2º subagente.
   - **OpenAI Codex CLI (`codex.exe` v0.167.0)**: O `ToolCallRuntime` (`codex-rs/core/src/tools/parallel.rs`) sincroniza chamadas usando um `tokio::sync::RwLock<()>`: chamadas com `supports_parallel_tool_calls() == true` adquirem `lock.read()` (paralelo), enquanto chamadas com `false` adquirem `lock.write()` (exclusivo). Em `codex-rs/core/src/tools/handlers/mcp.rs`, `McpHandler::supports_parallel_tool_calls()` retorna `self.definition.tool_info.supports_parallel_tool_calls || annotations.read_only_hint.unwrap_or(false)`. Como o Orin não passa `-c mcp_servers.orin.supports_parallel_tool_calls=true` (`src/agentos/providers/codex_cli/command.py:435-447`) **e** publica `readOnlyHint: false`, o Codex serializa todas as chamadas `Agent` via `lock.write()`.
   - **Google Antigravity CLI (`agy.exe` v1.5.9)**: A inspeção da tabela `.gopclntab` do binário Go revelou que a máquina de estados `oneharness/internal/core/core.(*Machine).stepToolDispatch` (`0x1a30a40`) executa `(*Machine).dispatchSingleToolStep` (`0x1a30cc0`) $\rightarrow$ `executeToolRequest` $\rightarrow$ `resolveNextToolStepIdx` (`0x1a32640`) **um passo por vez, de forma estritamente sequencial**, bloqueando na resposta HTTP de cada `tools/call` antes de despachar a próxima chamada de ferramenta do mesmo turno!
3. **Barreira Exclusiva no Servidor MCP do Orin (`ToolBatchCollector` em `src/agentos/agentic/native_turn.py:366-404, 864`)**:
   - Embora `AgenticTurnRuntime._run_toolset` (`src/agentos/agentic/runtime.py:2737, 2888-2898`) tenha sido atualizado para considerar `run_subagent`, `ask_agent` e `check_subagent` elegíveis para o `ThreadPoolExecutor(max_workers=MAX_PARALLEL_SUBAGENT_TOOLS)`, o adaptador MCP de turnos nativos (`RuntimeToolExecutor.call_tool` em `native_turn.py:864`) passa apenas `read_only = self._is_read_only(...)` (que é `False` para `run_subagent`) para `ToolBatchCollector.submit()`.
   - Em `ToolBatchCollector.submit()` (`native_turn.py:366-391`), qualquer chamada com `read_only=False` fecha o lote aberto e chama **`self._run_exclusive([entry], msg_key)` com uma lista de 1 único item**, segurando `self._exec_lock` (`threading.Lock()`) durante **toda a execução do subagente em foreground** (minutos inteiros!). Assim, o `ThreadPoolExecutor` de `runtime.py:2894` **jamais é alcançado com `len(run_indexes) > 1` em turnos CLI** (tornando-se *dead code* na prática).
4. **Barreira Temporal da Janela Fixa `BATCH_GATHER_SECONDS = 0.1` (`src/agentos/agentic/native_turn.py:78, 406-441`)**:
   - Mesmo que `run_subagent` passe a entrar em `_OpenReadBatch`, a janela de coleta de 100ms (`BATCH_GATHER_SECONDS = 0.1`) fecha assim que `100ms` decorrem desde a 1ª chamada, e o líder do lote adquire `self._exec_lock` por toda a duração dos subagentes. Se a 2ª ou 3ª requisição HTTP `POST /mcp` chegar com `>100ms` de diferença (comum em payloads grandes ou sob carga), ela cai em um 2º lote que fica **bloqueado no `self._exec_lock` atrás do 1º lote inteiro**.
5. **Lacuna de Prompt no Orin Code (`src/agentos/code_mode/system_prompt.py:105-106`) e Lacuna Visual Inicial no Frontend (`TurnTimeline.tsx:880-889`)**:
   - O prompt de sistema do Orin Code (`system_prompt.py:105-106`) não instrui o modelo a usar `run_in_background=True` + `CheckSubagent` (nem expõe uma ferramenta de lote `ask_agents` em `CODE_MODE_CORE_TOOLS`), que é a **única** forma de paralelizar subagentes quando o líder roda em um CLI de despacho sequencial como o `agy.exe`.
   - No frontend, `SubagentTaskContainer` só é instanciado quando surge um evento `kind === 'tool'` com `agentId === subagent_id`. Durante os primeiros 10–25 segundos de boot do processo CLI do subagente (entre `AGENT_MESSAGE_SENT` do pai e a 1ª tool call do filho), nenhum card `Task (...)` aparece para aquele subagente.

---

## 2. Diagnóstico Completo das Causas-Raiz no Orin

### 2.1. Reconstrução Exata do Fluxo da Evidência Visual (`image (4).png`)

Na captura de tela fornecida pelo usuário:
1. O agente líder (executando no Orin Code via CLI) planejou 3 frentes independentes e escreveu:
   > *"Disparando em paralelo: (1) geração das 3 locuções completas + masterização e mapa de timecodes reais da voz vencedora (`Puck`), (2) construção dos scripts reutilizáveis (`new-video`, `run-qc`) e template `002+`, e (3) implementação do motor 3D estilizado + renderização da Prova de Conceito 3D de 6 segundos (`poc-3d.mp4`)."*
2. O modelo emitiu chamadas para a ferramenta `Agent` (`run_subagent`, com `subagent_type="antigravity"`, `run_in_background=False` por padrão).
3. A 1ª chamada (`antigravity-5`) chegou ao `TurnToolServer` (`src/agentos/providers/cli_tools_mcp/server.py:285`) via `POST /mcp` (`tools/call`).
4. **Se o agente líder estava rodando no `agy.exe` ou `claude.exe` ou `codex.exe`**:
   - No cliente CLI, como `Agent` foi anunciado em `tools/list` com `"annotations": {"readOnlyHint": false}` (e no `agy.exe` o despachante `stepToolDispatch` é intrinsecamente sequencial por step), o próprio processo CLI **reteve** as chamadas (2) e (3) sem enviar os respectivos HTTP `POST /mcp`, aguardando o término da chamada HTTP (1).
   - Simultaneamente, no servidor Orin, `RuntimeToolExecutor.call_tool` (`src/agentos/agentic/native_turn.py:864`) avaliou `read_only = False` e enviou a chamada (1) para `ToolBatchCollector.submit(..., read_only=False)` (`native_turn.py:366`), que invocou `self._run_exclusive([entry], msg_key)` (`native_turn.py:391`), **adquirindo `self._exec_lock`**.
5. Assim, `antigravity-5` rodou **sozinho** em foreground por mais de 1 minuto (executando 24 ações: `Leu brief.md`, `Leu 2 arquivos`, `Criou generate-tts.mjs`, `Executou 5 comandos`, etc.) enquanto segurava tanto o cliente CLI quanto o `self._exec_lock` do `ToolBatchCollector`.
6. Na interface (`TurnTimeline.tsx`), como apenas `antigravity-5` estava em execução real no backend, `waveAgentCount` (`TurnTimeline.tsx:1285`) foi igual a `1` (e não `>= 2`), razão pela qual o container `<ParallelSubagentsGroup>` (*"3 subagentes em paralelo"*) jamais foi renderizado e apenas `Task ( antigravity-5 ) 24 ações` apareceu!

---

### 2.2. Gargalo 1: `read_only=False` e `policy_tags=("mutates",)` em `src/agentos/agentic/tools/agents/agent.py`

Em `src/agentos/agentic/tools/agents/agent.py:210-277`, as especificações das ferramentas de subagente estão declaradas como:

```python
# src/agentos/agentic/tools/agents/agent.py:210-259
    ToolSpec(
        key="ask_agent",
        ...
        handler="ask_agent",
        kind="agent",
        policy_tags=("mutates",),  # <-- read_only default é False
        available=lambda self: (self._delegate is not None),
        order=360,
    ),
    ToolSpec(
        key="ask_agents",
        ...
        handler="ask_agents",
        kind="agent",
        policy_tags=("mutates",),  # <-- read_only default é False
        available=lambda self: (self._delegate_batch is not None),
        order=370,
    ),
    ToolSpec(
        key="run_subagent",
        ...
        handler="run_subagent",
        kind="agent",
        input_model=AgentInput,
        public_description="Delegate a self-contained task to a subagent...",
        policy_tags=("mutates",),  # <-- read_only default é False
        available=lambda self: (self._run_subagent is not None or self._delegate is not None),
        order=380,
    ),
```

Efeitos em cascata dessa declaração:
1. `AgentToolset.is_read_only("run_subagent")` (`src/agentos/agentic/agent_tools.py:551-555`) retorna `False`.
2. `RuntimeToolExecutor._build_specs()` (`src/agentos/agentic/native_turn.py:633-640`) cria `McpToolSpec(name="Agent", ..., read_only=False)`.
3. `TurnToolServer._dispatch_single` (`src/agentos/providers/cli_tools_mcp/server.py:263-274`) responde ao `tools/list` com:
   ```python
   "annotations": {"readOnlyHint": bool(spec.read_only)}  # False para Agent!
   ```
4. Além disso, em `AgenticTurnRuntime._run_toolset` (`src/agentos/agentic/runtime.py:2678-2701`), qualquer chamada cujo `is_read_only(name)` seja `False` (incluindo `run_subagent`!) ativa a barreira de invalidação de cache de leitura (`write_seen = True` na linha `2683` e `first_seen.clear()` na linha `2700`), e em `invoke(index)` (`runtime.py:2785-2801`):
   ```python
   mutating = (
       bool(is_mutating(name))
       if callable(is_mutating)
       else (not read_only and name not in {"run_subagent", "ask_agent", "check_subagent"})
   )
   ```
   Como `self.toolset.is_mutating` **é** `callable` (`agent_tools.py:569`) e verifica `"mutates" in self.resolve(name).policy_tags`, `is_mutating("run_subagent")` retorna **`True`** (ignorando o fallback `name not in {"run_subagent", ...}` da linha `2788`!) e incrementa `self._writes += 1` (`runtime.py:2801`) mesmo para um subagente `explore` ou `plan` 100% somente-leitura!

---

### 2.3. Gargalo 2: Desconexão entre `ToolBatchCollector` (`native_turn.py`) e `_run_toolset` (`runtime.py`)

Existe uma assimetria arquitetural direta entre o loop clássico de API (`runtime.py`) e o loop nativo de CLI via MCP (`native_turn.py`):

- Em **`src/agentos/agentic/runtime.py:2731-2739`**, a elegibilidade para execução paralela no `ThreadPoolExecutor` foi definida como:
  ```python
  eligible = [
      error is None
      and not duplicate[i]
      and repeated[i] is None
      and first_index[i] is None
      and not same_batch_read[i]
      and ((callable(is_read_only) and is_read_only(name)) or name in {"run_subagent", "ask_agent", "check_subagent"})
      for i, (_, name, _, error) in enumerate(prepared)
  ]
  ```
- Entretanto, em **`src/agentos/agentic/native_turn.py:864-876`**, quando as ferramentas vêm do servidor MCP (`TurnToolServer` $\rightarrow$ `RuntimeToolExecutor.call_tool`), o coletor **não** usa a mesma regra de `eligible`:
  ```python
  # src/agentos/agentic/native_turn.py:864-876
  read_only = self._is_read_only(raw_name, internal_name)
  ...
  return self._call_collector(
      call_id,
      internal_name,
      request.arguments,
      message_id=message_id,
      read_only=read_only,
      **extra,
  )
  ```
- E em **`ToolBatchCollector.submit` (`src/agentos/agentic/native_turn.py:366-404`)**:
  ```python
  if not entry.read_only:
      with self._state_lock:
          open_batch = self._open_batches.pop(msg_key, None)
          if open_batch is not None and not open_batch.closed:
              open_batch.closed = True
              with open_batch.cond:
                  open_batch.cond.notify_all()
          ...
      for prior in prior_entries:
          while not prior.done.wait(EXEC_WAIT_POLL_SECONDS):
              if entry.abandoned():
                  break
      ...
      try:
          results = self._run_exclusive([entry], msg_key)
  ```
- Como `read_only` é `False` para `run_subagent`, `ask_agent` e `ask_agents`, cada invocação MCP entra em `if not entry.read_only:` e chama `self._run_exclusive([entry], msg_key)` (`native_turn.py:318-335`), que adquire `self._exec_lock` e invoca `self._execute_batch([entry], msg_key)` $\rightarrow$ `self.runtime._run_toolset(self.turn, raw_calls)` com `len(raw_calls) == 1`!
- Consequentemente, em `runtime.py:2888`, `if len(run_indexes) > 1:` é **sempre `False`** para subagentes invocados via MCP por qualquer CLI!

---

### 2.4. Gargalo 3: Análise Matemática e Temporal do `ToolBatchCollector` (`BATCH_GATHER_SECONDS` e `_exec_lock`)

Suponha que apenas marquemos `run_subagent` como `read_only=True` (ou `parallel_safe=True` no `ToolBatchCollector`). Ainda assim, a arquitetura atual de `ToolBatchCollector` (`src/agentos/agentic/native_turn.py:287-469`) possui duas falhas graves para ferramentas de longa duração como subagentes:

#### Falha 3A: Janela Estática Não-Deslizante de 100ms (`BATCH_GATHER_SECONDS = 0.1`)
Em `ToolBatchCollector.submit` (`native_turn.py:406-439`):
1. A chamada $C_1$ chega no instante $t_1 = 0\text{ ms}$ e abre `_OpenReadBatch(msg_key, deadline = t_1 + 100\text{ ms})`.
2. A thread de $C_1$ torna-se `is_leader = True` e aguarda `batch.cond.wait(timeout=rem)` até $t = 100\text{ ms}$.
3. No instante $t = 100\text{ ms}$, o líder fecha o lote (`batch.closed = True`), remove-o de `self._open_batches` e entra em `self._run_exclusive(entries, msg_key)`, adquirindo `self._exec_lock`.
4. Se o modelo gerou 3 chamadas `Agent` com prompts extensos ($C_1, C_2, C_3$) e, devido ao tempo de parsing/validação/despacho HTTP do cliente ou hooks `PreToolUse`, $C_2$ chega em $t_2 = 60\text{ ms}$ e $C_3$ chega em $t_3 = 115\text{ ms}$:
   - $C_1$ e $C_2$ entram no Lote 1, que fecha em $t = 100\text{ ms}$ e adquire `self._exec_lock`.
   - $C_3$ chega em $t_3 = 115\text{ ms}$, encontra `self._open_batches[msg_key]` vazio, abre o Lote 2, aguarda até $t = 215\text{ ms}$ e chama `self._run_exclusive([C_3], msg_key)`.
   - Como o Lote 1 ($C_1, C_2$) está executando subagentes que levam $T_{\text{sub}} = 120\text{ s}$, **$C_3$ fica bloqueado esperando `self._exec_lock` por 120 segundos inteiros** antes de sequer iniciar!

#### Falha 3B: `self._exec_lock` Global Serializa Lotes Concorrentes e Mutação de `self.messages` / `self.tool_rounds`
Por que `ToolBatchCollector` usa `self._exec_lock` hoje?
Porque `RuntimeToolExecutor._execute_batch` (`native_turn.py:1059-1315`) muta estados de rodada do `RuntimeToolExecutor` e do `AgenticTurnRuntime`:
- `self.action_count += len(results)` (`linha 1134`)
- `self.tool_rounds += 1` (`linhas 1138, 1142`)
- `self.consume_round_buffers()` (`linha 1200`)
- `self.messages.append(self.runtime._assistant_tool_message(...))` (`linha 1204`)
- `self.messages.extend(self.runtime._tool_result_messages(self.turn, res))` (`linha 1233`)
- `self.runtime._native_message_first_read_keys = msg_first_reads` (`linha 1116`)

Se `self._exec_lock` envolve a chamada inteira `self.runtime._run_toolset(self.turn, raw_calls)` (`linha 1118`), **nenhuma chamada que chegue após os 100ms iniciais pode executar em paralelo com subagentes já em andamento**, porque o lock é mantido durante a execução das ferramentas, e não apenas durante a preparação/consolidação de estado (`checkpoint_lock`)!

---

### 2.5. Gargalo 4: Timeout de Chamadas MCP nos CLIs e `ABANDONED_BEFORE_START`

Quando subagentes demoram vários minutos ou ficam enfileirados em `_run_exclusive`:
1. **`TurnToolServer` e `notifications/cancelled` (`src/agentos/providers/cli_tools_mcp/server.py:226-233, 347-359`)**:
   - Cada requisição `tools/call` registra um `cancel_event = threading.Event()` em `in_flight_calls[call_key]`.
   - Se o cliente CLI atingir seu timeout de ferramenta MCP, ele envia uma notificação JSON-RPC `notifications/cancelled` com o `requestId`, que aciona `cancel_event.set()`.
2. **Descarte por `ABANDONED_BEFORE_START` (`src/agentos/agentic/native_turn.py:272-275, 318-326`)**:
   - Enquanto o 2º e 3º subagentes aguardam `self._exec_lock.acquire(timeout=0.2)` atrás do 1º subagente, se o timeout do cliente expirar, `entry.abandoned()` torna-se `True` e eles retornam imediatamente `ABANDONED_BEFORE_START` sem jamais executar.
3. **Configuração de Timeout por Adaptador CLI**:
   - Em `start_native_turn_for_provider` (`src/agentos/providers/claude_cli/native_loop.py:1447-1462`), o Orin calcula `mcp_tool_timeout_ms = max(1_000, int((request.deadline_monotonic - now) * 1000))` (o tempo restante do turno inteiro):
     - **Claude CLI (`claude_cli/command.py:113-122`)**: recebe `MCP_TOOL_TIMEOUT = str(mcp_tool_timeout_ms)` no ambiente.
     - **Codex CLI (`codex_cli/command.py:574-583`)**: recebe `-c mcp_servers.orin.tool_timeout_sec=<segundos restantes>`.
     - **Antigravity CLI (`antigravity_cli/command.py:663, 817, 1305-1309`)**: recebe `"timeoutSeconds": <segundos restantes>` tanto no `.agents/mcp_config.json` quanto no YAML frontmatter do `.agents/agents/orin.md`.
   - **Atenção em `native_loop.py:1280-1292` vs `1450-1455`**:
     - `AntigravityCliAdapter.capabilities.max_tool_call_seconds` é `180` (`src/agentos/providers/antigravity_cli/adapter.py:76`). Na linha `1292` de `native_loop.py`, `bind_cap(180)` é chamado inicialmente, e depois na linha `1455` é sobrescrito por `bind_cap(mcp_tool_timeout_ms / 1000)` para o toolset do turno. Porém, para comandos Bash (`run_command`), `max_tool_call_seconds` move comandos longos para background; já para `run_subagent` em foreground, se o subagente exceder o deadline ou se o cliente `agy` aplicar um limite interno de stream/step, a conexão MCP cai.

---

## 3. Descobertas na Web e nos Clientes CLI (Claude, Codex, Antigravity, MCP Spec)

### 3.1. Especificação MCP (`2025-03-26`) — `ToolAnnotations` e `readOnlyHint`
- **Fontes consultadas**:
  - `https://modelcontextprotocol.io/specification/2025-03-26/server/tools`
  - `https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-03-26/schema.ts`
- Na especificação oficial MCP `2025-03-26`, `ToolAnnotations` define 4 dicas booleanas (`hints`) opcionais em cada `Tool` retornado por `tools/list`:
  - `readOnlyHint?: boolean` (default: `false`): *"If true, the tool does not modify its environment."*
  - `destructiveHint?: boolean` (default: `true`, significativo apenas quando `readOnlyHint == false`): *"If true, the tool may perform destructive updates to its environment. If false, the tool performs only additive updates."*
  - `idempotentHint?: boolean` (default: `false`, significativo apenas quando `readOnlyHint == false`).
  - `openWorldHint?: boolean` (default: `true`).
- Como o protocolo MCP **não possui** um campo separado `concurrencySafeHint` ou `parallelizableHint` em `ToolAnnotations`, os clientes MCP do mercado (Claude Code e OpenAI Codex) utilizam `annotations.readOnlyHint === true` (ou flags de configuração por servidor) como o sinal primário para decidir se múltiplas chamadas `tools/call` de uma mesma resposta do assistente podem ser despachadas em paralelo!

---

### 3.2. Claude Code CLI (`claude.exe` v2.1.325)
- **Evidência binária local (`C:\Users\carlos\.local\bin\claude.exe`, offsets `313903571`, `344376312`, `358800131`, `358920928`)**:
  - Ao registrar cada ferramenta vinda de um servidor MCP em `tools/list`, o `claude.exe` constrói o objeto de ferramenta com:
    ```javascript
    isConcurrencySafe() {
      return Fe.mayOverlap || (ne.annotations?.readOnlyHint ?? !1);
    },
    isReadOnly() {
      return ne.annotations?.readOnlyHint ?? !1;
    },
    isDestructive() {
      return ne.annotations?.destructiveHint ?? !1;
    },
    isOpenWorld() {
      return ne.annotations?.openWorldHint ?? !1;
    }
    ```
  - Onde `Fe.mayOverlap` (offset `358800131`) só é `true` para containers remotos internos da Anthropic (`x-ccr-tool-container-call-owed`). Para todos os servidores MCP HTTP/stdio normais (como o servidor `orin`), `Fe.mayOverlap` é `false`.
  - Portanto, **no Claude Code CLI, uma ferramenta MCP só é executada em paralelo (`isConcurrencySafe() === true`) se, e somente se, `annotations.readOnlyHint === true`!**
  - Quando `annotations.readOnlyHint` é `false`, o executor de ferramentas do Claude Code (`StreamingToolExecutor`, limitado por `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY = 10`) trata a ferramenta como não-segura para concorrência e executa cada invocação como uma barreira serial isolada.

---

### 3.3. OpenAI Codex CLI (`codex.exe` v0.167.0)
- **Fontes oficiais e binário local (`C:\Users\carlos\AppData\Roaming\npm\node_modules\@openai\codex\...\codex.exe`)**:
  - Documentação oficial: `https://developers.openai.com/codex/config-reference` (`https://learn.chatgpt.com/docs/config-file/config-reference`)
  - Código-fonte oficial analisado:
    - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/parallel.rs`
    - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/router.rs`
    - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/handlers/mcp.rs`
- **Mecanismo exato no Rust (`codex-rs`)**:
  1. Em `codex-rs/core/src/tools/parallel.rs`, `ToolCallRuntime` possui `parallel_execution: Arc<tokio::sync::RwLock<()>>`.
  2. Para cada `ToolCall` emitida pelo modelo no turno, `handle_tool_call_with_source` verifica `let supports_parallel = router.tool_supports_parallel(&call);`:
     - Se `supports_parallel == true`: a task adquire `lock.read().await` (`ToolAdmission::Parallel`), permitindo que todas as chamadas paralelas disparem seus HTTP POSTs `tools/call` simultaneamente!
     - Se `supports_parallel == false`: a task adquire `lock.write().await` (`ToolAdmission::Exclusive`), bloqueando todas as chamadas anteriores e posteriores!
  3. Em `codex-rs/core/src/tools/handlers/mcp.rs`, `McpHandler` implementa `supports_parallel_tool_calls` assim:
     ```rust
     fn supports_parallel_tool_calls(&self) -> bool {
         // Correctly implemented MCP servers should tolerate parallel calls to
         // tools that advertise themselves as read-only.
         self.definition.tool_info.supports_parallel_tool_calls
             || self
                 .definition
                 .tool_info
                 .tool
                 .annotations
                 .as_ref()
                 .and_then(|annotations| annotations.read_only_hint)
                 .unwrap_or(false)
     }
     ```
  4. Na configuração `config.toml` / flag `-c`, o Codex oferece:
     - `mcp_servers.<id>.supports_parallel_tool_calls = true | false` (default: `false`).
     - Quando `supports_parallel_tool_calls = false` (o default atual em `wire_codex_tool_server`, `src/agentos/providers/codex_cli/command.py:435-447`), o Codex **exige** `annotations.readOnlyHint == true` na ferramenta MCP para permitir `lock.read()`.
     - **Atenção**: Ativar `mcp_servers.orin.supports_parallel_tool_calls = true` para o servidor inteiro faria o Codex disparar `write_file` e `edit_file` sob `lock.read()`, mas se `readOnlyHint: true` for exposto nas ferramentas paralelamente seguras (incluindo `Agent` / `run_subagent`, `ask_agent`, `ask_agents`, `check_subagent`), o Codex adquire `lock.read()` especificamente para elas e mantém `lock.write()` para `write_file`, `edit_file` e `run_command`!

---

### 3.4. Google Antigravity CLI (`agy.exe` v1.5.9)
- **Evidência binária local (`C:\Users\carlos\AppData\Local\agy\bin\agy.EXE`, `.gopclntab` em `0x53c3d80`)**:
  - A inspeção da máquina de estados interna em Go (`google3/third_party/oneharness/internal/core/machine.go`, `run_tool.go`, `scheduler.go`) mostrou os seguintes métodos e grafo de chamadas:
    - `(*Machine).Step` (`0x1a2fdc0`) $\rightarrow$ `(*Machine).stepToolDispatch` (`0x1a30a40`)
    - Dentro de `(*Machine).stepToolDispatch`:
      1. `(*Scheduler).Advance` (`+0x4a`)
      2. `validatePendingToolStep` (`+0xa7`)
      3. `(*Machine).dispatchSingleToolStep` (`+0xc5` $\rightarrow$ `0x1a30cc0`)
      4. `resolveNextToolStepIdx` (`+0x140` $\rightarrow$ `0x1a32640`)
      5. `(*Machine).finishModelOutput` (`+0x1a0` $\rightarrow$ `0x1a312e0`)
    - Dentro de `(*Machine).dispatchSingleToolStep` (`0x1a30cc0`):
      - Invoca `(*Toolbox).ValidateToolCall` (`+0x80`) e `executeToolRequest` (`+0x1a3` $\rightarrow$ `0x1a32c40`).
      - `executeToolRequest` executa hooks `runPreToolHooks` (`+0x9b2`), `runPermissionCheck` (`+0xb79`), dispara a chamada da ferramenta via `(*Scheduler).startAwait` (`+0x1367`) e aguarda no `runtime.selectgo` (`+0x15c2`) até aquela única chamada terminar!
  - **Descoberta Crucial sobre o `agy.exe`**:
    - Diferentemente do `claude.exe` e do `codex.exe`, **o `oneharness` do `agy.exe` v1.5.9 despacha cada `tool_step` de uma resposta do modelo sequencialmente (`dispatchSingleToolStep` $\rightarrow$ aguarda retorno HTTP $\rightarrow$ `resolveNextToolStepIdx`)**, independentemente de `readOnlyHint`!
    - Isso significa que se o agente principal estiver rodando sobre o provedor `google-antigravity` (`agy.exe`) e chamar `Agent` 3 vezes com `run_in_background=False` (bloqueante), **o próprio `agy.exe` jamais enviará o 2º `POST /mcp` enquanto o 1º `POST /mcp` não retornar HTTP 200**!
    - Para que um agente principal rodando no `agy.exe` execute 3 subagentes simultaneamente, **existem apenas duas possibilidades físicas**:
      1. Cada chamada `Agent` (`run_subagent`) retornar quase instantaneamente ao `agy.exe` (via `run_in_background=True` ou auto-backgrounding no servidor MCP) e os resultados serem recolhidos em lote (`CheckSubagent` ou barreira de espera automática); **ou**
      2. O modelo invocar uma única ferramenta de despacho em lote (`ask_agents` / parâmetro `tasks` em `Agent`) que recebe as $N$ tarefas em uma única requisição `tools/call` e as executa em paralelo no `ThreadPoolExecutor` do Orin!

---

## 4. Análise de Riscos de Thread-Safety e Deadlocks

Ao habilitar a execução real de múltiplos subagentes em paralelo dentro da mesma `TurnSession`, inspecionamos todas as camadas compartilhadas do Orin e identificamos **7 riscos concretos de concorrência e deadlock**:

### 4.1. Risco de Deadlock em `CliSlotQueue` e `NativeTurnHandle._after_tool_call` (`cli_queue.py` + `native_loop.py`)
- **Arquivos**: `src/agentos/providers/cli_queue.py:194-239` e `src/agentos/providers/claude_cli/native_loop.py:511-577`.
- **Como funciona hoje**:
  - Na máquina atual (Windows 10), `free_memory_mb()` usa `ctypes.windll.kernel32.GlobalMemoryStatusEx` (`ullAvailPhys // (1024 * 1024)`), reportando `~13.277 MB` livres, com `DEFAULT_MIN_FREE_MEMORY_MB = 1024`.
  - Quando o processo CLI pai recebe uma chamada `tools/call`, `_SlotReleasingExecutor.call_tool` chama `handle._before_tool_call()` (`native_loop.py:511`), que libera o `CliSlotTicket` do pai enquanto `_active_tool_calls > 0`.
  - Quando `_active_tool_calls` volta a `0`, `handle._after_tool_call()` (`native_loop.py:523-577`) **bloqueia a thread do servidor MCP** tentando readquirir um `CliSlotTicket` via `acquire_cli_slot()` antes de devolver o resultado HTTP ao processo CLI pai!
- **Cenário de Deadlock Pai $\leftrightarrow$ Subagente em Background / Teammate**:
  1. Suponha que `ORIN_CLI_MAX_PROCESSES` esteja definido (ex.: `limit = 2`) ou que a memória livre caia abaixo de `1024 MB` após iniciar subagentes.
  2. O agente pai dispara 2 subagentes com `run_in_background=True` (ou 2 `teammates` de Agent Teams).
  3. Os 2 subagentes em background iniciam seus processos CLI e adquirem os 2 slots disponíveis na `CliSlotQueue` (`_active_total = 2`).
  4. A ferramenta `run_subagent(run_in_background=True)` termina no Orin e entra no `finally: handle._after_tool_call()` (`native_loop.py:410`).
  5. `_after_tool_call()` chama `acquire_cli_slot()` para o pai, mas `_active_total == 2 == limit`!
  6. O pai fica bloqueado indefinidamente dentro de `_after_tool_call()` (sem receber a resposta da ferramenta `run_subagent`!) esperando os subagentes em background terminarem — **anulando completamente o propósito de `run_in_background=True`**! Pior: se os subagentes em background forem `teammates` aguardando coordenação do líder, ou se um subagente em foreground tentar adquirir slot enquanto subagentes em background ocupam todos os slots, ocorre contenção ou deadlock!
- **Cenário de Contenção de `_after_tool_call` entre Chamadas Concorrentes**:
  - Se a chamada 1 terminar no instante $t_1$ (`_active_tool_calls` cai de 1 para 0) e iniciar `_reacquiring_slot = True` (`native_loop.py:534`), e no instante $t_1 + 5\text{ ms}$ uma 2ª chamada `tools/call` chegar em `_before_tool_call()` (`_active_tool_calls` sobe para 1), a `cancel_check` da linha `542` (`self._active_tool_calls > 0`) aborta a reaquisição corretamente, mas gera churn desnecessário na `CliSlotQueue`.

### 4.2. Bug de Promoção Parcial em `_run_foreground_subagent_watching_queue` (`src/agentos/agentic/session.py:3132-3303`)
- Quando $N$ subagentes rodam em paralelo em foreground a partir do agente principal (`caller.is_main`), **cada uma das $N$ threads** entra em `_run_foreground_subagent_watching_queue` (`session.py:3028`) e faz polling a cada `50ms` em `self._has_queued_user_input()` (`session.py:3254`).
- Se o usuário enviar uma nova mensagem no chat enquanto os $N$ subagentes estão rodando:
  - A primeira thread ($T_1$) que executar `texts = self._claim_queued_user_inputs()` (`linha 3257`) **drena a fila do banco de dados**, promove **apenas o subagente 1** para background (`promoted.set()`) e retorna `ToolOutcome("succeeded", "subagent-1 continua em segundo plano", "... The user wrote: ...")`.
  - As outras $N-1$ threads ($T_2, \dots, T_N$) chamam `self._has_queued_user_input()` 1ms depois, veem a fila vazia (porque $T_1$ já consumiu a mensagem!) e **continuam bloqueadas em foreground** até terminarem suas tarefas inteiras!
  - Como `ThreadPoolExecutor` em `_run_toolset` (`runtime.py:2899-2901`) aguarda `future.result()` de **todas** as $N$ threads do lote, o agente principal **continua travado** esperando $T_2, \dots, T_N$ terminarem, apesar de $T_1$ ter sido promovido para background para entregar a mensagem do usuário!

### 4.3. Sobrescrita de `self.toolset._active_invocation_id` entre Threads (`runtime.py:2804` e `agent_tools.py:155, 690`)
- Em `src/agentos/agentic/runtime.py:2804`:
  ```python
  setattr(self.toolset, "_active_invocation_id", call_id)
  ```
- Em `src/agentos/agentic/agent_tools.py:155`, `self._active_invocation_id: str | None = None` é um atributo simples da instância `AgentToolset` (e **não** um `threading.local()`).
- Quando múltiplas ferramentas rodam concorrentemente no `ThreadPoolExecutor` de `_run_toolset` (`runtime.py:2894`), a thread B sobrescreve `self.toolset._active_invocation_id` enquanto a thread A está em execução. Qualquer código interno do `AgentToolset` que leia `self._active_invocation_id` sem receber `call_id` explícito (ex.: `_request_tool_permission` em `agent_tools.py` ou callbacks de progresso) associará o evento ao `invocation_id` errado.

### 4.4. Mutação Concorrente de `self._read_file_state` sem Lock (`session.py:1539, 5014-5018` e `read_state.py:41-46`)
- `self._read_file_state` é um `dict` padrão compartilhado com o `main_toolset`.
- Quando um subagente `fork` é iniciado (`inherit_context=True`), `TurnSession._toolset` (`session.py:5017`) chama `copy_read_state(self._read_file_state)`, que itera sobre `state.items()` (`src/agentos/agentic/read_state.py:45`) sem nenhum lock.
- Se outra thread estiver simultaneamente lendo um arquivo (`read_file` no mesmo lote paralelo) e inserindo/atualizando chaves em `self._read_file_state`, o Python pode levantar `RuntimeError: dictionary changed size during iteration`.

### 4.5. Dicionários e Estados de Sessão Mutados sem Lock em `session.py` e `agent.py`
- **`_SUBAGENT_CHARACTER_NAMES` (`src/agentos/agentic/tools/agents/agent.py:14, 29-73`)**: dicionário global de módulo `dict[tuple[str, str], str]` lido e escrito concorrentemente por múltiplas threads sem lock.
- **`self._agent_parents` (`src/agentos/agentic/session.py:1526, 2145-2188`)**: mutado em `_adopt_subagent` (`self._agent_tree()[agent_id] = parent_agent_id`) e lido em `_parent_of` / `_is_descendant` durante emissões de eventos `emit_lifecycle` concorrentes de múltiplos subagentes.
- **`self._code_mode_stage` (`src/agentos/agentic/session.py:1777, 1885, 1894, 1904`)**: lido e atribuído sem lock dentro de `emit_lifecycle` quando múltiplos subagentes emitem `tool_started` / `tool_finished` simultaneamente.
- **Consulta `existing_count` fora de `self._subagent_lock` (`src/agentos/agentic/session.py:2832-2864`)**: em `_ask_agent`, a varredura de `self.agents_store.list()` para calcular `existing_count` ocorre antes do `with self._subagent_lock:`. Embora `self._builtin_type_counts[builtin_key]` seja incrementado dentro do lock (`linha 2861`), mover a resolução completa de `instance_name` e `_ensure_builtin_subagent_record` para dentro de `self._subagent_lock` evita qualquer condição de corrida TOCTOU na criação de registros no `ConversationAgentStore`.

### 4.6. Colisão de Atribuição de Artefatos em `write_claims.py` entre Subagentes da Mesma Conversa
- Em `src/agentos/agentic/write_claims.py:42-65`, `claim(root, paths, owner)` e `foreign(root, paths, owner, since=...)` usam `owner = conversation_id`.
- Quando dois subagentes paralelos ($S_1$ e $S_2$) rodam na **mesma conversa** (`conversation_id` idêntico) e $S_1$ executa um comando demorado (`run_command`, ex.: `ffmpeg` ou `npm test`) enquanto $S_2$ cria arquivos com `write_file`/`edit_file`, o diff pós-comando de $S_1$ chama `write_claims.foreign(..., owner=conversation_id)`. Como os arquivos de $S_2$ foram registrados sob o **mesmo** `owner` (`conversation_id`), `foreign()` retorna vazio e o comando de $S_1$ reivindica indevidamente todos os arquivos criados por $S_2$ como artefatos próprios (`ARTIFACT_CREATED` duplicado no card de $S_1$)!

### 4.7. Frontend: Latência de Aparição do Card e Quebra de Onda (`turnTimelineFold.ts`, `activitySummary.ts`, `TurnTimeline.tsx`)
1. **Invisibilidade durante o boot do subagente (`TurnTimeline.tsx:880-889`)**:
   - `isSubagentActivityGroup(group)` exige `group.kind === 'tool'`.
   - Quando o agente pai dispara `run_subagent`, o backend emite `AGENT_MESSAGE_SENT` (`kind === 'agent'`, `agentId = main_agent_id`, `to_agent_id = subagent_id` em `session.py:3586`) e `TURN_STARTED` (`kind === 'lifecycle'`, `agentId = subagent_id` em `session.py:2122`), mas **nenhum evento `kind === 'tool'`** com `agentId = subagent_id` até que o subagente conclua seu boot de processo CLI e invoque sua 1ª ferramenta (ou atinja 20s de `waiting_model`).
   - Resultado: durante os primeiros 10–20 segundos, o card `Task ( subagent )` não aparece como `Em execução`!
2. **Limpeza prematura de `subagentWaveItems` / `subagentWaveGroups` (`turnTimelineFold.ts:404-406, 475-477` e `activitySummary.ts:238-240`)**:
   - Em `turnTimelineFold.ts:475`:
     ```ts
     const subagentEvent = isSubagentEvent(event)
     if (!subagentEvent && event.kind !== 'agent') {
       subagentWaveItems.clear()
     }
     ```
   - Se, enquanto 3 subagentes estão rodando em paralelo (ou em background), o agente principal emitir **qualquer** evento que não seja `kind === 'agent'` (por exemplo, um `thinking_delta` na linha `404`, um `waiting_model` do pai, um `context.updated`, ou uma chamada `check_subagent` que tem `agentId = primaryAgentId` e `kind = 'tool'`), `subagentWaveItems.clear()` é executado!
   - Isso **quebra** os containers dos subagentes em múltiplos blocos fragmentados antes e depois do evento do agente principal e impede que `<ParallelSubagentsGroup>` (`TurnTimeline.tsx:1288`) agrupe a onda inteira sob *"3 subagentes em paralelo"*!

---

## 5. Formas de Resolver (Opções Arquiteturais)

Abaixo comparamos 4 abordagens arquiteturais para resolver o problema de ponta a ponta:

| Critério | Opção A: `readOnlyHint: true` em `Agent` + `parallel_safe` no `ToolBatchCollector` | Opção B: Auto-Backgrounding / Async Launch (`run_in_background` automático) | Opção C: Ferramenta de Lote Explícita (`ask_agents` / `AgentBatch` em uma única tool call) | **Opção D (Híbrida Recomendada): Paralelismo Nativo MCP (A) + Janela Adaptativa Sem Lock Global + Suporte Universal a Background/Batch (B+C)** |
| :--- | :--- | :--- | :--- | :--- |
| **Funciona no Claude CLI (`claude`)?** | Sim (via `readOnlyHint: true` $\rightarrow$ `isConcurrencySafe = true`) | Sim | Sim | **Sim (Foreground paralelo + Background)** |
| **Funciona no Codex CLI (`codex`)?** | Sim (via `readOnlyHint: true` $\rightarrow$ `lock.read()`) | Sim | Sim | **Sim (Foreground paralelo + Background)** |
| **Funciona no Antigravity CLI (`agy`)?** | **Não para foreground** (`agy` despacha 1 tool step por vez em `dispatchSingleToolStep`) | **Sim** (cada `Agent` retorna o handle imediatamente e roda em paralelo) | **Sim** (1 único tool step dispara todos no backend) | **Sim (em todos os modos!)** |
| **Experiência do Modelo (Ergonomia)** | Transparente (o modelo já emite múltiplas calls `Agent` na mesma resposta) | Requer `CheckSubagent` ou barreira de espera automática no fim do turno | Exige que o modelo use schema de array `tasks` | **100% Transparente em todos os CLIs** |
| **Risco de Timeout MCP (`notifications/cancelled`)** | Médio em subagentes muito longos (>15 min) | Zero (chamada MCP retorna em `<50ms`) | Médio em lotes muito longos | **Baixo/Zero** |
| **Complexidade de Implementação** | Baixa | Média | Baixa | **Média (cirúrgica e modular)** |

### Análise Detalhada de Cada Opção

#### Opção A: Habilitar Concorrência MCP (`readOnlyHint: true` para `Agent`) + Corrigir `ToolBatchCollector`
- **Como funciona**:
  - Separar a semântica de `read_only` (cacheabilidade de leituras e permissão em `plan_mode`) da semântica de `parallel_safe` / `mcp_read_only_hint` (segurança de despacho paralelo MCP).
  - Expor `"annotations": {"readOnlyHint": true}` em `tools/list` para `Agent` (`run_subagent`), `ask_agent`, `ask_agents` e `check_subagent`.
  - No `ToolBatchCollector` (`native_turn.py`), permitir que `run_subagent`, `ask_agent`, `ask_agents` e `check_subagent` entrem no lote concorrente em vez de `_run_exclusive([entry])`.
- **Prós**: Faz com que `claude.exe` e `codex.exe` despachem imediatamente múltiplas chamadas `Agent` em paralelo na mesma rodada, sem exigir `CheckSubagent`.
- **Contras**: Sozinha, **não resolve o `agy.exe` em foreground**, pois o binário do `agy.exe` (`oneharness/internal/core/machine.go`) executa `dispatchSingleToolStep` sequencialmente esperando o retorno HTTP de cada `tools/call`. Além disso, se depender apenas de `BATCH_GATHER_SECONDS = 0.1` com `self._exec_lock` bloqueante, chamadas que chegarem com `>100ms` de atraso serão serializadas.

#### Opção B: Execução Assíncrona (`run_in_background=True` por padrão no `agy` ou instruído no prompt + `background_wait.py`)
- **Como funciona**:
  - Quando o modelo dispara 3 chamadas `Agent` com `run_in_background=True`, cada chamada registra o subagente em `BACKGROUND_AGENTS`, submete ao `self._bg_subagent_executor` e retorna o `ToolOutcome` em `<10ms`.
  - Mesmo no `agy.exe` (que despacha 1 tool step por vez), os 3 steps completam em `~30ms` totais e os **3 subagentes passam a rodar 100% em paralelo em background**!
  - Se o modelo quiser esperar os resultados no mesmo turno, ele chama `CheckSubagent(agent_id=..., wait_seconds=60)` ou simplesmente encerra sua fala parcial: o mecanismo já existente em `native_turn.py:2645-2722` (`has_pending_background_work` / `wait_for_background_work` em `background_wait.py`) **automaticamente aguarda os subagentes em background terminarem e reinjeta os relatórios `<subagent-finished>` no próprio turno ativo**!
- **Prós**: Funciona em **100% dos CLIs** (inclusive `agy.exe`), elimina timeouts de chamada MCP longa e permite que o usuário envie mensagens no meio sem perder o trabalho dos outros subagentes.
- **Contras**: Exige que o prompt de sistema do Orin Code (`system_prompt.py`) ensine claramente esse fluxo (hoje omitido!) e exige corrigir o bug de `_after_tool_call` quando há limite de slots na `CliSlotQueue`.

#### Opção C: Suporte a Lote em Chamada Única (`tasks` em `Agent` ou publicação de `ask_agents` no Code Mode)
- **Como funciona**:
  - Permitir que a ferramenta `Agent` (`run_subagent`) aceite opcionalmente uma lista `tasks: list[AgentTaskItem]` (ou publicar `ask_agents` no Code Mode), executando todas as tarefas da lista em paralelo dentro de `TurnSession` via `ThreadPoolExecutor`.
- **Prós**: Mesmo em um cliente estritamente serial como `agy.exe`, 1 única chamada HTTP `tools/call` dispara $N$ subagentes em paralelo em foreground e retorna todos os relatórios juntos.
- **Contras**: Modelos treinados no hábito do Claude Code (`Agent` chamado $N$ vezes em blocos `tool_use` separados) continuarão emitindo $N$ tool calls separadas a menos que o coletor ou o modo background também os trate.

#### Opção D (Arquitetura Recomendada): Combinação Sinérgica (A + B + C)
Unir o melhor das três abordagens para garantir paralelismo real independentemente de qual CLI (`claude`, `codex`, `agy`) ou estilo de chamada (`N` chamadas foreground, `N` chamadas `run_in_background=True`, ou 1 chamada batch) o modelo escolher.

---

## 6. Arquitetura Recomendada Passo a Passo

Abaixo detalhamos exatamente o que deve ser alterado em cada camada na fase de implementação, com caminhos de arquivo, linhas de referência e testes de verificação.

---

### Passo 1: Separar `parallel_safe` de `read_only` e Expor `readOnlyHint: true` no MCP para Ferramentas de Subagente
**Arquivos afetados**:
- `src/agentos/agentic/tools/spec.py`
- `src/agentos/agentic/tools/agents/agent.py:210-277`
- `src/agentos/agentic/agent_tools.py:551-581`
- `src/agentos/agentic/native_turn.py:598-641, 864-876`

**Mudanças exatas**:
1. Em `ToolSpec` (`src/agentos/agentic/tools/spec.py`) e `ToolDefinition` (`src/agentos/agentic/agent_tools.py`), adicionar a propriedade booleana `parallel_safe: bool = False` (que é `True` automaticamente quando `read_only=True`, mas pode ser `True` em ferramentas de coordenação/delegação como `run_subagent`, `ask_agent`, `ask_agents` e `check_subagent`).
2. Em `src/agentos/agentic/tools/agents/agent.py:210-259`:
   - Remover `"mutates"` de `policy_tags` em `run_subagent`, `ask_agent` e `ask_agents` (subagentes gerenciam seus próprios efeitos e claims através de seus próprios `AgentToolset`s; a chamada de delegação em si não deve invalidar o cache de leitura do pai quando um subagente `explore`/`plan` roda, a menos que o subagente realmente modifique caminhos em `subagent_tools._changed_paths`).
   - Definir `parallel_safe=True` em `run_subagent`, `ask_agent`, `ask_agents` e `check_subagent`.
3. Em `AgentToolset` (`src/agentos/agentic/agent_tools.py:551`):
   - Adicionar `def is_parallel_safe(self, name: str) -> bool:` retornando `True` se `definition.read_only or definition.parallel_safe`.
   - Manter `is_read_only(name)` retornando `False` para `run_subagent` (exceto quando `subagent_type in {"explore", "plan"}`), garantindo que em `plan_mode` um subagente `general` não burle restrições de escrita, mas permitindo paralelismo no coletor e no MCP!
4. Em `RuntimeToolExecutor._build_specs` (`src/agentos/agentic/native_turn.py:633-640`) e `call_tool` (`native_turn.py:864`):
   - Usar `self._is_parallel_safe(pub_name, int_name)` para popular `McpToolSpec.read_only` (que controla `"annotations": {"readOnlyHint": True}` no `TurnToolServer`, fazendo `claude.exe` e `codex.exe` habilitarem despacho paralelo para `Agent`!) e para passar `read_only=is_parallel_safe` ao `ToolBatchCollector.submit()`.

---

### Passo 2: Redesenhando `ToolBatchCollector` e Eliminação da Serialização no `agy.exe` (`src/agentos/agentic/native_turn.py`)
**Arquivos afetados**:
- `src/agentos/agentic/native_turn.py:287-469, 1059-1315`

**Mudanças exatas**:
1. **Janela Deslizante e Coleta Consciente de Stream (`ToolBatchCollector.submit`, `native_turn.py:406-439`)**:
   - Quando uma chamada `parallel_safe` chega em `submit()`, estender o deadline do `_OpenReadBatch` aberto em `+gather_seconds` a cada nova chegada (até um teto máximo de ex. `500ms`), ou aguardar enquanto o stream do CLI já tiver anunciado `on_tool_use_started` para chamadas adicionais da mesma mensagem que ainda não enviaram seu HTTP `POST /mcp` (`len(self._pending_tool_uses) > 0`).
2. **Desacoplar `self._exec_lock` da Execução de Ferramentas `parallel_safe` (Subagentes e Leituras)**:
   - Em vez de segurar `self._exec_lock` durante os minutos inteiros em que `self.runtime._run_toolset` executa subagentes, substituir `self._exec_lock` por um **Read-Write Lock (`_BatchRwLock`)** (exatamente como o `Arc<RwLock<()>>` do `ToolCallRuntime` do Codex!):
     - Lotes `parallel_safe` (`read_only=True` / `run_subagent`) adquirem **lock compartilhado de leitura (`rwlock.acquire_shared()`)** durante a execução da ferramenta, e usam um lock curto (`self._commit_lock`) **apenas** nas ~20 linhas finais de `_execute_batch` que atualizam `self.messages`, `self.action_count`, `self.tool_rounds` e `self.runtime._record_step`!
     - Ferramentas mutáveis (`write_file`, `edit_file`, `run_command`) adquirem **lock exclusivo de escrita (`rwlock.acquire_exclusive()`)**, aguardando qualquer leitura ou subagente em voo terminar antes de mutar o workspace!
   - **Impacto matemático**: Mesmo que o 2º `POST /mcp` de `run_subagent` chegue `150ms`, `500ms` ou `2s` depois do 1º `POST /mcp` (após o 1º lote já ter fechado!), o 2º lote adquirirá `rwlock.acquire_shared()` **imediatamente** e rodará 100% em paralelo com o 1º subagente, sem jamais ficar bloqueado atrás dele!
3. **Contorno para o Despacho Sequencial do `agy.exe` (`dispatchSingleToolStep`)**:
   - Como provamos na Seção 3.4 que o binário `agy.exe` (`oneharness/internal/core/machine.go`) só envia o 2º `POST /mcp` após receber a resposta HTTP do 1º `POST /mcp`, temos duas proteções complementares quando o adaptador ativo for `antigravity`:
     - **No Prompt (`system_prompt.py` e `isolated_cli_note`)**: instruir explicitamente que, ao delegar 2+ subagentes em paralelo, passe `run_in_background=True` em cada `Agent` (retornando o `agent_id` instantaneamente para que todas as chamadas disparem juntas) e depois aguarde-os com `CheckSubagent` (ou deixe a barreira de fim de turno do Orin recolher os relatórios automaticamente).
     - **Suporte a lote `tasks` em `Agent` (`agent.py`)**: permitir opcionalmente passar `tasks=[{subagent_type, prompt, description, model}, ...]` em uma única chamada `Agent` para que 1 único `dispatchSingleToolStep` dispare $N$ subagentes em paralelo em foreground!

---

### Passo 3: Corrigir Promoção em Fila e Thread-Safety em `TurnSession` e `AgentToolset`
**Arquivos afetados**:
- `src/agentos/agentic/session.py:1520-1545, 2145-2197, 2832-2872, 3132-3303, 5014-5018`
- `src/agentos/agentic/agent_tools.py:155, 690`
- `src/agentos/agentic/tools/agents/agent.py:14, 29-73`
- `src/agentos/agentic/write_claims.py:42-65`

**Mudanças exatas**:
1. **Promoção Coordenada de Subagentes em Foreground (`session.py:3132-3303`)**:
   - Em `_run_foreground_subagent_watching_queue`, quando múltiplas threads de subagentes em foreground estiverem rodando na mesma rodada e o usuário enviar uma mensagem na fila:
     - Usar um evento compartilhado de rodada (`self._foreground_wave_promote_event`) sob `self._subagent_lock`: quando a 1ª thread detectar e reclamar a mensagem do usuário (`_claim_queued_user_inputs()`), ela sinaliza `self._foreground_wave_promote_event.set()`, fazendo com que **todos os subagentes em foreground daquele lote** sejam promovidos conjuntamente para `BACKGROUND_AGENTS` e retornem imediatamente, liberando o turno pai na hora para processar a mensagem do usuário!
2. **`_active_invocation_id` Thread-Local (`agent_tools.py:155` e `runtime.py:2804`)**:
   - Transformar `_active_invocation_id` em `AgentToolset` em uma `@property` respaldada por `self._tls = threading.local()`, eliminando a condição de corrida entre threads do `ThreadPoolExecutor`.
3. **Lock em `_read_file_state`, `_agent_parents`, `_code_mode_stage` e `_SUBAGENT_CHARACTER_NAMES`**:
   - Proteger `copy_read_state(self._read_file_state)` e mutações em `self._read_file_state` com `self._read_state_lock = threading.RLock()`.
   - Mover a leitura de `existing_count` e `self._ensure_builtin_subagent_record` (`session.py:2832-2871`) para dentro de `with self._subagent_lock:`.
   - Proteger `_SUBAGENT_CHARACTER_NAMES` em `agent.py` com um `threading.Lock()`.
   - Propagar os caminhos lidos/escritos pelo subagente (`subagent_tools._changed_paths`) para invalidar/atualizar `self._read_file_state` do pai de forma thread-safe sob lock ao final de cada subagente.
4. **Escopo de `owner` em `write_claims.py` por Agente (`f"{conversation_id}:{agent_id}"`)**:
   - Em `src/agentos/agentic/write_claims.py` e nos pontos de chamada em `AgentToolset`, passar `owner = f"{conversation_id}:{actor_id}"` em vez de apenas `conversation_id`. Assim, quando o Subagente A roda um comando `Bash` em paralelo com um `Write`/`Edit` do Subagente B na mesma conversa, `write_claims.foreign()` filtra corretamente os arquivos escritos pelo Subagente B!

---

### Passo 4: Prevenir Deadlock Pai $\leftrightarrow$ Subagente em `CliSlotQueue` / `NativeTurnHandle`
**Arquivos afetados**:
- `src/agentos/providers/claude_cli/native_loop.py:511-577`
- `src/agentos/providers/cli_queue.py:194-239`

**Mudanças exatas**:
1. Em `NativeTurnHandle._before_tool_call` / `_after_tool_call` (`src/agentos/providers/claude_cli/native_loop.py:511-577`):
   - **Não liberar (nem precisar readquirir) o `CliSlotTicket` do processo CLI pai para ferramentas rápidas/assíncronas** (como `read_file`, `glob_files`, `todo_write` ou `run_subagent(run_in_background=True)`), pois o processo CLI pai já está residente em RAM e voltará a rodar em milissegundos!
   - Liberar o slot do pai (`_before_tool_call`) **apenas** quando a ferramenta for um subagente bloqueante em foreground (`run_subagent` com `run_in_background=False`, `ask_agent`, `ask_agents` ou `check_subagent` com `wait_seconds > 0`) — ou seja, quando um processo CLI filho vai de fato substituir o pai na execução ativa!
   - Isso elimina:
     - O churn de soltar e readquirir ticket a cada `Read`/`Grep`/`Edit` MCP; e
     - O risco de deadlock onde o pai lança subagentes com `run_in_background=True`, os filhos tomam todos os slots da `CliSlotQueue`, e o pai fica travado em `_after_tool_call()` sem conseguir receber o retorno da própria chamada `run_in_background=True`!

---

### Passo 5: Atualizar o Prompt do Orin Code (`system_prompt.py`) e a Renderização de Ondas Paralelas no Frontend (`turnTimelineFold.ts`, `TurnTimeline.tsx`)
**Arquivos afetados**:
- `src/agentos/code_mode/system_prompt.py:105-106`
- `frontend/src/features/conversations/turnTimelineFold.ts:399-570`
- `frontend/src/features/conversations/activitySummary.ts:237-311`
- `frontend/src/features/conversations/TurnTimeline.tsx:880-929, 1250-1317`

**Mudanças exatas**:
1. **Em `src/agentos/code_mode/system_prompt.py:105-106`**:
   - Atualizar a instrução de `run_subagent` (`Agent`) para orientar claramente sobre paralelismo e `run_in_background`:
     ```python
     if options.subagents_enabled and has("run_subagent"):
         using.append(
             f"- Delegate broad searches or independent parts of a large task with {t('run_subagent')}. "
             f"To run multiple subagents in parallel, emit all {t('run_subagent')} calls in the same response "
             f"(or pass `run_in_background=True` on each call to launch them asynchronously and await them with {t('check_subagent')}). "
             "A subagent does not see this conversation unless it is a fork, so give it everything it needs."
         )
     ```
2. **No Frontend (`turnTimelineFold.ts`, `activitySummary.ts`, `TurnTimeline.tsx`)**:
   - **Exibição imediata do card `Task (...)` no instante do spawn**: Quando o evento `agent.message_sent` (`to_agent_id`) ou `turn.started` de um subagente (`parentAgentId != null`) chegar, criar imediatamente o `SubagentTaskContainer` em estado `running` (*"Iniciando subagente..."* / `0 ações · Em execução`), sem esperar 15–20 segundos pela 1ª tool call do subagente!
   - **Não limpar `subagentWaveItems` / `subagentWaveGroups` em eventos intersticiais sem texto visível**: Em `turnTimelineFold.ts:404, 475` e `activitySummary.ts:238`, limpar `subagentWaveItems` apenas quando o agente principal emitir **texto visível de resposta** (`flushText()` com conteúdo) ou uma ferramenta que não seja de coordenação/espera de subagente (`check_subagent`, `waiting_subagents`, `waiting_model`, `thought_delta`). Assim, 3 subagentes disparados juntos permanecerão agrupados de forma coesa dentro de `<ParallelSubagentsGroup>` (*"3 subagentes em paralelo · Em execução"*).

---

### Passo 6: Plano de Testes Automatizados para Validação da Implementação
1. **`tests/unit/test_cli_tools_mcp_server.py` & `tests/unit/test_native_turn_collector.py`**:
   - Verificar que `tools/list` retorna `"annotations": {"readOnlyHint": True}` para `Agent` (`run_subagent`), `ask_agent`, `ask_agents` e `CheckSubagent` (`check_subagent`), mantendo `False` para `Write`, `Edit`, `Bash`.
   - Testar que 3 chamadas `run_subagent` submetidas concorrentemente ao `RuntimeToolExecutor.call_tool` (mesmo com atrasos escalonados de `0ms`, `60ms` e `180ms` > `BATCH_GATHER_SECONDS`) executam **simultaneamente em paralelo** (tempo total $\approx \max(T_1, T_2, T_3)$ e nunca $T_1 + T_2 + T_3$).
2. **`tests/unit/test_session_parallel_subagents.py`**:
   - Testar 3 chamadas `run_subagent` em foreground rodando em paralelo quando o usuário envia uma mensagem na fila (`queued_inputs`): validar que **todos os 3 subagentes** são promovidos para background simultaneamente e que o turno pai recebe a mensagem do usuário em `<200ms`.
   - Testar thread-safety de `copy_read_state`, `_active_invocation_id` e `write_claims` com 2 subagentes paralelos (um rodando `run_command` e outro rodando `write_file` na mesma `conversation_id`).
3. **`tests/unit/test_cli_queue_subagents.py`**:
   - Testar `NativeTurnHandle` com `CliSlotQueue(limit=2)` quando o agente pai lança 2 subagentes com `run_in_background=True`: garantir que `_after_tool_call` não causa deadlock na resposta HTTP do MCP.
4. **`frontend/src/features/conversations/__tests__/TurnTimeline.parallelSubagents.test.tsx`**:
   - Verificar que 3 subagentes disparados em paralelo renderizam imediatamente seus 3 `SubagentTaskContainer` dentro de `<ParallelSubagentsGroup>` (`"3 subagentes em paralelo"`) desde o evento `agent.message_sent`/`turn.started` e não se fragmentam quando eventos `thought_delta` ou `check_subagent` ocorrem durante a espera.

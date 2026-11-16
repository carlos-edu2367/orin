# Plano de Correção — Subagentes em Paralelo + Codex Lendo Arquivos/Imagens

- **Data:** 2026-11-15
- **Autor:** Arquiteto Principal (revisão crítica dos relatórios + inspeção direta do código)
- **Insumos:**
  - `docs/reports/2026-11-15-relatorio-subagentes-paralelos.md` (Relatório 1)
  - `docs/reports/2026-11-15-relatorio-codex-leitura-arquivos-imagens.md` (Relatório 2)
  - Achados empíricos da sessão (TodoWrite vazando entre pai e filho; `CheckSubagent` 60 s + 60 s)
- **Status:** plano aprovado para implementação. Nenhum código foi alterado nesta etapa.

---

## 0. Regras de execução (valem para todos os workstreams)

1. **Preservar a modificação não comitada do usuário** em `src/agentos/providers/antigravity_cli/catalog.py`. Nenhum workstream edita esse arquivo. É proibido `git stash`, `git checkout -- .`, `git reset --hard`, `git add -A` / `git add .` e `git commit -a`. Commits sempre com caminhos explícitos (`git add <arquivos do WS>`). Antes de cada commit, `git status --short` deve continuar mostrando ` M src/agentos/providers/antigravity_cli/catalog.py` intocado.
2. **Até o WS-B ser integrado, os subagentes implementadores não devem chamar `TodoWrite`**: hoje a lista é compartilhada com a conversa pai (ver §1, achado T1). Este próprio subagente recebe nos `<system-reminder>` a checklist do agente principal, o que confirma o bug também no sentido pai → filho.
3. Workstreams da Fase 1 tocam conjuntos **disjuntos** de arquivos (§4). Quando um WS depende de um método de outro, ele usa o **contrato de interface** do §3 via `getattr(..., None)` com fallback, para que os dois possam ser implementados em paralelo e integrados em qualquer ordem.
4. TDD: cada WS escreve primeiro os testes novos (que falham), depois o código. Python: `./.venv/Scripts/python.exe -m pytest <arquivos> -q`. Frontend: `cd frontend && NODE_OPTIONS=--no-experimental-webstorage npx vitest run <arquivos>`.
5. Windows 10: nada de `sleep` fixo como sincronização em testes de concorrência; usar `threading.Event`/`Barrier` com timeouts folgados (≥ 2 s), porque a resolução do timer no Windows é de ~15 ms.

---

## 1. Validação das hipóteses no código (o que se confirmou e o que não)

| # | Hipótese | Veredito | Evidência |
|---|---|---|---|
| P1 | `Agent`/`ask_agent(s)` publicados com `readOnlyHint:false` | **Confirmado** | `tools/agents/agent.py:210-259` (`policy_tags=("mutates",)`, sem `read_only`); `cli_tools_mcp/server.py:270`; `native_turn.py:633-640` |
| P2 | Claude CLI só paraleliza MCP com `readOnlyHint:true`; Codex usa `readOnlyHint` ou `supports_parallel_tool_calls` | Aceito (evidência binária/upstream dos relatórios) | Relatório 1 §3.2–3.3 |
| P3 | `agy.exe` despacha um tool step por vez | Aceito | Relatório 1 §3.4 |
| P4 | `ToolBatchCollector` executa toda chamada não-read-only em `_run_exclusive([entry])` segurando `_exec_lock` pela duração inteira | **Confirmado** | `native_turn.py:301, 318-335, 366-404` |
| P5 | Janela fixa de 100 ms; lote seguinte fica preso no `_exec_lock` | **Confirmado** e é a causa exata do **60 s + 60 s** observado: `check_subagent` já é `read_only=True` (`agent.py:274`), mas as duas chamadas chegaram em janelas diferentes e o 2º lote esperou o `_exec_lock` do 1º, que estava bloqueado em `future.result(timeout=60)` (`session.py:4282-4287`) | `native_turn.py:78, 406-441` |
| P6 | `_execute_batch` e `_run_toolset` mutam estado compartilhado | **Confirmado e é mais amplo do que o relatório diz**: além de `messages/action_count/tool_rounds` (`native_turn.py:1110-1143, 1204, 1233`), o `_run_toolset` lê/escreve `_failed_signatures`, `_succeeded_reads`, `_consecutive_tool_failures`, `counters` (`runtime.py:2634-2701, 2912-2919`) e o slot único `runtime._native_message_first_read_keys` (`native_turn.py:1116-1120`, lido em `runtime.py:2710`). O `checkpoint_lock` é **local por chamada** (`runtime.py:2749`), não protege duas execuções simultâneas | — |
| P7 | `_run_toolset` elegível só para `{"run_subagent","ask_agent","check_subagent"}` (faltando `ask_agents`) | **Confirmado** | `runtime.py:2737, 2890` |
| P8 | `is_mutating("run_subagent") == True` incrementa `_writes` "indevidamente" | **Refutado como bug**: é o comportamento correto e conservador. Um subagente `general` edita arquivos; sem o incremento, leituras em cache do pai ficariam velhas. **Manter `"mutates"`** | `agent_tools.py:569-580`, `runtime.py:2797-2801` |
| P9 | `_active_invocation_id` sobrescrito entre threads | **Parcialmente refutado**: o runtime já passa `call_id` explícito (`runtime.py:2807-2809`) e `agent_tools.py:690` o prefere. Trocar por `threading.local` quebraria callbacks de progresso que rodam em outra thread → **evitar** | — |
| P10 | Promoção parcial na fila (`_run_foreground_subagent_watching_queue`) | **Confirmado**: só a thread que reivindica a mensagem promove o próprio subagente | `session.py:3254-3299` |
| P11 | Deadlock/estol `CliSlotQueue` × `_after_tool_call` | **Confirmado (estol)**: o pai solta o slot em **toda** tool call (`native_loop.py:402-410, 511-521`) e, se filhos em background ocuparem os slots (limite via `ORIN_CLI_MAX_PROCESSES` ou guarda de memória de 1024 MB, `cli_queue.py:194-204`), o pai trava em `_after_tool_call` até um filho terminar | — |
| P12 | `ToolSpec` precisa de um novo campo `parallel_safe` | **Refinado**: o campo **já existe** como `concurrency_safe` (`tools/spec.py:51, 83`; `tools/_shared.py:460`) e não é usado por ninguém. Reaproveitar | — |
| T1 | `TodoWrite` de subagente sobrescreve a checklist do pai | **Confirmado (causa-raiz exata)**: `session.py:5019` passa a **mesma lista** `self._todos_state` para todo `AgentToolset` (pai e filhos); `todo.py:54-55` faz `clear()+extend()` nela; `runtime.py:507-512` monta o lembrete do pai a partir dela. Além disso, `session.py:1893-1899` emite `CODE_MODE_STAGE_CHANGED` com `todos` também para atores-subagente, e o frontend troca o TodoBar com **qualquer** evento que traga `todos` (`activityReducer.ts:216, 248`) | — |
| C1 | Catálogo Codex grava `input_modalities=["text"]` | **Confirmado** | `codex_cli/catalog.py:25-74, 173-180`; `provider_catalog/service.py:190`; sem auto-cura equivalente a `service.py:462-472` |
| C2 | Já existe teste que **trava** o comportamento errado | **Confirmado**: `tests/unit/providers/test_cli_registry.py:678` exige `("text",)` para Codex. Precisa ser atualizado (com a mesma justificativa já usada para o agy nas linhas 679-680) | — |
| C3 | `CodexCliAdapter` já declara `images_in_tool_results=True` | **Confirmado** — o catálogo é que está incoerente com o adapter | `codex_cli/adapter.py:42-51` |
| C4 | `VisionReader` aceita como transcrição a resposta "imagem omitida" de um transporte CLI | **Confirmado** | `reading/vision.py:33-65`; `workers/chat.py:1127-1188`; `codex_cli/command.py:684-691` (Codex também troca imagens sem caminho por nota) |
| C5 | `search_mcp_tools(select:...)` omite ferramentas já carregadas | **Confirmado** (`already_available` só vai para `tool_output`) | `tools/mcp/admin.py:218-236, 291-308` |
| C6 | Prompt do Codex manda carregar ferramentas "on demand" embora todas já venham no `tools/list` | **Confirmado**: `allow_deferral=False` para Codex (`native_turn.py:2169-2173`), mas `session.py:6007-6011` e `agent_tools.py:431-435` ignoram isso | — |
| C7 | `ProjectMap` cita nomes internos e não tem fallback com índice vazio | **Confirmado** | `tools/orin/search_code.py:107, 158-170` |
| C8 | Barreira de fim de turno recolhe subagentes em background | **Confirmado (só agente principal)** | `native_turn.py:2614-2660` (`not is_subagent_turn`) |

---

## 2. Decisões arquiteturais e trade-offs

### 2.1 Problema A — subagentes não rodam em paralelo

**Decisão: "Opção D refinada" = paralelismo nativo nos clientes (`readOnlyHint` derivado de `concurrency_safe`) + coletor com *faixa compartilhada de delegação* no servidor + caminho ergonômico background/multi-espera para o `agy`.**

| Item | Decisão | Justificativa |
|---|---|---|
| Publicar `readOnlyHint:true` para `run_subagent`, `ask_agent`, `ask_agents`, `check_subagent` | **Aprovar**, derivado de `concurrency_safe=True` no spec | É o único sinal que o Claude CLI usa (`isConcurrencySafe`) e um dos dois que o Codex usa. Não abre brecha: a autorização do Orin é feita **dentro** de cada ferramenta (broker); Claude recebe as ferramentas via `--allowedTools` (`claude_cli/command.py:746`), Codex via `default_tools_approval_mode="approve"` (`codex_cli/command.py:445-446`), agy via `permissions.allow`. A especificação MCP trata anotações como dicas não confiáveis. `is_read_only()` **não muda** → `plan_mode`, cache de leitura e permissões ficam idênticos |
| `mcp_servers.orin.supports_parallel_tool_calls=true` no Codex | **Evitar** | Vale para o servidor inteiro: o Codex admitiria `Write`/`Edit` em paralelo e a ordem de chegada ao Orin ficaria não determinística (o coletor serializa, mas não recupera a ordem do modelo) |
| Remover `"mutates"` das ferramentas de delegação | **Evitar** (P8) | Invalidação de cache de leitura do pai é correta |
| Novo campo `parallel_safe` | **Refinar** → reutilizar `concurrency_safe` existente (P12) | Zero schema novo |
| RW-lock no coletor com janela deslizante de até 500 ms | **Refinar** → *três faixas*, **sem** janela deslizante | Com a faixa de delegação compartilhada, a janela fica irrelevante para subagentes; aumentar a janela atrasaria toda leitura |
| `tasks=[...]` dentro de `Agent` | **Evitar** | Diverge do schema do `Agent` do Claude Code, concentra N subagentes numa chamada MCP longa (risco de timeout/`ABANDONED_BEFORE_START`) e duplica o que background + multi-espera já resolvem |
| Auto-background no `agy` | **Evitar** | Muda o contrato da ferramenta (o modelo espera o relatório), surpreende o modelo e o usuário |
| `CheckSubagent(agent_ids=[...])` (espera múltipla) | **Aprovar** | Aditivo, resolve o `agy` (despacho serial) com 1 chamada e elimina a soma de esperas mesmo em clientes paralelos |
| Promoção conjunta da onda foreground quando o usuário escreve | **Aprovar** (P10) | Com paralelismo real, o CLI espera **todas** as respostas da mensagem; sem isso o pai continua travado |
| Slot do pai: soltar só em esperas longas por outro agente/usuário | **Aprovar** (P11) | Elimina o estol e o churn; `run_in_background=True` volta a retornar na hora |
| `_active_invocation_id` em `threading.local` | **Evitar** (P9) | Regressão em callbacks de outras threads |
| Locks em `_read_file_state`/`_SUBAGENT_CHARACTER_NAMES` | **Aprovar como hardening barato** (snapshot atômico + `threading.Lock`) | Baixo risco |
| `existing_count` dentro de `_subagent_lock` | **Adiar** | Sem evidência de falha; seguraria o lock durante I/O de banco |
| `write_claims` com dono por agente | **Adiar (P3)** | Não causa os sintomas relatados; muda semântica de claims entre conversas |
| Frontend: card no spawn e onda não fragmentada | **Aprovar** | Visível só depois do paralelismo real; isolado em WS-G |

#### 2.1.1 Desenho do coletor (núcleo do WS-A)

Matriz de compatibilidade (substitui o `_exec_lock` único):

| Faixa | Quem entra | Compatível com |
|---|---|---|
| `agent` (compartilhada) | ferramentas com `kind == "agent"` e `concurrency_safe` (`run_subagent`, `ask_agent`, `ask_agents`, `check_subagent`) | `agent` e `read` |
| `read` (compartilhada + mutex próprio) | lotes de leitura (como hoje, janela de 100 ms) | `agent`; lotes de leitura continuam **um por vez** entre si (`_read_mutex`) |
| `write` (exclusiva) | todo o resto | nada |

- Chamadas `agent` **não esperam janela**: cada uma vira imediatamente seu próprio lote em modo compartilhado. Assim `C1@0ms, C2@150ms, C3@2s` rodam juntas.
- Preferência de escritor: uma faixa compartilhada não entra se houver escritor esperando (sem inanição de escrita). Não há ciclo: um subagente em foreground nunca depende de uma escrita do mesmo pai (o cliente só envia a escrita depois de ver os resultados anteriores — barreira serial no Claude, `lock.write()` no Codex, despacho serial no agy).
- Polling de abandono mantido (`EXEC_WAIT_POLL_SECONDS=0.2`) em todas as esperas.
- **Reentrância do runtime**: `AgenticTurnRuntime._run_toolset` passa a ser seguro para duas execuções sobrepostas porque (a) as fases de preparação e de montagem de resultados ficam sob um `RLock` de instância e (b) a fase de invocação (longa) fica fora dele. Ordem de locks fixa: `RuntimeToolExecutor._commit_lock` → `runtime._toolset_state_lock`; nunca o inverso; **nenhum lock é segurado durante `toolset.invoke`**.
- O slot `runtime._native_message_first_read_keys` só é escrito por lotes `read`/`write` (que já são mutuamente exclusivos entre si pelo `_read_mutex` e pela faixa exclusiva); lotes `agent` nunca o tocam.
- Cache de leitura × subagente sobreposto: além do incremento de `_writes` no início de uma chamada `mutating`, incrementar **também ao fim** dela, para que uma leitura feita *durante* um subagente `general` não seja reaproveitada depois.

#### 2.1.2 Como fica cada CLI

| CLI | Mecanismo | Resultado |
|---|---|---|
| `claude` | `readOnlyHint:true` → até 10 chamadas `Agent` simultâneas; faixa `agent` no Orin | N subagentes em foreground em paralelo, transparente |
| `codex` | `readOnlyHint:true` → `lock.read()`; prompt ensina `await Promise.all([...])` dentro de `functions.exec` (com `await` sequencial o JS serializa sozinho) | Paralelo quando o modelo usa `Promise.all` |
| `agy` | despacho serial é do binário → nota específica: `run_in_background=true` em cada `Agent` + `CheckSubagent(agent_ids=[...], wait_seconds=60)` ou encerrar a mensagem (barreira C8, só agente principal) | Lançamentos em ~ms, execução paralela real |
| API (HTTP) | já paralelo no pool (`runtime.py:2888-2907`); ganha `ask_agents` e o lock de estado | Sem regressão |

### 2.2 Problema B — Codex não vê arquivos e imagens

**Decisão: corrigir a cadeia inteira na origem (catálogo + auto-cura + defesa no `_model_sees_images`), blindar o `VisionReader`, ensinar `functions.exec` na nota por adapter, alinhar deferral/ToolSearch, e (Fase 2) anexar imagens do usuário com `--image`.**

| Item | Decisão | Justificativa |
|---|---|---|
| `discover_codex_models` lê `input_modalities` do `models_cache.json`; fallback `("text","image")`; `codex-image` continua `("text",)` | **Aprovar** | Alinha com `images_in_tool_results=True` do adapter e com o próprio Codex (sanitiza por modalidade) |
| `_codex_modalities_are_stale` + auto-refresh | **Aprovar** (espelha `_antigravity_modalities_are_stale`) | Cura bancos existentes sem intervenção do usuário |
| Defesa em `ChatWorker._model_sees_images` | **Aprovar** | Funciona mesmo antes de qualquer listagem do catálogo; cobre subagentes (`session._sees_images_for` usa o mesmo resolvedor) |
| `VisionReader` com provedores CLI | **Aprovar filtro + sentinela**; **evitar** arquivo temporário para o agy | O transporte one-shot de `google-antigravity` e `openai-codex` descarta imagem inline; passar arquivo exigiria ferramentas nativas num modo `tools=[]` — incerto. Filtrar candidatos/override e rejeitar respostas que contenham a nota de mídia omitida |
| Nota por adapter em `isolated_cli_note` | **Aprovar** | Codex: `functions.exec`, `image()`/`content()`, `Promise.all`, não usar ToolSearch para ferramentas do núcleo. agy: paralelismo via background. Não mistura com o `read_file` nativo do agy |
| `deferred_tool_names`/prompt respeitando `allow_deferral` | **Aprovar** | Some o "Other tools load on demand" para Codex; `ToolSearch` passa a tratar tudo como já disponível |
| `ToolSearch(select:...)` devolvendo schema de ferramentas já carregadas | **Aprovar** | O modelo nunca mais recebe `tools: []` para `Glob`/`Agent` |
| `features.mcp_tool_list_changed=true` | **Adiar (experimento)** | Feature "under development" no 0.167; o próprio código avisa que chaves `-c` variam entre builds (`codex_cli/command.py:389-392`). Em vez disso, `ToolSearch` instrui a chamar ferramentas MCP externas ativadas via `CallMcpTool` quando o cliente não recarrega a lista |
| `ProjectMap`: nomes públicos + fallback de listagem | **Aprovar** | Barato e útil em repositório recém-clonado |
| `--image <FILE>` para anexos do usuário | **Aprovar com blindagem, Fase 2** | Zero tool call para anexos. Cuidados: `--image` é multi-valor no clap (pode engolir o `-` final) e tem `value_delimiter=','` → emitir logo após `exec`/`resume <id>` e antes de `--json`; rejeitar caminhos com vírgula; validar dentro do workspace |
| `features.view_image=true`, `--cd`, `--add-dir` | **Evitar** | Resolvem contra o cwd vazio isolado e/ou expõem o workspace ao sandbox nativo do Codex, contornando o broker |
| `cwd` por processo para Codex | **Evitar** | Sem evidência de dano |
| `ask_agents` no Code Mode | **Evitar** | Um padrão só (Agent + background + CheckSubagent) |

---

## 3. Contratos de interface entre workstreams (implementar exatamente estes nomes)

| Contrato | Dono | Consumidores | Fallback do consumidor |
|---|---|---|---|
| `AgentToolset.is_concurrency_safe(name: str) -> bool` → `resolve(name).concurrency_safe`; `False` se desconhecida | WS-B (`agent_tools.py`) | WS-A (`native_turn.py`, `runtime.py`) | `is_read_only(name) or name in _DELEGATION_TOOLS` |
| `AgentToolset.is_parallel_delegation(name: str) -> bool` → `definition.kind == "agent" and definition.concurrency_safe` | WS-B | WS-A | `name in _DELEGATION_TOOLS` |
| `_DELEGATION_TOOLS = frozenset({"run_subagent","ask_agent","ask_agents","check_subagent"})` (constante local de fallback) | WS-A (definida em `native_turn.py` e reusada em `runtime.py`) | — | — |
| `AgentToolset.set_allow_deferral(allowed: bool) -> None` e atributo `_allow_deferral: bool = True`; `deferred_tool_names` retorna `frozenset()` quando `False` | WS-B | WS-B (`session.py`), WS-E (`admin.py`) | `getattr(self, "_allow_deferral", True)` |
| `McpToolSpec.parallel_safe: bool = False` | WS-A (`cli_tools_mcp/types.py`) | WS-A (`server.py`) | — |
| `RuntimeToolExecutor.releases_cli_slot(request: ToolCallRequest) -> bool` | WS-A (`native_turn.py`) | WS-C (`native_loop.py`, `_SlotReleasingExecutor`) | sem o método → comportamento atual (sempre solta) |
| `check_subagent` aceita `agent_ids: list[str] | None`; o callable da sessão recebe `agent_ids=` como kwarg | WS-B (`agent.py` + `session.py`) | WS-C (texto de prompt) | — |
| `isolated_cli_note(..., adapter_name: str | None = None)` / `with_isolated_cli_note(..., adapter_name=...)` | WS-C | — | `None` → texto atual |
| `CliTurnRequest.image_paths: tuple[Path, ...] = ()` | WS-F | WS-F | — |

---

## 4. Workstreams e fases

### Mapa de arquivos (cada arquivo pertence a exatamente um WS por fase)

| WS | Fase | Arquivos de produção | Testes |
|---|---|---|---|
| **WS-A** Coletor/runtime concorrente + anotação MCP | 1 | `src/agentos/agentic/native_turn.py`, `src/agentos/agentic/runtime.py`, `src/agentos/providers/cli_tools_mcp/types.py`, `src/agentos/providers/cli_tools_mcp/server.py` | `tests/unit/agentic/test_native_turn.py` (ajuste), `tests/unit/agentic/test_tool_call_abandon.py`, `tests/unit/providers/cli_tools_mcp/test_turn_tool_server.py`, **novo** `tests/unit/agentic/test_native_turn_agent_lane.py`, **novo** `tests/unit/agentic/test_run_toolset_reentrant.py` |
| **WS-B** Ferramentas de subagente + sessão (todos, promoção, multi-espera, deferral) | 1 | `src/agentos/agentic/tools/agents/agent.py`, `src/agentos/agentic/agent_tools.py`, `src/agentos/agentic/session.py`, `src/agentos/agentic/read_state.py` | **novos** `tests/unit/agentic/test_subagent_todos_isolation.py`, `tests/unit/agentic/test_check_subagent_many.py`, `tests/unit/agentic/test_foreground_wave_promotion.py`, `tests/unit/agentic/test_tool_deferral_coherence.py` |
| **WS-C** `native_loop` (nota por adapter, slot) + prompt do Code Mode | 1 | `src/agentos/providers/claude_cli/native_loop.py`, `src/agentos/code_mode/system_prompt.py` | `tests/unit/providers/claude_cli/test_native_loop.py`, `tests/unit/agentic/test_subagent_same_freedom.py`, **novo** `tests/unit/providers/claude_cli/test_slot_release_policy.py`, **novo** `tests/unit/providers/claude_cli/test_isolated_cli_note_adapters.py` |
| **WS-D** Catálogo Codex + visão | 1 | `src/agentos/providers/codex_cli/catalog.py`, `src/agentos/provider_catalog/service.py`, `src/agentos/workers/chat.py`, `src/agentos/reading/vision.py`, `src/agentos/reading/selection.py` (se necessário), `src/agentos/agentic/tools/files/read.py` | `tests/unit/providers/codex_cli/test_catalog.py`, `tests/unit/providers/test_cli_registry.py` (ajuste linha 678), `tests/unit/reading/test_vision.py`, `tests/unit/workers/test_vision_reader_factory.py`, `tests/unit/agentic/test_view_file_vision.py`, **novo** `tests/unit/provider_catalog/test_codex_modalities_autoheal.py` |
| **WS-E** ToolSearch + ProjectMap | 1 | `src/agentos/agentic/tools/mcp/admin.py`, `src/agentos/agentic/tools/orin/search_code.py` | `tests/unit/agentic/tools/test_tool_search.py`, `tests/unit/agentic/test_native_turn_deferred_tools.py`, **novo** `tests/unit/agentic/tools/test_project_map_fallback.py` |
| **WS-G** Frontend (TodoBar, card no spawn, onda) | 1 | `frontend/src/features/conversations/activityReducer.ts`, `turnTimelineFold.ts`, `activitySummary.ts`, `TurnTimeline.tsx` | `ParallelSubagentsCount.test.tsx`, `ParallelSubagentsAndSessions.test.tsx`, `SubagentCardOutcome.test.tsx`, `TurnTimeline*.test.tsx`, **novos** `__tests__/activityReducerSubagentTodos.test.ts`, `__tests__/TurnTimeline.parallelSubagents.test.tsx` |
| **WS-F** Codex `--image` para anexos | **2** (depois de A e C) | `src/agentos/providers/cli_adapter.py`, `src/agentos/providers/codex_cli/command.py`, **novo** `src/agentos/providers/codex_cli/attachments.py`, `src/agentos/providers/claude_cli/native_loop.py` (só plumbing), `src/agentos/agentic/native_turn.py` (só plumbing) | `tests/unit/providers/codex_cli/test_command.py`, **novo** `tests/unit/providers/codex_cli/test_image_attachments.py` |
| **WS-V** Verificação integrada | 3 | — | suíte completa + E2E manual (§7) |

Ordem: **Fase 0** (baseline) → **Fase 1** (A, B, C, D, E, G em paralelo) → integração/rodar suíte → **Fase 2** (F) → **Fase 3** (V + Red Team).

### Fase 0 — baseline (antes de qualquer edição)

```bash
git status --short     # deve mostrar apenas o catalog.py do agy modificado + docs não rastreados
./.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_native_turn.py tests/unit/agentic/test_tool_call_abandon.py tests/unit/agentic/test_parallel_and_background_subagents.py tests/unit/providers/claude_cli/test_native_loop.py tests/unit/providers/codex_cli tests/unit/providers/test_cli_registry.py tests/unit/reading tests/unit/workers/test_vision_reader_factory.py tests/unit/agentic/tools/test_tool_search.py -q
cd frontend && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/features/conversations
```
Registrar falhas pré-existentes para não atribuí-las aos WS.

---

## 5. Especificação por arquivo

### WS-A — Coletor e runtime concorrentes

#### `src/agentos/providers/cli_tools_mcp/types.py`
- `McpToolSpec`: adicionar `parallel_safe: bool = False` (dataclass frozen; default mantém compatibilidade com todos os construtores atuais).

#### `src/agentos/providers/cli_tools_mcp/server.py` (`TurnToolServer._dispatch_single`, ~linha 263-274)
- `"annotations": {"readOnlyHint": bool(spec.read_only or spec.parallel_safe)}`.
- Invariante: nenhuma outra lógica do servidor passa a depender de `parallel_safe`.

#### `src/agentos/agentic/native_turn.py`
1. **Constantes**: `_DELEGATION_TOOLS` (§3). Manter `BATCH_GATHER_SECONDS = 0.1`, `EXEC_WAIT_POLL_SECONDS`, `ABANDONED_BEFORE_START` (exportados e usados por testes).
2. **Nova classe `_LaneGate`** (antes de `ToolBatchCollector`):
   - Estado sob um `threading.Condition`: `_exclusive_active: bool`, `_shared_active: int`, `_exclusive_waiting: int`.
   - `acquire_shared(timeout) -> bool`: espera enquanto `_exclusive_active or _exclusive_waiting > 0`.
   - `acquire_exclusive(timeout) -> bool`: incrementa `_exclusive_waiting` durante a espera (decrementa sempre, inclusive em timeout); espera enquanto `_exclusive_active or _shared_active > 0`.
   - `release_shared()`, `release_exclusive()` com `notify_all()`.
   - Todas as esperas com `timeout=EXEC_WAIT_POLL_SECONDS` em laço para checar abandono.
3. **`ToolBatchCollector`**:
   - Substituir `self._exec_lock` por `self._gate = _LaneGate()` e `self._read_mutex = threading.Lock()`.
   - `self._running: list[_BatchEntry]` → conjunto/lista de **todas** as entradas em execução (união), protegido por `_state_lock`.
   - Novo `ContextVar` de módulo `_RUNNING_ENTRIES: ContextVar[tuple[_BatchEntry, ...] | None]`. Ao executar um lote, `token = _RUNNING_ENTRIES.set(tuple(live))` / `reset(token)` no `finally`. `running_call_abandoned()`: se o ContextVar estiver definido → `all(e.abandoned() for e in entries)`; senão, comportamento atual sobre a união. (Os threads do pool do `_run_toolset` herdam o contexto via `contextvars.copy_context().run`, `runtime.py:2896`.)
   - `_run_gated(entries, msg_key, mode: Literal["read","write","agent"])` substitui `_run_exclusive`:
     - `write` → `acquire_exclusive`; `read` → `_read_mutex` (polling) e depois `acquire_shared`; `agent` → `acquire_shared`.
     - Mesmo tratamento de abandono de hoje (antes de entrar e ao entrar, `live = [e for e in entries if not e.abandoned()]`).
     - Liberar na ordem inversa no `finally`.
   - `submit(..., read_only: bool = False, lane: str | None = None, cancelled=None)`:
     - `lane is None` → `"read"` se `read_only` senão `"write"` (compatível com todos os testes atuais).
     - `lane == "agent"` → sem janela: `results = self._run_gated([entry], msg_key, "agent")`; devolve `results[0]` (mesmo contrato de erro do caminho de escrita). **Não** registra em `_in_flight_reads`, **não** fecha lotes de leitura abertos.
     - Caminhos `read`/`write` inalterados exceto pela troca de `_run_exclusive` por `_run_gated`.
4. **`RuntimeToolExecutor`**:
   - `self._in_flight` passa a ser alterado sob um `threading.Lock` (hoje `+= 1` sem lock, `native_turn.py:879-883`).
   - Novo `self._commit_lock = threading.RLock()` e `self._active_batches = 0`.
   - Helpers `_is_concurrency_safe(pub, int)` e `_is_parallel_delegation(int)` com o fallback do §3.
   - `_build_specs`: `McpToolSpec(..., read_only=ro, parallel_safe=ro or self._is_concurrency_safe(pub_name, int_name))`.
   - `call_tool`: `lane = "agent" if self._is_parallel_delegation(internal_name) else None`; passar `lane=lane` ao coletor (mantendo `read_only=read_only`).
   - **`releases_cli_slot(request) -> bool`** (contrato §3): resolve o nome interno e retorna `True` apenas para esperas longas por outro agente ou pelo usuário:
     - `run_subagent` com `run_in_background` falso **e** sem `teammate_name`/`name` (teammate sempre roda em background);
     - `ask_agent`, `ask_agents`;
     - `check_subagent` com `wait_seconds > 0`;
     - ferramentas que esperam o usuário: confirmar as chaves internas com `grep` (esperado: `ask_user`, `exit_plan_mode`) e listá-las numa constante `_USER_WAIT_TOOLS`.
     - Qualquer outra → `False`.
   - **`_execute_batch`** (sem mudar a assinatura):
     - Fase pré (sob `_commit_lock`): checagem de `abort_code`/`ACTION_LIMIT`, registros adiados, `_life("waiting_tool")` só quando `_active_batches` passa de 0 para 1, `_batch_seq`, `_first_read_keys_by_message_id`, `_forget_reads_already_seen`, `before_discovered`. Incrementar `_active_batches`.
     - Definir `self.runtime._native_message_first_read_keys` **somente se o lote não for de delegação** (todas as entradas com `_is_parallel_delegation`) — lotes `agent` nunca escrevem nem limpam esse slot.
     - `self.runtime._run_toolset(...)` **fora** de qualquer lock.
     - Fase pós inteira (do `finally` de leituras até a montagem de `out_results`) sob `_commit_lock`; `self.messages.append(assistant_tool_message)` + `extend(tool_results)` ficam adjacentes e atômicos. Decrementar `_active_batches` num `finally`; `_life("running")` só quando chega a 0 e `abort_code is None`.
     - Invariante: `_commit_lock` **nunca** é segurado durante `_run_toolset` nem durante `handle.refuse_tools` em espera longa.
   - `bind_abandon(self.collector.running_call_abandoned)` permanece.

#### `src/agentos/agentic/runtime.py`
1. Em `__init__` de `AgenticTurnRuntime`: `self._toolset_state_lock = threading.RLock()`.
2. `_run_toolset` (`runtime.py:2570-...`):
   - Envolver a fase de preparação (parse, `tool_started`, `duplicate`, `repeated`, `write_seen`, `first_index`, `same_batch_read`, `eligible`; linhas ~2581-2739) com `with self._toolset_state_lock:`.
   - `checkpoint_lock = self._toolset_state_lock` (em vez de `threading.Lock()` local, linha 2749).
   - Em `invoke`, depois do `toolset.invoke` de uma chamada `mutating` (sucesso ou falha), sob o lock: `self._writes += 1` adicional (o incremento inicial na linha 2801 permanece).
   - `eligible`: trocar `name in {"run_subagent","ask_agent","check_subagent"}` por `is_concurrency_safe(name)` (via `getattr(self.toolset, "is_concurrency_safe", None)`, fallback `_DELEGATION_TOOLS`), o que inclui `ask_agents`. `has_subagent_calls` idem.
   - Envolver a montagem de resultados (laço `for index, (...) in enumerate(prepared)` em diante, linhas ~2912-fim) com o lock.
   - **Nenhum lock durante `self.toolset.invoke`** nem durante `future.result()` do pool.
3. Invariantes a preservar: ordem dos resultados igual à ordem pedida; escrita nunca começa antes de uma chamada anterior do mesmo lote terminar; dedupe por assinatura; `READ_IN_SAME_BATCH`.

### WS-B — Ferramentas de subagente e sessão

#### `src/agentos/agentic/tools/agents/agent.py`
- `ask_agent`, `ask_agents`, `run_subagent`, `check_subagent`: `concurrency_safe=True`. Manter `policy_tags=("mutates",)` nas três de delegação e `read_only=True` em `check_subagent`.
- `check_subagent(self, agent_id: str | None = None, wait_seconds: int = 0, agent_ids: list[str] | None = None)`:
  - Normalizar: juntar `agent_id` (se não vazio) + `agent_ids`, remover vazios/duplicados preservando ordem; máximo 16; nenhum → `AgentToolError("Pass agent_id or agent_ids.")`.
  - Um id → `self._check_subagent(id, wait_seconds=wait)` (caminho atual).
  - Vários → `self._check_subagent(ids[0], wait_seconds=wait, agent_ids=ids)`; em `TypeError` (callable antigo), fallback: consultar cada id com `wait_seconds=0` em laço até o prazo, depois montar o relatório.
- `legacy_schema` de `check_subagent`: adicionar `"agent_ids": {"type": "array", "items": _TEXT, "maxItems": 16, "description": "Several background subagents to inspect or await together (wait_seconds waits until all finish)."}`; `required` passa a `()`; descrição: "Inspect or await one or more background subagents...". Conferir em `tools/public_names.py` se há mapa de argumentos para `CheckSubagent` e incluir `agent_ids`.
- Descrição de `run_in_background` em `run_subagent`: mencionar `check_subagent(agent_ids=[...])`.
- `_SUBAGENT_CHARACTER_NAMES` (`agent.py:14, 29-73`): proteger leitura+escrita com um `threading.Lock()` de módulo.

#### `src/agentos/agentic/agent_tools.py`
- `is_concurrency_safe(name)` e `is_parallel_delegation(name)` (§3), ao lado de `is_read_only` (`agent_tools.py:551`).
- `self._allow_deferral = True` no `__init__`; `set_allow_deferral(allowed)`; em `deferred_tool_names` (`agent_tools.py:431-435`): `if not self._code_mode_active or not self._allow_deferral: return frozenset()`.
- Não alterar `is_read_only`, `is_mutating`, `_active_invocation_id`.

#### `src/agentos/agentic/read_state.py`
- `copy_read_state`: iterar sobre um snapshot `dict(state)` (cópia atômica sob o GIL) em vez de `state.items()` ao vivo.

#### `src/agentos/agentic/session.py`
1. **Isolamento de todos (T1)**
   - `_toolset(..., todos_state: list[dict[str, Any]] | None = None)`; na construção (`session.py:5019`): `todos_state=self._todos_state if todos_state is None else todos_state`.
   - Chamada do subagente (`session.py:3652-3669`): `todos_state=[]` (inclusive `fork`: checklist própria, vazia).
   - `emit_lifecycle` (`session.py:1893-1900`): só registrar `CODE_MODE_STAGE_CHANGED` para `todo_write/task_create/task_update` quando o ator for o agente principal (`actor == self.main_agent_id`; confirmar nome da variável no método). Para subagentes, não emitir o evento de estágio (o `tool.finished` do subagente continua sendo emitido normalmente, com seu `agent_id`/`parent_agent_id`).
   - Confirmar que eventos de ferramenta de subagente carregam `parent_agent_id` (usado pelo frontend no WS-G); se não carregarem, adicionar no ponto em que `emit_lifecycle` resolve o pai (`_parent_of`).
2. **Promoção conjunta da onda (P10)** em `_run_foreground_subagent_watching_queue` (`session.py:3132-3302`):
   - `self._fg_promotion_seq = 0` no `__init__` (perto de `session.py:1545`).
   - Na entrada do método: `start_seq = self._fg_promotion_seq`.
   - Extrair o bloco repetido de registro em background (`BackgroundAgentEntry` + `self._background_subagents[...]` + `ToolOutcome`) para uma função local `_promote(extra_text: str) -> ToolOutcome` (hoje duplicado em 3210-3251 e 3255-3299).
   - No laço, antes de `_has_queued_user_input()`: se `self._fg_promotion_seq != start_seq` → sob `promote_lock`, se não concluiu, `promoted.set()` e `return _promote(" The user sent a message; it was delivered with another subagent's result.")`.
   - Quando esta thread reivindica textos: `with self._subagent_lock: self._fg_promotion_seq += 1` antes de retornar.
   - Edge: subagentes iniciados depois da reivindicação têm `start_seq` novo e não são promovidos.
3. **Multi-espera** em `_check_subagent` (`session.py:4226-...`):
   - Assinatura: `_check_subagent(self, agent_id: str = "", *, wait_seconds: int = 0, caller=None, agent_ids: Sequence[str] | None = None)`.
   - Refatorar a resolução de uma entrada (registro local → `BACKGROUND_AGENTS` → registro em disco) para `_resolve_subagent_entry(agent_id, caller)`.
   - Com `agent_ids`: recusa de posse por id; futures vivos → `concurrent.futures.wait(futures, timeout=wait_seconds, return_when=ALL_COMPLETED)` **uma vez**, dentro de `with self._lend_slot(caller)`; depois montar o resultado chamando o caminho de um id com `wait_seconds=0` para cada id. Conteúdo: seções `## <nome> (agent_id="...") — <status>` + relatório; payload `{"agents": [...], "status": "running" se algum ainda roda senão "completed", "tool_kind": "agent", "background": True}`; `status` do `ToolOutcome` = `"failed"` só se todos falharam.
4. **Coerência de deferral (C6)**:
   - Helper `_tool_deferral_allowed(provider: str | None) -> bool`: resolve o adapter CLI do provedor no registro `CLI_PROVIDERS` (mesmo registro usado em `tests/unit/providers/test_cli_registry.py:651`); retorna `False` só quando `capabilities.native_tool_loop and not capabilities.tool_list_changed`; provedores não-CLI → `True`.
   - Depois de criar o toolset principal (`session.py:6420`): `toolset.set_allow_deferral(self._tool_deferral_allowed(self.turn.get("provider")))` **antes** de montar `CodePromptOptions` (`session.py:6002-6012`), que já lê `toolset.deferred_tool_names`.
   - Subagente (`session.py:3652`): `subagent_tools.set_allow_deferral(self._tool_deferral_allowed(provider_name))`.
5. **Texto de orientação** em `session.py:638` (modo chat, nomes internos): acrescentar que vários `run_subagent` independentes na mesma resposta rodam em paralelo e que `check_subagent(agent_ids=[...], wait_seconds=...)` aguarda vários de uma vez.

### WS-C — `native_loop` e prompt do Code Mode

#### `src/agentos/providers/claude_cli/native_loop.py`
1. **`isolated_cli_note(server_name, *, permission_mode=None, workspace_root=None, read_only=False, adapter_name: str | None = None)`** (`native_loop.py:98-139`) e `with_isolated_cli_note` (`native_loop.py:142-...`): repassar `adapter_name`. O texto atual **não muda** (testes existentes); seções novas são **anexadas** ao final, estáveis por adapter (cache de prompt):
   - `adapter_name == "codex"`:
     ```
     ## Codex functions.exec
     - Orin's tools are already loaded: call them as tools.mcp__<srv>__<Name>(args) and list them with ALL_TOOLS. Do not call ToolSearch for Read, Glob, Grep, ListFiles, ViewFile, Bash, Edit, Write, Agent or Skill.
     - To see an image returned by ViewFile or Read, hand its blocks to the model: for (const b of r.content ?? []) { if (b.type === "image") image(b); else if (b.type === "text") text(b.text); } (or content(r.content)). Never text(r): that turns the picture into truncated base64 text.
     - Independent calls run in parallel only when awaited together: await Promise.all([...]) — for example several Agent subagents or several reads.
     ```
     (O laço `image(b)` é o padrão comprovado no rollout de 2026-11-09; `content()` fica como alternativa.)
   - `adapter_name == "antigravity"`:
     ```
     ## Parallel subagents on this CLI
     This CLI runs tool calls one at a time, so two Agent (run_subagent) calls in one response still run one after the other. To run subagents at the same time, call Agent with run_in_background=true for each one (each returns at once with an agent_id), then wait for all of them with CheckSubagent (check_subagent) passing agent_ids=[...] and wait_seconds, or, as the main agent, end your message: Orin waits for background subagents and gives you their reports in this same turn.
     ```
   - Claude: sem seção nova (o despacho paralelo é nativo e o system prompt já orienta).
   - Chamada em `start_native_turn_for_provider` (`native_loop.py:1434-1441`): `adapter_name=str(getattr(adapter, "name", "") or "")`.
2. **Política de slot** em `_SlotReleasingExecutor.call_tool` (`native_loop.py:402-410`) e `NativeTurnHandle._before_tool_call/_after_tool_call` (`native_loop.py:511-576`):
   - `release = getattr(self._inner, "releases_cli_slot", None)`; `should_release = bool(release(request)) if callable(release) else True`.
   - `_before_tool_call(release: bool = True)`: sempre incrementa `_active_tool_calls`; só solta ticket/slot se `release` e se segura o slot.
   - `_after_tool_call()`: lógica atual (só readquire se `not self._holds_slot` e `_active_tool_calls == 0`) — chamadas que não soltaram não provocam reaquisição.
   - Invariantes: `in_flight` (`native_loop.py:502-506`) continua contando todas as chamadas; chamadas mistas (uma solta, outra não) terminam com o slot readquirido uma única vez.

#### `src/agentos/code_mode/system_prompt.py` (`system_prompt.py:105-106`)
- Substituir a linha de `run_subagent` por:
  `f"- Delegate broad searches or independent parts of a large task with {t('run_subagent')}. Several {t('run_subagent')} calls in the same response run in parallel; to launch them without waiting, pass run_in_background and later wait for all of them with {t('check_subagent')} (agent_ids). A subagent does not see this conversation unless it is a fork, so give it everything it needs."`
- Usar `t('check_subagent')` só se `has("check_subagent")`; senão manter a frase sem a parte de espera.

### WS-D — Catálogo Codex e visão

#### `src/agentos/providers/codex_cli/catalog.py`
- `codex_input_modalities(model_id: str | None, raw: Any = None) -> tuple[str, ...]`:
  - `codex-image` → `("text",)`.
  - `raw` sequência válida → normalizar (`lower/strip`, só `text`/`image`, `text` sempre presente, ordem `("text","image")` quando houver imagem).
  - Caso contrário → `("text", "image")`.
- `FALLBACK_CODEX_MODELS`: `input_modalities=("text","image")` em todos.
- `discover_codex_models`: `input_modalities=codex_input_modalities(slug, entry.get("input_modalities"))`.
- Exportar em `__all__`.

#### `src/agentos/provider_catalog/service.py`
- `_codex_modalities_are_stale(items)` espelhando `service.py:462-472` (ignora `is_custom`, só linhas com `"text"` em `output_modalities`).
- `_try_auto_refresh` (`service.py:364-372`): ramo `elif normalized_provider == "openai-codex": if current and not _codex_modalities_are_stale(current): return True`.
- `list` (`service.py:393-398`): ramo equivalente para `openai-codex` com refresh quando houver linhas obsoletas.
- `add_custom` (`service.py:441-445`): `openai-codex` → `codex_input_modalities(normalized_id)`.
- Não alterar `service.py:190` (fallback genérico).

#### `src/agentos/workers/chat.py`
- `_model_sees_images` (`chat.py:1062-1072`): se `turn["provider"] == "openai-codex"`, unir `names` com `codex_input_modalities(model_id)` antes da decisão (defesa contra linha ainda não curada). Garantir que `codex-image` continua `False`.
- `_vision_reader_factory.build()` (`chat.py:1167-1188`): descartar candidatos e override cujo provedor esteja em `PROVIDERS_WITHOUT_INLINE_IMAGES = frozenset({"google-antigravity", "openai-codex"})` (constante em `reading/selection.py`, junto de `choose_vision_model`). Override incompatível → ignorado (cai na escolha automática); sem candidatos → `None` (o `read.py` já devolve `_no_visual_reading`).

#### `src/agentos/reading/vision.py`
- Depois de juntar o texto: se contiver a nota de mídia omitida (importar `CODEX_OMITTED_MEDIA_NOTE` de `codex_cli/command.py`, a nota equivalente de `antigravity_cli/command.py` — **não** de `antigravity_cli/catalog.py` — e o prefixo `"[Imagem/mídia"`), `raise VisionUnavailable("the visual reading model did not receive the image")`.

#### `src/agentos/agentic/tools/files/read.py`
- `public_description` de `Read` e `ViewFile` (`read.py:842-850`): acrescentar "For an image the result's content holds an image block (type "image")." Não mudar `output_model` nem o comportamento de `_view_image`.

### WS-E — ToolSearch e ProjectMap

#### `src/agentos/agentic/tools/mcp/admin.py` (`search_mcp_tools`, `admin.py:206-308`)
- No modo `select:`, para cada nome em `already_available`, montar um descritor nativo a partir de `self.definitions()` com nome e schema **públicos** quando o toolset usa nomes públicos (reaproveitar o tradutor de nomes já usado para construir `native_engine`) e anexar a `tools_payload` com `"tool_kind": "native", "already_available": True`. Não chamar `discover` para eles.
- Incluir no `payload` (e portanto no `content` JSON devolvido ao modelo) as chaves `"already_available"` e `"not_found"`.
- Se `getattr(self, "_allow_deferral", True)` for `False` e alguma ferramenta MCP externa tiver sido ativada, acrescentar `"note": "This client cannot reload its tool list during the turn: call these tools with CallMcpTool(server=..., tool=..., arguments={...})."` — somente depois de confirmar (grep) que a ferramenta `call_mcp_tool`/`CallMcpTool` está disponível nesse toolset; se não estiver, não emitir a nota.

#### `src/agentos/agentic/tools/orin/search_code.py`
- `_index_building_note` (`search_code.py:158-170`): passar o texto por `public_text(...)` (`tools/public_names.py:177`) quando o toolset usa nomes públicos.
- `project_map` (`search_code.py:74-107`): quando o índice está vazio, anexar um fallback com até 40 entradas de topo do workspace (profundidade 2, ignorando `.git`, `node_modules`, `.orin`, `.venv`), usando o helper de listagem já existente do workspace/ferramenta `list_files` (não reimplementar travessia; respeitar a mesma resolução de caminho e limites de workspace).

### WS-G — Frontend

#### `frontend/src/features/conversations/activityReducer.ts`
- Em `applyEvent` (`activityReducer.ts:216`) e nos laços de `snapshot`/`reconcile_snapshot` (`activityReducer.ts:244-255, 274-...`): só aceitar `event.todos` quando `!event.parentAgentId`. `settleTodos` só por desfecho de turno de evento sem `parentAgentId`.

#### `turnTimelineFold.ts` (`~399-570`) e `activitySummary.ts` (`~149-311`)
- Não limpar `subagentWaveItems`/`subagentWaveGroups` em eventos do agente principal que sejam de coordenação/espera: ferramenta `check_subagent`/`CheckSubagent`, lifecycle `waiting_subagents`/`waiting_model`, deltas de pensamento, `context.updated`. Limpar apenas com texto visível do principal ou ferramenta não-coordenação do principal.

#### `TurnTimeline.tsx` (`~880-929, 1250-1317`)
- Criar o `SubagentTaskContainer` em estado `running` ("Iniciando subagente…", 0 ações) no `agent.message_sent` com `to_agent_id` (ou no `turn.started` do subagente), sem esperar a 1ª tool call. Quando a 1ª tool call chegar, o mesmo card é reutilizado (chave = id do subagente) — sem card duplicado.

### WS-F (Fase 2) — `--image` no Codex para anexos do usuário

#### `src/agentos/providers/cli_adapter.py`
- `CliTurnRequest.image_paths: tuple[Path, ...] = ()`.

#### Novo `src/agentos/providers/codex_cli/attachments.py`
- `validated_codex_images(paths: Sequence[str | Path], workspace_root: Path, *, max_images: int = 8, max_bytes: int = 20 * 1024 * 1024) -> tuple[Path, ...]`:
  - `Path(workspace_root, p)` se relativo; `resolve(strict=True)`; deve estar dentro de `workspace_root.resolve()` (`is_relative_to`; `WindowsPath` compara sem diferenciar maiúsculas; `resolve` segue junções/symlinks, então um link que aponta para fora é rejeitado).
  - Rejeitar: não-arquivo, extensão fora de `{.png,.jpg,.jpeg,.gif,.webp}`, tamanho > `max_bytes`, caracteres de controle, nome começando com `-`, **vírgula no caminho** (o clap do Codex declara `value_delimiter=','` para `--image`).
  - Silenciosamente pula inválidos (o anexo continua listado no texto da mensagem e acessível via `ViewFile`); deduplica; limita a `max_images`.

#### `src/agentos/providers/codex_cli/command.py` (`build_codex_command`, `command.py:455-586`)
- Logo após `cmd = [binary, "exec"]` e, se houver, `["resume", resume_id]`, emitir para cada caminho `["--image", str(path)]` — **antes** de `--json`, para que o próximo token sempre comece com `--` e o clap nunca engula o `-` final como imagem.
- Não emitir nada para o modelo `codex-image`.
- Validar de novo com `_validate_path_arg` (defesa em profundidade).

#### `src/agentos/agentic/native_turn.py` e `src/agentos/providers/claude_cli/native_loop.py` (plumbing apenas)
- `NativeTurnRunner`: na **primeira** criação de processo que entrega a nova mensagem do usuário (turno novo ou `resume` da nova mensagem), obter `store.attachments_for_turn(turn)` (`conversations/chat.py:3276`), filtrar `kind == "image"` e repassar os caminhos no pedido nativo (campo novo `user_image_paths`). Nunca nas re-criações internas do mesmo turno (continuação da barreira de background `native_turn.py:2648-2660`, desvio de builtin, retry).
- `start_native_turn_for_provider`: se `adapter.name == "codex"` e o modelo enxerga imagens, `image_paths=validated_codex_images(request.user_image_paths, workspace_root)` no `CliTurnRequest` (`native_loop.py:1483-1507`).
- Verificação obrigatória durante a implementação: rodar `codex exec --help` e `codex exec resume --help` no binário instalado e confirmar `--image` em ambos; se o `resume` não aceitar, emitir `--image` só em turnos novos.

---

## 6. Plano de testes automatizados

### 6.1 Testes existentes que precisam de ajuste (mudança de contrato intencional)
| Teste | Ajuste |
|---|---|
| `tests/unit/agentic/test_native_turn.py:1080-1083` | `readOnlyHint == (is_read_only(name) or is_concurrency_safe(name))`; adicionar `assert by_name["Agent"]["annotations"]["readOnlyHint"] is True` e manter `Edit` `False` |
| `tests/unit/providers/test_cli_registry.py:678` | Codex: todos os modelos de chat com `("text","image")`; `codex-image` (se listado) com `("text",)`. Comentário com a mesma justificativa do agy (imagem em resultado MCP chega ao modelo) |
| Testes de schema de `check_subagent` (se algum exigir `required == ["agent_id"]`) | `required == []` + `agent_ids` no schema |

### 6.2 Novos testes por WS

**WS-A**
- `test_native_turn_agent_lane.py`
  - 3 `submit(..., lane="agent")` em threads com atrasos de 0, 150 ms e 400 ms (> janela) contra um `run_batch` falso que bloqueia num `Event`: as 3 entram em `run_batch` antes de o evento ser liberado (contador chega a 3; `Barrier(3, timeout=2)`).
  - Escrita submetida enquanto 2 chamadas `agent` rodam só entra depois de ambas terminarem; chamada `agent` que chega com escritor esperando aguarda o escritor (preferência de escritor) e depois roda.
  - Lote de leitura roda **junto** com uma chamada `agent` em andamento; dois lotes de leitura continuam um por vez.
  - Abandono: chamada `agent` esperando a faixa com `cancelled.set()` retorna `ABANDONED_BEFORE_START` sem executar.
  - `running_call_abandoned()` dentro de um lote só considera as entradas desse lote (ContextVar), inclusive em thread do pool com `copy_context()`.
  - Regressão do achado de sessão: duas `check_subagent(wait_seconds=1)` separadas por 300 ms terminam em ~1 s, não ~2 s (tolerância: total < 1,8 s).
- `test_run_toolset_reentrant.py`
  - Duas chamadas `runtime._run_toolset` sobrepostas (cada uma com uma ferramenta de delegação falsa que bloqueia num `Event`) completam, com `_consecutive_tool_failures`/`counters` consistentes e sem `RuntimeError: dictionary changed size`.
  - `ask_agents` entra no pool (elegível).
  - Leitura feita durante um `run_subagent` mutante não é reaproveitada do cache depois que ele termina (incremento de `_writes` no fim).
- `test_turn_tool_server.py`: `Agent`/`CheckSubagent` com `readOnlyHint: true`; `Write`/`Edit`/`Bash` com `false`.
- `test_native_turn.py`: `releases_cli_slot` — `True` para `run_subagent` foreground, `ask_agent(s)`, `check_subagent(wait_seconds>0)`; `False` para `run_subagent(run_in_background=True)`, teammate, `Read`, `Bash`, `check_subagent(wait_seconds=0)`.
- `test_native_turn.py`: `messages` mantém pares `assistant tool_calls` + `tool results` adjacentes com 3 lotes `agent` concorrentes; `_life("running")` emitido uma vez ao fim.

**WS-B**
- `test_subagent_todos_isolation.py`: `todo_write` no toolset de um subagente não altera `session._todos_state` nem o `_todos` do toolset principal; o lembrete do pai (`runtime.py:507-512`) só lista os itens do pai; `CODE_MODE_STAGE_CHANGED` não é registrado para ator-subagente; o principal continua emitindo.
- `test_check_subagent_many.py`: 3 futures falsos registrados (2 terminam, 1 roda) → uma chamada com `agent_ids` e `wait_seconds=1` espera uma única vez (~1 s), retorna as 3 seções e `status: running`; todos terminados → `completed`; id de outro dono → recusa por id; nenhum id → erro de validação; >16 ids → truncado/erro conforme especificado.
- `test_foreground_wave_promotion.py`: dois `_run_foreground_subagent_watching_queue` em threads; enfileirar 1 mensagem do usuário → ambos retornam `promoted_to_background: True` em < 1 s; o texto do usuário aparece em exatamente um resultado; um terceiro iniciado depois não é promovido.
- `test_tool_deferral_coherence.py`: provedor `openai-codex` → `deferred_tool_names == frozenset()` e o prompt do Code Mode não contém "load on demand" das ferramentas do núcleo; provedor `anthropic` (API) → comportamento atual; subagente `codex` com principal `claude` recebe o flag do próprio provedor.
- `copy_read_state` com outra thread inserindo chaves em laço não levanta exceção.
- Rodar também: `test_parallel_and_background_subagents.py`, `test_nested_subagents.py`, `test_subagent_user_controls.py`, `test_subagents_and_permissions_v076.py`, `test_todos_survive_turn_end.py`, `test_claude_code_tools_parity.py`, `tests/unit/test_orin_code_phase_c.py`.
- **Invariante de plan_mode** (novo caso em `test_subagents_and_permissions_v076.py` ou arquivo próprio): em `plan_mode`, `run_subagent` `general` continua com a mesma decisão de permissão de antes (o `concurrency_safe` não é consultado pelo broker).

**WS-C**
- `test_isolated_cli_note_adapters.py`: `adapter_name="codex"` contém `functions.exec`, `image(b)`, `Promise.all` e "Do not call ToolSearch"; `"antigravity"` contém `run_in_background=true` e `agent_ids`; `None`/`"claude"` == texto atual byte a byte; a nota do agy **não** menciona `functions.exec` e a do Codex **não** menciona `read_file` nativo.
- `test_slot_release_policy.py`: executor falso com `releases_cli_slot` → `False` não solta nem readquire (`CliSlotQueue(limit=1)` com ticket do pai preservado); `True` solta e readquire; sem o método → comportamento atual. Cenário P11: `limit=2`, pai lança 2 filhos com `run_in_background` (que tomam slots) → a chamada do pai retorna sem bloquear.
- Rodar: `test_native_loop.py`, `test_native_loop_one_shot.py`, `test_cli_queue.py`, `redteam/test_fase3_group4_cli_queue_and_ui.py`, `test_subagent_same_freedom.py`.

**WS-D**
- `providers/codex_cli/test_catalog.py`: `models_cache.json` com `["text","image"]` → `("text","image")`; sem o campo → `("text","image")`; `codex-image` → `("text",)`; fallback inteiro com imagem.
- `test_codex_modalities_autoheal.py`: linhas `openai-codex` gravadas com `["text"]` → `list()` dispara refresh e passa a devolver `("text","image")`; linha custom não dispara refresh; `add_custom` Codex grava imagem.
- `workers/test_vision_reader_factory.py`: override `google-antigravity` ou `openai-codex` é ignorado; só candidatos compatíveis são escolhidos; `_model_sees_images` → `True` para `openai-codex/gpt-6.1-sol` com linha obsoleta `["text"]`, `False` para `codex-image`.
- `reading/test_vision.py`: transporte falso que responde com a nota "[Imagem/mídia omitida…]" → `VisionUnavailable`.
- `agentic/test_view_file_vision.py`: turno `openai-codex` → `ViewFile`/`Read` de PNG retornam `images=[{type:"image"}]` e nunca chamam o leitor visual.
- Rodar: `test_catalog_vision.py` (agy, sem alteração), `test_catalog_store.py`, `test_catalog_contracts.py`, `api/test_vision_model_routes.py`, `tests/unit/providers/test_cli_registry.py`.

**WS-E**
- `tools/test_tool_search.py`: `select:Skill,Glob,Agent` com `Glob`/`Agent` carregados → `tools` contém os 3 com schema, `Glob`/`Agent` com `already_available: true`; `content` JSON inclui `already_available` e `not_found`; com `_allow_deferral=False` e MCP externo ativado → nota de `CallMcpTool` (se disponível).
- `test_project_map_fallback.py`: índice vazio → saída lista entradas de topo e cita `Glob`/`Grep` (nomes públicos) no Code Mode e `glob_files/search_files` fora dele.
- Rodar: `test_native_turn_deferred_tools.py`, `retrieval/test_project_map_*`.

**WS-G**
- `activityReducerSubagentTodos.test.ts`: evento com `todos` e `parentAgentId` não altera `state.todos` (live, snapshot e reconcile); desfecho de turno de subagente não "assenta" os todos do pai; evento do principal continua atualizando.
- `TurnTimeline.parallelSubagents.test.tsx`: 3 `agent.message_sent` seguidos → 3 cards "em execução" imediatamente dentro de `ParallelSubagentsGroup` ("3 subagentes em paralelo"); intercalar `check_subagent` do principal, `waiting_subagents` e delta de pensamento não fragmenta o grupo; texto visível do principal encerra a onda.
- Rodar: `ParallelSubagentsCount.test.tsx`, `ParallelSubagentsAndSessions.test.tsx`, `SubagentCardOutcome.test.tsx`, `TurnTimelineCollapsibleAndText.test.tsx`, `TurnTimelineExtensions.test.tsx`, `processExitedActivity.test.tsx` + typecheck do frontend.

**WS-F**
- `test_image_attachments.py`: dentro do workspace (com espaço e acento no nome, ex.: `portfólio/Imagem do ChatGPT (2).png`) → aceito; fora do workspace / via junção para fora / `..` / vírgula / começa com `-` / extensão `.svg` / > 20 MB / inexistente → rejeitados; dedupe e limite.
- `test_command.py`: `--image <abs>` aparece logo após `exec` (ou após `resume <id>`), antes de `--json`; o token seguinte a cada caminho começa com `--`; o último token continua `-`; nenhum `--image` para `codex-image` nem com `image_paths=()`.
- Teste de plumbing no `test_native_turn.py`: `user_image_paths` só na primeira criação de processo da mensagem; ausente na continuação pós-barreira.

### 6.3 Comandos de verificação final (Fase 3)
```bash
./.venv/Scripts/python.exe -m pytest tests/unit -q
./.venv/Scripts/python.exe -m pytest tests/integration -q   # testes opcionais de Postgres pulam sem banco
cd frontend && NODE_OPTIONS=--no-experimental-webstorage npx vitest run
git status --short   # catalog.py do agy continua " M" e intocado
```

---

## 7. Verificação E2E manual (Fase 3, com os 3 CLIs reais no Windows)

1. **Claude principal**: pedir 3 tarefas independentes → 3 `Agent` na mesma resposta; nos eventos, os `tool.started` dos 3 subagentes se sobrepõem no tempo; UI mostra "3 subagentes em paralelo" desde o spawn.
2. **Codex principal**: idem; confirmar no rollout (`~/.codex/sessions/...`) que o modelo usou `Promise.all` e que os `tools/call` chegaram juntos.
3. **agy principal**: confirmar `run_in_background=true` ×3 + `CheckSubagent(agent_ids=[...])` (ou barreira de fim de turno) e execução sobreposta.
4. **TodoWrite**: subagente com checklist própria → TodoBar e lembretes do principal intactos.
5. **CheckSubagent**: duas esperas de 60 s em paralelo terminam em ~60 s.
6. **Codex + imagem**: anexar PNG (nome com espaço/acento) → modelo descreve sem tool call (Fase 2); pedir `ViewFile` de outra imagem do workspace → modelo a vê (sem "imagem omitida"); subagente `codex` idem.
7. **Codex + ToolSearch**: o modelo não gasta rodada com `ToolSearch` para ferramentas do núcleo; `ProjectMap` em repositório recém-clonado lista arquivos.
8. Usuário escreve durante 2 subagentes foreground → ambos promovidos e a mensagem é respondida na hora.

---

## 8. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Corrida nova por `_run_toolset` sobreposto | Lock de estado de instância nas fases de preparo/montagem; ordem de locks fixa; teste dedicado de reentrância; leituras entre si continuam serializadas; slot `_native_message_first_read_keys` nunca tocado por lotes `agent` |
| Inanição de escritor | Preferência de escritor no `_LaneGate` |
| Prompt do Codex mudar o comportamento de modelos que já funcionavam | Seções anexadas apenas para `codex`/`antigravity`; texto base inalterado (teste byte a byte) |
| Codex `--image` engolir o `-` ou dividir caminho por vírgula | Posição antes de `--json`, rejeição de vírgula, teste de argv, verificação no `--help` real |
| Auto-refresh do catálogo em loop | `_codex_modalities_are_stale` ignora linhas custom; backoff existente em `_try_auto_refresh` |
| Usuário com override de visão incompatível | Override ignorado com fallback automático; mensagem existente de "sem leitura visual" quando não houver candidato |
| Rollback | Cada WS é um commit isolado; reverter WS-A sozinho volta ao `_exec_lock` sem afetar B–G (os consumidores usam `getattr` com fallback) |

---

## 9. Fora de escopo / adiado (com motivo)

- `write_claims` com dono por agente (P3): não causa os sintomas; muda semântica entre conversas.
- `existing_count` sob `_subagent_lock`: sem evidência; seguraria lock durante I/O.
- `features.mcp_tool_list_changed=true` no Codex: experimento futuro, após validar no binário (feature "under development").
- Mensagens `<multi_agent_role>`/`<skills_instructions>` injetadas pelo Codex 0.167: investigar à parte.
- `TaskOutput`(bloqueante) para agentes na faixa `agent`: avaliar depois de medir uso real.

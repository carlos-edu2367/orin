# Trilha A — Assertividade do runtime agêntico: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para executar tarefa a tarefa. Os passos usam checkbox (`- [ ]`).

**Goal:** Fazer o agente conservar o que descobriu entre turnos, planejar antes de agir, ver só as ferramentas da fase atual e usar a janela real do modelo — com a queda no número de chamadas de ferramenta medida contra uma linha de base.

**Architecture:** Três conceitos novos no kernel do turno — `TurnTranscript` (durável), `PhaseController` (fases determinísticas) e `TaskContract` (plano pinado) — mais duas correções de parametrização (janela real, compactação estruturada). Tudo degrada para o comportamento atual em caso de falha.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x, Alembic, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-orin-trilha-a-assertividade-design.md`

## Global Constraints

- Python `>=3.13`; SQLAlchemy `>=2.0,<3`; Alembic `>=1.14,<2`; pydantic `>=2.9,<3`.
- Migrations são aditivas: sem alteração de tabela existente, sem backfill.
- `MAX_TOOL_RESULT_CHARS = 12_000` já limita todo resultado de ferramenta (`src/agentos/agentic/agent_tools.py:44`). O teto do transcript é rede de segurança acima disso, não o limite operativo.
- A garantia de reconciliação de efeitos (`EFFECT_RECONCILIATION_REQUIRED`) é invariável: nenhuma tarefa altera quando `reconciliation_required` é consultado nem o que ele produz.
- Toda peça nova degrada para o comportamento atual em falha; nenhuma delas pode encerrar um turno.
- O `num_ctx` do Ollama (`_num_ctx_for`) não é liberado junto com a janela: o custo ali é VRAM real na máquina do usuário.
- Textos voltados ao usuário em pt-BR; identificadores e comentários de código em inglês, seguindo o repositório.

---

## Ordem e checkpoints

Tarefas 1–2 são a linha de base e **precisam estar em `main` antes** de qualquer mudança de comportamento, para que o bench tenha contra o que comparar. Tarefas 3–4 são correções isoladas e de baixo risco. Tarefa 5 é a correção da causa raiz. Tarefas 6–7 dependem de 5.

| tarefa | entrega | depende de |
|---|---|---|
| 1 | `turn_quality_metrics` gravando e agregando | — |
| 2 | `cached_input_tokens` lido dos providers | 1 |
| 3 | janela real do modelo | — |
| 4 | compactação estruturada e marcador honesto | — |
| 5 | transcript durável e reidratação | — |
| 6 | `write_contract` e contrato pinado | 5 |
| 7 | fases, toolset por fase e orçamento por fase | 6 |
| 8 | bench de referência | 1 |

---

### Task 1: Métricas de qualidade por turno

**Files:**
- Create: `src/agentos/persistence/postgres/migrations/versions/0041_turn_quality_metrics.py`
- Create: `src/agentos/agentic/quality.py`
- Modify: `src/agentos/persistence/postgres/schema.py` (tabela `turn_quality_metrics`)
- Modify: `src/agentos/agentic/runtime.py` (contagem no loop; `_succeeded_signatures` espelhando `_failed_signatures:127`)
- Modify: `src/agentos/conversations/chat.py` (`record_quality`)
- Modify: `src/agentos/api/gateway.py` (`GET /agent-runtime/quality`)
- Test: `tests/unit/agentic/test_turn_quality_metrics.py`

**Interfaces:**
- Produces: `TurnQualityCounters` — dataclass mutável com `tool_calls: int`, `redundant_tool_calls: int`, `iterations: int`, `input_tokens: int`, `output_tokens: int`, `cached_input_tokens: int | None`; método `note_call(name: str, arguments: Mapping[str, object], status: str) -> None`.
- Produces: `ChatApplication.record_quality(turn, *, counters: TurnQualityCounters, outcome: str, error_code: str | None, duration_ms: int) -> None`.

- [ ] **Step 1:** Escrever `tests/unit/agentic/test_turn_quality_metrics.py` cobrindo: a primeira chamada com uma assinatura não conta como redundante; a segunda chamada bem-sucedida com a mesma assinatura conta; uma repetição de chamada que **falhou** não conta (esse caminho já é barrado por `_failed_signatures`); `cached_input_tokens` permanece `None` quando nenhum evento de usage o reporta.
- [ ] **Step 2:** Rodar e ver falhar por `ModuleNotFoundError: agentos.agentic.quality`.
- [ ] **Step 3:** Criar `quality.py` com `TurnQualityCounters`. A assinatura reusa `AgenticTurnRuntime._signature` (`runtime.py:663`) para ficar idêntica à do dedup de falhas.
- [ ] **Step 4:** Adicionar a tabela em `schema.py` e a migration `0041`, `down_revision = "0040_execution_recovery_journal"`.
- [ ] **Step 5:** Instrumentar `AgenticTurnRuntime.run`: instanciar os contadores, alimentar em `_run_toolset` e no bloco de usage, e chamar `record_quality` nos quatro terminais (`_fail`, `_cancel`, `completed`, `waiting_user`).
- [ ] **Step 6:** Expor `GET /agent-runtime/quality` agregando por `(provider, model_id)` numa janela de dias.
- [ ] **Step 7:** Rodar `pytest tests/unit/agentic -q` e a suíte de persistência.
- [ ] **Step 8:** Commit `feat(runtime): measure per-turn tool efficiency`.

---

### Task 2: Tokens de cache reportados pelo provider

**Files:**
- Modify: `src/agentos/providers/models.py` (`ProviderUsage.cached_input_tokens`)
- Modify: `src/agentos/agentic/provider_stream.py` (`_usage`, leitura Anthropic e OpenAI)
- Test: `tests/unit/agentic/test_provider_stream_payload.py` (estender)

**Interfaces:**
- Consumes: `TurnQualityCounters` da Task 1.
- Produces: `ProviderUsage.cached_input_tokens: int | None`.

- [ ] **Step 1:** Teste: um evento Anthropic com `usage.cache_read_input_tokens: 900` produz `cached_input_tokens == 900`; um payload OpenAI com `prompt_tokens_details.cached_tokens: 120` produz `120`; um payload sem nenhum dos dois produz `None`.
- [ ] **Step 2:** Rodar e ver falhar.
- [ ] **Step 3:** Adicionar o campo opcional em `ProviderUsage` (default `None`, para não quebrar nenhum construtor existente) e lê-lo nos dois formatos.
- [ ] **Step 4:** Ligar ao contador da Task 1.
- [ ] **Step 5:** Rodar `pytest tests/unit/agentic -q`.
- [ ] **Step 6:** Commit `feat(providers): report cached input tokens`.

---

### Task 3: Janela de contexto real do modelo

**Files:**
- Modify: `src/agentos/workers/chat.py:657` (`_max_context_tokens_for`) e o bloco de constantes em `:94`
- Test: `tests/unit/agentic/test_context_window.py` (estender)

**Interfaces:**
- Produces: `_max_context_tokens_for(turn) -> int` sem o teto de 60k; `_context_reserve_for(window: int) -> int` = `min(64_000, max(12_000, ceil(window * 0.10)))`.

- [ ] **Step 1:** Testes: janela de 200.000 devolve 180.000; janela de 1.000.000 devolve 936.000; janela de 8.192 devolve `MIN_MAX_CONTEXT_TOKENS`; catálogo sem janela devolve `DEFAULT_MAX_CONTEXT_TOKENS`; e `_num_ctx_for` **não** muda para nenhum desses casos.
- [ ] **Step 2:** Rodar e ver falhar nos dois primeiros.
- [ ] **Step 3:** Remover o `min(DEFAULT_MAX_CONTEXT_TOKENS, ...)` e introduzir a reserva proporcional. `DEFAULT_MAX_CONTEXT_TOKENS` passa a valer só para janela desconhecida.
- [ ] **Step 4:** Rodar `pytest tests/unit/agentic/test_context_window.py -q`.
- [ ] **Step 5:** Commit `fix(runtime): use the model's real context window`.

---

### Task 4: Compactação estruturada e marcador honesto

**Files:**
- Modify: `src/agentos/agentic/runtime.py` (`_request_compaction_summary:463`, `_fallback_compaction_summary:474`, `_maybe_compact:402`, marcador em `_request_messages:499`)
- Test: `tests/unit/agentic/test_structured_compaction.py`

**Interfaces:**
- Produces: `COMPACTION_SECTIONS: tuple[str, ...]` = `("Arquivos tocados", "Decisões", "Dados apurados", "Pendências")`.

- [ ] **Step 1:** Testes: o resumo produzido contém as quatro seções; o fallback também; nem o cabeçalho do resumo nem o marcador de corte contêm as palavras que instruem reexecução (`re-read`, `re-run`, `confirmar detalhes`).
- [ ] **Step 2:** Rodar e ver falhar.
- [ ] **Step 3:** Trocar o prompt do sumarizador para pedir as seções; reescrever o fallback para preenchê-las; trocar os dois textos.
- [ ] **Step 4:** Rodar `pytest tests/unit/agentic -q` — em especial `test_agentic_runtime_loop.py`, que já cobre compactação.
- [ ] **Step 5:** Commit `fix(runtime): make compaction preserve facts instead of inviting rework`.

---

### Task 5: Transcript de turno durável

**Files:**
- Create: `src/agentos/persistence/postgres/migrations/versions/0042_conversation_turn_steps.py`
- Modify: `src/agentos/persistence/postgres/schema.py` (`conversation_turn_steps`)
- Modify: `src/agentos/conversations/chat.py` (`record_step`, `turn_steps`, `history_for_turn:516`)
- Modify: `src/agentos/agentic/runtime.py` (chamar `record_step` onde já monta as mensagens)
- Modify: `src/agentos/workers/chat.py` (`_RuntimeStore` repassa `record_step`)
- Test: `tests/unit/agentic/test_turn_transcript.py`, `tests/integration/test_turn_continuity.py`

**Interfaces:**
- Produces: `ChatApplication.record_step(turn, *, kind, agent_id, payload, tool_name=None, tool_call_id=None) -> None`.
- Produces: `ChatApplication.turn_steps(conversation_id, *, before_turn_id, token_budget) -> list[dict]`.
- Produces: `REHYDRATION_BUDGET_FRACTION = 0.40`.

- [ ] **Step 1:** Testes unitários: ordem de sequência preservada; `content_bytes` guarda o tamanho original quando `truncated`; a reidratação nunca devolve um `tool_result` sem o `tool_call` correspondente; o orçamento é respeitado; conversa sem passos devolve exatamente o histórico atual.
- [ ] **Step 2:** Rodar e ver falhar.
- [ ] **Step 3:** Tabela + migration `0042`, `down_revision = "0041_turn_quality_metrics"`.
- [ ] **Step 4:** `record_step` no store, envolto em try/except que registra e segue.
- [ ] **Step 5:** Chamar `record_step` em `AgenticTurnRuntime` nos pontos onde `_assistant_tool_message` e `_tool_result_messages` já são construídos.
- [ ] **Step 6:** Reescrever `history_for_turn` para intercalar mensagens e passos, projetando na forma do provider **do turno atual**.
- [ ] **Step 7:** Teste de integração: turno 1 lê um arquivo; turno 2 sobre a mesma conversa não emite `read_file` para o mesmo caminho.
- [ ] **Step 8:** Rodar `pytest tests/unit tests/integration -q`.
- [ ] **Step 9:** Commit `feat(runtime): persist and rehydrate the agentic turn transcript`.

---

### Task 6: Contrato de tarefa pinado

**Files:**
- Create: `src/agentos/agentic/contract.py`
- Modify: `src/agentos/agentic/agent_tools.py` (ferramenta `write_contract`)
- Modify: `src/agentos/agentic/runtime.py` (pin, imunidade a trim e compactação)
- Test: `tests/unit/agentic/test_task_contract.py`

**Interfaces:**
- Produces: `TaskContract` (frozen dataclass) com `objective: str`, `deliverables: tuple`, `constraints: tuple`, `acceptance: tuple`, `toolkits: frozenset[str]`, `steps: tuple`; `parse(payload) -> TaskContract` levantando `ContractError` com o campo faltante; `render() -> str`; `synthesize(request: str) -> TaskContract`.
- Produces: `TOOLKITS = frozenset({"files", "terminal", "web", "browser", "delegation", "mcp", "plugins"})`.

- [ ] **Step 1:** Testes: falta de `objective`, `acceptance` ou `toolkits` levanta `ContractError` nomeando o campo; toolkit desconhecido é rejeitado; `synthesize` produz contrato válido; o bloco renderizado sobrevive a `_request_messages` com orçamento apertado e a `_maybe_compact`.
- [ ] **Step 2:** Rodar e ver falhar.
- [ ] **Step 3:** Implementar `contract.py`.
- [ ] **Step 4:** Registrar `write_contract` em `_build_definitions` com schema fechado, `kind="planning"`, não read-only.
- [ ] **Step 5:** Guardar o contrato no runtime e injetá-lo no bloco volátil a cada iteração; excluí-lo dos candidatos a corte e a compactação.
- [ ] **Step 6:** Rodar `pytest tests/unit/agentic -q`.
- [ ] **Step 7:** Commit `feat(runtime): add a pinned task contract`.

---

### Task 7: Fases, toolset por fase e orçamento por fase

**Files:**
- Create: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/agent_tools.py` (`schemas(phase)`, mapa fase → ferramentas)
- Modify: `src/agentos/agentic/runtime.py` (integrar `PhaseController`; `AgenticLimits.phase_budgets`)
- Modify: `src/agentos/agentic/session.py:209` (`build_system_prompt` em três camadas)
- Test: `tests/unit/agentic/test_phase_controller.py`, `tests/unit/agentic/test_phase_toolsets.py`

**Interfaces:**
- Produces: `Phase` (StrEnum: `ORIENT`, `PLAN`, `EXECUTE`, `VERIFY`, `RESPOND`); `PhaseBudget(iterations: int, actions: int)`; `PhaseController(limits, *, model_calls_tools: bool, resumed_contract: TaskContract | None)` com `current: Phase`, `advance() -> None`, `observe(used_tools: bool, wrote_contract: bool) -> None`, `exhausted: bool`.
- Produces: `DEFAULT_PHASE_BUDGETS: Mapping[Phase, PhaseBudget]` conforme §10 do spec.
- Produces: `MAX_SCHEMAS_PER_REQUEST = 12`.
- Produces: `build_system_prompt(..., phase: Phase) -> tuple[str, str, str]` devolvendo (estático, volátil, de fase).

- [ ] **Step 1:** Testes de `PhaseController`: turno conversacional (primeira iteração sem ferramenta) vai `ORIENT → RESPOND` numa iteração; turno com ferramenta atravessa o ciclo; contrato reidratado começa em `EXECUTE`; orçamento esgotado avança em vez de falhar; `model_calls_tools=False` degenera para `ORIENT → RESPOND`.
- [ ] **Step 2:** Testes de toolset: nenhuma fase publica mais de 12 schemas; `VERIFY` publica só read-only; `mcp` e `plugins` ausentes quando o contrato não os declara; ferramenta fora dos toolkits devolve `TOOLKIT_NOT_DECLARED` sem falhar o turno.
- [ ] **Step 3:** Rodar e ver falhar.
- [ ] **Step 4:** Implementar `phases.py`.
- [ ] **Step 5:** `AgentToolset.schemas(phase, toolkits)` filtrando e respeitando o teto de 12.
- [ ] **Step 6:** Integrar no loop: a fase decide os schemas, o bloco de prompt e o orçamento; a saída de fase é avaliada ao fim de cada iteração.
- [ ] **Step 7:** Separar `build_system_prompt` em três camadas; os blocos condicionais de browser/skills/subagentes/PDF passam a viver na camada de fase.
- [ ] **Step 8:** Rodar `pytest tests/unit tests/integration -q`.
- [ ] **Step 9:** Commit `feat(runtime): drive the turn through deterministic phases`.

---

### Task 8: Bench de referência

**Files:**
- Create: `scripts/agent_bench.py`
- Create: `tests/fixtures/agent_bench/` (12 tarefas conforme §4.4 do spec)

- [ ] **Step 1:** Escrever as doze tarefas com critérios de aceite por asserção.
- [ ] **Step 2:** Escrever o runner: recebe provider/modelo, executa cada tarefa numa conversa nova, lê `turn_quality_metrics` e imprime a tabela comparativa.
- [ ] **Step 3:** Rodar contra o modelo de referência e gravar o resultado em `docs/agent_memory/`.
- [ ] **Step 4:** Commit `test: add the agent reference bench`.

**Nota:** este bench exige um provider real e credenciais do operador. Não roda em CI e não pode ser validado por um agente sem essas credenciais — o resultado precisa ser produzido por quem tem a chave.

---

## Self-review

**Cobertura do spec:** §4 → Tasks 1, 2, 8. §6 (A1) → Task 5. §7 (A2) → Task 7. §8 (A3) → Task 6. §9 (A4) → Tasks 3, 4. §10 (A5) → Task 7. §11 (degradação) → coberto em cada tarefa. §12 (testes) → distribuído. §14 critérios 1–6 → Tasks 1–7; critério 7 → Task 8.

**Consistência de tipos:** `TurnQualityCounters` (Task 1) é consumido pela Task 2. `TaskContract` (Task 6) é consumido por `PhaseController` (Task 7) e por `history_for_turn` (Task 5, via `kind="contract"`). `Phase` (Task 7) é consumido por `build_system_prompt` e `AgentToolset.schemas`.

**Lacuna conhecida:** o critério de aceite 7 do spec (queda de 50% medida) não é verificável sem credencial de provider. As Tasks 1–7 entregam a máquina e a instrumentação; a comprovação depende da Task 8 executada pelo operador.

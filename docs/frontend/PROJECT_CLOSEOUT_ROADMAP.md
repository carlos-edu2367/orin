# Roadmap de finalização — AgentOS Frontend

> Escopo: fechar `docs/frontend/NEXT_SESSION_PROMPT.md`. Fase 0 (o gate bloqueante) está concluída e verificada contra Postgres real — ver evidência abaixo. Este documento sequencia o que falta.

## Estado atual (verificado, não estimado)

| Item | Status | Evidência |
| --- | --- | --- |
| Fase 0 — Security/Execution/Events em produção | **Concluído** | `tests/integration/api/test_frontend_contracts.py` (6/6, Postgres real); verificação final `701 passed, 2 skipped` |
| Frontend Fases 1–6 | Concluído (sessões anteriores) | 97 testes unitários, 20 E2E, 4 baselines visuais — não tocado nesta rodada |
| B — Bridge tool_runtime/multi_agent → stream público | **Concluído** | `tests/integration/persistence/test_event_stream_postgres_optional.py::test_a_tool_event_and_a_delegation_event_cross_the_bridge_into_the_same_client_event_stream` (Postgres real) — ver Fase B abaixo |
| C — Resolução de `result_ref` → `display_text` | **Fechado como limitação documentada** | investigação real (grep/leitura) confirma que não há dado resolvível hoje — ver Fase C abaixo |
| D — `ProviderConfigurationApplication` em produção | **Concluído** | `tests/integration/api/test_provider_configuration_postgres_optional.py` (4/4, Postgres real) — ver Fase D abaixo |
| E — UI de input para `WAITING_USER` | **Concluído** | `ExecutionInputComposer.test.tsx`, `ExecutionRoute.test.tsx` e `execution-input.spec.ts` (E2E isolado 3×) |
| F — Bug de sobreposição no `AgentRail` | **Concluído** | `reduced-motion.spec.ts` sem clique de fechamento; RED documentou interceptação de pointer e GREEN 3× isolado |
| H — Teste flaky `agentGraphProjection` | **Concluído** | mock da fronteira lazy no unit; 24/24 isolado 3× e 100/100 na suíte Vitest completa |
| Verificação completa + docs | **Concluído** | backend com/sem Postgres, compileall, Vitest, E2E, visual, lint e build verdes nesta sessão |

**Arquivos novos desta sessão (Fase 0):**
`src/agentos/persistence/postgres/{security,execution_adapters,event_stream}.py`, migrations `0004_security`, `0005_event_stream_bindings`, `tests/integration/persistence/test_{security,execution_adapters,event_stream}_postgres_optional.py`, `tests/integration/api/test_frontend_contracts.py`. Modificados: `bootstrap/production.py`, `api/{gateway,contracts}.py`, `persistence/postgres/schema.py`, `tests/unit/persistence/test_postgres_schema.py`.

**Arquivos novos desta sessão (Fases B/D — ver `docs/frontend/IMPLEMENTATION_PLAN.md` para o detalhamento completo com decisões locais):**
`src/agentos/persistence/postgres/{multi_agent_events,tool_activity,provider_configuration}.py`, `src/agentos/bootstrap/multi_agent.py`, migrations `0006_multi_agent_and_tool_events`, `0007_provider_configurations`, `tests/integration/persistence/test_{multi_agent_events,tool_activity_events}_postgres_optional.py`, `tests/integration/api/test_provider_configuration_postgres_optional.py`. Modificados: `persistence/postgres/{schema,event_stream}.py`, `tool_runtime/{models,runtime}.py`, `bootstrap/production.py`, `tests/unit/persistence/test_postgres_schema.py`, `tests/unit/tool_runtime/test_runtime_contracts.py`, `tests/integration/persistence/test_event_stream_postgres_optional.py`. Fase C produziu nenhum arquivo novo (limitação documentada, não implementação).

**Decisões locais já tomadas (não reabrir sem novo motivo):**
- Sem endpoint de login/emissão de PAT — confirmado ausente em `BACKEND_DISCOVERY.md`; provisionamento fica em API Python (`add_pat`/`add_session`), igual ao `InMemorySecurityService`.
- `correlation_id` do gateway passou a ser determinístico por `execution_id` (não mais aleatório por request) — a persistência real exige o mesmo tuple de escopo em toda leitura/escrita do mesmo registro.
- `execution_id` em `POST /v1/executions` passou a ser determinístico por `(credential_ref, idempotency_key)` — idempotência real exige isso; era gerado aleatório antes da checagem de idempotência.
- Novas categorias de erro `CONFLICT`, `INDETERMINATE`, `NOT_FOUND` no gateway, incluindo `cursor_invalid` (já está em `RESYNC_CODES` do frontend).

---

## Fase B — Bridge tool_runtime/multi_agent → `ClientEventStream`

**Status: Concluído nesta sessão.** Ver `docs/frontend/IMPLEMENTATION_PLAN.md`, seção "Fase B", para arquivos, evidência de teste e decisões locais completas. Resumo: `PostgresMultiAgentEventRecorder` (B.1), sink durável injetável em `ToolRuntimeService` + `PostgresToolActivitySink` (B.2), e `PostgresClientEventStream.read()` unificando as 3 fontes (B.3) — todos com TDD RED→GREEN contra Postgres real.

**Por que ainda não estava pronta antes desta sessão:** investigação confirmou que os dois módulos estavam em estados diferentes.

- `multi_agent` já tem uma porta limpa: `MultiAgentEventRecorder` (Protocol, `record_event(event) -> bool`), hoje implementada só por `InMemoryMultiAgentStore`. **Trabalho contido.**
- `tool_runtime.ToolRuntimeService.outbox` é um `list` em processo, sem porta de injeção nenhuma. Tornar isso durável exige mudar `tool_runtime/runtime.py` para aceitar um sink injetável — não é só escrever um projetor.

### B.1 — `PostgresMultiAgentEventRecorder` (S)
- Nova tabela (migration `0006`) ou reaproveitar `persistence_outbox` com `source="multi-agent"` — **decisão a tomar no início desta fase**: `persistence_outbox` já tem `event_id` único, `execution_id`, `user_id`, `workspace_id`, `classification`, `event` JSON — é suficiente para `EventEnvelope` de multi_agent, mas exige as colunas de escopo (`agent_id`, `correlation_id`, `purpose`, `actor`, FK para `persistence_records`) que multi_agent não tem naturalmente (delegação não é necessariamente uma "record" de execution). Recomendação: tabela dedicada `multi_agent_events` (sem FK para `persistence_records`), mais simples e correta.
- Implementar `PostgresMultiAgentEventRecorder(engine).record_event(event) -> bool` satisfazendo o Protocol.
- TDD: `tests/integration/persistence/test_multi_agent_events_postgres_optional.py` — grava `DelegationCreated`/`AgentMessageCreated`, confirma idempotência de `event_id` (retorna `False` em duplicata, igual ao `InMemoryMultiAgentStore`).
- Compor em produção onde `MultiAgentService` é construído (achar o composition root real — não existe hoje; provavelmente precisa ser adicionado em `bootstrap/production.py` ou em um novo `bootstrap/multi_agent.py`).

### B.2 — Sink durável em `tool_runtime` (M)
- Mudar `ToolRuntimeService.__init__` para aceitar um `sink: Callable[[ToolOutboxEntry], None] | None = None` opcional; `_entry()` chama `self.outbox.append(...)` **e** `self._sink(entry)` se presente. Preserva 100% do comportamento atual (outbox em memória continua existindo — é o que os testes existentes usam).
- Nova tabela `tool_activity_events` (mesma lógica de B.1: sem FK para persistence_records, tool invocations não são um "record" com versão).
- `PostgresToolActivitySink` grava `ToolOutboxEntry` como `EventEnvelope`-compatível.
- TDD: teste que injeta o sink real, dispara uma invocação de tool fake, confirma a linha em `tool_activity_events`. Depois, teste de regressão que roda a suíte atual de `tool_runtime` sem sink (garante que nada quebrou).

### B.3 — Projetor unificado no `ClientEventStream` (M)
- `PostgresClientEventStream.read()` hoje só lê `persistence_outbox`. Estender para fazer `UNION` (ordenado por `occurred_at`/id) com `multi_agent_events` e `tool_activity_events`, projetando os três para o mesmo envelope `ClientEvent`.
- Preservar nomes de payload que o frontend já assume (`ToolActivityView`-like, `DelegationCreated.{delegation_id, child_execution_id, child_agent_id}`, `AgentMessageCreated.{sender_agent_id, recipient_agent_id}`, `AgentWait*.{wait_id}`) — checar contra `frontend/src/features/activities/activityNormalizer.ts` e `frontend/src/features/agents/agentGraphProjection.ts` antes de fixar o payload; traduzir no projetor se o nome real do domínio divergir, documentando a tradução.
- TDD: estender `tests/integration/persistence/test_event_stream_postgres_optional.py` com um evento de cada fonte, um único `read()` retornando os três em ordem.

**Critério de saída da Fase B:** um evento de Tool e um evento de Delegation atravessam outbox → stream público → `ClientEvent` real (não fixture), com teste de integração provando isso.

---

## Fase C — Resolução de `result_ref` → `display_text`

**Status: Fechado nesta sessão como limitação documentada.** Ver `docs/frontend/IMPLEMENTATION_PLAN.md`, seção "Fase C", para a investigação completa (grep/leitura real) e a razão registrada. Resumo: `result_ref` só é produzido hoje por `providers/compat.py:RuntimeProviderAdapter._map_outcome` como uma string sintética (`f"result:{invocation_id}"`) que descarta o texto real gerado; `RuntimeService`/`RuntimeProviderAdapter` não são compostos em nenhum lugar de `bootstrap/`, então nenhuma execution em produção chega a `COMPLETED` com um `result_ref` real por esse caminho hoje. Sem armazenamento texto-seguro por trás do ref, nenhum adapter foi inventado.

**Investigar antes de codar** (nesta ordem): `src/agentos/artifact_storage/` (modelos + adapters Postgres — já existe um adapter Postgres testado em `tests/integration/artifact_storage/test_artifact_postgres_optional.py`), depois `src/agentos/memory/`. Provável caminho: `artifact_storage` já é o armazenamento seguro e sanitizado certo para isso (é o que existe e é testado contra Postgres real); `result_ref` provavelmente referencia um artifact.

- Confirmar (grep em `runtime/service.py` e `execution/control.py`) exatamente onde/como `result_ref` é produzido hoje e se já é um `artifact_storage` ref ou um ref opaco de outro subsistema.
- Se for um artifact ref: adapter mínimo que resolve `result_ref` → artifact → texto, com a mesma autorização (`user_id`/ownership) já usada em `ExecutionQueryAdapter`, sanitizando antes de expor.
- Se não houver nenhum armazenamento texto-seguro por trás do ref: implementar o menor adapter necessário (não deixar como "gap" sem tentar).
- Alterar `ExecutionQueryAdapter._to_execution_view` para popular `result.display_text` quando resolvível; omitir o campo (não inventar texto) quando não.
- TDD: `test_execution_adapters_postgres_optional.py` ganha um caso "execução completa com result_ref real → display_text presente e sanitizado".

**Risco conhecido:** se `result_ref` não for de fato um artifact hoje (pode ser produzido só pelo Runtime interno, nunca persistido em lugar consultável pela API), essa fase pode legitimamente terminar em "limitação documentada" — só depois de grep real confirmando isso, nunca por suposição.

---

## Fase D — `ProviderConfigurationApplication` em produção

**Status: Concluído nesta sessão.** Ver `docs/frontend/IMPLEMENTATION_PLAN.md`, seção "Fase D", para arquivos, evidência de teste e decisões locais completas. Resumo: `PostgresProviderConfigurationAdapter` sobre uma tabela dedicada `provider_configurations`, composto em `compose_production_services`, provado fim a fim por `PUT`/`GET`/`DELETE /v1/providers/{provider}` contra Postgres real sem nunca vazar a API key.

Mais contida que B/C — `src/agentos/providers/` (catálogo, resolver, compat) já existe e é testado.

- Investigar `providers/catalog.py`, `providers/resolver.py`, `providers/compat.py` para achar o storage real de configuração de provider (provavelmente outra tabela Postgres ou reaproveita `persistence_records` com `record_type="provider_configuration"`).
- Adapter satisfazendo `ProviderConfigurationApplication.configure/inspect/revoke`, análogo ao `FakeProviderConfiguration` já usado em `tests/unit/api/test_api_asgi.py` — nunca reler/reexibir a API key (mesma garantia que `_provider_public()` no gateway já reforça).
- Compor em `compose_production_services`.
- TDD: estender `test_frontend_contracts.py` ou novo arquivo com `PUT/GET/DELETE /v1/providers/openai` fim a fim contra Postgres real.

---

## Fase E — UI de input para `WAITING_USER`

**Status: Concluído nesta sessão.** `ExecutionInputComposer` é exibido apenas em `WAITING_USER`, envia `{ input_ref, expected_state_version }` por `provideExecutionInput` e preserva uma `Idempotency-Key` por intenção. Evidência: unit tests em `ExecutionInputComposer.test.tsx`/`ExecutionPage.test.tsx`/`ExecutionRoute.test.tsx` e `tests/e2e/execution-input.spec.ts` (3 execuções isoladas verdes).

Só depois de B/C/D estarem minimamente estáveis (o composer precisa saber se `input_ref` requer a mesma resolução de C, ou se é só opaco na escrita).

- Componente mínimo em `ExecutionPage`/`ExecutionRoute`, visível só quando `execution.state === 'WAITING_USER'`, reusando `ExecutionControls`/`Disclosure` e o padrão de `Idempotency-Key` por intenção já estabelecido.
- TDD: unit test do componente (`frontend/tests/unit/`) + E2E seguindo o padrão de fake documentado em `execution-controls.spec.ts` (comentário no topo do arquivo explicando o que o fake simula).
- Rodar o novo E2E isolado 2–3× antes de considerar fechado.

---

## Fase F — Bug de sobreposição no `AgentRail`

**Status: Concluído nesta sessão.** Ao remover o workaround, Playwright comprovou que `.agent-glyph__detail` interceptava o pointer de `Expandir grafo`; o rail agora reserva espaço só durante o detalhe aberto. `reduced-motion.spec.ts` passa sem o clique extra, em três execuções isoladas.

Independente de B–E, pode ser feito em paralelo por não depender de backend.

- Investigar `.agent-glyph__detail`, `.agent-rail__glyphs`, `.agent-rail__expand` em `frontend/src/styles/index.css` e `AgentRail.tsx` — provavelmente um problema de `z-index`/`position` quando o painel de detalhe expande sobre elementos abaixo.
- Menor diff possível; sem redesenhar a tela.
- Depois de corrigido: remover o clique extra "fecha antes de expandir" em `frontend/tests/e2e/reduced-motion.spec.ts` e confirmar que passa sem o workaround.

---

## Fase H — Estabilizar teste flaky `agentGraphProjection`

**Status: Concluído nesta sessão.** A suíte completa reproduziu o timeout global durante o import lazy real de R3F; o teste de rail agora isola essa fronteira e espera por condição sem timeout explícito. A cena real segue coberta em browser; o E2E foi serializado porque quatro workers concorriam pelo mesmo chunk no único Vite server.

Independente, pode ser feito em paralelo.

- `frontend/tests/unit/agentGraphProjection.test.ts` — suspeita já registrada: race entre fake timers/`act()` e o `lazy()` do `OrchestrationScene`. Investigar com `--reporter=verbose` rodando isolado várias vezes vs. na suíte completa para confirmar a hipótese antes de mexer.
- Corrigir a causa raiz (provavelmente aguardar a resolução do lazy-import antes de avançar os timers, ou usar `findBy*` em vez de `getBy*` no ponto de transição). Não apenas aumentar timeout.

---

## Fase Verificação + Documentação (fecha o escopo)

**Status: Concluído nesta sessão.** Evidência final: `AGENTOS_TEST_POSTGRES_DSN=postgresql://agentos@localhost:5433/agentos python -m pytest -q` → 701 passed, 2 skipped; sem a variável → 663 passed, 40 skipped; `python -m compileall -q src tests`; `npm run test` → 100/100; `npm run test:e2e` → 21/21; `npm run test:visual` → 4/4; `npm run lint`; `npm run build`. A passagem read-only confirmou scope por usuário/credential no stream, ausência de fallback in-memory no bootstrap de produção, payloads de Tool limitados a metadata (`purpose`, `stream_sequence`, `progress_kind`, outcome/error/effect state) e resultado/input sem texto resolvido.

Só depois de B–H:

1. `python -m pytest -q` (com e sem `AGENTOS_TEST_POSTGRES_DSN` setado, para provar que o skip funciona igual e que o caminho real passa).
2. `python -m compileall -q src tests`, `git diff --check`.
3. `npm run test`, `npm run test:e2e` (novos specs 2–3× isolados), `npm run test:visual`, `npm run lint`, `npm run build`.
4. Segunda passagem read-only independente focada em: autorização/ownership dos novos adapters, ausência de fallback in-memory em produção, sanitização de payload na ponte de eventos (B.3), ausência de vazamento no result/input resolvido (C).
5. Atualizar `IMPLEMENTATION_PLAN.md` (checkboxes reais da Fase 0 + novas "Decisões locais" de B–D, itens de Fase 6 fechados por E/F), `BACKEND_DISCOVERY.md`, `BACKEND_CAPABILITY_MATRIX.md`, `BACKEND_UI_MAPPING.md`.
6. Preencher o "Registro de encerramento" em `NEXT_SESSION_PROMPT.md`.

---

## Sequenciamento recomendado

```
Fase 0 [FEITO] ──▶ B.1 (multi_agent, contido) ──▶ B.3 (projetor)
                └─▶ B.2 (tool_runtime, precisa mexer no runtime.py) ──▶ B.3

Fase 0 [FEITO] ──▶ C (result_ref) ──▶ E (input, se input_ref precisar da mesma resolução)

Fase 0 [FEITO] ──▶ D (providers)                          [paralelo, independente]

F (AgentRail bug) ──────────────────────────────────────  [paralelo, frontend puro]
H (flaky test)    ──────────────────────────────────────  [paralelo, frontend puro]

B + C + D + E + F + H ──▶ Verificação + Docs (fecha o escopo)
```

**Tamanho relativo:** B (M-L, dois módulos + projetor), C (S-M, depende do que a investigação achar), D (S, infraestrutura já existe), E (S, frontend contido), F (S, CSS), H (S, um teste), Verificação (M, é checklist mas com E2E rodado múltiplas vezes).

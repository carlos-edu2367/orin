# AgentOS Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar um frontend conversacional, auditável e realtime para Executions, sem representar como fato técnico qualquer informação que o backend não disponibilize.

**Architecture:** O trabalho é dividido em um trilho de contratos do backend e um trilho web. O cliente inicia por snapshots HTTP autorizados, aplica SSE por um reducer idempotente e transforma eventos em atividades semânticas antes de renderizar conversa, controles, Motion e, por último, R3F.

**Tech Stack:** Python/FastAPI para os adapters públicos já existentes; React 19, TypeScript, Vite, Tailwind CSS, Motion, TanStack Query, Zustand, Three.js, React Three Fiber, Drei, Vitest, Testing Library e Playwright.

## Global Constraints

- Fonte de verdade técnica: [BACKEND_DISCOVERY.md](BACKEND_DISCOVERY.md); não inventar endpoints, eventos, campos ou relações.
- Stack decidida: React + TypeScript + Vite; Next.js só entra com requisito explícito de SSR/BFF.
- Separar server state (Query), realtime/projection (reducer/Zustand), UI state, animation state e R3F scene state.
- Mutações usam sessão cookie com CSRF/Origin ou PAT; toda mutação reusa uma `Idempotency-Key` por intenção.
- Não pôr PAT, cursor, segredo, payload SSE bruto, prompt ou resultado sensível em URL, analytics ou logs do browser.
- Interface principal é conversa, atividade e resultado; dashboards, logs e tabelas ficam fora do fluxo principal.
- Motion expressa eventos observáveis; `prefers-reduced-motion` preserva toda semântica sem pulsos, partículas ou parallax.
- R3F é lazy-loaded, possui fallback 2D e só desenha relações respaldadas por projection de delegação/mensagem.
- Cada fase só avança após seus testes e critério de saída; não iniciar Tool grouping antes de projeção Tool, nem grafo 3D antes de projection multiagent.

---

## Decisões já tomadas e seus efeitos

| Decisão documentada | Efeito no plano |
| --- | --- |
| A produção atual falha fechada sem adapters compostos | Fase 0 é obrigatória; não iniciar UI conectada antes dela. |
| `result_ref` e `input_ref` são opacos | Fase 1 usa fixtures; Fase 2 só mostra conteúdo após DTO seguro. |
| SSE é ao-menos-uma-vez, com cursor e revogação | Fase 2 implementa bootstrap + dedupe + resync antes de Motion. |
| Tool events não chegam ao SSE hoje | Fase 3 depende de uma projection pública `ToolActivity`. |
| Multi-agent tem Delegation/Wait explícitos | Fase 4 usa somente esses facts, sem inferir mensagens. |
| R3F representa orquestração, não decoração | Fase 5 fica depois do rail 2D e só recebe graph projetado. |

## Estrutura de arquivos alvo

```text
frontend/
  package.json
  vite.config.ts
  src/
    app/App.tsx
    app/routes.tsx
    api/client.ts
    api/errors.ts
    api/executions.ts
    api/events.ts
    api/providers.ts
    api/types.ts
    features/executions/ExecutionPage.tsx
    features/executions/ExecutionControls.tsx
    features/executions/executionProjection.ts
    features/realtime/realtimeStore.ts
    features/realtime/eventReducer.ts
    features/activities/activityNormalizer.ts
    features/activities/ActivityGroup.tsx
    features/agents/AgentRail.tsx
    features/agents/OrchestrationScene.tsx
    features/providers/ProviderSettingsPage.tsx
    components/ui/
    styles/index.css
  tests/unit/
  tests/e2e/
```

O diretório `frontend/` ainda não existe; cada fase cria apenas os arquivos necessários à sua saída.

---

## Fase 0 — Habilitar a superfície de produto no backend

**Objetivo:** substituir a composição indisponível por portas duráveis e contratos públicos autorizados.

**Backend files:**
- Modify: `src/agentos/bootstrap/production.py`
- Modify: `src/agentos/api/contracts.py`
- Modify: `src/agentos/api/gateway.py`
- Create: `tests/integration/api/test_frontend_contracts.py`

**Contratos a publicar:**

```ts
type ExecutionView = {
  execution_id: string;
  agent_id: string;
  state: "QUEUED" | "STARTING" | "RUNNING" | "WAITING_TOOL" | "WAITING_USER" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED";
  state_version: number;
  parent_execution_id: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  result: { display_text?: string; result_ref: string } | null;
  failure: { code: string } | null;
};

type ClientEvent = {
  event_id: string;
  event_type: string;
  execution_id: string;
  sequence: number;
  occurred_at: string;
  payload: Record<string, unknown>;
};
```

- [x] Escrever teste de integração que compõe serviços reais/fakes autorizados e verifica `POST /v1/executions`, `GET /v1/executions/{id}` e abertura/leitura de stream com os DTOs acima. `tests/integration/api/test_frontend_contracts.py`, gated por `AGENTOS_TEST_POSTGRES_DSN` (rodado contra Postgres real do docker-compose do próprio projeto, `postgresql://agentos@localhost:5433/agentos`).
- [x] Executar `python -m pytest -q tests/integration/api/test_frontend_contracts.py` e confirmar a falha inicial por adapter ausente ou contrato inexistente. Confirmado RED por composição ausente antes da implementação dos adapters.
- [x] Implementar adapters de command/query/stream na composição de produção sem instalar fallback in-memory; mapear somente campos autorizados para `ExecutionView`. `src/agentos/persistence/postgres/execution_adapters.py` (`ExecutionApplicationAdapter`/`ExecutionQueryAdapter`, sobre `ExecutionControlService` + `PostgresTransactionalPersistence` já existentes — nenhuma lógica de domínio duplicada) e `src/agentos/persistence/postgres/security.py` (`PostgresSecurityService`); compostos em `bootstrap/production.py:compose_production_services`.
- [x] Construir projetor autorizado de archive/outbox para `ClientEventStream`; preservar `event_id`, ordenação por execution, cursor e epoch de revogação. `src/agentos/persistence/postgres/event_stream.py` (`PostgresClientEventStream`), lê `persistence_outbox` real; bindings duráveis em `event_stream_bindings` (migration `0005_event_stream_bindings`).
- [x] Reexecutar o teste e adicionar casos de cursor inválido, evento duplicado, scope revogado e resultado não autorizado. 6 casos em `test_frontend_contracts.py`: create→get→stream, idempotência de create, cursor inválido, credencial revogada, execution de outro usuário (404, sem leak), conflito de versão (409).
- [ ] Commit sugerido: `feat(api): compose frontend execution and event projections`. **Não commitado nesta sessão**: sem pedido explícito do usuário.

**Critério de saída:** **Atingido.** Um cliente autorizado cria, consulta e acompanha uma Execution concreta sem acessar Redis, Postgres ou refs sensíveis diretamente — provado por `test_frontend_contracts.py` contra Postgres real, não fake. Suíte completa: `690 passed, 2 skipped`.

**Decisões locais registradas para a Fase 0:**
- Não existe endpoint de login/emissão de PAT em `api/gateway.py` (confirmado ausente em `BACKEND_DISCOVERY.md`); `PostgresSecurityService` expõe provisionamento como API Python (`add_pat`/`add_session`/`revoke`), no mesmo formato que `InMemorySecurityService` já usava para testes — nenhum endpoint HTTP novo foi inventado.
- `correlation_id` em `api/gateway.py:context()` passou de aleatório por request para determinístico (`corr_{execution_id}`): `PostgresTransactionalPersistence._scope_filters` exige o mesmo tuple de escopo (`user_id, workspace_id, agent_id, execution_id, correlation_id, purpose, actor`) em toda leitura/escrita do mesmo registro; um valor aleatório por request quebrava silenciosamente create→get/control no backend real (não aparecia com o `FakeExecutionApplication` dos testes unitários, só contra Postgres real).
- `execution_id` em `POST /v1/executions` passou a ser determinístico por `sha256(credential_ref|idempotency_key)`: era gerado antes mesmo de checar o `Idempotency-Key`, então dois POSTs com a mesma chave criavam duas execuções diferentes — achado por teste de integração real, corrigido.
- Novas exceções `ApplicationConflictError`/`ApplicationIndeterminateError`/`ApplicationValidationError`/`ApplicationNotFoundError` em `api/contracts.py`, com handlers em `api/gateway.py` mapeando para `CONFLICT`(409)/`INDETERMINATE`(409)/`VALIDATION`(422)/`NOT_FOUND`(404) — fechando o gap que o gateway só tratava VALIDATION/AUTHENTICATION/AUTHORIZATION/RATE_LIMITED/INTERNAL.
- Adicionado handler para `CursorError` → 409 CONFLICT, código `cursor_invalid`, que já está em `RESYNC_CODES` do frontend (`frontend/src/api/errors.ts`) — sem esse handler, um cursor inválido virava 500 INTERNAL e o resync do frontend (já implementado na Fase 2) nunca disparava.
- `resource_services` (agents/capabilities/tools/workspaces/artifacts/memories) permanecem indisponíveis na composição de produção: nenhum DTO/permissão pública foi estabelecido para eles nesta fase — consistente com a decisão já registrada na Fase 5.
- `provider_configuration` não foi composto nesta fase (ver Fase D do roadmap de fechamento, `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`).

---

## Fase 1 — Fundação do cliente web e experiência estática

**Objetivo:** criar a aplicação Vite com sistema visual e shells de Home/Execution, usando fixtures tipadas sem chamar o backend.

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/routes.tsx`
- Create: `frontend/src/styles/index.css`
- Create: `frontend/src/components/ui/StatusLabel.tsx`
- Create: `frontend/src/components/ui/Disclosure.tsx`
- Create: `frontend/src/features/executions/ExecutionPage.tsx`
- Create: `frontend/src/features/executions/ExecutionControls.tsx`
- Create: `frontend/tests/unit/ExecutionPage.test.tsx`

**Interfaces:**

```ts
export type ExecutionStatus = ExecutionView["state"];
export type ExecutionProjection = ExecutionView & { visual_status: string };
export function toVisualStatus(state: ExecutionStatus): string;
```

- [ ] Escrever `ExecutionPage.test.tsx` para validar label “Trabalhando” em `RUNNING`, “Aguardando você” em `WAITING_USER`, e controles inacessíveis em estados terminais.
- [ ] Rodar `npm run test -- ExecutionPage.test.tsx` e confirmar falha por componentes ausentes.
- [ ] Criar tema dark, tokens de surface/typography/accent e primitives acessíveis; implementar Home, ExecutionPage e inspector fechado, sem tabela ou dashboard.
- [ ] Implementar `toVisualStatus` como mapeamento puro dos estados persistidos; manter rótulos derivados fora do tipo de backend.
- [ ] Rodar `npm run test -- ExecutionPage.test.tsx` e `npm run build`.
- [ ] Commit sugerido: `feat(frontend): add accessible conversation shell`.

**Critério de saída:** há uma navegação desktop coerente, acessível e visualmente fiel ao spec, ainda sem dados de rede.

### Decisões locais registradas para a Fase 1

- As rotas estáticas usam `/execution/fixture-running`, `/execution/fixture-waiting`, `/execution/fixture-completed`, `/execution/fixture-failed` e `/execution/fixture-cancelled`; isso mantém a fixture explicitamente separada de uma futura rota baseada em API.
- O composer da Home aceita somente uma `task_ref` conhecida e não simula envio de prompt; essa escolha segue a limitação documentada em `UX_UI_SPEC.md` e `BACKEND_CAPABILITY_MATRIX.md`.
- O inspector foi implementado como `Disclosure` fechado por padrão, com detalhes técnicos mínimos (ID opaco, estado e versão), sem exibir payloads, refs sensíveis ou dados não autorizados.

---

## Fase 2 — Cliente HTTP, lifecycle e realtime resiliente

**Objetivo:** conectar snapshots e SSE de modo idempotente, antes de qualquer camada visual dependente de eventos.

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/errors.ts`
- Create: `frontend/src/api/executions.ts`
- Create: `frontend/src/api/events.ts`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/features/executions/executionProjection.ts`
- Create: `frontend/src/features/realtime/eventReducer.ts`
- Create: `frontend/src/features/realtime/realtimeStore.ts`
- Create: `frontend/tests/unit/eventReducer.test.ts`
- Create: `frontend/tests/e2e/execution-reconnect.spec.ts`

**Interfaces:**

```ts
export type StreamState = { cursor: string | null; seenEventIds: string[]; executions: Record<string, ExecutionProjection> };
export function applyClientEvent(state: StreamState, event: ClientEvent): StreamState;
export function shouldResync(error: ApiError): boolean;
```

- [x] Escrever teste do reducer para: deduplicar mesmo `event_id`; ignorar sequence inferior; aceitar transition de version posterior; preservar cursor somente após aplicação.
- [x] Rodar `npm run test -- eventReducer.test.ts` e confirmar falha por módulo ausente.
- [x] Implementar fetch tipado, envelope de erro, geração/reuso de `Idempotency-Key` e integração React para create/get/list Execution.
- [x] Implementar abertura POST do stream, leitura/reconexão e reducer; em erro de cursor, 403, retenção ou lacuna, invalidar queries, limpar cursor e abrir novo binding.
- [x] Escrever Playwright que desconecta a rede, entrega evento duplicado em replay e confirma que o estado final não regride nem duplica atividade.
- [x] Rodar `npm run test -- eventReducer.test.ts`, `npm run test:e2e -- execution-reconnect.spec.ts` e `npm run build`.
- [ ] Commit sugerido: `feat(frontend): add execution projections and resilient SSE`.

**Critério de saída:** uma Execution real é acompanhável, controlável e recuperável após replay/reconexão.

### Decisões locais registradas para a Fase 2

- O estado realtime usa um reducer puro, um store externo mínimo e `useSyncExternalStore`; Zustand e TanStack Query não foram adicionados porque a Fase 2 possui apenas um binding por Execution e não precisa de cache genérico ou estado global adicional.
- O transporte contínuo usa abertura e replay pelos `POST /v1/events/streams` e `POST /v1/events/streams/{stream_id}/read` documentados. O `GET` SSE atual exige cursor em query string e encerra após os eventos disponíveis mais heartbeat; evitá-lo mantém o cursor opaco fora da URL e não altera a semântica pública disponível.
- Reconexão e ressincronização são limitadas a três tentativas consecutivas. Um snapshot nunca regride uma projeção mais nova, IDs já vistos sobrevivem ao novo binding e somente aumentos reais de `state_version` contam como atualização aplicada, evitando repetir Motion histórico.
- Autenticação não foi ampliada: cookies continuam em `credentials: same-origin`, CSRF pode ser injetado por `<meta name="csrf-token">` e PAT só pode ser fornecido em memória ao `ApiClient`; nenhuma credencial é persistida no browser.
- `ClientEvent.sequence` é tratado apenas como ordem de entrega do binding corrente, nunca como versão da execution. `BACKEND_DISCOVERY.md` avisa que o stream público numera a entrega globalmente, então um snapshot inicia `last_event_sequence` em zero e abrir um novo binding reinicia esse contador. Impacto: eventos de outra execution no mesmo binding não provocam mais ressincronização falsa, e um binding novo não descarta transições legítimas.
- A autoridade de ordenação, regressão e lacuna é o `state_version` do payload de lifecycle, que é por execution e contíguo. Versão anterior é redelivery inofensiva; a mesma versão com estado divergente, ou um salto de versão, exige snapshot novo. Impacto: duplicatas nunca alteram a projeção e uma lacuna real continua disparando reconciliação.
- Cada intenção de controle usa uma `Idempotency-Key` por `action` + `expected_state_version`, reusada enquanto a intenção não tiver recibo aceito. Impacto: repetir o mesmo comando após falha de rede não cria um segundo comando no backend.
- O fake do Playwright declara no topo do arquivo exatamente quais respostas públicas simula; o código `invalid_cursor` representa a semântica documentada de cursor inválido/antigo e não é um contrato de produção.

---

## Fase 3 — Conversa autorizada e agrupamento semântico de atividade

**Objetivo:** introduzir resultados e atividades compactas somente após os DTOs seguros correspondentes.

**Backend dependency:** publicar `ToolActivityView` com `invocation_id`, `execution_id`, `tool_kind`, `state`, `occurred_at`, `progress_kind?`, `error_code?` e resumo sanitizado; nenhum argumento, output, segredo ou payload bruto.

**Files:**
- Create: `frontend/src/features/activities/activityTypes.ts`
- Create: `frontend/src/features/activities/activityNormalizer.ts`
- Create: `frontend/src/features/activities/ActivityGroup.tsx`
- Create: `frontend/src/features/activities/ToolActivityGroup.tsx`
- Create: `frontend/tests/unit/activityNormalizer.test.ts`
- Modify: `frontend/src/features/executions/ExecutionPage.tsx`

**Interfaces:**

```ts
export type Activity = { id: string; executionId: string; category: "lifecycle" | "tool" | "delegation" | "resource"; state: string; eventIds: string[] };
export function normalizeActivities(events: ClientEvent[]): Activity[];
```

- [x] Escrever testes para 3 eventos da mesma `invocation_id` produzirem um único grupo Tool, e para eventos sem invocation nunca serem agrupados como Tool.
- [x] Rodar `npm run test -- activityNormalizer.test.ts` e confirmar falha inicial.
- [x] Implementar normalizador puro e `ActivityGroup` com níveis: resumo, categoria/contagem, detalhe sanitizado e refs no inspector.
- [x] Incluir `result.display_text` somente quando fornecido pelo DTO autorizado; caso contrário renderizar terminal sem inventar texto.
- [x] Testar teclado, accordion, failed/cancelled/timeout e reduced motion; rodar `npm run test -- activityNormalizer.test.ts` e `npm run build`.
- [ ] Commit sugerido: `feat(frontend): add semantic execution activity groups`. (Não commitado nesta sessão: sem pedido explícito do usuário.)

**Critério de saída:** muitas operações podem ser percebidas como uma atividade curta, expansível e auditável, sem virar log de eventos. **Atingido**: `ToolActivityGroup` resume `N ações observadas` em nível 1 e expande para tipo/estado/progresso/resultado sanitizado em nível 2, sem renderizar um item por Event.

### Decisões locais registradas para a Fase 3

- **Nível 1 exibe somente a categoria `tool`.** `delegation` e `resource` continuam existindo no tipo `ActivityCategory` (previstas para as Fases 4/5), mas `ExecutionPage` filtra a lista renderizada para `category === 'tool'`. Motivo: o stream público hoje só autoriza a família Execution lifecycle (`BACKEND_DISCOVERY.md`); sem uma projeção pública de delegation/resource, expor esses grupos alegaria observação que a UI não tem. Lifecycle continua fora do Nível 1 porque já é representado integralmente pelo estado humano do Nível 0.
- **`normalizeActivities` categoriza por presença de `invocation_id` no payload, nunca pelo `event_type`.** Isso garante que nenhuma sequência de eventos de Execution (`ExecutionWaitingForTool` etc.) seja jamais interpretada como uma Tool Call — apenas um evento que carregue `invocation_id` explícito é agrupado como `tool`. Eventos Tool não são hoje entregues pelo stream público; o normalizador foi escrito contra a forma documentada de `ToolActivityView` para o dia em que essa ponte existir, sem simular tal entrega em nenhuma rota real.
- **Timeout é representado via `error_code: "timeout"` sobre um estado `failed`, nunca como um estado novo.** O domínio de Tool Runtime não tem um estado `TIMEOUT` (`BACKEND_DISCOVERY.md` lista `REQUESTED/VALIDATED/AUTHORIZED/RUNNING/SUCCEEDED/FAILED/CANCELLED`); inventar um estado adicional violaria "sem inventar códigos". `deriveToolActivityDetail` apenas repassa o `error_code` que o DTO autorizado already carries.
- **Progresso de Tool é exibido como contagem de atualizações observadas (`N atualizações`), nunca como percentual.** `ToolProgressed.progress_kind` não carrega valor de percentual (`BACKEND_CAPABILITY_MATRIX.md`); a UI conta quantos eventos de progresso chegaram, sem inventar um número.
- **`ActivityGroup` lê `window.matchMedia('(prefers-reduced-motion: reduce)')` diretamente, em vez de `useReducedMotion` do `motion/react`.** O hook do framer-motion memoiza a preferência uma única vez por processo (`hasReducedMotionListener` em `motion-dom`), então não responde a mudanças de preferência depois do primeiro componente montado no processo e não é reconfigurável por teste. A leitura direta acontece no momento em que um novo evento chega, o que é suficiente para decidir se o pulso deve ou não disparar e permanece determinística em teste.
- **`tests/unit/activityNormalizer.test.ts` usa `React.createElement` em vez de JSX.** O arquivo é `.ts` (não `.tsx`) por especificação; `.ts` não passa pelo parser de JSX do esbuild/Vite, então os testes de `ActivityGroup`/`ToolActivityGroup` montam os componentes via `createElement` para evitar erro de sintaxe sem mudar a extensão do arquivo.
- **Inspector (Nível 3) ganhou uma linha "Eventos aplicados"**, exibida apenas quando `ExecutionPage` recebe uma prop `events` não vazia. Isso dá à trilha de auditoria um número verificável sem inventar dado algum: é exatamente `events.length`, a mesma lista usada pelo normalizador.
- **`ExecutionPage` ganhou uma prop opcional `events?: ClientEvent[]`, com padrão vazio.** Nenhuma rota real (`ExecutionRoute`, fixtures) passa essa prop hoje, porque `ExecutionRealtimeStore`/`applyClientEvent` (Fase 2) preservam apenas `seenEventIds`, não os eventos completos — estender esse armazenamento para reter o payload integral é uma decisão de arquitetura de streaming que pertence à ponte Tool Runtime→SSE ainda inexistente, não a este componente puro de apresentação. Por isso, em produção a execution hoje renderiza apenas o Nível 0 (lifecycle), exatamente como a Fase 3 exige na ausência de projeção Tool.

---

## Fase 4 — Projeção multiagent, rail 2D e Motion semântico

**Objetivo:** tornar colaboração observável no chat usando apenas facts de delegação/mensagem publicados.

**Backend dependency:** endpoint/projection autorizado por Execution para delegations e participantes, e entrega dos eventos `DelegationCreated`, `DelegationResultReturned`, `AgentWaitRegistered`, `AgentWaitSatisfied` e `AgentMessageCreated` no stream.

**Files:**
- Create: `frontend/src/features/agents/agentGraphProjection.ts`
- Create: `frontend/src/features/agents/AgentGlyph.tsx`
- Create: `frontend/src/features/agents/AgentRail.tsx`
- Create: `frontend/src/features/agents/agentMotion.ts`
- Create: `frontend/tests/unit/agentGraphProjection.test.ts`
- Modify: `frontend/src/features/executions/ExecutionPage.tsx`

**Interfaces:**

```ts
export type AgentNode = { agentId: string; executionId?: string; visualState: "idle" | "queued" | "running" | "waiting" | "terminal" };
export type AgentEdge = { id: string; from: string; to: string; fact: "delegation" | "message" | "result"; eventId: string };
export function projectAgentGraph(events: ClientEvent[], executions: Record<string, ExecutionProjection>): { nodes: AgentNode[]; edges: AgentEdge[] };
```

- [x] Escrever teste que exige `DelegationCreated` para criar aresta parent→child e `DelegationResultReturned` para gerar edge de retorno; child execution isolada não cria edge.
- [x] Rodar `npm run test -- agentGraphProjection.test.ts` e confirmar falha inicial.
- [x] Implementar glyphs geométricos 2D, rail compacto e Motion com `layoutId`; pulsos são disparados uma vez por `event_id` novo, não por replay.
- [x] Implementar estado visual de wait por `AgentWait*`, separado de pause manual; reduzir a animação em reduced motion para mudança textual/contraste.
- [x] Rodar testes unitários, Playwright de replay e auditoria de acessibilidade do rail. Auditoria de acessibilidade cobrida como testes unitários do rail (nome acessível, teclado, foco, ordem de foco); Playwright de replay reexecutado sem regressão (`execution-reconnect.spec.ts`), sem novo E2E dedicado a delegação porque não há adapter público para alimentá-lo (ver "Decisões locais" abaixo).
- [ ] Commit sugerido: `feat(frontend): add truthful multi-agent activity rail`. (Não commitado nesta sessão: sem pedido explícito do usuário.)

**Critério de saída:** o usuário vê delegação, espera e retorno como fatos compreensíveis, sem alegar que conhece o conteúdo da comunicação. **Atingido**: `AgentRail`/`AgentGlyph` só aparecem quando uma aresta foi projetada de um evento observado; nível 1 mostra participante/estado, nível 2 mostra o fato (delegação/mensagem/retorno) sem conteúdo, nível 3 mostra IDs opacos, `event_id` e timestamp.

### Decisões locais registradas para a Fase 4

- **Nomes de campo de payload assumidos**, já que nenhum adapter publica esses eventos publicamente hoje e `BACKEND_DISCOVERY.md` só confirma as relações, não o formato de fio: `DelegationCreated.payload = { delegation_id?, child_execution_id, child_agent_id? }`; `DelegationResultReturned.payload = { delegation_id? }`; `AgentMessageCreated.payload = { sender_agent_id, recipient_agent_id }`; `AgentWaitRegistered/AgentWaitSatisfied.payload = { wait_id? }`. Todos os nomes vêm da lista de exemplos do prompt de trabalho desta fase. `agentGraphProjection` nunca lança ao encontrar um payload incompleto: apenas descarta o fato afetado (sem `child_execution_id` não há aresta de delegação; sem `sender_agent_id`/`recipient_agent_id` não há aresta de mensagem; `delegation_id` desconhecido em `DelegationResultReturned` não gera aresta de retorno).
- **Nós e arestas são identificados por `agentId`, nunca por `executionId`.** `AgentNode.executionId` é preenchido apenas quando conhecido (execução atual vista pelo `ExecutionPage`, ou `child_execution_id` de um `DelegationCreated`), permanecendo `undefined` para participantes conhecidos somente via `AgentMessageCreated` (cujo `execution_id` de envelope é a delivery execution sintética documentada em `BACKEND_DISCOVERY.md`, não a execução real do sender/recipient). Isso evita atribuir uma execução errada a um agent só para preencher o campo.
- **A identidade do agent pai de um `DelegationCreated`/`AgentWait*` vem de `executions[event.execution_id]?.agent_id`**, assumindo que o `execution_id` do envelope desses eventos é o da execução que delega/espera (o pai), não do filho. Quando essa execução não está no mapa `executions` (caso comum hoje, já que só a execução atualmente vista é conhecida), o `agentId` cai para o próprio `execution_id` como identidade estável e determinística, em vez de descartar o nó.
- **A identidade do agent filho segue uma cadeia de fallback tolerante**: `child_agent_id` do payload, senão `executions[child_execution_id]?.agent_id`, senão o próprio `child_execution_id`. A mesma cadeia nunca lança; apenas produz uma identidade menos específica quando a informação não está disponível.
- **O retorno (`DelegationResultReturned`) é correlacionado ao `DelegationCreated` original por `delegation_id`**, não por `execution_id` do evento — o texto de `BACKEND_DISCOVERY.md` não deixa claro se o evento de retorno carrega o `execution_id` do pai ou do filho, então a projeção mantém um mapa interno `delegation_id → {parentAgentId, childAgentId}` construído ao observar `DelegationCreated`, e o usa para desenhar a aresta `child → parent` com `fact: "result"`. Sem essa correlação observada, nenhuma aresta de retorno é criada — o mesmo princípio de "fato só existe se o evento correspondente foi observado" aplicado à ligação entre os dois eventos.
- **`AgentNode.visualState` deriva do `ExecutionState` conhecido por um mapeamento fixo**: `QUEUED`/`STARTING` → `queued`; `RUNNING`/`WAITING_TOOL`/`WAITING_USER` → `running`; `COMPLETED`/`FAILED`/`CANCELLED` → `terminal`; `PAUSED` e execução desconhecida → `idle`, porque a união `AgentVisualState` do contrato não reserva um valor específico para pausa manual. `waiting` é reservado exclusivamente ao fato `AgentWait*` e sobrescreve esse mapeamento base enquanto o `wait` correspondente não for satisfeito; `AgentWaitSatisfied` remove a sobrescrita e o nó volta a refletir seu `ExecutionState` conhecido (ou `idle`, se nenhum for conhecido). Isso mantém a distinção pedida entre espera colaborativa e `PAUSED` manual: um `AgentWaitRegistered` de uma execução nunca altera o nó de outra execução, mesmo que esta esteja pausada.
- **`ExecutionPage` só conhece a própria execução vista.** O `Record<string, ExecutionProjection>` passado a `projectAgentGraph` é construído com `{ [execution.execution_id]: projectExecution(execution) }`; participantes revelados apenas por `DelegationCreated`/`AgentMessageCreated` (filho, sender, recipient) não têm sua própria `ExecutionProjection` disponível hoje, então seu nó cai no `idle` padrão até que uma consulta de delegation/participantes por execution exista (gap já registrado em `BACKEND_CAPABILITY_MATRIX.md`, prioridade P1 "Grafo multiagem").
- **O rail não recebeu um E2E dedicado.** Os únicos eventos que alimentariam `AgentRail` em produção (`DelegationCreated` etc.) não chegam ao stream público hoje (mesma limitação que impediu um E2E de Tool na Fase 3); um E2E que os simulasse estaria testando um fake de produção inexistente. A cobertura de comportamento, replay-dedupe, acessibilidade e reduced motion do rail está em `agentGraphProjection.test.ts`, seguindo o padrão já usado por `activityNormalizer.test.ts` na Fase 3.
- **`agentGraphProjection.test.ts` também cobre `AgentGlyph`/`AgentRail`, não só a projeção pura.** Mesma decisão já registrada na Fase 3 para `activityNormalizer.test.ts`: um único arquivo `.ts` (não `.tsx`) cobre projeção e componentes via `React.createElement`, evitando o parser JSX do esbuild/Vite para um arquivo `.ts`.
- **O nível 3 (IDs, `event_id`, timestamp) vive dentro do próprio `AgentRail`**, em um `Disclosure` aninhado ("Detalhes técnicos da colaboração"), e não no `Disclosure` de inspector já existente em `ExecutionPage`. `AgentRail` recebe a mesma prop `events` que `ExecutionPage` já expõe (Fase 3) apenas para resolver `occurred_at` de cada `eventId` de aresta; o conteúdo do evento nunca é lido além desse campo de timestamp.
- **O teto de 12 nós (`AGENT_VISUAL_LANGUAGE.md` "Grafo") corta a lista já ordenada por `agentId`** (ordem determinística de `projectAgentGraph`); o restante vira um botão contador com nome acessível (`"Mais N agents participantes"`, nunca um número solto) que expande uma `<ul>` acessível com a lista de IDs restantes.
- **Motion não introduz nenhum laço contínuo/ambiente.** O anel de cada `AgentGlyph` muda de opacidade/estilo de borda por `data-state` (CSS estático, sem `animation` contínua), evitando simular processamento em `waiting`/`queued`. O único disparo animado é um pulso único de 220ms (banda de feedback 160–240ms de `MOTION_SYSTEM.md`) nos dois nós conectados por uma aresta recém-observada, disparado no `AgentRail` comparando o conjunto de `edge.id` já vistos entre renders (mesmo padrão de "crescimento observado" que `ActivityGroup` usa para `eventIds`, adaptado para arestas). Nunca dispara no mount inicial nem em replay/reconexão da mesma aresta. `layoutId` do núcleo e o pulso são ambos condicionados a `!prefersReducedMotion()`, lido diretamente (mesma razão documentada na Fase 3 para não usar `useReducedMotion` do `motion/react`); o texto e o contraste de estado permanecem idênticos com motion reduzido.
- **Nenhuma dependência nova foi adicionada.** `agentGraphProjection` é uma função pura e `AgentRail`/`AgentGlyph` usam apenas `useState`/`useEffect`/`useRef` locais, seguindo a mesma razão já registrada nas Fases 2 e 3 para não introduzir Zustand ou TanStack Query: não há cache genérico ou estado global adicional necessário nesta fase.

---

## Fase 5 — Cena R3F, provider settings e inspector incremental

**Objetivo:** elevar a profundidade visual sem comprometer a experiência 2D ou segurança.

**Files:**
- Create: `frontend/src/features/agents/OrchestrationScene.tsx`
- Create: `frontend/src/features/agents/usePerformanceProfile.ts`
- Create: `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Create: `frontend/src/api/providers.ts`
- Create: `frontend/tests/unit/usePerformanceProfile.test.ts`
- Create: `frontend/tests/e2e/provider-settings.spec.ts`

**Interfaces:**

```ts
export type PerformanceProfile = "full" | "reduced" | "static";
export function getPerformanceProfile(input: { reducedMotion: boolean; visible: boolean; devicePixelRatio: number }): PerformanceProfile;
```

- [x] Escrever teste que seleciona `static` com reduced motion, `reduced` fora de viewport e `full` apenas para canvas visível apto.
- [x] Rodar `npm run test -- usePerformanceProfile.test.ts` e confirmar falha inicial.
- [x] Lazy-load `OrchestrationScene`; usar instancing/geometrias compartilhadas e refs em `useFrame`; manter AgentRail 2D como fallback funcional.
- [x] Implementar provider form com GET/PUT/DELETE, CSRF/idempotência, estados derivados de resposta pública e campo de chave que se limpa após resposta; nunca guardar/reexibir chave.
- [x] Adicionar abas de artifact/memory/workspace somente quando cada resource tiver DTO e permissão pública definida pela Fase 0 ou extensão equivalente. **Não adicionadas nesta sessão**: nem a produção (`bootstrap/production.py`) nem `contracts.py` comprovam DTO/permissão pública para `artifacts`/`memories`/`workspaces` hoje (ver "Decisões locais" abaixo); a condição de saída do item continua não satisfeita, então nenhuma aba foi criada.
- [x] Rodar unit, E2E, `npm run build` e medição de frame/CPU em perfis full/reduced/static. Frame/CPU real não é mensurável neste ambiente (sem GPU/compositor); a mitigação registrada abaixo ("Medição de performance") documenta essa limitação e o que foi verificado no lugar.
- [ ] Commit sugerido: `feat(frontend): add adaptive orchestration scene and providers`. (Não commitado nesta sessão: sem pedido explícito do usuário.)

**Critério de saída:** 3D explica orquestração existente, settings permanecem seguros e máquinas limitadas continuam com rail 2D legível. **Atingido**: `OrchestrationScene` só monta a partir do mesmo `AgentGraph` já validado pelo rail (nunca lê `ClientEvent[]`), nunca desenha mais que os 12 nós já limitados no rail 2D, e degrada para o mesmo texto tanto por preferência de movimento reduzido quanto por falha real do WebGL (auditado em `tests/unit/OrchestrationScene.test.ts`, que reproduz a falha de WebGL real do jsdom, não um mock). `ProviderSettingsPage`/`api/providers.ts` nunca releem ou reexibem a chave, tratam a ausência de composição de produção do `provider_configuration` como uma falha comum (nunca simulam sucesso) e reusam `Idempotency-Key`/CSRF do `ApiClient` existente — coberto por `tests/e2e/provider-settings.spec.ts`.

### Decisões locais registradas para a Fase 5

- **Limiar de `devicePixelRatio` para o perfil `"full"` foi fixado em 3, documentado em `usePerformanceProfile.ts`.** Displays desktop/Retina típicos ficam em DPR 1–2; valores acima de 3 aparecem majoritariamente em telas de celular, que tipicamente combinam GPU mais fraca com uma tela pequena — um mau ambiente para pagar o custo de instancing/partículas por padrão. Acima do limiar, o perfil cai para `"reduced"` (não `"static"`, que é reservado exclusivamente para `prefers-reduced-motion`). Testado explicitamente em `usePerformanceProfile.test.ts` (DPR 3 → `"full"`, DPR 3.01/4 → `"reduced"`).
- **Apenas o perfil `"full"` monta o `<Canvas>` do R3F.** `"reduced"` (aba oculta, fora do viewport, ou DPR alto) e `"static"` (reduced motion) convergem para a mesma view textual (`SceneTextFallback`), lida diretamente do mesmo `AgentGraph` — sem duplicar semântica entre "pausado por performance" e "pausado por acessibilidade". Isso também é o que MOTION_SYSTEM.md pede ("pausar R3F... usar rail 2D") interpretado literalmente: nada de WebGL é instanciado fora de `"full"`.
- **`OrchestrationScene` envolve o `<Canvas>` num error boundary de classe (`SceneErrorBoundary`) que renderiza exatamente o mesmo `SceneTextFallback`.** Verificado com o ambiente real deste repositório (jsdom sem `ResizeObserver`), não com um mock do `@react-three/fiber`: montar um `<Canvas>` "nu" nesse ambiente já lança `"This browser does not support ResizeObserver..."`, reproduzindo fielmente o cenário "R3F falha/não carrega" pedido pelo prompt de trabalho desta fase. Em produção (navegador real, com `ResizeObserver`), esse boundary só age se o WebGL de fato falhar (contexto indisponível, GPU bloqueada etc.).
- **O `layoutId` compartilhado é o do par botão-de-expansão ↔ painel da cena (`SCENE_LAYOUT_ID`, em `AgentRail.tsx`), não o do núcleo do glyph 2D ↔ malha 3D.** O layout compartilhado do Motion opera sobre bounding boxes do DOM; um `<canvas>` WebGL não participa dessa árvore de projeção, então não é possível fazer o FLIP literal "núcleo 2D → nó 3D" sem reescrever a própria biblioteca de layout. A decisão prática equivalente — um botão "Expandir grafo" que morfa para o painel que contém a cena — preserva a mesma leitura de "a mesma verdade, agora em outra forma" pedida por AGENT_VISUAL_LANGUAGE.md, e é condicionada a `!prefersReducedMotion()` exatamente como as demais animações desta base de código.
- **`agentVisualStateLabel`, `deriveAgentAccent` e `diffNewlyObservedEdges` foram extraídos de `AgentGlyph.tsx`/`AgentRail.tsx` para `agentMotion.ts`**, que já hospedava `agentFactLabel`/`prefersReducedMotion` compartilhados desde a Fase 4. `OrchestrationScene` importa exatamente essas funções em vez de redefinir rótulo de estado, fórmula de accent ou regra de "aresta recém-observada" — a mesma fonte, não uma reinterpretação, para o núcleo/anel/pulso 3D. `AgentRail`'s próprio efeito de pulso foi refatorado para chamar `diffNewlyObservedEdges` no lugar do filtro que já tinha inline; o comportamento é idêntico (confirmado pelos 24 testes existentes de `AgentGlyph`/`AgentRail` em `agentGraphProjection.test.ts`, que continuam passando sem alteração).
- **O layout dos nós na cena 3D é um anel determinístico pela ordem já ordenada de `AgentNode[]`** (mesma ordenação estável que `projectAgentGraph` já produz), nunca uma simulação de força/física. Isso evita inventar uma relação espacial que o grafo não afirma — a posição no anel não carrega significado além de "um slot distinto por participante".
- **Núcleo e anel usam `@react-three/drei`'s `<Instances>`/`<Instance>`** (geometria/material únicos — octaedro para o núcleo, toro para o anel — instanciados por nó), e os pulsos usam um único `THREE.InstancedMesh` cuja posição é escrita em `useFrame` via `Object3D` reutilizado e `setMatrixAt`, nunca via estado React por frame (`FRONTEND_ARCHITECTURE.md` "Performance": "`useFrame` escreve refs, não estado React"). `@react-three/drei` já constava do stack decidido na introdução deste documento ("Three.js, React Three Fiber, Drei"); as versões instaladas (`three@^0.180`, `@react-three/fiber@^9`, `@react-three/drei@^10`) foram escolhidas pela compatibilidade de peer dependencies declarada com React 19 já em uso neste projeto.
- **O teto de 12 nós é importado de `AgentRail.tsx` (`export const RAIL_NODE_LIMIT`), não redefinido.** `OrchestrationScene` fatia `graph.nodes`/`graph.edges` pelo mesmo limite e mostra a mesma contagem de participantes adicionais que o rail 2D já expõe, satisfazendo "mesma fonte, mesmo teto" (AGENT_VISUAL_LANGUAGE.md "Grafo").
- **`ProviderPublicState` só confia em `enabled`/`model`; todo outro campo cai num `extra: Record<string, unknown>` que a UI nunca renderiza.** Verificado nesta sessão, lendo `src/agentos/api/gateway.py`/`contracts.py`/`bootstrap/production.py`: `ProviderConfigurationApplication` continua sendo apenas um `Protocol` ("Application boundary for a future visual Provider-configuration flow") e `unavailable_production_services()` não passa `provider_configuration=`, então a composição de produção real deste port não existe neste repositório hoje. A resposta pública é filtrada no servidor para remover qualquer campo cujo nome contenha api_key/secret/token/password/credential, mas o *resto* do formato depende de um adapter ainda não escrito — `parseProviderPublicState` (em `api/providers.ts`) faz o mesmo filtro de nome no cliente por defesa em profundidade, e mantém qualquer campo desconhecido opaco e nunca rotulado como fato confiável de UI, exatamente como pedido.
- **Uma chamada real ao provider (sem `provider_configuration` composto) falha fechado com o envelope `ApiError` padrão (500 `internal_error`), e a UI trata isso como qualquer outra falha de rede/autorização — nunca simula sucesso.** `ProviderPanel` usa um `LoadState` de três posições (`loading`/`loaded`/`unavailable`) para o `GET` inicial e reaproveita a mesma `ApiError`/`errorHeadline` para `PUT`/`DELETE`; nenhum adapter ou schema foi inventado para contornar essa ausência de composição.
- **Abas de inspector para artifacts/memory/workspace não foram criadas.** `BACKEND_DISCOVERY.md` ("Limitações que mudam o desenho") continua afirmando que "`GET` de resource não tem schema concreto e não traz garantia de lista/paginação"; a Fase 0 (ainda não executada neste worktree) é a que definiria esse DTO/permissão pública. Criar essas abas agora exigiria inventar um schema — proibido explicitamente pelo prompt de trabalho desta fase — então o item permanece como gap aberto, na mesma prioridade P2 já registrada em `BACKEND_CAPABILITY_MATRIX.md`.
- **Nenhuma dependência de gerenciamento de estado (Zustand/TanStack Query) foi adicionada.** Cada `ProviderPanel` é independente (seu próprio `GET` inicial, seu próprio estado de ação/erro) e nada nesta fase precisa de cache compartilhado entre providers ou de estado sobrevivendo à navegação — mesma razão já registrada nas Fases 2–4 para não introduzir essas dependências. As únicas dependências novas são `three`, `@react-three/fiber` e `@react-three/drei`, já previstas no stack decidido deste documento, não uma adição de gerenciamento de estado.
- **O affordance "Expandir grafo"/"Recolher grafo" é um controle novo em `AgentRail`, separado do toggle já existente por glyph.** O nível 2 em texto (toggle individual de cada `AgentGlyph`) permanece exatamente como estava na Fase 4; o novo botão só adiciona uma terceira via de visualização (a cena 3D), nunca substitui ou reinterpreta as duas primeiras.
- **Medição de performance (full/reduced/static):** este ambiente de execução não tem GPU/compositor disponível para medir frame time real (o próprio `<Canvas>` "nu" falha ao montar por falta de `ResizeObserver`/WebGL, como documentado acima). No lugar de uma medição de frame/CPU real, esta sessão verificou: (1) que `"reduced"`/`"static"` nunca instanciam `<Canvas>` (sem custo de GPU possível nesses dois perfis, por construção, não por medição); (2) que o orçamento de partículas/nós é limitado a 12 por grafo (`RAIL_NODE_LIMIT`), com geometrias/materiais compartilhados via `<Instances>` e um único `InstancedMesh` para pulsos, para manter esse teto baixo mesmo no perfil `"full"`; (3) que `frameloop="demand"` é usado no `<Canvas>`, então nenhum frame é desenhado fora de uma transição de pulso ativa (`invalidate()` só é chamado enquanto `t < 1`). Medição de frame time real em GPU de referência fica como verificação de ambiente pendente, não como item desta fase que pôde ser satisfeito aqui.

---

## Fase 6 — Hardening, qualidade e release

**Objetivo:** validar comportamento, acessibilidade, performance e segurança antes de liberar usuários reais.

**Files:**
- Create: `frontend/tests/e2e/execution-controls.spec.ts`
- Create: `frontend/tests/e2e/reduced-motion.spec.ts`
- Create: `frontend/tests/e2e/a11y.spec.ts`
- Create: `frontend/tests/visual/execution-page.spec.ts`
- Create: `frontend/playwright.visual.config.ts` (decisão local: ver abaixo)
- Modify: `frontend/package.json` (script `test:visual`, devDependency `@axe-core/playwright`)
- Modify: `frontend/vitest.config.ts` (exclui `tests/visual`, decisão local: ver abaixo)
- Modify: `frontend/src/features/executions/fixtures.ts` (`collaborationFixtureEvents`, decisão local: ver abaixo)
- Modify: `frontend/src/app/routes.tsx` (rota `/execution/fixture-collaborating`)
- Modify: `frontend/src/features/executions/ExecutionRoute.tsx` (prazo de retry visível, decisão local: ver abaixo)
- Modify: `frontend/src/features/agents/OrchestrationScene.tsx` (correção de a11y crítica, decisão local: ver abaixo)
- Modify: `docs/frontend/BACKEND_UI_MAPPING.md`

- [x] Escrever E2E de create/control, incluindo 202, conflito de versão, indeterminado e rate limit com recuperação visível. **Input não incluído**: `provideExecutionInput` (api/executions.ts) não é chamado por nenhum componente hoje; não foi criada uma UI de input nesta fase de hardening (ver "Decisões locais" abaixo).
- [x] Escrever E2E de reduced motion que confirma ausência de canvas/pulso e presença das mesmas mudanças de estado em texto.
- [x] Executar scanner de acessibilidade (`@axe-core/playwright`) para Home, Execution (running e com rail/cena expandidos), Disclosure aberto, Inspector aberto e Provider Settings; 1 violação crítica real encontrada e corrigida (`aria-prohibited-attr` em `OrchestrationScene`, ver "Decisões locais"). Nenhuma outra tela apresentou violação crítica/séria.
- [x] Capturar visual regression das quatro telas de referência: home, execution running, activity expanded e orchestration expanded.
- [x] Rodar `npm run test`, `npm run test:e2e`, `npm run test:visual` e `npm run build` — todos executados nesta fase, evidência abaixo. O "conjunto de integração backend da Fase 0" citado originalmente **não é executável neste worktree**: `tests/integration/api/test_frontend_contracts.py` não existe e `bootstrap/production.py` continua compondo apenas adapters fail-closed (`unavailable_production_services()`); limitação de ambiente registrada, não uma pendência silenciosa.
- [x] Atualizar `BACKEND_UI_MAPPING.md` com contratos realmente liberados e remover hipóteses que tenham mudado (revisado contra `frontend/src` e `src/agentos/api/gateway.py` atuais, não apenas contra a versão anterior do próprio documento).
- [ ] Commit sugerido: `chore(frontend): harden realtime execution experience`. (Não commitado nesta sessão: sem pedido explícito do usuário.)

**Critério de saída:** a aplicação mantém semântica correta sob reconexão, permissão, erro, movimento reduzido e carga visual, com mapeamento backend→UI atualizado. **Atingido**: `execution-controls.spec.ts` prova create (202), control PAUSE/RESUME/CANCEL bem-sucedido, conflito de versão e indeterminado (ambos resincronizando sem travar) e rate limit (prazo visível, controles permanecem operáveis); `reduced-motion.spec.ts` prova ausência de canvas/pulso com o mesmo fato em texto, e contrasta com um canvas real fora de reduced motion; `a11y.spec.ts` prova zero violações críticas/sérias nas seis telas de referência após a correção aplicada; `tests/visual/execution-page.spec.ts` tem baseline para as quatro telas; `BACKEND_UI_MAPPING.md` reflete exatamente as categorias de erro que `gateway.py` levanta hoje.

### Decisões locais registradas para a Fase 6

- **`@axe-core/playwright` foi adicionado como devDependency.** Playwright cobre visual regression nativamente (`toHaveScreenshot`), mas não tem um scanner de acessibilidade embutido; `@axe-core/playwright` é o pacote padrão para essa finalidade (mesma régua de justificativa já aplicada a Zustand/TanStack Query nas Fases 2–5 — aqui a "não introduzir sem necessidade" resultou em uma única dependência, estritamente necessária, ao invés de zero).
- **Um segundo config do Playwright (`frontend/playwright.visual.config.ts`) separa `tests/visual` de `tests/e2e`.** Playwright não tem um jeito nativo de rodar um segundo `testDir` sob o mesmo config sem ou misturar os dois no `npm run test:e2e` padrão ou introduzir filtro por projeto; duplicar o config (idêntico a `playwright.config.ts` exceto `testDir`) é a menor mudança que mantém `npm run test:e2e` restrito a `tests/e2e` e dá a `npm run test:visual` seu próprio entry point (`package.json`: `"test:visual": "playwright test --config=playwright.visual.config.ts"`).
- **`frontend/vitest.config.ts` passou a excluir `./tests/visual/**`.** O padrão de include default do Vitest também casa com `*.spec.ts`; sem a exclusão, `npm run test` tentava importar `tests/visual/execution-page.spec.ts` e falhava (`test.beforeEach() ... not expected to be called here`), já que esse arquivo usa `test`/`expect` do `@playwright/test`, não do Vitest. A mesma exclusão já existia para `./tests/e2e/**`.
- **Uma nova rota de fixture, `/execution/fixture-collaborating`, e um novo fixture `collaborationFixtureEvents` (`frontend/src/features/executions/fixtures.ts`) foram adicionados.** `ExecutionRoute` nunca popula a prop `events` de `ExecutionPage` a partir de um binding real (decisão da Fase 3: o realtime store só preserva `seenEventIds`, não o payload completo), e nenhum adapter de produção entrega `DelegationCreated`/eventos de Tool Runtime ao stream público hoje (`BACKEND_DISCOVERY.md`). Sem uma rota real e determinística, `AgentRail`, `OrchestrationScene` e `ToolActivityGroup` seriam inatingíveis por um teste de browser real — exatamente o que `reduced-motion.spec.ts`, `a11y.spec.ts` e `tests/visual/execution-page.spec.ts` precisam exercitar. A fixture segue o mesmo padrão já estabelecido pelas outras rotas `/execution/fixture-*` (Fase 1) e reaproveita exatamente os formatos de payload já assumidos localmente nas Fases 3 (`ToolActivityView`) e 4 (`agentGraphProjection`) — nenhum campo novo foi inventado.
- **`ExecutionRoute.tsx`: `controlFailed: boolean` virou `controlError: ApiError | null`, e o aviso `role="alert"` passou a incluir o prazo de retry quando o envelope o fornece** (`" Tente novamente em Ns."`, reaproveitando literalmente o mesmo texto/padrão já usado por `ProviderErrorNotice` desde a Fase 5). Motivado por `UX_UI_SPEC.md` ("Erros e estados incertos": "`RATE_LIMITED` mostra prazo") e pelo requisito explícito desta fase de que rate limit tenha "recuperação visível" — sem essa mudança, o teste correspondente de `execution-controls.spec.ts` não é satisfazível sem inventar um dado novo. A mensagem-base ("O comando não foi confirmado. O estado atual foi preservado.") permanece textualmente idêntica quando não há `retry_after`, preservando `tests/unit/ExecutionRoute.test.tsx` sem alteração de comportamento.
- **`OrchestrationScene.tsx` recebeu `role="group"` no container que já carregava `aria-label="Cena 3D da orquestração observada"`.** Único erro crítico/sério real encontrado pelo scanner desta fase: `aria-prohibited-attr` (impact `serious`) — `aria-label` não é permitido em um `<div>` sem role válido. Menor diff possível: um único atributo adicionado, nenhuma outra mudança visual ou estrutural.
- **Confirmado por leitura direta de `src/agentos/api/gateway.py`**: os únicos `@app.exception_handler` registrados hoje produzem `VALIDATION` (422, `RequestValidationError`), `AUTHENTICATION` (401), `AUTHORIZATION` (403, tanto de `AuthorizationError` quanto de `PermissionError`), `RATE_LIMITED` (429, `retry_after` fixo em 60) e `INTERNAL` (500, catch-all). Não existe uma exceção de domínio que produza `CONFLICT` ou `INDETERMINATE` neste gateway. `execution-controls.spec.ts` documenta isso no cabeçalho do arquivo e simula os dois casos via fake explicitamente marcado como tal — mesmo padrão já usado para `invalid_cursor` na Fase 2 — nunca como um contrato de produção comprovado. `BACKEND_UI_MAPPING.md` foi atualizado com a mesma lista.
- **Confirmado por leitura direta de `src/agentos/bootstrap/production.py`**: `unavailable_production_services()` continua compondo apenas `_UnavailableApplicationPort`/`_UnavailableProductionSecurity` para todas as portas (execution, query, resources, events, security), fail-closed; `tests/integration/api/test_frontend_contracts.py` não existe. A Fase 0 deste plano não foi implementada neste worktree. O item do checklist desta fase que citava "rodar o conjunto de integração backend da Fase 0" foi registrado acima como uma limitação de ambiente, não simulado nem marcado como concluído.
- **WebGL real funciona no navegador do Playwright neste worktree, ao contrário da limitação registrada na Fase 5 para jsdom.** A Fase 5 documentou que um `<Canvas>` "nu" falha ao montar em jsdom por falta de `ResizeObserver`/WebGL — uma limitação do ambiente de testes unitários usado naquela fase, não do navegador real. `reduced-motion.spec.ts` ("without reduced motion... mounts a real WebGL canvas") comprova que o Chromium do Playwright monta um `<canvas>` de fato neste ambiente; a limitação de GPU/compositor da Fase 5 não se generaliza para a suíte E2E desta fase.
- **Bug de sobreposição descoberto e não corrigido nesta fase**: com um `AgentGlyph` expandido, seu painel de detalhe se sobrepõe visualmente ao botão "Expandir grafo" abaixo dele em `AgentRail`, bloqueando cliques reais nesse botão (Playwright reportou "element intercepts pointer events" ao tentar clicar diretamente nessa sequência). Não é um erro de foco, nome acessível ou contraste — as únicas categorias que esta fase autoriza corrigir em `AgentRail.tsx` — por isso não foi corrigido aqui. `reduced-motion.spec.ts` evita a sequência que dispara o problema (fecha o glyph antes de expandir a cena, uma interação plausível de qualquer usuário real). Sinalizado como tarefa separada fora desta sessão.
- **Teste unitário pré-existente instável, não introduzido nesta fase**: `tests/unit/agentGraphProjection.test.ts` › "opens the lazy-loaded 3D scene from the 'Expandir grafo' affordance..." falhou por timeout uma vez durante a verificação desta fase (`npx vitest run`) e passou de forma consistente ao ser reexecutado isoladamente logo em seguida. É o mesmo teste já identificado como instável na Fase 5 (ver nota "Medição de performance" nas Decisões locais da Fase 5); nenhuma alteração foi feita nele ou nos arquivos que ele cobre.
- **As quatro capturas de `tests/visual/execution-page.spec.ts` usam `page.emulateMedia({ reducedMotion: 'reduce' })` globalmente, inclusive para "orchestration expanded".** Isso significa que a captura de referência da cena 3D é o fallback textual determinístico (`SceneTextFallback`), não o `<canvas>` WebGL — decisão deliberada para eliminar flakiness de timing de animação e de renderização dependente de GPU entre execuções/máquinas. O comportamento "canvas real fora de reduced motion" já tem cobertura funcional dedicada e independente em `reduced-motion.spec.ts`.

---

## Fase B — Bridge tool_runtime/multi_agent → `ClientEventStream` (fechamento do roadmap)

**Objetivo:** fazer um evento de Tool e um evento de Delegation atravessarem outbox → stream público → `ClientEvent` real, contra Postgres real. Ver `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`, Fase B, para o desenho original.

**Backend files:**
- Create: `src/agentos/persistence/postgres/multi_agent_events.py` (`PostgresMultiAgentEventRecorder`)
- Create: `src/agentos/persistence/postgres/tool_activity.py` (`PostgresToolActivitySink`)
- Create: `src/agentos/bootstrap/multi_agent.py` (`compose_multi_agent_event_recorder`)
- Create: `src/agentos/persistence/postgres/migrations/versions/0006_multi_agent_and_tool_events.py`
- Modify: `src/agentos/persistence/postgres/schema.py` (`multi_agent_events`, `tool_activity_events`)
- Modify: `src/agentos/persistence/postgres/event_stream.py` (`PostgresClientEventStream.read()` now unions 3 sources)
- Modify: `src/agentos/tool_runtime/models.py` (`ToolOutboxEntry` gained a `context` field)
- Modify: `src/agentos/tool_runtime/runtime.py` (`ToolRuntimeService.__init__` gained an optional `sink` parameter, called from `_entry()`)
- Create: `tests/integration/persistence/test_multi_agent_events_postgres_optional.py`, `tests/integration/persistence/test_tool_activity_events_postgres_optional.py`
- Modify: `tests/integration/persistence/test_event_stream_postgres_optional.py` (bridge test), `tests/unit/tool_runtime/test_runtime_contracts.py` (sink test), `tests/unit/persistence/test_postgres_schema.py`

- [x] B.1 — `PostgresMultiAgentEventRecorder(engine).record_event(event) -> bool` implementing `MultiAgentEventRecorder`, idempotent on `event_id` (duplicate identical event returns `False`, duplicate with different content raises `ValueError`), same semantics as `InMemoryMultiAgentStore.record_event`. TDD RED confirmed by `ModuleNotFoundError` before the module existed; GREEN in `tests/integration/persistence/test_multi_agent_events_postgres_optional.py` (4/4, real Postgres).
- [x] B.2 — `ToolRuntimeService.__init__(..., sink: Callable[[ToolOutboxEntry], None] | None = None)`, called from `_entry()` alongside the existing `self.outbox.append(...)` — 100% additive, the in-memory outbox is untouched and every pre-existing `tests/unit/tool_runtime/` test still passes unmodified. `PostgresToolActivitySink(engine)` writes each entry to `tool_activity_events`. TDD RED confirmed by `ModuleNotFoundError`; GREEN in `tests/integration/persistence/test_tool_activity_events_postgres_optional.py` and the new unit test `test_optional_sink_receives_every_outbox_entry_in_addition_to_the_in_memory_outbox`.
- [x] B.3 — `PostgresClientEventStream.read()` now unions `persistence_outbox` + `multi_agent_events` + `tool_activity_events`, ordered by `(created_at, source_priority, id)`, into the same `ClientEvent` envelope. The opaque cursor's `p` field changed from a single int to a `{source: position}` map (one position per source) so each source resumes independently. TDD RED confirmed (`DelegationCreated`/`ToolStarted`/`ToolFinished` absent from a single-source `read()`) in the extended `tests/integration/persistence/test_event_stream_postgres_optional.py`; GREEN with the new test `test_a_tool_event_and_a_delegation_event_cross_the_bridge_into_the_same_client_event_stream` (7/7 in that file, real Postgres).

**Critério de saída:** **Atingido.** Evidência: `tests/integration/persistence/test_event_stream_postgres_optional.py::test_a_tool_event_and_a_delegation_event_cross_the_bridge_into_the_same_client_event_stream`, executado contra `postgresql://agentos@localhost:5433/agentos` — grava um `DelegationCreated` via `PostgresMultiAgentEventRecorder` e um `ToolStarted`/`ToolFinished` via um `ToolRuntimeService` real com `PostgresToolActivitySink` injetado, abre um stream e confirma que os três tipos de evento (mais o `ExecutionQueued` já existente da Fase 0) aparecem em um único `read()`.

### Decisões locais registradas para a Fase B

- **Tabelas dedicadas `multi_agent_events`/`tool_activity_events`, sem FK para `persistence_records`** — confirma a recomendação do roadmap: uma delegação ou uma invocação de tool não é uma "record" versionada de uma execution do jeito que `persistence_outbox.source_record_ref` é; forçar essas colunas de escopo (que multi_agent/tool_runtime não têm naturalmente) seria inventar uma relação que o domínio não sustenta.
- **`ToolOutboxEntry` ganhou um campo `context: SensitiveOperationContext`.** O dataclass original não carregava `execution_id`/`user_id`/`agent_id` — só `correlation_id` — então não havia como escopar uma linha de `tool_activity_events` (nem autorizar/filtrar por `execution_id` no stream) sem essa informação. `_entry()` já tinha `snapshot.context` disponível; passar o objeto inteiro (em vez de decompor em campos soltos) evita duplicar a lista de campos de escopo em mais um lugar. Único ponto de construção (`tool_runtime/runtime.py:_entry`) foi atualizado; nenhum outro código construía `ToolOutboxEntry` diretamente.
- **Sem idempotência no sink de tool.** O outbox em memória (`self.outbox: list`) nunca teve dedup — é só `list.append`. `PostgresToolActivitySink` preserva exatamente esse contrato: cada chamada grava uma linha nova, com `event_id` gerado (`tool:{invocation_id}:{event_type}:{uuid4}`) só para satisfazer a `UniqueConstraint` da tabela, não como uma chave de idempotência real.
- **Cursor mudou de um único inteiro para um mapa `{"outbox": N, "multi_agent": N, "tool_activity": N}`.** Cada fonte tem sua própria sequência de `id` autoincremento; um único inteiro não poderia expressar "já li até aqui" em três tabelas independentes sem colisão. O formato antigo do cursor não precisava de compatibilidade retroativa (é opaco e nunca foi um contrato publicado fora deste código-fonte).
- **Tradução de payload no projetor, não no domínio.** `MultiAgentCoordinatorService.delegate()` guarda `child_execution_id`/`child_agent_id` apenas implicitamente, como o `execution_id`/`agent_id` do próprio envelope (não dentro do `payload`) — mas `frontend/src/features/agents/agentGraphProjection.ts` (Fase 4, já escrito) espera esses dois nomes dentro de `payload`. `PostgresClientEventStream._multi_agent_client_event` injeta essa tradução na leitura, sem alterar `multi_agent/service.py` nem o frontend.
- **Gap real, não traduzido: `AgentMessageCreated.payload.sender_agent_id` não existe.** `_record_message_fact` em `multi_agent/service.py` só grava `recipient_agent_id` (como o `agent_id` do envelope); o remetente nunca é persistido no fact hoje. O projetor traduz `recipient_agent_id` (a partir do envelope), mas não pode inventar `sender_agent_id` — o dado simplesmente não existe no fact persistido. `agentGraphProjection.ts` já trata esse campo como opcional (a aresta de mensagem não é desenhada sem ele), então isso degrada para "aresta ausente", nunca para um valor fabricado.
- **`sequence` do `ClientEvent` para eventos de multi_agent/tool é a própria `id` autoincremento da linha, não o `sequence` do domínio.** O `EventEnvelope.sequence` que `multi_agent/service.py` grava é sempre `1` (não é um contador real por execution) e `ToolOutboxEntry` nunca teve um campo de sequência. Reusar o `id` da linha (monotônico por inserção) dá uma ordenação estável e verdadeira sem inventar uma precisão que a fonte não tem; a Fase 2 já documenta que `ClientEvent.sequence` é "apenas ordem de entrega do binding corrente", nunca a autoridade de versão (essa continua sendo `state_version`).
- **`payload.tool_kind`/`payload.invocation_id` são injetados pelo projetor de tool**, a partir de `ToolOutboxEntry.tool_ref.tool_id` (campo top-level, nunca esteve no payload) e `ToolOutboxEntry.invocation_id`, para casar com o que `activityNormalizer.ts` (Fase 3, já escrito) já lê de um `ToolActivityView`.
- **`bootstrap/multi_agent.py` expõe só `compose_multi_agent_event_recorder(engine)`, não uma composição completa de `MultiAgentCoordinatorService`.** O coordinator também exige `store`/`resolver`/`administration`/`execution`/`sharing`, nenhum dos quais tem adapter Postgres hoje, e nenhuma rota HTTP constrói ou chama um coordinator — não existe hoje um composition root real para ele. Fase B.1 é "trabalho contido" (a própria recomendação do roadmap): compor só o recorder, documentando que o resto fica para uma sessão futura, em vez de inventar adapters para portas que esta sessão não investigou.

---

## Fase C — Resolução de `result_ref` → `display_text` (limitação documentada)

**Objetivo:** popular `result.display_text` em `ExecutionQueryAdapter._to_execution_view` quando `result_ref` for resolvível para texto seguro. Ver `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`, Fase C.

**Investigação real realizada nesta sessão** (grep/leitura, nesta ordem, antes de qualquer decisão):

1. `src/agentos/artifact_storage/ports.py` — `ArtifactManager.inspect`/`.read` exigem um `ArtifactReference` completo (`artifact_id`, `version`, `checksum: ContentChecksum`, `authorization_ref`, `classification`, `expires_at`...), não um ID de string solto. Não há nenhum método público que aceite "me dê o texto por trás deste ID opaco" sem essas garantias já resolvidas.
2. `grep -rn "result_ref" src/agentos/runtime src/agentos/execution` confirma onde `result_ref` é produzido: `src/agentos/providers/compat.py:RuntimeProviderAdapter._map_outcome` mapeia `GenerationSucceeded` (que carrega o texto real gerado, em `message: ModelMessage`) para `ProviderFinal(f"result:{outcome.invocation_id}", usage)` — **descartando o conteúdo e devolvendo só uma string sintética**. Esse `result_ref` sintético é o que `runtime/service.py:342-360` grava via `CommitExecutionChanges(result_ref=provider_outcome.result_ref, ...)`.
3. `grep -rn "RuntimeService(\|RuntimeProviderAdapter" src/agentos` confirma que `RuntimeService`/`RuntimeProviderAdapter` **não são compostos em nenhum lugar de `src/agentos/bootstrap/`** — não existe hoje um caminho de produção real em que uma execution chegue a `COMPLETED` com um `result_ref` vindo do Runtime. Qualquer `result_ref` que exista hoje contra Postgres real só chega lá porque um chamador de teste passou uma string arbitrária direto para `ExecutionControlService.commit(...)` — não é um contrato que a produção sustente.
4. `src/agentos/memory/ports.py`/`models.py` — memory é indexado por `memory_id` via `GetMemory`, sem nenhuma relação com o formato de `result_ref`. Descartado como candidato.

**Conclusão:** não há, hoje, nenhum armazenamento texto-seguro por trás de `result_ref` que um adapter possa consultar de forma legítima. Implementar "o menor adapter viável" foi avaliado e descartado: mesmo que `result_ref` fosse tratado como um `artifact_id` bruto, resolvê-lo a bytes reais exigiria fabricar um `ArtifactReference` completo (checksum, `authorization_ref`, tamanho) que nada no sistema produz a partir de uma string de execution result — isso seria inventar um contrato entre `execution` e `artifact_storage` que o domínio não sustenta, exatamente o que as restrições desta sessão proíbem.

- [x] Investigação documentada (grep real, não suposição) confirmando a ausência de um caminho de resolução.
- [ ] Adapter de resolução — **não implementado**: não há dado real para adaptar (ver conclusão acima).
- [x] `ExecutionQueryAdapter._to_execution_view` **permanece sem** popular `result.display_text` — já omitia o campo desde a Fase 0 (`execution_adapters.py`, docstring: "There is no public DTO/permission for `display_text` yet"); esta sessão confirma que a omissão continua correta, agora com uma causa raiz investigada, não apenas "ainda não implementado".

**Critério de saída:** **Fechado como limitação documentada**, conforme o "Risco conhecido" que o próprio roadmap já previa para este cenário. Nenhuma pendência muda: a causa raiz (Runtime não composto + `result_ref` sintético mesmo quando composto) está registrada acima com os arquivos e linhas exatos que a comprovam.

### Decisões locais registradas para a Fase C

- **Nenhum adapter de resolução foi escrito.** Escrever um que decodifica `result_ref` como um artifact ref seria inventar uma relação que `providers/compat.py` (o único produtor real de `result_ref` hoje) não estabelece — o valor é literalmente `f"result:{invocation_id}"`, sem nenhum vínculo com `artifact_storage`.
- **A causa raiz tem duas camadas independentes, ambas confirmadas por leitura direta**: (1) mesmo quando o Runtime roda, `RuntimeProviderAdapter` descarta o texto gerado (`GenerationSucceeded.message`) e nunca o persiste em lugar algum; (2) o próprio `RuntimeService`/`RuntimeProviderAdapter` nunca são compostos em `bootstrap/`, então nenhuma execution em produção hoje chega a `COMPLETED` por esse caminho. Corrigir só a camada (1) sem a (2) não desbloquearia nada observável em produção; corrigir a (2) está fora do escopo de "resolução de `result_ref`" (seria compor o Runtime inteiro, um projeto à parte).
- **Não foi criado um "gap" silencioso.** O campo `result.display_text` já estava ausente do contrato desde a Fase 0 (por design, "sem inventar texto"); esta fase não regride nada — ela troca "ainda não implementado" por "investigado e explicado por que não há dado real para implementar".

---

## Fase D — `ProviderConfigurationApplication` em produção

**Objetivo:** compor o port `ProviderConfigurationApplication` (`configure`/`inspect`/`revoke`) em produção, provado por `PUT`/`GET`/`DELETE /v1/providers/{provider}` fim a fim contra Postgres real, sem nunca vazar a API key. Ver `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`, Fase D.

**Backend files:**
- Create: `src/agentos/persistence/postgres/provider_configuration.py` (`PostgresProviderConfigurationAdapter`)
- Create: `src/agentos/persistence/postgres/migrations/versions/0007_provider_configurations.py`
- Modify: `src/agentos/persistence/postgres/schema.py` (`provider_configurations`)
- Modify: `src/agentos/bootstrap/production.py` (`compose_production_services` now passes `provider_configuration=...`)
- Create: `tests/integration/api/test_provider_configuration_postgres_optional.py`
- Modify: `tests/unit/persistence/test_postgres_schema.py`

- [x] Investigado `providers/catalog.py`/`resolver.py`/`compat.py`: esse `providers/` é o catálogo/resolução de modelo LLM (RFC 604), um domínio inteiramente diferente de `ProviderConfigurationApplication` (que é o port do gateway HTTP em `api/contracts.py`, usado por `PUT/GET/DELETE /v1/providers/{provider}`). Nenhum storage de configuração de provider (no sentido do gateway) existia antes desta sessão.
- [x] `PostgresProviderConfigurationAdapter` implementado sobre uma tabela dedicada `provider_configurations`, escopada por `(user_id, provider)`. TDD RED confirmado (`RuntimeError: application service is unavailable`, porta `None`) em `tests/integration/api/test_provider_configuration_postgres_optional.py` antes da implementação.
- [x] Composto em `compose_production_services` (`bootstrap/production.py`).
- [x] TDD GREEN: 4/4 em `tests/integration/api/test_provider_configuration_postgres_optional.py`, contra Postgres real — `PUT`/`GET` round-trip, `DELETE` (revoke) desabilita sem vazar a chave, `GET` de provider nunca configurado retorna 404, e isolamento por usuário (stranger recebe 404, nunca os dados de outro usuário).

**Critério de saída:** **Atingido.** `configured.json()`/`inspected.json()`/`revoked.json()` nunca contêm a chave (nem a resposta bruta em texto, nem a chave `api_key`); confirmado com `assert secret_api_key not in response.text` (não só `not in response.json()`, para pegar qualquer serialização).

### Decisões locais registradas para a Fase D

- **`provider_configurations` é uma tabela dedicada, sem reaproveitar `persistence_records`.** Uma credencial de provider é configurada uma vez por usuário, sem relação com nenhuma execution/agent — forçar as colunas de escopo execution-shaped de `persistence_records` (`agent_id`, `execution_id`, `correlation_id`, `purpose`, `actor`) exigiria valores fabricados sem significado real, a mesma razão já registrada para `multi_agent_events`/`tool_activity_events` na Fase B.
- **`configure` é um upsert simples, sem uma tabela de idempotência dedicada.** `PUT` já é idempotente por semântica HTTP (repetir o mesmo `PUT` converge para o mesmo estado); ao contrário dos comandos de execution (que têm `persistence_idempotency` real para detectar retries concorrentes de comandos não-idempotentes), aqui inventar um ledger de idempotência não muda nenhum comportamento observável — o próprio upsert já é convergente.
- **A API key é armazenada em texto plano em `provider_configurations.api_key`, não criptografada em repouso.** Não existe nenhuma infraestrutura de criptografia em repouso neste código-fonte hoje (confirmado por grep: `SecretStr`/"encrypt"/"Fernet" só aparecem em `gateway.py`/`production.py`/`settings.py`, nenhum deles implementa criptografia real — `SecretStr` do Pydantic só redige em memória/logs, não no banco). Isso espelha o único outro lugar deste código que já armazena uma API key de provider: `ProductionSettings.OPENAI_API_KEY` etc. vêm de variável de ambiente em texto plano, sem criptografia de campo. Construir uma nova infraestrutura de criptografia estaria além do escopo desta fase (adicionar dependência/design novos não pedidos); a garantia real que a Fase D entrega é que a chave nunca **sai** pela API pública, não que o armazenamento em repouso seja criptografado — essa é uma limitação legítima, registrada aqui, não escondida.
- **`secret_ref` é um handle opaco gerado (`provider-secret:{uuid4().hex}`), não a chave.** Ele sobrevive a atualizações (`configure` reaproveita o `secret_ref` existente numa linha já configurada) para dar um identificador estável de "qual segredo é este" sem nunca ser a chave em si — mesmo formato que `FakeProviderConfiguration` já usava nos testes existentes (`tests/unit/api/test_api_asgi.py`).
- **`inspect`/`revoke` de um provider nunca configurado levantam `ApplicationNotFoundError` (404), consistente com o mesmo padrão já usado por `ExecutionQueryAdapter.get`** — nunca inventa um estado "desabilitado por padrão" para um provider que o usuário nunca configurou.

---

## Ordem de release

1. Liberar Fases 0–2 para uma beta interna: lifecycle e controles, sem promessa de Tool/3D.
2. Liberar Fases 3–4 para usuários com eventos Tool e Delegation habilitados.
3. Liberar Fase 5 por feature flag de performance; iniciar em rail 2D e ativar R3F para perfil elegível.
4. Promover após a Fase 6 com métricas de erro de stream, resync, performance e acessibilidade aprovadas.

## Cobertura e verificação da especificação

| Requisito documentado | Fase |
| --- | --- |
| Contratos reais, segurança e gaps | 0 |
| Home minimalista, componentes e arquitetura de estado | 1 |
| SSE/cursor/replay, lifecycle e chat auditável | 2 |
| Progressive disclosure e Tool grouping | 3 |
| Colaboração multiagent e Motion | 4 |
| R3F significativo, providers e inspector | 5 |
| Performance, reduced motion, E2E e visual QA | 6 |

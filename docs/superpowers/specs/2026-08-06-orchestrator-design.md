# Orchestrator Design — RFC 202

**Status:** implementado no escopo de contratos e adapters de referência em 2026-08-06.

## Objetivo e limites

O pacote `agentos.orchestrator` é um plano de controle: recebe intenções autorizadas, registra planos versionados, avalia dependências e janela de agendamento, cria uma `Execution` por tentativa através de `ExecutionControl`, solicita despacho mínimo e propaga cancelamento/retry por portas públicas. Ele não executa Provider, LLM, Tool, Capability ou Context e não conhece banco, fila, worker, scheduler físico, broker ou SDK.

O pacote não cria uma segunda máquina de estados de `Execution` nem uma segunda porta de persistência. `PlanStorePort` é uma fachada estreita para a transação canônica de RFC 601; o adapter in-memory implementa a mesma semântica para testes. A publicação de eventos continua sendo posterior ao `COMMITTED` da outbox.

## Alternativas consideradas

1. **Acoplamento direto aos formatos de Execution:** menor número de tipos, mas mistura estado de plano com estado de tentativa e dificulta a unidade plano + outbox.
2. **Workflow/DAG engine genérico:** flexível, mas cria estados, semânticas de retry e scheduler fora da RFC 202.
3. **Plano de controle fino (escolhida):** modelos imutáveis de plano/DAG; runtime de tentativas separado no `PlanStorePort`; `ExecutionFactory` traduz uma tentativa para `ExecutionControl`; scheduling, dispatch e supervision são Protocols lógicos.

## Modelo

`OrchestrationPlan` pertence a `Ownership(user_id, workspace_id)`, possui actor, purpose, correlation, classificação, versão e tuplas imutáveis de `PlannedWork` e `DependencyEdge`. Cada work declara Agent, `TaskSnapshot`, limites, finalidade, classificação, chave idempotente, schedule/deadline e política de falha. A validação ocorre antes de persistir: IDs e referências são únicos/válidos, edges não duplicadas nem cíclicas, condições e políticas são conhecidas, bounds são positivos e datas são timezone-aware.

O adapter mantém, por `(plan_id, version, work_id)`, no máximo uma tentativa materializada e a referência de expiração/cancelamento. Essa projeção não é uma Execution e não autoriza transições. Retry de uma tentativa terminal chama `ExecutionFactory` com nova chave e nova identidade, preservando correlação e causalidade.

## Fluxos

- `submit` valida actor/owner/Agent/purpose, calcula fingerprint canônico bounded e grava o plano com `OrchestrationSubmitted`/`PlanVersionCreated` na mesma unidade atômica. Chave igual e fingerprint igual retorna o mesmo receipt; fingerprint divergente retorna conflito sanitizado. `NOT_COMMITTED` não produz plano; `UNKNOWN` exige `inspect_commit` antes de qualquer retry ou afirmação.
- `evaluate` relê e reautoriza o plano, aplica relógio e dependências, limita paralelismo, resolve Agent/configuração novamente e só então materializa work elegível. A mudança de plano/projeção e seu evento mínimo são confirmados antes de `DispatchPort`; falha posterior fica reconciliável.
- Antes de `not_before`, work permanece planejado. Depois de `expires_at`, não há Execution; uma decisão de expiração e `OrchestrationExpired` são registradas. `SchedulingPort` apenas registra/cancela triggers.
- `request_cancel` impede novas materializações, cancela triggers e solicita `ExecutionControl.request_cancel` com versão esperada apenas para tentativas não terminais. Terminais não são reabertos e fatos já confirmados não são desfeitos.
- `request_retry` só aceita tentativa terminal autorizada, cria nova Execution em `QUEUED`, nova chave, causalidade explícita e relação com a tentativa anterior.

## Contratos e segurança

`DispatchRequest` carrega somente execution id, versão esperada, classe de processamento, correlação, purpose e chave. Events carregam IDs, versões, ownership, causa, correlação, purpose e classificação; payloads passam por bounds/sanitização e nunca incluem prompt, Context, Memory, credencial, resposta, histórico, SQL ou exceção tecnológica. ID conhecido não concede acesso; falhas cross-owner são indistinguíveis de ausência.

`UNKNOWN` usa `inspect_commit` do PlanStore antes de retry. `COMMITTED` pode ser retornado como receipt idempotente; `NOT_COMMITTED` permite nova tentativa segura somente com a mesma intenção/fingerprint. Conflitos de versão exigem releitura.

## Arquivos e adapters

- `models.py`: tipos imutáveis, bounds, políticas, requests, receipts e outcomes.
- `ports.py`: Protocols do Orchestrator, factory, plano, schedule, dispatch e supervision.
- `security.py`: fingerprint, DAG, ownership, classificação, refs e erros públicos.
- `in_memory.py`: `InMemoryPlanStore`, scheduling, dispatch, supervision, factory e clock de referência.
- `compat.py`: `ExecutionControlExecutionFactory`, comandos de cancelamento/retomada e tradução de Agent/Administração, persistência e Events sem importar tecnologia.
- `__init__.py`: exports públicos estáveis.

## Verificação e limitações

A suíte nova cobre bounds, DAG, ownership, idempotência, UNKNOWN, expiração, materialização única, retry, cancelamento, dispatch mínimo, supervisão observacional, revalidação de Agent/config, outbox e fronteiras. A suíte existente continua obrigatória. PostgreSQL permanece teste opcional pulado quando `AGENTOS_TEST_POSTGRES_DSN` não está configurado. Scheduler físico, worker pool, broker, fila, lease, recuperação distribuída, DR e RFC 203 continuam fora do pacote.

## Auditoria normativa

RFCs 050/060: modelos imutáveis, bounds, purpose, ownership e referências opacas; RFCs 101/102: uma Execution por tentativa, `QUEUED`, versões esperadas e terminais imutáveis; RFC 103: eventos mínimos em outbox, sem publicação direta; RFC 104: apenas referências de seed, sem composição de Context; RFC 201: Agent/config revalidado e administração via Execution; RFC 202: planos, DAG, schedule, dispatch, cancelamento, retry e supervisão; RFC 203: não implementada; RFC 601: PlanStore delega a transação canônica e inspeciona `COMMITTED/NOT_COMMITTED/UNKNOWN`. ADRs 002/009/012/013 permanecem satisfeitas por ausência de dependência tecnológica no pacote.

## Evidência a registrar ao concluir

O plano de implementação deve registrar commits, arquivos alterados, resultado fresco de `python -m pytest -q`, `python -m compileall -q src tests`, scan de dependências proibidas no pacote e transversal, `git diff --check`, `git status --short --branch` e o motivo de eventual skip PostgreSQL.

## Verificação final de 2026-08-06

- `python -m pytest -q`: **280 passed, 1 skipped** em 3.01s; o único skip é o teste PostgreSQL opcional sem `AGENTOS_TEST_POSTGRES_DSN`.
- `python -m compileall -q src tests`: passou.
- Scan obrigatório de dependências no pacote: zero matches (exit 1 por ausência de resultados).
- Scan transversal dos domínios existentes: zero imports de `agentos.orchestrator` e zero tokens proibidos.
- `git diff --check`: passou; warnings restantes são apenas normalização LF/CRLF de arquivos preexistentes.
- Commits próprios: `c5c5901`, `37b5cbe`, `915974d`, `da9c263`, `e029d84`, `0d501ef`, `15054e8`, `d5b2709`.
- Arquivos próprios: `src/agentos/orchestrator/{__init__,models,ports,security,in_memory,compat,service}.py`, `tests/unit/orchestrator/*.py`, esta spec e o plano correspondente.

O review independente identificou e foi incorporado: não registrar trigger após `UNKNOWN`, inspeção obrigatória antes de retry da mesma tentativa, IDs determinísticos por chave na bridge do Kernel, ownership nos adapters de schedule/supervisão, pending reconciliation para evitar segunda Execution, limite agregado de fingerprint, limite de retries, handlers de falha, expiração inclusiva e cancelamento de triggers após commit.

Limitações residuais explícitas: o adapter in-memory é referência de processo e não substitui um PlanStore persistente; a recuperação distribuída de lease, o scheduler físico, pool de workers, broker/fila, retry distribuído, DR e PostgreSQL do Orchestrator ainda exigem adapters futuros sob RFC 601. RFC 203 — Multi-agent permanece fora do escopo.

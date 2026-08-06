# Prompt da próxima sessão — Orchestrator do AgentOS

Você é responsável por planejar e implementar completamente o próximo
subsistema backend do AgentOS: o Orchestrator da RFC 202.

Não entregue uma análise parcial. Só encerre quando o subsistema estiver
implementado dentro do escopo normativo, testado, auditado contra as docs e
com as limitações restantes registradas. A resposta final da sessão deve ser
curta e baseada em evidências frescas; não declare sucesso com testes ou
requisitos ainda não verificados.

## Estado atual do workspace

O repositório já contém, em escopo de contratos e adapters de referência:

- `agentos.execution`: máquina de estados, `ExecutionControl`, ownership,
  idempotência, versões otimistas, cancelamento, pausa/retomada e resultados;
- `agentos.runtime`: loop público, accounting, deadlines, recuperação e
  integração somente por portas;
- `agentos.context`: pipeline efêmero, snapshots, manifestos, orçamento,
  proveniência e sanitização;
- `agentos.providers`: Provider API, Model Catalog, resolução determinística,
  pricing, fallback e adapters de compatibilidade;
- `agentos.events`: envelope canônico, EventBus, archive, replay, outbox,
  deduplicação, ordering, ownership e classificação;
- `agentos.agents`: identidade versionada, resolução autorizada, administração,
  snapshots imutáveis, estados ACTIVE/SUSPENDED/ARCHIVED e compatibilidade com
  o Kernel;
- `agentos.persistence`: porta canônica RFC 601, adapter in-memory, adapter
  SQLAlchemy/PostgreSQL isolado, migrations Alembic explícitas, atomicidade,
  idempotência, recuperação `COMMITTED/NOT_COMMITTED/UNKNOWN`, outbox e
  bridge de compatibilidade;
- suíte atual verde: `248 passed, 1 skipped`; o teste PostgreSQL opcional é
  pulado quando `AGENTOS_TEST_POSTGRES_DSN` não está configurado.

O próximo pacote ainda não existe: `src/agentos/orchestrator/`.
RFC 203 — Multi-agent, Scheduler concreto, Workers, broker e API continuam
fora desta sessão.

Preserve alterações preexistentes no working tree. Antes de editar, inspecione
`git status`, histórico recente, contratos atuais e testes; não faça reset,
checkout destrutivo ou limpeza ampla.

## Leitura obrigatória antes de editar

Leia integralmente:

- `docs/architecture/000-overview.md`
- `docs/architecture/050-design-principles.md`
- `docs/architecture/060-glossary-and-conventions.md`
- `docs/architecture/100-kernel/101-runtime.md`
- `docs/architecture/100-kernel/102-execution-lifecycle.md`
- `docs/architecture/100-kernel/103-event-system.md`
- `docs/architecture/100-kernel/104-context-pipeline.md`
- `docs/architecture/200-agents/201-agent.md`
- `docs/architecture/200-agents/202-orchestrator.md`
- `docs/architecture/200-agents/203-multi-agent.md`
- `docs/architecture/500-providers-models/501-provider-api.md`
- `docs/architecture/500-providers-models/502-model-catalog.md`
- `docs/architecture/600-platform-data/601-persistence.md`
- `docs/adr/002-postgresql-as-system-of-record.md`
- `docs/adr/009-redis-for-ephemeral-coordination.md`
- `docs/adr/012-sqlalchemy-alembic-persistence-adapters.md`
- `docs/adr/013-asyncio-concurrency-runtime.md`

Inspecione também:

- `src/agentos/execution/`
- `src/agentos/runtime/`
- `src/agentos/context/`
- `src/agentos/providers/`
- `src/agentos/events/`
- `src/agentos/agents/`
- `src/agentos/persistence/`
- `tests/unit/execution/`
- `tests/unit/runtime/`
- `tests/unit/context/`
- `tests/unit/providers/`
- `tests/unit/events/`
- `tests/unit/agents/`
- `tests/unit/persistence/`
- `tests/unit/integration/`
- `docs/superpowers/specs/2026-08-06-agent-design.md`
- `docs/superpowers/plans/2026-08-06-agent.md`
- `docs/superpowers/specs/2026-08-06-persistence-design.md`
- `docs/superpowers/plans/2026-08-06-persistence.md`

Não comece editando código. Faça um brainstorming curto e técnico, proponha
as alternativas de desenho e escolha uma. Depois registre:

- `docs/superpowers/specs/2026-08-06-orchestrator-design.md`
- `docs/superpowers/plans/2026-08-06-orchestrator.md`

Só então execute o plano inline em TDD, com commits pequenos e verificáveis.

## Objetivo

Implementar o Orchestrator como plano de controle que transforma intenções
autorizadas em Executions, coordena planos e dependências, materializa trabalho
somente quando elegível, solicita despacho mínimo, propaga cancelamento,
coordena retries como novas Executions e supervisiona progresso por portas.

O Orchestrator decide quando e qual trabalho autorizado deve virar ou retomar
uma Execution. `ExecutionControl` continua sendo a autoridade da máquina de
estados; Runtime governa uma Execution adquirida; ContextManager monta Context;
Providers executam somente atrás de suas portas; Persistência confirma fatos e
outbox. O Orchestrator nunca executa LLM, Provider, Tool ou Capability.

## Escopo obrigatório

Crie um pacote canônico `src/agentos/orchestrator/`, mantendo responsabilidades
separadas e dependências somente em contratos públicos:

- `models.py`: planos, trabalhos planejados, dependências, políticas,
  schedules, triggers, comandos, receipts, outcomes e referências opacas;
- `ports.py`: `Orchestrator`, `ExecutionFactory`, `SchedulingPort`,
  `DispatchPort`, `SupervisionPort`, `PlanStorePort` e portas administrativas
  estreitas necessárias para Agent/Execution;
- `security.py`: ownership, finalidade, idempotência, limites, DAG,
  classificação, sanitização e ausência de conteúdo sensível;
- `in_memory.py`: adapters substituíveis para PlanStore, scheduling,
  dispatch, supervision e coordenação de referência/testes;
- `compat.py`: traduções mínimas para `ExecutionControl`, Agent,
  `TransactionalPersistence` e Events, sem vazar adapters concretos;
- `__init__.py`: somente exports públicos estáveis.

Alinhe os nomes e resultados aos contratos já existentes. Não crie uma segunda
máquina de estados de Execution nem uma segunda porta de persistência sem uma
justificativa explícita na spec.

### Submit e idempotência

`submit` deve aceitar intenção autenticada com `user_id`, `workspace_id`,
actor, Agent(s), correlação, causa, purpose, timestamp e idempotency key.

Cubra pelo menos:

- `RunAgentTask` com Agent/configuração autorizados, Task snapshot e limites;
- `AdministerAgent` delegando a administração pela porta apropriada;
- `ExecutePlan` com plano acíclico e dependências válidas;
- `ContinueExecution` somente quando o contrato existente permitir retomada;
- fingerprint estável e bounded;
- mesma chave + mesmo fingerprint retornando o mesmo receipt;
- mesma chave + fingerprint divergente produzindo conflito sanitizado;
- nenhuma criação parcial após rejeição, cancelamento pré-confirmação,
  `NOT_COMMITTED` ou falha;
- `UNKNOWN` exigindo `inspect_commit` antes de retry ou afirmação de sucesso.

### Planos, DAG e materialização

`OrchestrationPlan` deve ser versionado, pertencente ao owner e Workspace
corretos, com `PlannedWork` imutável por versão e edges explícitas.

Valide antes de persistir ou materializar:

- IDs únicos, referências existentes e ausência de ciclos;
- dependências sem auto-loop e sem edges duplicadas;
- `COMPLETED`, `TERMINAL` e `RESULT_MATCHED` conforme contrato público;
- `DO_NOT_MATERIALIZE`, handler de falha e cancelamento relacionado;
- Agent ativo, config version resolvida e autorizada;
- limites e deadlines bounded;
- `maximum_parallel_executions` e política de cancelamento;
- ownership, purpose, correlation e classificação em todas as decisões.

Um node só vira tentativa concreta quando todas as pré-condições estiverem
satisfeitas. Cada tentativa materializada recebe sua própria Execution em
`QUEUED`, nunca reutiliza Execution terminal e nunca contorna `ExecutionControl`.
Retry de uma tentativa terminal cria nova Execution com nova idempotency key,
mantendo relação explícita com a tentativa anterior.

### Agendamento, dispatch e supervisão

Implemente somente contratos lógicos:

- antes de `not_before`, o trabalho permanece planejado;
- depois de `expires_at`, não materialize e registre resultado/política de
  expiração;
- `SchedulingPort` registra/cancela triggers sem timer, thread ou scheduler
  concreto;
- `DispatchPort` recebe somente `execution_id`, versão esperada, classe de
  processamento, correlação, purpose e idempotency key;
- nenhum dispatch contém prompt, Context, Memory, credencial, resposta,
  histórico ou payload proprietário;
- `SupervisionPort` observa estado/progresso e não muta storage;
- recuperação, cancelamento e timeout operacional passam por comandos e
  versões esperadas das portas do Kernel;
- não mantenha worker ocupado durante espera longa que possa ser retomada por
  Event.

### Cancelamento, falha e continuidade

`request_cancel` deve respeitar owner, versão e política explícita:

- cancelamento antes da materialização impede novas tentativas;
- cancelamento após materialização solicita cancelamento pela porta do Kernel;
- Execution terminal não é reaberta;
- fato confirmado não é desfeito por falha posterior de dispatch/publicação;
- falha de entrega permanece reconciliável pela outbox;
- timeout operacional não é convertido silenciosamente em cancelamento, falha
  ou sucesso;
- cancelamento e falha de predecessor seguem a `DependencyFailurePolicy`.

### Events, outbox e persistência

O Orchestrator não publica diretamente no EventBus. Quando uma mudança é
confirmada, produza somente envelopes mínimos e entradas de outbox através das
portas existentes. A unidade conceitual deve manter mudança de plano, estado
necessário e outbox coerentes; publicação acontece depois de `COMMITTED`.

Eventos possíveis incluem apenas fatos necessários, por exemplo:

- `OrchestrationSubmitted`;
- `PlanVersionCreated`;
- `WorkMaterialized`;
- `WorkDispatchRequested`;
- `OrchestrationCancelled`;
- `OrchestrationExpired`;
- `RetryMaterialized`.

Não invente eventos só para aumentar cobertura. Cada Event deve carregar IDs,
versões, ownership, correlação, causa, purpose e referências mínimas; nunca
prompt, Context integral, Memory, segredo, credencial ou output proprietário.

## Segurança e fronteiras

- `workspace_id = null` significa somente escopo estritamente do usuário;
- conhecer `plan_id`, `work_id`, `execution_id`, correlação ou Agent ID não
  concede acesso;
- queries cross-user/cross-Workspace/cross-Agent não revelam existência;
- toda resolução revalida owner, Agent, configuração, purpose, grants e
  classificação;
- referências são opacas e bounded;
- erros, `repr`, logs e Events não contêm SQL, credenciais, prompts, payloads
  proprietários ou exceções tecnológicas;
- Runtime, Agent, Events, Context, Providers e Persistence não importam o
  pacote concreto de Orchestrator; integração é por Protocols;
- não importar FastAPI, HTTP, SDK de Provider, SQLAlchemy, Alembic, Redis,
  filesystem, broker, fila, scheduler concreto, worker ou storage tecnológico.

## Fora de escopo explícito

Não implemente nesta sessão:

- RFC 203 Multi-agent, delegação, handoff ou collaboration;
- Scheduler físico, Worker pool, broker, fila, lease ou Redis;
- Provider concreto, loop LLM, Tool, Capability, Skill ou Resource;
- composição de Context, Memory, Artifact, Workspace ou Configuration;
- endpoint, FastAPI, SSE, autenticação de transporte ou UI;
- autoscaling, SLO, prioridade comercial ou retry distribuído;
- DAG engine genérico, workflow engine ou exactly-once;
- nova máquina de estados de Execution ou persistência paralela.

## Processo obrigatório

1. Use `superpowers:brainstorming` antes do desenho/implementação.
2. Leia integralmente as RFCs, ADRs, specs, planos, código e testes listados.
3. Registre a spec e o plano antes de tocar no código.
4. Use `superpowers:test-driven-development`: RED observado, GREEN mínimo,
   refatoração somente com testes verdes.
5. Use `superpowers:executing-plans` para executar o plano e checkpoints.
6. Use `superpowers:systematic-debugging` antes de corrigir qualquer falha
   inesperada.
7. Faça revisão técnica e trate bloqueadores antes de declarar conclusão.
8. Preserve alterações alheias e reporte honestamente o estado do Git.

## Testes obrigatórios

Cubra pelo menos:

- contratos, bounds, timestamps, referências e sanitização;
- ownership completo e ausência de vazamento em `get/list/resolve/evaluate`;
- plano acíclico, edges inválidas, DAG, conditions e failure policies;
- submit idempotente, fingerprint divergente e conflito de versão;
- dependências prontas, não prontas, falhas e resultado incompatível;
- `not_before`, expiração e não materialização fora da janela;
- uma única materialização por work/version e retry como nova Execution;
- cancelamento antes/depois da materialização e terminalidade;
- dispatch mínimo, idempotente e sem payload sensível;
- supervisão somente observacional e recuperação por versão esperada;
- Agent/config snapshot revalidado antes de criar Execution;
- estado de plano + outbox confirmados juntos pelo contrato de persistência;
- `NOT_COMMITTED`, `COMMITTED`, `UNKNOWN`, inspeção e retry seguro;
- eventos mínimos, causalidade, ownership, classificação e sequência;
- nenhuma publicação antecipada ou mutação direta de Execution;
- Runtime/Agent/Events/Context/Providers/Persistence sem dependência concreta
  do Orchestrator;
- suíte existente completa sem regressões.

## Verificação obrigatória

Execute ao final, com saída fresca:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit|broker|scheduler|worker" src/agentos/orchestrator
git diff --check
git status --short --branch
```

O scan do pacote Orchestrator deve retornar zero matches. Faça também uma
varredura transversal para provar que nenhum domínio existente ganhou uma
dependência concreta indevida.

Audite requisito por requisito contra RFCs 050, 060, 101, 102, 103, 104, 201,
202, 203 e 601, além das ADRs 002, 009, 012 e 013. Registre na spec/plano:

- evidência de cada requisito coberto;
- testes PostgreSQL opcionais, caso executados, ou o motivo do skip;
- limitações de scheduler/worker/broker/lease/DR que permanecem fora do
  escopo;
- commits e arquivos alterados.

Só declare o subsistema concluído quando a implementação, testes, fronteiras,
docs e limitações estiverem coerentes. Depois desta sessão, o próximo domínio
normativo será RFC 203 — Multi-agent, ainda sujeito a novo planejamento.

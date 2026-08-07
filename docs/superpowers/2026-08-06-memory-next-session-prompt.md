# Prompt da próxima sessão — Memory do AgentOS

Você vai implementar o próximo subsistema backend ainda ausente no AgentOS:
Memory, conforme a RFC 301.

O repositório já possui contratos e adapters de referência para Execution,
Runtime, Context, Events, Agents, Providers, Persistence, Orchestrator e
Multi-agent. Memory deve entrar como domínio independente, sem transformar
Context em armazenamento persistente e sem introduzir uma tecnologia concreta
de busca ou banco.

## Estado atual do workspace

O estado verificado da sessão anterior é:

- agentos.persistence: porta RFC 601, adapter in-memory, adapter SQLAlchemy/Alembic isolado, migrations explícitas, atomicidade, idempotência, optimistic concurrency e inspeção COMMITTED/NOT_COMMITTED/UNKNOWN;
- agentos.context: pipeline efêmero, manifestos, proveniência, classificação, orçamento e contratos de compartilhamento RFC 303;
- agentos.events: envelope canônico, Event Bus/archive/replay/outbox em memória, deduplicação, ordering e autorização;
- agentos.agents: identidade versionada, resolução autorizada e estados ACTIVE/SUSPENDED/ARCHIVED;
- agentos.orchestrator: planos/DAG, materialização, cancelamento, falha e retry por portas públicas;
- agentos.multi_agent: colaboração, mensagens, delegação, handoff, espera, cancelamento e propagação de falhas;
- última suíte registrada: 329 passed, 1 skipped; o teste PostgreSQL opcional é pulado quando AGENTOS_TEST_POSTGRES_DSN não está configurado.

Preserve alterações preexistentes no working tree. Antes de editar, leia o
estado do Git e não use reset, checkout destrutivo ou limpeza ampla.

## Leitura obrigatória antes de editar

Leia integralmente:

- C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\201-agent.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\203-multi-agent.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\301-memory.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\303-context-sharing.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md
- C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\602-artifact-storage.md
- C:\Users\reali\Documents\AgentOS\docs\adr\002-postgresql-as-system-of-record.md
- C:\Users\reali\Documents\AgentOS\docs\adr\008-artifact-storage-abstraction.md
- C:\Users\reali\Documents\AgentOS\docs\adr\009-redis-for-ephemeral-coordination.md
- C:\Users\reali\Documents\AgentOS\docs\adr\012-sqlalchemy-alembic-persistence-adapters.md
- C:\Users\reali\Documents\AgentOS\docs\adr\013-asyncio-concurrency-runtime.md
- C:\Users\reali\Documents\AgentOS\docs\adr\014-pydantic-boundary-validation.md

Inspecione também:

- src/agentos/context/
- src/agentos/events/
- src/agentos/agents/
- src/agentos/multi_agent/
- src/agentos/persistence/
- tests/unit/context/
- tests/unit/events/
- tests/unit/agents/
- tests/unit/multi_agent/
- tests/unit/persistence/
- tests/unit/integration/
- docs/superpowers/specs/2026-08-06-context-pipeline-design.md
- docs/superpowers/specs/2026-08-06-multi-agent-design.md
- docs/superpowers/specs/2026-08-06-persistence-design.md
- os planos correspondentes de Context, Multi-agent e Persistence.

Comece executando:

~~~text
git status --short --branch
git log --oneline -12
python -m pytest -q
~~~

Não comece editando código. Faça um brainstorming técnico curto, compare 2–3
desenhos e aguarde aprovação. Depois registre:

- docs/superpowers/specs/2026-08-06-memory-design.md
- docs/superpowers/plans/2026-08-06-memory.md

Só então implemente em ciclos TDD, com commits pequenos e verificáveis.

## Objetivo

Implementar a fronteira pública de Memory da RFC 301, preservando a separação:

~~~text
Memory persistente ──porta──> Context temporário
~~~

Memory só pode ser criada, lida, invalidada, consolidada ou retida por uma
operação explícita, autorizada, versionada, idempotente, auditável e vinculada
a uma Execution. Nenhuma inclusão no Context deve gravar ou renovar Memory
automaticamente.

## Escopo obrigatório

Crie um pacote src/agentos/memory/ com fronteiras separadas:

- models.py: MemoryRecord, MemoryReference, MemoryProvenance, MemoryRevision,
  MemoryScope, MemoryKind, status, classificação, comandos, filtros, matches,
  receipts, conflitos e erros sanitizados;
- ports.py: MemoryManager, MemoryStore, MemorySearchAdapter e portas estreitas
  para auditoria/outbox ou fatos confirmados;
- security.py: validação de ownership, classificação, proveniência, escopo,
  grants, referências opacas, limites e redaction;
- in_memory.py: adapter de referência substituível, bounded e determinístico;
- context_compat.py ou equivalente somente se necessário para expor
  AuthorizedMemory ao ContextSource existente, sem alterar a semântica de
  montagem do Context;
- __init__.py: apenas exports públicos estáveis.

### Contratos e escopos

Implemente contratos públicos equivalentes à RFC 301 para:

- SaveMemory, GetMemory, SearchMemory, InvalidateMemory,
  ConsolidateMemory e ApplyMemoryRetention;
- MemoryScope = PRIVATE | WORKSPACE | USER;
- MemoryKind = EPISODIC | PROCEDURAL | PREFERENCE | FACT | SEMANTIC;
- MemoryStatus = ACTIVE | INVALIDATED | EXPIRED | SUPERSEDED;
- MemoryProvenance com source_kind, referências, autoria, timestamps,
  confiança, transformações e integridade;
- MemoryReference com ID/version, ownership, Agent autorizado, finalidade,
  expiração, grant e integridade, sem conteúdo ou path físico;
- resultados MemoryWriteReceipt, AuthorizedMemory, MemorySearchResult,
  MemoryConsolidationReceipt, RetentionReceipt, conflito de versão e outcomes
  explícitos.

Todo comando sensível deve carregar, quando aplicável, user_id,
workspace_id, agent_id, execution_id, correlation_id, purpose, actor,
classificação, idempotency key e expected version.

### Ownership, autorização e classificação

- PRIVATE exige owner_agent_id e nunca é herdada por Agent filho,
  colaborador, Orchestrator ou Workspace;
- WORKSPACE exige workspace_id e autorização explícita no Workspace;
- USER nasce sem Workspace e não pode virar ponte silenciosa entre projetos;
- SEMANTIC é um tipo de recuperação, não um novo escopo de ownership;
- toda leitura e escrita revalida ownership, Agent ativo, finalidade,
  classificação, grant, status, versão e validade;
- IDs conhecidos não concedem acesso; ausência de autorização não revela se a
  Memory existe;
- filtros de escopo/classificação são aplicados antes de busca/ranking e antes
  de materializar conteúdo;
- grants são mínimos, revogáveis, bounded e não permitem redelegação implícita;
- MemoryReference expirada, invalidada, superseded ou revogada falha fechada.

### Escrita, concorrência e idempotência

- save aceita somente conteúdo bounded ou ArtifactReference; não aceita
  ContextSnapshot, histórico bruto, prompt completo, segredo, token,
  credencial ou handle vivo;
- atualização usa expected_version e nunca aplica last-write-wins;
- mesma idempotency key e fingerprint retorna o mesmo receipt sem duplicar
  Memory, auditoria ou Event;
- fingerprint divergente produz conflito explícito e sanitizado;
- invalidamento cria mudança versionada/tombstone conforme contrato, sem
  ressuscitação por retry ou cache;
- consolidação cria uma Memory nova, preserva lineage, mantém a classificação
  mais restritiva, não muta fontes retroativamente e não publica saída parcial;
- retenção opera somente sobre referências explicitamente autorizadas e não
  amplia escopo nem apaga fora do conjunto permitido.

### Busca e integração com Context

- search deve filtrar ownership, classificação, status, finalidade e limites
  antes de ranking;
- a implementação in-memory pode oferecer somente busca textual/por filtros
  bounded, declarando que embeddings e busca semântica real são capacidades
  futuras/substituíveis;
- resultados devem retornar referências, trechos mínimos, proveniência,
  relevância e razões, nunca coleções completas por padrão;
- qualquer adapter para ContextSource entrega AuthorizedMemory ou refs e
  trechos mínimos ao ContextManager, respeitando o orçamento dele;
- descarte/finalização de Context não altera Memory;
- não adicione SourceKind nova se o contrato existente já possui
  SourceKind.MEMORY; reutilize o contrato canônico.

### Auditoria e Events

Implemente fatos mínimos posteriores a commit, usando o envelope público de
Events e uma porta de gravação/outbox injetada. Cubra, quando aplicável:

- MemorySaved, MemoryRead, MemorySearched, MemoryInvalidated,
  MemorySuperseded, MemoryConsolidated, MemoryExpired,
  MemoryAccessDenied e MemoryOperationFailed;
- payloads com IDs, versão, escopo, razões categóricas, ownership,
  correlação, finalidade e refs;
- nenhum prompt, conteúdo completo, segredo, credencial, token, SQL,
  exceção tecnológica ou dado proprietário em Event, log, erro ou repr;
- publicação só depois de commit confirmado; retry preserva IDs e é deduplicável.

## Fora de escopo explícito

Não implemente nesta sessão:

- PostgreSQL schema/migration específico de Memory, ORM, vetor, embeddings,
  Redis, broker, worker, scheduler ou retenção física de produção;
- Artifact Storage, upload/download, filesystem, Blackboard ou Workspace como
  domínios completos;
- gravação automática de mensagens, prompts, Context ou resultados;
- Provider, Tool, Browser, LLM, ranking semântico proprietário ou rede;
- API, FastAPI, HTTP, SSE, autenticação, frontend ou consentimento visual;
- mudança da máquina de estados de Execution ou nova porta de persistência;
- cópia de Context/Memory entre Agents, Workspaces ou Executions;
- garantia de exactly-once, busca semântica real ou disaster recovery executável.

Se durabilidade real exigir uma composição posterior com a porta RFC 601,
registre isso como limitação; não simule PostgreSQL com um adapter in-memory.

## Testes obrigatórios

Use TDD: cada comportamento novo começa com teste RED pelo motivo correto,
implementação mínima GREEN e suíte relevante. Cubra pelo menos:

- contexto completo e rejeição de operação sem ownership/finalidade/Execution;
- invariantes de PRIVATE, WORKSPACE, USER e SEMANTIC;
- cross-user, cross-workspace, Agent incorreto, purpose incorreto e grant
  revogado retornando falha fechada sem vazamento;
- classificação acima do ceiling, status inválido e referência expirada;
- payload bounded, proveniência obrigatória, integridade e ausência de
  segredo/prompt/credencial em dados e representações públicas;
- save, update por versão, conflito concorrente, idempotência e fingerprint;
- atomicidade conceitual de Memory + auditoria + outbox no adapter de teste;
- rollback/rejeição sem registro, auditoria ou Event parcial;
- busca bounded, filtros aplicados antes de materialização e resultados
  autorizados com trechos mínimos;
- invalidamento, supersession, tombstone e ausência de ressurreição;
- consolidação com lineage, classificação conservadora, fontes autorizadas e
  nenhuma saída parcial;
- retenção limitada ao conjunto autorizado e contagens auditáveis;
- integração opcional com ContextSource sem gravação implícita em Memory;
- Event mínimo após commit e deduplicação por ID;
- Memory sem dependência concreta de SQLAlchemy, Alembic, Redis, Artifact ou
  Provider;
- suíte existente completa sem regressões.

## Processo obrigatório da sessão

1. Leia integralmente as RFCs, ADRs, planos, specs, código e testes listados.
2. Registre git status, histórico e baseline da suíte.
3. Faça brainstorming curto, compare 2–3 desenhos e aguarde aprovação.
4. Escreva e revise docs/superpowers/specs/2026-08-06-memory-design.md.
5. Escreva docs/superpowers/plans/2026-08-06-memory.md com tarefas TDD
   pequenas, arquivos exatos e critérios de verificação.
6. Implemente sem tocar em código antes do primeiro teste RED.
7. Execute a suíte relevante após cada ciclo e atualize os docs somente com
   evidência real.
8. Faça auditoria requisito a requisito contra RFCs 050, 060, 101, 103, 104,
   201, 203, 301, 303, 601 e ADRs 002, 008, 009, 012, 013 e 014.
9. Execute exatamente:

~~~text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit|broker|worker|scheduler" src/agentos/memory
git diff --check
git status --short --branch
~~~

O rg deve retornar zero matches no pacote Memory. Faça também uma varredura
transversal para provar que Memory não introduziu imports concretos ou uma
escrita implícita em Context, Events, Execution ou Persistence.

## Critérios de conclusão

A sessão só está concluída quando:

- existe uma porta MemoryManager única, tipada, bounded e independente de
  tecnologia;
- Memory tem ownership, escopo, classificação, proveniência, versão,
  retenção, invalidamento, lineage e autorização explícitos;
- save/read/search/invalidate/consolidate/retention são idempotentes,
  versionados, auditáveis e sem last-write-wins;
- busca nunca materializa dado não autorizado e retorna referências/trechos
  mínimos;
- Context continua temporário e não grava Memory implicitamente;
- Events são mínimos, posteriores ao commit e deduplicáveis;
- adapters in-memory estão claramente identificados como referência e nenhuma
  tecnologia de produção foi criada;
- testes, compilação, scans, documentação e limitações possuem evidência
  fresca;
- nenhum Blackboard, Artifact, Redis, broker, Worker, Scheduler, API,
  Provider, Tool ou domínio fora do escopo foi introduzido.

Não declare “Memory concluído” apenas porque a suíte local passou. Diferencie
contratos públicos, adapter in-memory, integração com Context e durabilidade
de produção ainda dependente de uma composição explícita com a RFC 601.

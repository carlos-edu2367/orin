# AgentOS Backend Architecture Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar o acervo completo de RFCs e ADRs em PT-BR que especifica a arquitetura do backend do AgentOS, sem implementar o produto.

**Architecture:** O acervo é composto por RFCs numeradas por dependência conceitual e ADRs independentes para as decisões fundamentais. RFCs definem contratos, fluxos e invariantes; ADRs registram contexto, decisão e consequências. O documento raiz `AgentOS Backend Architecture.md` e o handoff são fontes de requisitos, não arquivos a serem alterados.

**Tech Stack:** Markdown CommonMark, Python 3.13+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis, ARQ, asyncio, Playwright e Pydantic v2 (citados somente como decisões arquiteturais).

## Global Constraints

- Escrever todo o conteúdo em PT-BR.
- Não criar código de produção, scaffolding, endpoints, schemas ORM ou arquivos de configuração executáveis.
- Tudo é uma `Execution`; o Runtime é o Kernel do AgentOS.
- Runtime não conhece FastAPI, React, Playwright nem banco de dados; depende exclusivamente de interfaces públicas.
- API não executa agentes; workers executam trabalho pesado; Browser roda somente em Browser Workers.
- Tools são atômicas e não chamam Tools; Capabilities coordenam Tools.
- Contexto é temporário; memória é permanente; compartilhamento entre agentes prefere referências e handoffs estruturados.
- Todas as entidades são preparadas para multiusuário por `user_id`, embora o primeiro lançamento seja single-user.
- Todo subsistema publica eventos observáveis, é substituível e mantém isolamento estrito de workspace.
- Não há repositório Git neste workspace; substituir passos de commit por revisão do diff e verificação de arquivos.

---

## Estrutura final de arquivos

```text
docs/
├── architecture/
│   ├── 000-overview.md
│   ├── 050-design-principles.md
│   ├── 060-glossary-and-conventions.md
│   ├── 100-kernel/{101-runtime,102-execution-lifecycle,103-event-system,104-context-pipeline}.md
│   ├── 200-agents/{201-agent,202-orchestrator,203-multi-agent}.md
│   ├── 300-context-memory/{301-memory,302-blackboard,303-context-sharing}.md
│   ├── 400-tools-resources/{401-tool-runtime,402-resource-manager,403-filesystem,404-terminal,405-browser,406-capabilities}.md
│   ├── 500-providers-models/{501-provider-api,502-model-catalog}.md
│   ├── 600-platform-data/{601-persistence,602-artifact-storage,603-workspaces,604-configuration}.md
│   ├── 700-api-security/{701-api-sse,702-security}.md
│   ├── 800-operations/{801-workers,802-scheduler,803-observability}.md
│   └── 900-extensibility/{901-plugin-sdk,902-skills,903-mcp-future}.md
└── adr/
    ├── 001-arq-workers.md
    ├── 002-postgresql-as-system-of-record.md
    ├── 003-sse-for-client-event-streaming.md
    ├── 004-playwright-browser-workers.md
    ├── 005-local-workspaces.md
    ├── 006-single-user-multi-tenant-ready.md
    ├── 007-server-side-sessions.md
    ├── 008-artifact-storage-abstraction.md
    ├── 009-redis-for-ephemeral-coordination.md
    └── 010-provider-ports-and-model-catalog.md
```

## Convenção de revisão

Para cada grupo de documentos, validar que: (1) toda seção obrigatória aparece quando aplicável; (2) dependências apontam apenas para RFCs existentes; (3) eventos usam fatos no passado, `execution_id` quando pertencentes a uma execução e correlação explícita; (4) não há termos provisórios; (5) nenhum contrato viola os invariantes globais. Usar `rg -n '(?i)\b(TBD|TODO|implementar depois|preencher)\b' docs/architecture docs/adr` para a varredura de pendências, interpretando palavras portuguesas comuns fora do sentido de marcador pelo contexto.

### Task 1: Criar fundação editorial

**Files:**
- Create: `docs/architecture/000-overview.md`
- Create: `docs/architecture/050-design-principles.md`
- Create: `docs/architecture/060-glossary-and-conventions.md`

**Interfaces:**
- Consumes: documento raiz e handoff arquitetural.
- Produces: terminologia e invariantes usados por todas as RFCs subsequentes.

- [ ] **Step 1: Escrever a visão geral**

Definir a missão, a analogia de sistema operacional, o mapa de camadas, os fluxos transversais e o índice navegável das RFCs.

- [ ] **Step 2: Escrever os princípios e fronteiras**

Formalizar as 20 invariantes do handoff, regra de dependência por portas e adapters, e as proibições explícitas entre Runtime, API, Browser, Provider e banco.

- [ ] **Step 3: Escrever glossário e convenções**

Definir `Agent`, `Execution`, `Task`, `Event`, `Tool`, `Capability`, `Resource`, `Workspace`, `Context`, `Memory`, `Artifact`, `Provider` e os formatos normativos para eventos, ids e máquinas de estado.

- [ ] **Step 4: Verificar fundação editorial**

Run: `rg -n 'TODO|TBD' docs/architecture/000-overview.md docs/architecture/050-design-principles.md docs/architecture/060-glossary-and-conventions.md`
Expected: nenhuma ocorrência.

### Task 2: Documentar o Kernel de execução

**Files:**
- Create: `docs/architecture/100-kernel/101-runtime.md`
- Create: `docs/architecture/100-kernel/102-execution-lifecycle.md`
- Create: `docs/architecture/100-kernel/103-event-system.md`
- Create: `docs/architecture/100-kernel/104-context-pipeline.md`

**Interfaces:**
- Consumes: princípios, glossário e convenções de Task 1.
- Produces: contratos `Runtime`, `ExecutionManager`, `EventBus` e `ContextManager` para todos os subsistemas.

- [ ] **Step 1: Escrever Runtime e ciclo de execução**

Especificar o loop contexto → provider → tool/capability → atualização → finalização, as portas consumidas, a propriedade de cancelamento, custo e checkpoints.

- [ ] **Step 2: Escrever ciclo de vida da Execution**

Definir estados `QUEUED`, `STARTING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`, `PAUSED`, `COMPLETED`, `FAILED` e `CANCELLED`; enumerar transições permitidas, idempotência, timeout e recuperação.

- [ ] **Step 3: Escrever Event Bus**

Definir envelope, sequência, ordenação por execução, entrega pelo menos uma vez, deduplicação, retenção, consumidores e os eventos de domínio essenciais.

- [ ] **Step 4: Escrever pipeline de contexto**

Definir orçamento de tokens, priorização de task/resumo/memórias/arquivos/resultados, compactação, proveniência, referências e proteção contra vazamento de escopo.

- [ ] **Step 5: Verificar coerência do Kernel**

Confirmar que Runtime não depende de adapters e que cada estado, evento e contrato citado existe nos quatro documentos.

### Task 3: Documentar agentes e orquestração

**Files:**
- Create: `docs/architecture/200-agents/201-agent.md`
- Create: `docs/architecture/200-agents/202-orchestrator.md`
- Create: `docs/architecture/200-agents/203-multi-agent.md`

**Interfaces:**
- Consumes: contratos de Execution, EventBus e ContextManager.
- Produces: contratos de identidade de agente, delegação, dependências, handoff e cancelamento entre agentes.

- [ ] **Step 1: Escrever agente persistente**

Definir configuração, identidade, escopo de workspace, ferramentas, capabilities, skills, memória privada e a separação entre agente e conversa.

- [ ] **Step 2: Escrever Kernel de orquestração**

Definir criação, suspensão, encerramento lógico, dependências, timeout, cancelamento e política de distribuição de contexto sem compartilhar histórico bruto.

- [ ] **Step 3: Escrever protocolo multiagente**

Definir mensagens estruturadas, handoffs, referências, permissões, espera de resultados, propagação de cancelamento e eventos de delegação.

- [ ] **Step 4: Revisar fluxos de delegação**

Garantir que criação, conversa, espera, falha e cancelamento entre agentes são representados por Executions e eventos.

### Task 4: Documentar memória e conhecimento compartilhado

**Files:**
- Create: `docs/architecture/300-context-memory/301-memory.md`
- Create: `docs/architecture/300-context-memory/302-blackboard.md`
- Create: `docs/architecture/300-context-memory/303-context-sharing.md`

**Interfaces:**
- Consumes: ContextManager e EventBus.
- Produces: contratos para `MemoryManager`, memória privada/workspace/usuário/semântica, Blackboard e handoffs.

- [ ] **Step 1: Escrever Memory Manager**

Definir tipos de memória, ownership, ciclo de escrita/leitura, proveniência, retenção, invalidamento, isolamento e recuperação semântica.

- [ ] **Step 2: Escrever Blackboard**

Definir itens de decisão, descoberta, bug, tarefa, contrato e arquitetura; versionamento, conflitos, visibilidade e trilha de auditoria.

- [ ] **Step 3: Escrever compartilhamento de contexto**

Definir referências, snapshots, handoff estruturado, filtros de escopo e as regras que proíbem transferência indiscriminada de conversas.

- [ ] **Step 4: Revisar separação de responsabilidades**

Garantir que memória persistente não é tratada como contexto temporário e que Blackboard não substitui a fonte de verdade transacional.

### Task 5: Documentar Tools, Capabilities e recursos

**Files:**
- Create: `docs/architecture/400-tools-resources/401-tool-runtime.md`
- Create: `docs/architecture/400-tools-resources/402-resource-manager.md`
- Create: `docs/architecture/400-tools-resources/403-filesystem.md`
- Create: `docs/architecture/400-tools-resources/404-terminal.md`
- Create: `docs/architecture/400-tools-resources/405-browser.md`
- Create: `docs/architecture/400-tools-resources/406-capabilities.md`

**Interfaces:**
- Consumes: Runtime, Execution, EventBus e Workspace.
- Produces: contratos de Tool, Capability e Resource adapters, além de políticas de acesso a filesystem, terminal e browser.

- [ ] **Step 1: Escrever Tool Runtime e Capability Runtime**

Definir registro, descoberta, validação, autorização, execução, streaming, cancelamento, resultado e eventos; formalizar que somente Capabilities coordenam múltiplas Tools.

- [ ] **Step 2: Escrever Resource Manager e filesystem**

Definir locação de recurso, ownership, ciclo de vida, raiz de workspace, canonicalização de paths, bloqueio de path traversal e auditoria.

- [ ] **Step 3: Escrever terminal persistente**

Definir sessão, `id`, `cwd`, `pid`, status, owner, buffer, workspace, política de processos, cancelamento e limpeza.

- [ ] **Step 4: Escrever Browser Runtime**

Definir Browser Workers, perfis, sessões, páginas, uploads, downloads, cookies, DOM, screenshots, isolamento e eventos; proibir acesso direto ao banco.

- [ ] **Step 5: Revisar políticas de recurso**

Verificar que todo recurso é mediado pelo Resource Manager e que nenhuma Tool pode fugir do workspace ou expor segredos.

### Task 6: Documentar providers e catálogo de modelos

**Files:**
- Create: `docs/architecture/500-providers-models/501-provider-api.md`
- Create: `docs/architecture/500-providers-models/502-model-catalog.md`

**Interfaces:**
- Consumes: Runtime, ContextManager e EventBus.
- Produces: porta uniforme de provider e contrato para resolução de perfil/modelo.

- [ ] **Step 1: Escrever Provider API**

Definir porta para geração, streaming, visão, tool calls, cancelamento, erros normalizados, limites, observabilidade e adapters iniciais OpenAI, Anthropic e OpenRouter.

- [ ] **Step 2: Escrever Model Catalog**

Definir metadados de provider, nome, contexto, custo, visão, tools, streaming, status, perfis e seleção com fallback explícito.

- [ ] **Step 3: Revisar encapsulamento**

Confirmar que SDKs de providers não vazam para Runtime, Tools, API ou outros subsistemas.

### Task 7: Documentar persistência, artefatos e workspaces

**Files:**
- Create: `docs/architecture/600-platform-data/601-persistence.md`
- Create: `docs/architecture/600-platform-data/602-artifact-storage.md`
- Create: `docs/architecture/600-platform-data/603-workspaces.md`
- Create: `docs/architecture/600-platform-data/604-configuration.md`

**Interfaces:**
- Consumes: entidades e ownership definidos nas RFCs anteriores.
- Produces: limites entre PostgreSQL, Redis, ArtifactStorage, workspace e configuração.

- [ ] **Step 1: Escrever persistência**

Definir fonte de verdade PostgreSQL, uso efêmero do Redis, consistência, transações, locks, pub/sub, retenção e recuperação.

- [ ] **Step 2: Escrever Artifact Storage e Workspaces**

Definir namespace de artefatos, metadados, checksums, uploads/downloads, logs, raiz local por workspace, quotas e limpeza recuperável.

- [ ] **Step 3: Escrever configuração**

Definir fontes de configuração, precedência, validação, segredo por referência, rotação de chave e separação entre configuração global, workspace e agente.

- [ ] **Step 4: Revisar propriedade dos dados**

Garantir que dados transacionais, coordenação efêmera e blobs de artefatos não tenham responsabilidades sobrepostas.

### Task 8: Documentar API e segurança

**Files:**
- Create: `docs/architecture/700-api-security/701-api-sse.md`
- Create: `docs/architecture/700-api-security/702-security.md`

**Interfaces:**
- Consumes: EventBus, ExecutionManager, ArtifactStorage e Security services.
- Produces: borda HTTP/SSE sem regra de negócio e políticas de identidade, sessão, token e segredo.

- [ ] **Step 1: Escrever API REST e SSE**

Definir o Gateway como adapter, comandos idempotentes de criação/controle de Execution, leitura de estado, stream SSE, cursores, reconexão, autorização e mapeamento de erros.

- [ ] **Step 2: Escrever segurança**

Definir sessão server-side em Redis, cookie HttpOnly, CSRF, PAT armazenado como hash, autorização por escopo, AES-256-GCM para segredos, APP_MASTER_KEY, auditoria e isolamento por `user_id` e workspace.

- [ ] **Step 3: Revisar fronteira de confiança**

Confirmar que API não executa agentes, eventos expostos são autorizados e segredo nunca é serializado para cliente, logs ou memória.

### Task 9: Documentar workers, scheduler e observabilidade

**Files:**
- Create: `docs/architecture/800-operations/801-workers.md`
- Create: `docs/architecture/800-operations/802-scheduler.md`
- Create: `docs/architecture/800-operations/803-observability.md`

**Interfaces:**
- Consumes: Queue, Runtime, EventBus, Resource Manager e persistência.
- Produces: contratos de pools, jobs agendados, watchdogs, logs, métricas e rastreamento.

- [ ] **Step 1: Escrever Worker Pool**

Definir filas e isolamento de Agent, Browser, Maintenance e Scheduler Workers; concorrência, backpressure, retries, locks, recuperação e cancelamento.

- [ ] **Step 2: Escrever Scheduler**

Definir execuções futuras, recorrências de Skill, watchdogs, rotinas de manutenção, semântica de disparo, idempotência e timezone.

- [ ] **Step 3: Escrever observabilidade**

Definir logs estruturados, correlação, métricas, tracing, auditoria orientada a eventos, custos de modelo e capacidade de reconstrução de uma Execution.

- [ ] **Step 4: Revisar operação sob falhas**

Cobrir indisponibilidade de fila, reinício de worker, execução órfã, evento duplicado, timeout de recurso e auditoria pós-incidente.

### Task 10: Documentar extensibilidade

**Files:**
- Create: `docs/architecture/900-extensibility/901-plugin-sdk.md`
- Create: `docs/architecture/900-extensibility/902-skills.md`
- Create: `docs/architecture/900-extensibility/903-mcp-future.md`

**Interfaces:**
- Consumes: registros de Tool, Capability, Provider, Resource e EventBus.
- Produces: contratos de extensão, empacotamento, permissões e compatibilidade.

- [ ] **Step 1: Escrever Plugin SDK**

Definir manifesto, ciclo de descoberta/registro, versionamento, permissões, isolamento, compatibilidade, desativação e observabilidade de extensões.

- [ ] **Step 2: Escrever Skills**

Definir Skill como workflow versionado que cria Executions, recebe contexto mínimo, declara permissões, produz artefatos e pode ser agendada.

- [ ] **Step 3: Escrever MCP futuro**

Definir a posição arquitetural futura, as portas de integração, limites de segurança e os critérios para adoção sem comprometer os contratos atuais.

- [ ] **Step 4: Revisar substituibilidade**

Verificar que extensões são registradas dinamicamente e que nenhum módulo central exige `switch/case` para conhecer implementações.

### Task 11: Escrever ADRs de execução, dados e interface de eventos

**Files:**
- Create: `docs/adr/001-arq-workers.md`
- Create: `docs/adr/002-postgresql-as-system-of-record.md`
- Create: `docs/adr/003-sse-for-client-event-streaming.md`
- Create: `docs/adr/009-redis-for-ephemeral-coordination.md`

**Interfaces:**
- Consumes: RFCs de Kernel, persistência, API e workers.
- Produces: justificativas estáveis para as escolhas transversais.

- [ ] **Step 1: Aplicar template de ADR**

Em cada ADR, registrar `Status`, `Contexto`, `Decisão`, `Consequências`, `Alternativas consideradas` e `Relações` para RFCs relevantes.

- [ ] **Step 2: Registrar decisões**

Documentar por que ARQ, PostgreSQL, SSE e Redis atendem, respectivamente, execução assíncrona, fonte transacional de verdade, streaming de eventos para o cliente e coordenação efêmera.

- [ ] **Step 3: Revisar consequências**

Confirmar que cada ADR explicita custos operacionais, falhas previsíveis e o que a decisão não resolve.

### Task 12: Escrever ADRs de recursos, tenancy e extensibilidade

**Files:**
- Create: `docs/adr/004-playwright-browser-workers.md`
- Create: `docs/adr/005-local-workspaces.md`
- Create: `docs/adr/006-single-user-multi-tenant-ready.md`
- Create: `docs/adr/007-server-side-sessions.md`
- Create: `docs/adr/008-artifact-storage-abstraction.md`
- Create: `docs/adr/010-provider-ports-and-model-catalog.md`

**Interfaces:**
- Consumes: RFCs de Browser, Workspace, Segurança, Artifact Storage e Providers.
- Produces: razões verificáveis para decisões de fronteira e substituibilidade.

- [ ] **Step 1: Registrar Browser e workspace local**

Explicar o uso de Playwright em Browser Workers e a escolha inicial de workspaces locais isolados, incluindo suas limitações de escala.

- [ ] **Step 2: Registrar modelo de usuário e sessão**

Explicar lançamento single-user com todas as entidades multi-tenant-ready e sessões server-side protegidas por cookie HttpOnly e CSRF.

- [ ] **Step 3: Registrar artefatos e providers**

Explicar ArtifactStorage como porta substituível e o catálogo de modelos/portas de provider como proteção contra acoplamento a SDK.

- [ ] **Step 4: Revisar relações entre ADRs e RFCs**

Garantir que cada ADR referencia RFCs existentes e que decisões não contradizem contratos normativos.

### Task 13: Fazer revisão editorial e arquitetural final

**Files:**
- Modify: todos os arquivos em `docs/architecture/`
- Modify: todos os arquivos em `docs/adr/`

**Interfaces:**
- Consumes: acervo completo.
- Produces: documentação internamente consistente e pronta para orientar uma futura fase de implementação.

- [ ] **Step 1: Validar cobertura**

Conferir cada item do handoff contra uma RFC ou ADR e registrar no `000-overview.md` a navegação definitiva do acervo.

- [ ] **Step 2: Validar referências e invariantes**

Run: `rg -n 'Runtime.*FastAPI|Runtime.*Playwright|Tool.*Tool|API.*executa agentes' docs/architecture`
Expected: ocorrências apenas em frases que expressam proibições, nunca como arquitetura permitida.

- [ ] **Step 3: Validar pendências e estrutura**

Run: `rg -n '(?i)\b(TBD|TODO|implementar depois|preencher)\b' docs/architecture docs/adr; Get-ChildItem -Recurse docs/architecture,docs/adr -Filter *.md | Measure-Object`
Expected: nenhum marcador provisório e 43 documentos Markdown novos no acervo arquitetural (33 RFCs e 10 ADRs).

- [ ] **Step 4: Revisar o diff local**

Run: `Get-ChildItem -Recurse docs/architecture,docs/adr -Filter *.md | Sort-Object FullName | Select-Object -ExpandProperty FullName`
Expected: lista coincide com a estrutura final; nenhuma alteração ocorre fora de `docs/`.

## Auto-revisão do plano

Cobertura: Tasks 1–10 cobrem cada grupo de RFCs definido na especificação; Tasks 11–12 cobrem os dez ADRs; Task 13 trata consistência, navegação e pendências. Consistência: os contratos fundamentais são produzidos antes das RFCs que os consomem. Não há etapas de implementação do produto, marcadores provisórios ou dependências indefinidas.

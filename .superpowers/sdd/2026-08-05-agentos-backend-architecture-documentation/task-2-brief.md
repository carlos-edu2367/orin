# Task 2 — Documentar o Kernel de execução

Leia este arquivo primeiro: ele é a fonte completa dos requisitos desta tarefa.

## Arquivos a criar

- `docs/architecture/100-kernel/101-runtime.md`
- `docs/architecture/100-kernel/102-execution-lifecycle.md`
- `docs/architecture/100-kernel/103-event-system.md`
- `docs/architecture/100-kernel/104-context-pipeline.md`

Crie a pasta necessária caso ela não exista. Use somente Markdown e pseudocódigo tipado para contratos, nunca implementação executável.

## Contexto vinculante

Leia as RFCs de fundação já existentes em `docs/architecture/000-overview.md`, `050-design-principles.md` e `060-glossary-and-conventions.md`. Os quatro documentos desta tarefa são a fonte normativa para Execution, Runtime, EventBus e ContextManager. O Runtime é Kernel; tudo é uma Execution; Runtime conhece somente portas públicas e nunca FastAPI, React, Playwright ou banco diretamente.

## Requisitos por documento

### 101 Runtime

Definir objetivo, responsabilidades e fronteiras; portas de Runtime para contexto, provider, tool/capability, eventos, checkpoints e controle de execução. Especificar o loop: receber Execution, montar contexto, selecionar modelo, chamar Provider, executar Tool/Capability, atualizar contexto e finalizar. Cobrir falhas, cancelamento cooperativo, custo, limite de iterações, checkpoints, recuperação e eventos. Explicitar dependências proibidas e extensibilidade.

### 102 Ciclo de vida da Execution

Definir entidade de Execution, ownership (`user_id`, `workspace_id`, agente), task, contexto, custos, eventos e rastreabilidade. Formalizar a máquina de estados `QUEUED`, `STARTING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`; listar transições permitidas e proibidas, idempotência de comandos, timeout, cancelamento, retomada e recuperação após falha de worker. Toda ação relevante deve ser representada como Execution ou evento associado.

### 103 Sistema de eventos

Definir EventBus interno e seu envelope: `event_id`, nome, `occurred_at`, `source`, `correlation_id`, `causation_id`, sequência por Execution e `execution_id` quando aplicável. Especificar eventos no passado em PascalCase, publicação transacional/outbox conceitual, semântica ao-menos-uma-vez, deduplicação, ordenação, consumidores, retenção, replay/auditoria, autorização e proteção contra dados sensíveis. Incluir catálogo inicial de eventos: AgentCreated, ExecutionStarted, ExecutionFinished, ExecutionCancelled, ToolStarted, ToolFinished, BrowserOpened, MemorySaved, DecisionCreated e CheckpointCreated.

### 104 Pipeline de contexto

Definir ContextManager separado da memória permanente. Especificar composição de task, resumo, mensagens, memórias, arquivos, decisões, eventos e resultados de tools; orçamento de tokens, prioridades, compactação, referências/proveniência, sanidade de dados, isolamento por usuário/workspace/agente, reprodutibilidade e atualização por turno. Proibir envio automático de todo o histórico e descrever degradação quando exceder o orçamento.

## Requisitos transversais

- Cada RFC deve conter objetivo, fora de escopo, responsabilidades/não responsabilidades, arquitetura, contratos, entidades ou dados pertinentes, eventos, fluxos normal/falha/cancelamento, segurança, observabilidade, extensibilidade, invariantes e futuro.
- Usar links relativos para as três RFCs de fundação quando referenciadas.
- Não inventar endpoints, tabelas ORM ou implementações concretas de fila/banco.
- Não contradizer as convenções existentes de eventos, IDs, tempo e ownership.

## Verificação

Conferir referências relativas, estados e transições, nomes de eventos, ausência de marcadores pendentes e coerência das dependências: Runtime não pode conhecer adapters; Context não pode ser confundido com Memory; EventBus não pode exigir acesso pelo frontend ao banco.

## Relatório exigido

Ao concluir, criar `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-2-report.md` usando `apply_patch`. Informe status, arquivos, verificações, decisões interpretativas e preocupações. Na resposta, retorne apenas status, arquivos e resumo de verificação.

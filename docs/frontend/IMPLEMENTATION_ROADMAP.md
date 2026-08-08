# Implementation Roadmap

## Gate 0 — Contratos operacionais (backend prerequisite)

1. Compor Security, command, query e client event stream duráveis na produção.
2. Versionar DTOs de execution/result/list e publicar OpenAPI/contract tests.
3. Projetar outboxes autorizados para stream, iniciando por lifecycle e delegations.
4. Validar retenção, cursor, revogação, replay, duplicação e ordenação com testes de integração.

**Saída:** frontend pode criar/consultar execution e reconciliar um stream real.

## Gate 1 — Fundação web

1. Criar app React/TypeScript/Vite, tema, tokens, autenticação e error boundary.
2. Implementar HTTP client, idempotency, envelope de erro e Query cache.
3. Construir Home, ExecutionShell vazio, inspector/sheet e estados acessíveis.

**Saída:** navegação simples e comandos seguros, sem R3F.

## Gate 2 — Conversation e lifecycle

1. Modelar `ExecutionProjection` e reducer versionado.
2. Implementar bootstrap GET + SSE cursor/dedupe/resync.
3. Exibir lifecycle, controles, waiting user e resultado autorizado.

**Saída:** uma execution é acompanhável sem logs e sem animation dependente de frame.

## Gate 3 — Atividade semântica

1. Publicar `ToolActivity` seguro do backend.
2. Normalizar invocations e agrupar por estado/tipo/intervalo.
3. Adicionar disclosure e falhas/cancelamentos com inspector.

**Saída:** ferramentas parecem atividade compacta e auditável.

## Gate 4 — Multi-agent e motion

1. Expor graph de delegation e facts por execution.
2. Implementar AgentRail 2D, pulso somente em facts explícitos e shared layout.
3. Validar replay/reconnect sem reanimar passado.

**Saída:** colaboração perceptível, mas semanticamente honesta.

## Gate 5 — Camada R3F e configurações

1. Lazy-load OrchestrationScene e aplicar orçamento/perfil de GPU.
2. Implementar settings de providers com escrita única de secret.
3. Adicionar abas de artifacts/memory/workspace conforme DTOs forem liberados.

## Gate 6 — Qualidade

Testes de reducer/normalizer, contratos HTTP/SSE, E2E de reconnect e controles, visual regression, keyboard/screen reader, reduced motion, rede lenta, GPU fallback e budget de performance. Só então aplicar polish de partículas e transições especiais.

## Dependências explícitas

Não iniciar Tool grouping antes da projeção Tool; não iniciar graph 3D antes da consulta/stream de delegations; não prometer chat streaming antes de um contrato de conteúdo e de provider streaming exposto ao cliente.

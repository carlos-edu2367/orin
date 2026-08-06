# Relatório final de correções arquiteturais

**Data:** 2026-08-06  
**Escopo:** documentação Markdown; nenhuma implementação de backend foi criada ou alterada.

## Resultado

O pacote final de inconsistências foi corrigido nas fontes canônicas e em seus consumidores.

1. **Provider e Model Resolver:** a RFC 101 agora declara `ProviderPort` e `ModelResolver` como aliases exatos das interfaces canônicas das RFCs 501 e 502. As versões abreviadas e seus outcomes duplicados foram removidos de 101; 501 e 502 declaram explicitamente sua autoridade sobre as assinaturas.
2. **Estado e Event:** `TransactionalPersistence.transact` da RFC 601 é a única fronteira atômica. `ExecutionControl` da RFC 102 é a fachada de domínio usada pelo Runtime, e estado, versão, mudanças relacionadas e `OutboxEntry` são confirmados no mesmo commit. A RFC 103 não possui mais uma porta concorrente de append; `OutboxPublisher` somente entrega Events já confirmados ao `EventBus`. As RFCs 202, 401 e 406 também removeram `EventPublisher` de seus diagramas e dependências: suas fachadas preparam `DomainChange` + `OutboxEntry`, confirmam pela RFC 601 e deixam a entrega posterior para a RFC 103.
3. **Handoff:** `StructuredHandoff` e `HandoffRef` da RFC 303 são canônicos. A RFC 203 usa aliases, referencia somente `HandoffRef` em mensagens/delegações e adota expiração não nula, versão, integridade, purpose, Execution de origem/destino e reautorização no uso. O Event foi unificado como `StructuredHandoffCreated`.
4. **Conclusão do acervo:** RFC 000 e a especificação de design registram 33 RFCs entregues; o índice deixou de descrever documentos existentes como planejados. O glossário passou a falar em acervo concluído e contratos existentes.
5. **`WAITING_CHILD`:** RFC 102 declara que não é `ExecutionState`. RFCs 203, 406 e 902 mapeiam a projeção interna para `PAUSED`, com checkpoint, liberação do Worker e retomada obrigatória por `PAUSED -> QUEUED`.
6. **Plugin Host:** RFC 901 passou a tipar `bind`, `invoke`, `cancel`, `heartbeat` e `cleanup`, incluindo planos e estados de binding, limites, outcomes, efeito incerto, heartbeat monotônico e limpeza idempotente. Também define `ActivatePluginVersion`, `DrainPluginVersion`, `DisablePluginVersion`, `QuarantinePluginVersion` e `RetirePluginVersion` como especializações fechadas de `PluginLifecycleCommand`, com origem/destino e campos discriminadores próprios.
7. **Critério de ADR:** foram aceitas as ADRs 011–014 para FastAPI, SQLAlchemy/Alembic, asyncio e Pydantic v2. O índice e a especificação agora registram 14 ADRs, preservando o critério de que toda decisão estrutural de stack tenha ADR.

## Arquivos alterados

- `docs/architecture/100-kernel/101-runtime.md`
- `docs/architecture/100-kernel/102-execution-lifecycle.md`
- `docs/architecture/100-kernel/103-event-system.md`
- `docs/architecture/200-agents/202-orchestrator.md`
- `docs/architecture/200-agents/203-multi-agent.md`
- `docs/architecture/300-context-memory/303-context-sharing.md`
- `docs/architecture/400-tools-resources/406-capabilities.md`
- `docs/architecture/400-tools-resources/401-tool-runtime.md`
- `docs/architecture/500-providers-models/501-provider-api.md`
- `docs/architecture/500-providers-models/502-model-catalog.md`
- `docs/architecture/600-platform-data/601-persistence.md`
- `docs/architecture/900-extensibility/901-plugin-sdk.md`
- `docs/architecture/900-extensibility/902-skills.md`
- `docs/architecture/000-overview.md`
- `docs/architecture/060-glossary-and-conventions.md`
- `docs/superpowers/specs/2026-08-05-agentos-backend-documentation-design.md`
- `docs/adr/011-fastapi-api-adapter.md`
- `docs/adr/012-sqlalchemy-alembic-persistence-adapters.md`
- `docs/adr/013-asyncio-concurrency-runtime.md`
- `docs/adr/014-pydantic-boundary-validation.md`

## Verificação

Validação read-only executada após as correções:

- contagem do acervo: `RFC_COUNT=33` e `ADR_COUNT=14`;
- todos os arquivos-alvo existem;
- fences Markdown estão balanceadas em todos os arquivos alterados;
- links Markdown relativos dos arquivos alterados resolvem para destinos existentes;
- padrões legados ausentes no escopo: `interface EventPublisher`, dependências `EventPublisher` nas RFCs 202/401/406, `AppendEvents`, `Handoff {`, descrições de índice como planejado e estado `Aprovado para redação`;
- as únicas declarações de interface para `ProviderPort` e `ModelResolver` permanecem nas RFCs 501 e 502; a RFC 101 contém somente aliases.

Não foram executados testes de código, build ou migrations porque o escopo proíbe implementação e contém apenas Markdown.

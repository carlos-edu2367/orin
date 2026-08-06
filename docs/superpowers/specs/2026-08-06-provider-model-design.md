# Provider API e Model Catalog — Especificação

**Data:** 2026-08-06  
**Escopo:** RFC 501 — Provider API e RFC 502 — Model Catalog  
**Estado:** aprovado para implementação

## Objetivo

Adicionar ao AgentOS contratos de domínio completos para seleção de modelos e invocação de Providers, sem SDK, rede, credenciais, persistência concreta ou Provider tecnológico. O Runtime continuará dependendo de portas públicas; a superfície reduzida existente será preservada por adapters de compatibilidade.

## Invariantes

- Toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`; `actor_ref` também é preservado onde a RFC exige.
- Referências são opacas: conhecer uma referência não concede acesso e nenhum contrato de domínio decompõe seus valores.
- Descriptors, profiles, pricing, snapshots e selections são imutáveis depois de publicados.
- `DISABLED` e `RETIRED` não participam de nova seleção; `RETIRED` é terminal.
- Hard constraints são aplicados antes de qualquer score ou preferência.
- Preço ausente é desconhecido, nunca zero; sob teto obrigatório, custo não comparável rejeita o candidato.
- Fallback é `DISABLED`, explícito ou materializado por policy versionada. Não amplia permissões, capabilities, classificação, finalidade, região ou budget.
- Uso e custo de todas as tentativas são monotônicos e preservados em sucesso, falha, timeout e cancelamento.
- Segredos, headers, credenciais, payloads proprietários, prompts e respostas completas não atravessam contratos públicos nem aparecem em `repr`, erros ou diagnósticos.
- O Provider nunca executa Tool, escolhe modelo alternativo ou altera Context, Memory, Execution ou policy.

## Arquitetura

O pacote canônico `agentos.providers` será dividido por responsabilidade:

```text
Runtime ──> compat ──> ModelResolver completo ──> CatalogPort
Runtime ──> compat ──> ProviderPort completo ──> fake/adapters futuros
                              │
                    descriptors, revisions,
                    snapshots e selections
```

`models.py` conterá somente tipos públicos e validação local. `ports.py` conterá Protocols. `catalog.py` fornecerá uma implementação de referência em memória, substituível por um adapter da RFC 601. `resolver.py` implementará resolução, explicação, snapshots e fallback sobre o catálogo. `provider.py` fornecerá validação/normalização de fronteira sem escolher tecnologia. `compat.py` traduzirá os contratos completos para a superfície atual do Runtime.

Nenhum arquivo em `agentos.providers` importará FastAPI, HTTP, SDKs, banco, ORM, Redis, filesystem, Memory ou Artifact Storage.

## Contratos públicos

### Contexto e referências

Serão definidos aliases opacos para Provider, Model, Binding, Selection, Invocation, Stream, Terminal, Requirements, Pricing, Catalog, Policy e diagnósticos. `ProviderOperationContext` e `ModelCatalogOperationContext` terão os seis campos sensíveis, `purpose` e `actor_ref`, com validação de valores não vazios.

### Catalog e resolução

Os tipos cobrirão:

- `ProviderDescriptor`/`ProviderRevision`/`ProviderStatus`;
- `ModelDescriptor`/`ModelRevision`/`ModelStatus`;
- capabilities públicas de visão, Tools, streaming e cancelamento;
- limites de contexto, input/output, response formats, sampling e classificação;
- `ModelProfile`, constraints, preferências e revisões;
- `ModelCost`/`PricingRevision`, validade e custo desconhecido;
- `ModelRequirements`, `ApprovedModelRequirementsSnapshot`;
- `ModelSelection`, `SelectedModel`, `FallbackRequest`, `SelectionExplanation` e rejeições categóricas;
- requests de registro, consulta, status, profile, pricing e seleção.

`ModelCatalogPort` exporá registro/listagem/consulta de revisões, profiles, pricing, status e selections. Mutations usarão `expected_catalog_version` e `idempotency_key`; repetição semanticamente igual será idempotente e payload divergente será conflito.

`ModelResolver.resolve` fixará versões de catálogo, policy, profile, pricing e disponibilidade; filtrará status e hard constraints; calculará custo comparável; aplicará preferências somente aos candidatos compatíveis; ordenará deterministicamente; materializará primary/fallbacks, explicação, validade e snapshot aprovado; e registrará a seleção antes de retorná-la.

`resolve_fallback` aceitará apenas failure categories e candidates já autorizados pela seleção. Revalidará ownership, cancellation, validade, status, budget restante e constraints. Nunca fará descoberta ilimitada nem inferirá successor de modelo.

### Provider API

Os tipos cobrirão:

- `ProviderInvocationRequest` com selection, snapshot aprovado, mensagens normalizadas, Tools como declarações não executáveis, response format, sampling, limits, cancellation e idempotency;
- partes de texto, imagem por referência opaca, resultado de Tool por referência e refusal sanitizado;
- `ProviderUsage`, `ProviderCost`, medição e pricing revision;
- `ProviderOutcome` para sucesso, Tool requests, user-input requests, falha, cancelamento e indeterminate outcome;
- `ProviderErrorCategory`, código público, retryability, `request_accepted`, timeout e causa opaca;
- `ProviderStream`, eventos sequenciados, terminal snapshot e await/read/cancel requests;
- `CancellationSignal`/`CancellationSignalRef` cooperativo e idempotente;
- `ProviderInvocationPort`/`ProviderPort` completos, incluindo `generate`, `open_stream`, `read_stream`, `cancel`, `await_terminal` e `inspect`.

As classes de request que podem carregar conteúdo sensível usarão `repr=False` ou `__repr__` sanitizado. Resultados só exporão referências e dados normalizados; argumentos de Tool serão explicitamente não confiáveis.

## Compatibilidade com Runtime

O Runtime manterá suas classes de transporte de referência para não quebrar os testes e consumidores atuais. `RuntimeModelResolverAdapter` converterá o request reduzido para `ResolveModel` e converterá `ModelResolved` para uma seleção reference-only, rejeitando outcomes de resolução incompatíveis de modo categórico. `RuntimeProviderAdapter` converterá a solicitação reduzida em invocação completa quando houver seleção/snapshot canônicos, e mapeará outcomes completos para os outcomes legados sem expor bindings ou payloads.

O loop do Runtime continuará responsável por limites, cancelamento, Tool round-trip, resultado e persistência via portas existentes. Ele não importará catálogo interno, adapter tecnológico, SDK ou pricing proprietário.

## Segurança, falhas e accounting

Validação ocorrerá antes de efeitos: contexto e ownership, integridade/validade de selection e snapshot, status, capabilities, limites, cancelamento e policy. Falhas são sanitizadas e categorizadas. Timeout, rate limit, auth, policy failure, invalid request, cancellation e indeterminate permanecem distinguíveis e não são convertidos em sucesso.

Retry não será implícito. Uma tentativa adicional exigirá retryability, idempotência, policy e budget; fallback será chamado explicitamente pelo Runtime/resolver. Uso/custo confirmados ou indisponíveis serão preservados, inclusive quando uma tentativa não terminar com sucesso.

## Testes

Os testes serão escritos antes da implementação, em ciclos RED/GREEN/REFACTOR, usando apenas fakes das portas. Cobrirão validação de escopo, imutabilidade, idempotência/conflito, status, constraints, custo desconhecido, seleção determinística, explicação, snapshot/revalidação, fallback, outcomes Provider, streaming conceitual, cancelamento, accounting, segurança de `repr` e integração do Runtime. A suíte existente de Execution, Runtime e Context deverá permanecer verde.

## Verificação de fronteira

Ao final serão executados:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|MemoryStore|ArtifactStorage" src/agentos/providers
```

Também será feita auditoria requisito por requisito contra RFCs 050, 060, 101, 104, 501, 502 e 601. O workspace não contém `.git`; portanto os documentos não poderão ser commitados nesta sessão, embora sejam gravados e revisados no caminho normativo.

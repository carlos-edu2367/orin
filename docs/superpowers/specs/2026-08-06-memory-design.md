# Memory Design

**Status:** aprovado para implementação após revisão da sessão

## Objetivo e fronteira

Implementar a fronteira pública da RFC 301 como um domínio independente. `MemoryManager` será a única porta de aplicação para salvar, ler, buscar, invalidar, consolidar e aplicar retenção. `Context` continuará efêmero: uma fonte de Memory poderá entregar somente `AuthorizedMemory`, referências e trechos mínimos ao `ContextSource` existente; montagem, descarte e finalização de Context não escrevem, renovam ou invalidam Memory.

O pacote não escolherá banco, ORM, índice vetorial, embeddings, Redis, Artifact Storage, Provider, API ou worker. A implementação entregue será um adapter in-memory bounded, determinístico e substituível. Durabilidade de produção exigirá uma composição posterior com a porta RFC 601.

## Arquitetura aprovada

```text
Commands / queries
        |
        v
MemoryManager
  |       |        |
  v       v        v
Policy  Store   SearchAdapter
                |
                v
     commit port: state + audit + outbox
                         |
                         v
                   Events RFC 103
```

`MemoryManager` concentra as regras de ownership, grants, classificação, proveniência, lifecycle, versionamento, idempotência e minimização. `MemoryStore` expõe apenas operações públicas de leitura e uma operação de commit transacional conceitual. `MemorySearchAdapter` recebe somente candidatos já autorizados e filtrados. Uma porta estreita de commit recebe a mudança de estado, `MemoryAuditRecord` e o `EventEnvelope` já sanitizado; o adapter in-memory confirma os três juntos e oferece uma outbox deduplicável.

O adapter de referência será composto por:

- `InMemoryMemoryStore`: registros, revisões, tombstones, idempotência, auditoria e outbox sob lock curto;
- `InMemoryMemorySearchAdapter`: busca textual bounded, determinística, sem embeddings;
- `InMemoryMemoryAuthorizationPolicy`: agentes ativos, permissões de Workspace e grants mínimos, revogáveis e limitados;
- `InMemoryMemoryManager`: orquestração das operações pela porta pública.

O domínio não importará módulos concretos de persistência, Events Bus, Agents, Artifact ou Context. `DataClassification` e `EventEnvelope` serão consumidos somente por seus contratos públicos canônicos.

## Contratos públicos

### Dados e limites

Os contratos serão frozen/slotted dataclasses e enums `StrEnum`, com validação na construção. Todos os identificadores e referências são strings não vazias e opacas. Instantes precisam ser timezone-aware.

Limites normativos do adapter inicial:

- conteúdo textual: no máximo 4096 caracteres;
- trecho de busca: no máximo 512 caracteres;
- intenção de busca e propósito: no máximo 256 e 128 caracteres, respectivamente;
- no máximo 100 resultados e 4096 unidades de conteúdo por consulta;
- no máximo 32 fontes por consolidação e 64 referências por retenção;
- proveniência: no máximo 32 referências e 32 transformações;
- grants: expiração obrigatória e máximo de usos positivo.

`BoundedMemoryContent` e `MemoryArtifactReference` são tipos distintos. O primeiro guarda somente texto bounded; o segundo guarda apenas ID, versão e integridade opacos, sem path, URL, bytes, credencial ou handle. `repr` de conteúdo, referências, comandos, receipts e erros não expõe conteúdo sensível.

`MemoryProvenance` exige `source_kind`, ao menos uma `source_ref`, integridade e cadeia de transformações bounded. `MemoryReference` contém ID/version, ownership, Agent permitido, finalidade, expiração, grant e integridade, mas nunca conteúdo ou localização física.

`MemoryScope` é `PRIVATE`, `WORKSPACE` ou `USER`; `MemoryKind` é `EPISODIC`, `PROCEDURAL`, `PREFERENCE`, `FACT` ou `SEMANTIC`; `MemoryStatus` é `ACTIVE`, `INVALIDATED`, `EXPIRED` ou `SUPERSEDED`. `SEMANTIC` permanece um `kind` e conserva `base_scope`; não cria ownership global.

Comandos carregam `MemoryOperationContext` com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`. Operações sensíveis também carregam classificação/ceiling e idempotency key quando produzem efeito.

### Ownership e autorização

- `PRIVATE` exige `owner_agent_id`; somente esse Agent pode ler e escrever por padrão. Parentesco, colaboração, Orchestrator ou Agent filho não herdam acesso.
- `WORKSPACE` exige `workspace_id`; o Agent precisa estar ativo e possuir autorização explícita para aquele Workspace.
- `USER` nasce com `workspace_id = null`; acesso sem Workspace depende da política do usuário. Exposição a um Workspace exige grant explícito e não altera o registro original.
- cross-user, cross-workspace, Agent incorreto, purpose incorreto, referência expirada/revogada e classificação acima do ceiling falham fechado sem revelar existência.
- grants são específicos para memória/escopo, Agent alvo, Execution, finalidade, ceiling, validade e máximo de usos; não permitem redelegação.

O `InMemoryMemoryAuthorizationPolicy` mantém o conjunto de Agents ativos, autorizações de Workspace e grants. O Manager chama a política em toda leitura, busca e mutação; conhecer um ID ou possuir uma referência não basta.

### Escrita, concorrência e lifecycle

`save` aceita criação ou atualização versionada. Criação usa versão 1; atualização exige `memory_ref` e `expected_version`, incrementa exatamente uma versão e rejeita divergência sem last-write-wins. O mesmo idempotency key e fingerprint devolve o receipt original. Fingerprint divergente produz conflito sanitizado.

Invalidamento é uma mudança versionada e cria tombstone lógico; retry não ressuscita o registro. Expiração e supersession também são terminais para resolução. Consolidação reautoriza todas as fontes no momento da operação, exige versões fixas, cria uma Memory nova, preserva lineage e fontes, usa a classificação mais restritiva e nunca publica resultado parcial. A retenção opera somente sobre as referências recebidas, com contagens explícitas e sem ampliar escopo.

O commit contém a mudança de registro/revisão, auditoria mínima e Event. O adapter valida tudo antes de mutar qualquer coleção e, em sucesso, grava os três atomicamente. Falhas ou rejeições não deixam registro, auditoria ou outbox parcial. O commit confirma uma sequência por `execution_id`; retry reaproveita o mesmo Event ID.

### Busca e Context

Busca textual usa somente termos bounded e filtros de ownership, escopo, classificação, status, purpose, proveniência e tempo antes de ranking e antes de criar `MemoryMatch`. O resultado contém `MemoryReference`, versão, tipo, escopo, trecho mínimo, relevância, razões e proveniência. O adapter declara que recuperação semântica real/embeddings são capacidades futuras.

`MemoryContextSource` implementará `ContextSource` pela porta existente, usando `SourceKind.MEMORY`. Ele converte `MemoryMatch` em candidatos reference-first, limita trechos ao orçamento recebido e nunca chama `save`. `ContextManagerService.finalize` continuará sem dependência de Memory.

### Events e proteção de dados

O Manager produzirá `MemorySaved`, `MemoryRead`, `MemorySearched`, `MemoryInvalidated`, `MemorySuperseded`, `MemoryConsolidated`, `MemoryExpired`, `MemoryAccessDenied` e `MemoryOperationFailed` conforme o outcome. Todos usarão o envelope público de Events, classificação categórica, IDs, versão, escopo, ownership, execução, correlação, purpose e razões sanitizadas. Nenhum Event, log, erro, `repr`, receipt ou auditoria conterá prompt, conteúdo completo, segredo, credencial, token, SQL, path ou exceção tecnológica.

## Arquivos

- `src/agentos/memory/models.py`: tipos, comandos, receipts, grants, conflitos e erros;
- `src/agentos/memory/ports.py`: `MemoryManager`, `MemoryStore`, `MemorySearchAdapter`, política e commit/fatos;
- `src/agentos/memory/security.py`: bounds, redaction, fingerprints e validação de invariantes;
- `src/agentos/memory/in_memory.py`: política, store, busca e Manager de referência;
- `src/agentos/memory/context_compat.py`: fonte opcional para Context;
- `src/agentos/memory/__init__.py`: somente exports públicos estáveis;
- `tests/unit/memory/`: contratos, segurança, lifecycle, busca, atomicidade, Events e Context;
- `tests/unit/integration/test_memory_boundaries.py`: scans de dependências e ausência de escrita implícita.

## Estratégia de testes

Cada comportamento começa por um teste RED. Os ciclos cobrem contratos e contexto completo; ownership/grants/classificação; bounds, proveniência, integridade e redaction; save/update/idempotência/conflitos; atomicidade e rollback; busca pré-filtro e trechos; invalidamento/tombstone/supersession; consolidação/lineage; retenção; Event pós-commit e deduplicação; integração opcional com Context; e scans transversais.

## Limitações explícitas

O adapter in-memory não oferece durabilidade, recuperação após processo, PostgreSQL, migrações, busca semântica, embeddings, Artifact Storage ou exactly-once. A conexão com RFC 601 fica para uma composição posterior que implemente a mesma porta de commit e preserve a autoridade transacional e a outbox.

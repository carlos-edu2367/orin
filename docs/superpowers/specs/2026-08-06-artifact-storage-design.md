# Artifact Storage — Especificação de desenho

**Data:** 2026-08-06  
**Escopo:** RFC 602 — Artifact Storage, integrado à RFC 601 — Persistência e à RFC 103 — Sistema de eventos  
**Status:** aprovado para implementação no escopo de referência/in-memory

## Objetivo

Entregar um vertical slice de Artifact Storage que preserve a semântica normativa da RFC 602 sem escolher filesystem, object storage, fornecedor, transporte HTTP ou infraestrutura distribuída. O domínio terá uma porta pública independente de tecnologia; o adapter de bytes será substituível; metadata, vínculos, idempotência e outbox usarão a fronteira transacional já existente da RFC 601.

O adapter in-memory é a referência determinística do contrato. Quando o padrão atual permitir, haverá um adapter PostgreSQL/Alembic para metadata, sem importar SQLAlchemy para o domínio e sem fingir que bytes em memória são armazenamento de produção.

## Decisões de fronteira

### Pacote público

`agentos.artifact_storage` será a única porta canônica do subsistema. Os contratos serão dataclasses congeladas com `slots`, enums e Protocols. Os campos públicos serão bounded e validáveis; `repr`, exceções e payloads não materializarão conteúdo nem segredo.

Os modelos cobrirão:

- `ArtifactOperationContext`, `ArtifactNamespace`, `ArtifactCategory`, `ArtifactProvenance` e `DataClassification`;
- `ArtifactMetadata`, `ArtifactReference`, `ArtifactGrant` e versões;
- requests/results de begin, append, finalize, abort, open/read, inspect, verify, delete e retenção;
- capacidades, handles opacos, receipts e `effect_state` (`NOT_APPLIED`, `APPLIED`, `UNKNOWN`);
- erros normalizados com retryability e estados de commit/lifecycle explícitos.

Nenhum modelo conterá path físico, bucket, URL permanente, credencial, handle nativo, bytes de tamanho não limitado ou decisão específica de fornecedor.

### Portas

`ArtifactManager` será a fachada de aplicação para autorização, política e lifecycle. `ArtifactStorage` será uma porta de bytes de menor nível: recebe somente namespace e operação já autorizados pelo Manager e retorna handles/receipts opacos. `ArtifactMetadataRepository` será a porta de metadata e vínculos, com implementação em memória e adapter compatível com a composição da RFC 601.

O Manager não delegará autorização ao adapter de bytes. O adapter não criará referências públicas, não escolherá namespace e não decidirá sobre grants, holds ou retenção. Falhas de adapter serão normalizadas e nunca convertidas em sucesso quando o efeito for incerto.

### Dependências

O Manager dependerá de:

1. `ArtifactStorage` para staging, chunks, seal, leitura, verify e delete lógico/físico;
2. `ArtifactMetadataRepository` para metadata versionada, grants, reservas, vínculos, referências e idempotência;
3. `TransactionalPersistence`/outbox existente para fatos confirmados quando a composição o fornecer;
4. relógio e gerador de IDs injetáveis para testes determinísticos.

O domínio de Artifact Storage não importará SQLAlchemy, Alembic, FastAPI, HTTP, SDKs, Redis, broker, filesystem ou cliente de object storage.

## Modelo de namespace, ownership e autorização

O namespace será derivado por política de `user_id`, `workspace_id` e categoria. `logical_name` será apenas metadata sanitizada e nunca influenciará localização física. O contexto obrigatório carregará `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`.

Cada resolução revalidará contexto, ownership, agent, execution, purpose, classificação, grant, versão, estado e expiração. Uma referência de outro usuário/workspace, namespace, versão, binding ou finalidade falhará de modo sanitizado e sem confirmar existência indevida. O ceiling de classificação será aplicado antes de materializar metadata ou bytes.

Nomes externos rejeitarão path traversal, separadores, controle, credenciais, segredos e valores fora do limite. Metadata, referência, evento e log não carregarão senha, token, chave, URL assinada, path ou conteúdo sensível não sanitizado.

## Escrita e integridade

`begin_write` autorizará o contexto, derivará namespace, validará categoria/classificação/política, consultará capabilities, reservará quota e criará uma sessão `STAGING` com expiração e limite de objeto/chunk.

`append` aceitará apenas bytes cujo comprimento declarado coincida com a fonte. A sessão será vinculada ao contexto, namespace, adapter e binding que emitiram o handle. Repetição de chunk só será aceita se offset, comprimento, checksum e idempotency key coincidirem. Offset conflitante, chunk excessivo, sessão expirada ou binding incorreto não concatenarão dados.

`finalize` exigirá tamanho total e checksum esperados conforme a política, chamará `seal` e aceitará `AVAILABLE` somente após o adapter calcular/verificar tamanho e checksum sobre os bytes efetivamente persistidos. A quota será reconciliada pelo consumo real; falha, timeout ou cancelamento depois de efeito possível retornará `UNKNOWN`/reconciliação e nunca fabricará `ArtifactReference`. Checksum/tamanho divergente manterá o conteúdo fora de `AVAILABLE` e poderá colocá-lo em `QUARANTINED`.

O conteúdo publicado será imutável. Alteração de metadata que não altere bytes, ownership, versão ou proveniência seguirá transição versionada; derivação criará novo Artifact.

## Leitura, verificação e cancelamento

`open_read` revalidará a referência e fixará artifact ID, versão, checksum, tamanho, classificação, finalidade, contexto e prazo do handle. `read` aceitará apenas offset bounded e `maximum_bytes` bounded, será cancelável e fará revalidação em cada limite seguro. Entrega parcial seguida de falha produzirá receipt com efeito aplicado; a retomada usará versão e offset confirmados, sem concatenar prefixos ambíguos.

`verify` comparará metadata e bytes. Divergência ou indeterminação nunca servirá conteúdo íntegro: o Manager marcará `QUARANTINED`, bloqueará novas resoluções e emitirá fato categórico após a mudança confirmada.

## Lifecycle, quota, retenção e delete

Os estados explícitos serão `STAGING`, `AVAILABLE`, `QUARANTINED`, `EXPIRED`, `DELETING` e `DELETED`, com transições idempotentes e versionadas. Reserva ocorrerá antes da escrita; staging, incompletos e janela recuperável contarão na quota. Reserva só será liberada após confirmar que não houve publish.

Retenção expirará referências e impedirá novas resoluções antes de qualquer cleanup. Delete primeiro invalidará novas resoluções e marcará `DELETING`; respeitará grants, referências ativas, holds, versão e política. Quando configurada, usará janela recuperável; cleanup incerto manterá `DELETING` e produzirá reconciliação/`ArtifactCleanupFailed`. Ressurreição pelo mesmo ID será proibida. `apply_retention` será bounded e idempotente.

## Persistência e eventos

Metadata e vínculos ficarão separados dos bytes. O adapter in-memory será a referência completa. O adapter PostgreSQL, se compatível com a fronteira atual, mapeará registros versionados de artifact, grants, quotas/reservas, vínculos e idempotência através da porta transacional existente; migration será explícita e o domínio não conhecerá schema ORM.

Os eventos mínimos serão registrados na outbox somente após o fato confirmado:

- `ArtifactWriteStarted` após staging e reserva confirmados;
- `ArtifactStored` após seal íntegro e metadata `AVAILABLE` confirmados;
- `ArtifactReadFinished` após leitura terminar com outcome explícito;
- `ArtifactQuarantined` após integridade/política confirmar quarentena;
- `ArtifactExpired` após retenção impedir novas resoluções;
- `ArtifactDeleted` após invalidação e remoção lógica confirmadas;
- `ArtifactCleanupFailed` quando cleanup permanecer incerto/incompleto.

Payloads terão apenas IDs, ownership, execution/correlação, categoria, versão, tamanho/checksum conforme necessidade e razão categórica sanitizada. Não haverá bytes, path, URL, credencial, segredo, digest tratado como segredo ou nome sensível bruto. Deduplicação usará `event_id` e a confirmação de outbox seguirá a semântica da RFC 601/103.

## Estratégia de testes

Os testes serão escritos em ciclos RED/GREEN/REFACTOR. A suíte unitária demonstrará contratos bounded/imutáveis, namespace, authorization binding, chunks/idempotência, checksum/tamanho, seal, handles, range/cancelamento, lifecycle, quotas, retenção, quarantine, delete recuperável, cleanup incerto, outbox pós-fato e sanitização.

Testes de fronteira provarão que metadata/outbox passam pela porta RFC 601, que o adapter SQLAlchemy/Alembic não vaza para o domínio e que migration não é executada implicitamente. O PostgreSQL real será opcional e marcado `skipped` sem `AGENTOS_TEST_POSTGRES_DSN`. A suíte inteira, `compileall`, scan de dependências proibidas, `git diff --check` e revisão independente serão gates de fechamento.

## Alternativas rejeitadas

- **Bytes e metadata em uma única porta:** rejeitada porque mistura conteúdo volumoso com persistência transacional e impede adapter substituível.
- **Manager que depende diretamente de SQLAlchemy:** rejeitada por violar RFC 050/601 e tornar o domínio não substituível.
- **Referência criada antes do seal:** rejeitada porque permite conteúdo incompleto ou checksum não confirmado.
- **Delete físico imediato:** rejeitado porque viola referências, holds, janela recuperável e reconciliação de efeitos incertos.

## Limitações explícitas

Este gate não implementa filesystem real, S3/GCS/Azure, CDN, URL assinada, API HTTP, multipart de transporte, malware scanner, DLP, preview, deduplicação física, content-addressing, worker, scheduler, Redis, broker, transação distribuída ou exactly-once. O adapter in-memory demonstra a semântica do contrato, não capacidade de produção; durabilidade, escala, replicação, backup, restore e limpeza física permanecem responsabilidade de gates operacionais posteriores.

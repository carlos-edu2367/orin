# RFC 602 — Artifact Storage closeout

**Data:** 2026-08-06  
**Escopo fechado:** contratos, Manager, bytes in-memory, metadata RFC 601, outbox, autorização, integridade, lifecycle, quotas e reconciliação no escopo de referência.

## Entregue

- Porta pública `agentos.artifact_storage` com modelos congelados, limites, handles opacos, receipts, erros normalizados, estados e capacidades.
- `ArtifactManagerService` com namespace derivado, ownership, provenance binding, grants, purpose, classification ceiling, begin/append/finalize/abort, open/read/inspect/verify, delete, retention e cleanup reconciliation.
- `InMemoryArtifactStorage` com staging limitado, chunks idempotentes, offset conflict, seal SHA-256, range read, expiry, verify, delete recuperável e efeitos `NOT_APPLIED/APPLIED/UNKNOWN`.
- `InMemoryArtifactMetadataRepository` com reservas antes da escrita, accounting real no finalize, estados versionados, holds, idempotência e quota.
- `TransactionalArtifactMetadataRepository` usando somente `TransactionalPersistence`; metadata e eventos mínimos são registrados como `RecordChange` + `AuditChange` + `OutboxChange` na mesma unidade RFC 601. O schema JSON genérico versionado já existente representa os campos bounded, portanto não houve migration específica.
- Sete eventos normativos, payload mínimo sem bytes/path/credenciais e deduplicação por IDs determinísticos no adapter transacional.
- Testes de segurança, integração de fronteira, outbox pós-confirmação, round-trip do adapter RFC 601 e PostgreSQL opcional condicionado a DSN.

## Decisões e motivos

- Metadata e bytes foram separados para preservar a substituibilidade e impedir conteúdo volumoso no banco/outbox.
- O namespace usa digest de ownership/categoria porque nome lógico não deve escolher localização física nem revelar ownership.
- O adapter PostgreSQL de Artifact Storage não duplica ORM/schema: reutiliza a porta e o registro JSON da RFC 601, evitando migration desnecessária e mantendo SQLAlchemy confinado ao adapter existente.
- Eventos in-memory são registrados depois do fato confirmado; com o repository transacional, os sete eventos aplicáveis entram na mesma transação RFC 601 e não são publicados diretamente pelo Manager.
- Cleanup incerto mantém `DELETING`, emite `ArtifactCleanupFailed` e exige `reconcile_cleanup`; o mesmo ID nunca é ressuscitado.

## Limitações tecnológicas deliberadas

Não foram implementados filesystem real, S3/GCS/Azure, CDN, URL assinada, API HTTP, multipart de transporte, malware scanner, DLP, preview, deduplicação física, content-addressing, worker, scheduler, Redis, broker, transação distribuída ou exactly-once. O adapter in-memory é referência de contrato, não storage de produção. PostgreSQL real só é validado quando `AGENTOS_TEST_POSTGRES_DSN` existe.

## Próximo passo documental

Seguir para RFC 603 — Workspaces, que agora pode referenciar Artifacts por identidade, versão, grant e lifecycle completos. Filesystem Resource, Browser Resource e gates operacionais continuam posteriores e devem consumir a porta pública, não os adapters internos.

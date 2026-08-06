# RFC 602 — Artifact Storage

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 301 — Memory](../300-context-memory/301-memory.md), [RFC 403 — Filesystem](../400-tools-resources/403-filesystem.md), [RFC 405 — Browser](../400-tools-resources/405-browser.md), [RFC 601 — Persistência](601-persistence.md), [RFC 603 — Workspaces](603-workspaces.md)

## Objetivo

Definir `ArtifactStorage` como porta substituível para conteúdo durável produzido, recebido ou referenciado pelo AgentOS. O contrato cobre namespace, ownership, metadata, checksum, integridade, uploads, downloads, screenshots, logs, referência versus conteúdo, quotas, retenção e limpeza segura sem expor backend, bucket ou caminho físico.

## Fora de escopo

- escolher filesystem, object storage, protocolo, fornecedor, região ou formato de URL;
- definir interface de upload HTTP, CDN, preview, editor ou visualizador;
- substituir Filesystem Resource, Memory, Context ou banco transacional;
- interpretar semanticamente documentos, executar arquivos ou confiar em conteúdo recebido;
- impor deduplicação física ou content-addressing;
- guardar credenciais dentro de Artifact ou `ArtifactReference`.

## Responsabilidades e não responsabilidades

`ArtifactStorage` DEVE:

- criar identidade estável e namespace derivado de ownership e finalidade;
- separar metadata transacional de conteúdo potencialmente volumoso;
- receber e entregar bytes por streams limitados e canceláveis;
- calcular e verificar tamanho e checksum antes de publicar conteúdo;
- suportar uploads, downloads, screenshots, logs e resultados volumosos por categorias explícitas;
- aplicar classificação, content type declarado, quota, retenção e lifecycle;
- resolver referências somente após reautorizar ownership, Agent, Execution e purpose;
- manter cleanup recuperável, idempotente e resistente a corridas;
- permitir adapters substituíveis sem alterar referência pública.

`ArtifactStorage` NÃO DEVE:

- expor chave física, bucket, pathname ou credencial como identidade do Artifact;
- colocar bytes volumosos em PostgreSQL, Event, Context ou log de aplicação;
- considerar nome, extensão, content type ou checksum fornecido pelo chamador como prova de segurança;
- publicar referência antes de confirmar integridade e metadata;
- apagar conteúdo ainda referenciado, em legal hold ou dentro de janela de recuperação;
- permitir acesso cross-user ou cross-workspace por conhecimento do `artifact_id`;
- prometer que armazenamento implica ausência de malware ou adequação ao consumo.

## Arquitetura

```text
Browser / Tool / Runtime / importação
             │ ArtifactWriteRequest + ByteSource
             ▼
       ArtifactManager
  ┌──────────┼───────────┐
  │ Namespace & Policy   │
  │ Metadata / Reference │──> PostgreSQL
  │ Integrity & Quota    │
  │ Lifecycle / Cleanup  │
  └──────────┬───────────┘
             ▼
       ArtifactStorage port
             ▼
     adapter substituível de bytes
```

O `ArtifactManager` possui política, autorização e lifecycle; a porta `ArtifactStorage` materializa bytes e primitivas de leitura/escrita/remoção. Metadata durável e vínculo de referência são confirmados pela fronteira transacional da RFC 601. Um staging object não é Artifact publicado.

## Dados, namespace e referências

```text
ArtifactOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

ArtifactNamespace {
  user_id: UserId
  workspace_id: WorkspaceId
  category: UPLOAD | DOWNLOAD | SCREENSHOT | LOG | RESULT | EXPORT | OTHER
  partition_ref: OpaquePartitionRef
}
```

`partition_ref` é criado pela plataforma e não deriva de concatenação fornecida pelo chamador. Nomes lógicos são metadata; não selecionam localização física. O namespace impede colisão e enumeração entre usuários e Workspaces, mesmo quando checksums ou nomes coincidem.

```text
ArtifactMetadata {
  artifact_id: ArtifactId
  namespace: ArtifactNamespace
  logical_name: SanitizedLogicalName
  category: ArtifactCategory
  media_type: MediaType
  declared_media_type: MediaType | null
  size_bytes: NonNegativeInteger
  checksum: ContentChecksum
  classification: DataClassification
  provenance: ArtifactProvenance
  retention_policy_ref: RetentionPolicyRef
  state: STAGING | AVAILABLE | QUARANTINED | EXPIRED | DELETING | DELETED
  version: Version
  created_at: Instant
  available_at: Instant | null
  expires_at: Instant | null
}

ContentChecksum {
  algorithm: ChecksumAlgorithm
  digest: OpaqueDigest
}

ArtifactProvenance {
  source_kind: USER_UPLOAD | BROWSER_DOWNLOAD | SCREENSHOT | TOOL_OUTPUT |
               AGENT_RESULT | LOG_STREAM | IMPORT | DERIVATION
  source_refs: SourceReference[]
  created_by: ActorRef
  execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

Algoritmos aceitos são definidos por política versionada e precisam oferecer resistência adequada a colisão; algoritmo fraco não vira prova de integridade. O checksum é calculado pelo lado confiável sobre os bytes efetivamente armazenados e comparado à expectativa quando fornecida.

```text
ArtifactReference {
  artifact_id: ArtifactId
  version: Version
  user_id: UserId
  workspace_id: WorkspaceId
  category: ArtifactCategory
  size_bytes: NonNegativeInteger
  checksum: ContentChecksum
  classification: DataClassification
  authorization_ref: ArtifactGrantRef
  purpose: AccessPurpose
  expires_at: Instant | null
}
```

`ArtifactReference` é identidade e autorização limitada; não contém bytes, pathname, URL permanente ou segredo. Resolver a referência revalida estado, versão, ownership, classificação, Grant e finalidade. Uma referência pode expirar antes do Artifact e não transfere ownership.

## Contratos tipados

```text
interface ArtifactManager {
  begin_write(request: BeginArtifactWrite) -> ArtifactWriteSession
  append(request: AppendArtifactChunk, source: ByteSource) -> ArtifactWriteProgress
  finalize(request: FinalizeArtifactWrite) -> ArtifactReference
  abort(request: AbortArtifactWrite) -> AbortArtifactResult
  open_read(request: OpenArtifactRead) -> ArtifactReadSession
  read(request: ReadArtifactRange, sink: ByteSink) -> ArtifactReadProgress
  inspect(query: InspectArtifact) -> AuthorizedArtifactMetadata
  delete(request: DeleteArtifact) -> ArtifactDeletionReceipt
  apply_retention(request: ApplyArtifactRetention) -> ArtifactRetentionReceipt

  pre: contexto e purpose foram autorizados no namespace
  post: apenas AVAILABLE produz ArtifactReference resolvível
}
```

### Porta de bytes substituível

`ArtifactManager` decide autorização, namespace, quota, metadata e lifecycle. `ArtifactStorage` é uma porta distinta e de menor nível: recebe somente operações já autorizadas sobre objetos opacos e implementa staging, leitura, verificação e remoção dos bytes. Adapters não podem tomar decisão de domínio nem fabricar `ArtifactReference`.

```text
interface ArtifactStorage {
  capabilities(query: ArtifactStorageCapabilityQuery) -> ArtifactStorageCapabilities
  begin_staging(request: StorageBeginStaging) -> ArtifactStorageResult<StorageStagingHandle>
  write_chunk(request: StorageWriteChunk, source: ByteSource) -> ArtifactStorageResult<StorageWriteReceipt>
  seal(request: StorageSealObject) -> ArtifactStorageResult<StorageSealedObject>
  abort_staging(request: StorageAbortStaging) -> ArtifactStorageResult<StorageAbortReceipt>
  open_read(request: StorageOpenRead) -> ArtifactStorageResult<StorageReadHandle>
  read_range(request: StorageReadRange, sink: ByteSink) -> ArtifactStorageResult<StorageReadReceipt>
  verify(request: StorageVerifyObject) -> ArtifactStorageResult<StorageIntegrityReceipt>
  delete(request: StorageDeleteObject) -> ArtifactStorageResult<StorageDeleteReceipt>

  pre: chamada vem do ArtifactManager com contexto completo e namespace já autorizado
  pre: object_ref e handles foram emitidos pelo mesmo binding de adapter e ownership
  post: adapter não retorna path, bucket, URL permanente, credencial ou handle nativo
  post: sucesso de seal inclui tamanho e checksum calculados sobre os bytes persistidos
  post: erro declara retryability e effect_state sem converter incerteza em sucesso
}

ArtifactStorageCapabilityQuery {
  context: ArtifactOperationContext
  namespace: ArtifactNamespace
  required: ArtifactStorageCapability[]
}

ArtifactStorageCapability = STREAM_WRITE | RESUMABLE_WRITE | RANGE_READ |
                            ATOMIC_SEAL | SERVER_SIDE_CHECKSUM |
                            RECOVERABLE_DELETE | IMMUTABLE_OBJECT

ArtifactStorageCapabilities {
  supported: ArtifactStorageCapability[]
  checksum_algorithms: ChecksumAlgorithm[]
  maximum_object_bytes: NonNegativeInteger
  maximum_chunk_bytes: PositiveInteger
  minimum_recovery_window: Duration | null
  adapter_contract_version: Version
}
```

Uma capability ausente causa rejeição antes do efeito quando o fluxo a exigir. O `ArtifactManager` pode escolher outro adapter já autorizado para o mesmo namespace antes de iniciar staging; não pode migrar silenciosamente uma sessão, enfraquecer integridade ou fazer fallback cross-workspace depois de bytes escritos.

```text
StorageBeginStaging {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  namespace: ArtifactNamespace
  provenance_ref: ArtifactProvenanceRef
  storage_object_id: StorageObjectId
  expected_size_bytes: NonNegativeInteger | null
  checksum_algorithm: ChecksumAlgorithm
  maximum_size_bytes: NonNegativeInteger
  expires_at: Instant
  idempotency_key: IdempotencyKey
}

StorageStagingHandle {
  staging_ref: OpaqueStorageStagingRef
  storage_object_id: StorageObjectId
  accepted_offset_bytes: NonNegativeInteger
  expires_at: Instant
  adapter_contract_version: Version
}

StorageWriteChunk {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  staging_ref: OpaqueStorageStagingRef
  offset_bytes: NonNegativeInteger
  length_bytes: PositiveInteger
  expected_chunk_checksum: ContentChecksum | null
  idempotency_key: IdempotencyKey
}

StorageWriteReceipt {
  storage_object_id: StorageObjectId
  accepted_offset_bytes: NonNegativeInteger
  accepted_length_bytes: NonNegativeInteger
  computed_chunk_checksum: ContentChecksum | null
  effect_state: NOT_APPLIED | APPLIED
}

StorageSealObject {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  staging_ref: OpaqueStorageStagingRef
  expected_total_size_bytes: NonNegativeInteger
  expected_checksum: ContentChecksum | null
  require_immutable: Boolean
  idempotency_key: IdempotencyKey
}

StorageSealedObject {
  object_ref: OpaqueStorageObjectRef
  storage_object_id: StorageObjectId
  size_bytes: NonNegativeInteger
  computed_checksum: ContentChecksum
  immutable: Boolean
  sealed_at: Instant
  integrity_state: VERIFIED
}
```

`storage_object_id` é alocado pelo Manager dentro do namespace e não é o `artifact_id`. `object_ref` permanece somente na metadata interna protegida. Um adapter aceita repetição de chunk apenas quando offset, comprimento, checksum e idempotency key coincidem. `seal` é idempotente e nunca devolve `VERIFIED` se o adapter não calculou ou releu evidência suficiente para o tamanho e digest informados.

```text
StorageAbortStaging {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  staging_ref: OpaqueStorageStagingRef
  provenance_ref: ArtifactProvenanceRef
  reason: AbortReason
  idempotency_key: IdempotencyKey
}

StorageReceiptContext {
  operation_id: ArtifactOperationId
  user_id: UserId
  workspace_id: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  provenance_ref: ArtifactProvenanceRef
}

ArtifactProvenanceRef {
  source_kind: USER_UPLOAD | BROWSER_DOWNLOAD | SCREENSHOT | TOOL_OUTPUT |
               AGENT_RESULT | LOG_STREAM | IMPORT | DERIVATION
  source_execution_id: ExecutionId
  source_correlation_id: CorrelationId
  integrity_ref: IntegrityRef
}

StorageAbortReceipt {
  receipt_context: StorageReceiptContext
  storage_object_id: StorageObjectId
  outcome: ABORTED | ALREADY_ABORTED | ALREADY_ABSENT
  removed_staging_bytes: NonNegativeInteger
  integrity_disposition: DISCARDED_UNPUBLISHED | NOTHING_TO_DISCARD
  effect_state: APPLIED | NOT_APPLIED
  aborted_at: Instant
}

StorageOpenRead {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  object_ref: OpaqueStorageObjectRef
  provenance_ref: ArtifactProvenanceRef
  expected_size_bytes: NonNegativeInteger
  expected_checksum: ContentChecksum
  maximum_bytes: NonNegativeInteger
}

StorageReadHandle {
  read_ref: OpaqueStorageReadRef
  storage_object_id: StorageObjectId
  size_bytes: NonNegativeInteger
  checksum: ContentChecksum
  expires_at: Instant
}

StorageReadRange {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  read_ref: OpaqueStorageReadRef
  offset_bytes: NonNegativeInteger
  maximum_bytes: NonNegativeInteger
}

StorageReadReceipt {
  receipt_context: StorageReceiptContext
  storage_object_id: StorageObjectId
  outcome: RANGE_DELIVERED | END_OF_OBJECT | NO_BYTES_AT_OFFSET
  requested_offset_bytes: NonNegativeInteger
  delivered_offset_bytes: NonNegativeInteger
  delivered_bytes: NonNegativeInteger
  next_offset_bytes: NonNegativeInteger | null
  object_size_bytes: NonNegativeInteger
  expected_checksum: ContentChecksum
  integrity_observation: NOT_RECHECKED | VERIFIED_BLOCKS | FULLY_VERIFIED
  completed_at: Instant
}

StorageVerifyObject {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  object_ref: OpaqueStorageObjectRef
  expected_size_bytes: NonNegativeInteger
  expected_checksum: ContentChecksum
  verification_mode: FULL | TRUSTED_BLOCKS
}

StorageIntegrityReceipt {
  storage_object_id: StorageObjectId
  observed_size_bytes: NonNegativeInteger
  observed_checksum: ContentChecksum | null
  integrity_state: VERIFIED | MISMATCH | INDETERMINATE
  verified_at: Instant
}

StorageDeleteObject {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  object_ref: OpaqueStorageObjectRef
  expected_checksum: ContentChecksum
  deletion_mode: RECOVERABLE | PERMANENT_AFTER_WINDOW
  not_before: Instant
  idempotency_key: IdempotencyKey
}

StorageDeleteReceipt {
  storage_object_id: StorageObjectId
  outcome: QUARANTINED | DELETED | ALREADY_ABSENT
  effect_state: APPLIED | UNKNOWN
  recoverable_until: Instant | null
}
```

```text
ArtifactStorageResult<T> =
  | ArtifactStorageSucceeded<T> { value: T }
  | ArtifactStorageFailed {
      error: ArtifactStorageError
      retryability: RETRYABLE | NON_RETRYABLE | AFTER_RECONCILIATION
      effect_state: NOT_APPLIED | APPLIED | UNKNOWN
    }

ArtifactStorageError = CAPABILITY_UNSUPPORTED | INVALID_HANDLE | HANDLE_EXPIRED |
                       OWNERSHIP_MISMATCH | NAMESPACE_MISMATCH | OFFSET_CONFLICT |
                       SIZE_LIMIT_EXCEEDED | CHECKSUM_MISMATCH | OBJECT_NOT_FOUND |
                       OBJECT_ALREADY_SEALED | OBJECT_QUARANTINED | IO_UNAVAILABLE |
                       TIMEOUT | CANCELLED | INTEGRITY_INDETERMINATE
```

Adapters normalizam falhas para esse vocabulário e não vazam exceções do fornecedor. `UNKNOWN` exige `verify` ou reconciliação pelo mesmo contexto antes de retry destrutivo. `CHECKSUM_MISMATCH` nunca é retryado sobre o objeto selado como se fosse íntegro; o Manager o mantém fora de `AVAILABLE`. `delete` só recebe objeto já marcado pelo Manager e não decide se referências ou legal holds permitem a remoção.

`StorageAbortReceipt` só representa staging que foi descartado ou já estava ausente; `APPLIED` corresponde a `ABORTED`, enquanto outcomes idempotentes usam `NOT_APPLIED`. Objeto já selado retorna `OBJECT_ALREADY_SEALED`, nunca sucesso de abort. `StorageReadReceipt` descreve exclusivamente bytes confirmados no `ByteSink` e mantém tamanho e checksum esperados da versão fixada. Se falha, timeout ou cancelamento ocorrer depois de entrega parcial, `ArtifactStorageFailed.effect_state = APPLIED` informa que o sink pode conter um prefixo; retry reabre leitura na versão e offset confirmados, sem concatenar dados ambíguos. Divergência de checksum retorna `CHECKSUM_MISMATCH` ou `INTEGRITY_INDETERMINATE`, bloqueia receipt de leitura íntegra e aciona `verify`. `provenance_ref` é fornecida pelo Manager e não interpretada pelo adapter; `receipt_context` liga cada outcome ao ownership, Agent, Execution, correlação, finalidade e proveniência autorizados sem expor conteúdo ou localização física.

```text
BeginArtifactWrite {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  category: ArtifactCategory
  logical_name: SanitizedLogicalName
  declared_media_type: MediaType | null
  expected_size_bytes: NonNegativeInteger | null
  expected_checksum: ContentChecksum | null
  classification: DataClassification
  retention_policy_ref: RetentionPolicyRef
  provenance: ArtifactProvenance
  idempotency_key: IdempotencyKey
}

ArtifactWriteSession {
  write_session_id: ArtifactWriteSessionId
  artifact_id: ArtifactId
  accepted_offset_bytes: NonNegativeInteger
  maximum_size_bytes: NonNegativeInteger
  expires_at: Instant
  state: STAGING
}

AppendArtifactChunk {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  write_session_id: ArtifactWriteSessionId
  offset_bytes: NonNegativeInteger
  length_bytes: PositiveInteger
  chunk_checksum: ContentChecksum | null
  idempotency_key: IdempotencyKey
}

FinalizeArtifactWrite {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  write_session_id: ArtifactWriteSessionId
  expected_total_size_bytes: NonNegativeInteger
  expected_checksum: ContentChecksum | null
  idempotency_key: IdempotencyKey
}

AbortArtifactWrite {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  write_session_id: ArtifactWriteSessionId
  reason: AbortReason
  idempotency_key: IdempotencyKey
}
```

Chunks podem ser repetidos somente na mesma posição e com o mesmo digest. Offset conflitante rejeita a sessão. `finalize` verifica comprimento, checksum, quota e metadata antes de promover `STAGING` a `AVAILABLE` por protocolo idempotente; confirmação incerta exige inspeção, não segundo publish cego.

```text
OpenArtifactRead {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  artifact_ref: ArtifactReference
  expected_checksum: ContentChecksum | null
  maximum_bytes: NonNegativeInteger
  purpose: AccessPurpose
}

ArtifactReadSession {
  read_session_id: ArtifactReadSessionId
  artifact_id: ArtifactId
  version: Version
  size_bytes: NonNegativeInteger
  checksum: ContentChecksum
  media_type: MediaType
  maximum_bytes: NonNegativeInteger
  expires_at: Instant
}

ReadArtifactRange {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  read_session_id: ArtifactReadSessionId
  offset_bytes: NonNegativeInteger
  maximum_bytes: NonNegativeInteger
}

InspectArtifact {
  context: ArtifactOperationContext
  artifact_ref: ArtifactReference
  purpose: AccessPurpose
}
```

Leitura fixa uma versão e checksum. Mudança ou revogação durante o stream encerra no próximo limite seguro. URLs temporárias, quando um adapter as suportar, são detalhes de entrega de curta duração, audience-restricted e auditáveis; não substituem `ArtifactReference` nem são persistidas em Event.

```text
DeleteArtifact {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  artifact_ref: ArtifactReference
  expected_version: Version
  reason: DeletionReason
  recovery_window: Duration
  idempotency_key: IdempotencyKey
}

ApplyArtifactRetention {
  operation_id: ArtifactOperationId
  context: ArtifactOperationContext
  namespace: ArtifactNamespace
  retention_policy_ref: RetentionPolicyRef
  cutoff_at: Instant
  maximum_artifacts: PositiveInteger
  idempotency_key: IdempotencyKey
}
```

Remoção primeiro impede novas resoluções e marca `DELETING`; depois verifica referências, holds e versão, move bytes para estado recuperável ou quarentena quando suportado e somente então confirma `DELETED`. Limpeza física pode ser posterior, mas ressurreição pelo mesmo ID é proibida.

## Quotas e accounting

Quotas podem limitar bytes armazenados, tamanho unitário, taxa de ingestão, número de Artifacts, streams simultâneos e retenção por usuário, Workspace, categoria e classificação. Reserva ocorre antes da escrita; consumo real é reconciliado ao finalizar. Staging, multipart incompleto e material em janela de recuperação também contam para evitar bypass.

Concorrência usa reserva versionada e idempotente. Falha libera reserva apenas depois de confirmar que nenhum publish ocorreu. Exceder quota nunca causa limpeza automática de Artifact não elegível nem fallback para namespace alheio.

## Uploads, downloads, screenshots e logs

- `UPLOAD`: entrada do usuário é não confiável, limitada, classificada e submetida a inspeções de política antes do uso.
- `DOWNLOAD`: URL, headers e nome externo são proveniência, não autoridade; o conteúdo fica isolado até integridade e política serem verificadas.
- `SCREENSHOT`: registra Browser/Execution e viewport como metadata sanitizada, sem cookies ou página completa em Event.
- `LOG`: usa segmentos imutáveis e limites; redaction precede persistência, e segredo detectado provoca quarentena ou rejeição.
- `RESULT`: conteúdo volumoso de Tool, Agent ou Execution é referenciado no resultado e não incorporado ao envelope.

Derivação cria novo Artifact com lineage; nunca muta bytes de um Artifact existente. Imutabilidade de conteúdo não impede atualização versionada de metadata que não altere checksum, ownership ou proveniência.

## Integridade e referência versus conteúdo

Disponibilidade só é publicada após leitura confiável de metadata e bytes com tamanho/checksum confirmados. Leituras podem revalidar checksum integral ou por blocos conforme política; qualquer divergência muda o Artifact para `QUARANTINED`, bloqueia novas resoluções e gera alerta. Réplicas ou caches nunca servem versão sem o mesmo digest esperado.

PostgreSQL guarda `ArtifactMetadata`, estado e vínculos; o adapter guarda conteúdo. A referência durável pode sobreviver à indisponibilidade temporária do conteúdo, mas o consumidor recebe erro explícito, nunca bytes de outro objeto. Conteúdo órfão não cria referência; referência órfã não é considerada íntegra.

## Fluxo normal

1. Produtor solicita staging com contexto, categoria, limites, proveniência e idempotência.
2. Política deriva namespace, autoriza e reserva quota.
3. Bytes chegam por stream limitado; tamanho e checksums são calculados.
4. `finalize` valida integridade, classificação e quota real.
5. Conteúdo e metadata entram em protocolo de publicação recuperável.
6. Estado `AVAILABLE`, referência e `ArtifactStored` são confirmados.
7. Leitor reautoriza a referência e recebe stream da versão fixa.

## Fluxo de falha

- input inválido, quota, ownership ou media policy rejeitam antes do publish;
- chunk conflitante invalida ou pausa a sessão sem concatenar bytes ambíguos;
- checksum ou tamanho divergente coloca staging em quarentena e não produz referência;
- falha após armazenar bytes e antes da metadata cria órfão reconciliável, não Artifact visível;
- falha após metadata e antes da confirmação exige inspeção de estado;
- Artifact indisponível, expirado ou corrompido falha fechado;
- cleanup falho mantém `DELETING` e retenta sem reutilizar o namespace físico.

## Fluxo de cancelamento

Antes de `finalize`, cancelamento interrompe novos chunks, fecha streams e agenda staging para limpeza recuperável. Durante finalização, o Manager estabiliza o protocolo e retorna `CANCELLED`, `AVAILABLE` ou `UNKNOWN`; nunca apaga bytes possivelmente publicados sem inspeção. Leitura cancelada fecha sessão sem mudar o Artifact. Exclusão cancelada antes do bloqueio de leitura não tem efeito; depois de `DELETING`, continua a reconciliação para um estado seguro e auditável.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `ArtifactWriteStarted` | staging e reserva de quota foram criados |
| `ArtifactStored` | conteúdo íntegro tornou-se `AVAILABLE` |
| `ArtifactReadFinished` | leitura autorizada terminou com bytes e outcome explícitos |
| `ArtifactQuarantined` | conteúdo deixou de ser resolvível por integridade ou política |
| `ArtifactExpired` | retenção encerrou novas resoluções |
| `ArtifactDeleted` | referência foi invalidada e remoção lógica confirmada |
| `ArtifactCleanupFailed` | limpeza permaneceu incompleta e requer reconciliação |

Payloads incluem `artifact_id`, categoria, versão, tamanho, algoritmo de checksum, ownership, `execution_id`, correlação e razão categórica. Não incluem bytes, digest usado como segredo, path, URL assinada, nome sensível não sanitizado ou credencial.

## Segurança

- toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- namespace é derivado por política e não controlado pelo nome externo;
- referências são opacas, limitadas e reautorizadas a cada resolução;
- conteúdo recebido é não confiável e pode permanecer em quarentena;
- content type detectado e declarado podem divergir sem promover confiança;
- criptografia, isolamento e região seguem classificação e policy;
- malware scanning futuro não substitui autorização nem integridade;
- logs, Events e traces não contêm bytes, URLs temporárias ou segredos;
- exportação e compartilhamento exigem Grant explícito, finalidade e expiração.

## Observabilidade

Métricas incluem bytes escritos/lidos, latência, sessões, throughput, quota reservada/usada, checksum divergente, Artifacts por estado/categoria, órfãos, quarentena, cleanup, expiração e falhas do adapter. Logs e traces correlacionam operação, Artifact, Workspace, Execution, versão e códigos sanitizados. Auditoria responde quem armazenou, leu, exportou, reteve ou removeu qual referência e por quê, sem copiar conteúdo.

## Invariantes

- `ArtifactStorage` é porta substituível e não vaza localização física.
- Artifact publicado é imutável em conteúdo e identificado por versão e checksum.
- somente estado `AVAILABLE` gera referência resolvível.
- referência nunca equivale a conteúdo nem concede acesso por si só.
- namespace e quota preservam ownership por usuário e Workspace.
- bytes volumosos não entram em Event, Context, log ou estado transacional.
- checksum é calculado sobre bytes armazenados por componente confiável.
- staging incompleto, órfão e conteúdo corrompido nunca são servidos.
- limpeza respeita referências, holds, versão e janela recuperável.
- retry e cancelamento não duplicam publish nem removem Artifact alheio.

## Extensibilidade

Adapters de filesystem, object storage ou armazenamento remoto podem implementar a porta. Capabilities opcionais como multipart, range, versionamento físico, tiering e URL temporária são declaradas e não mudam a semântica mínima. Novas categorias declaram proveniência, classificação, quotas, retenção, inspeção e eventos.

## Futuro

Content-addressing, deduplicação segura por tenant, replicação, arquivamento frio, antivírus, DLP, preview derivado e exportação assinada poderão ser adicionados. Nenhuma evolução pode tornar checksum uma credencial, expor backend, servir conteúdo em quarentena ou apagar sem reconciliação.

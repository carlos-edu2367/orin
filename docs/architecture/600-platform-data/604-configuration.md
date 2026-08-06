# RFC 604 — Configuração e segredos

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 201 — Agent](../200-agents/201-agent.md), [RFC 401 — Tool Runtime](../400-tools-resources/401-tool-runtime.md), [RFC 501 — Provider API](../500-providers-models/501-provider-api.md), [RFC 502 — Model Catalog](../500-providers-models/502-model-catalog.md), [RFC 601 — Persistência](601-persistence.md), [RFC 603 — Workspaces](603-workspaces.md)

## Objetivo

Definir fontes, precedência, escopos, validação, versionamento e aplicação da configuração do AgentOS, além da separação obrigatória entre configuração pública, dados sensíveis e segredos. Segredos são sempre usados por referência, possuem rotação auditável e, quando armazenados pela aplicação, são protegidos sob uma `APP_MASTER_KEY` externa ao banco e aos artefatos.

## Fora de escopo

- escolher biblioteca, formato de arquivo, serviço de secrets, KMS, HSM ou algoritmo concreto;
- listar todas as chaves de cada módulo ou definir valores de produção;
- implementar hot reload, painel administrativo, CLI ou endpoint;
- permitir configuração remota não autenticada ou instrução de modelo como fonte;
- expor segredo para Agent, Tool, Provider, frontend, log, Event ou diagnóstico;
- usar configuração para contornar Capability, política, ownership ou autorização.

## Responsabilidades e não responsabilidades

O sistema de configuração DEVE:

- manter catálogo tipado de chaves com escopo, default, validação e sensibilidade;
- resolver fontes em precedência determinística e registrar proveniência;
- separar configuração global, de Workspace e de Agent;
- validar estrutura, semântica, compatibilidade e policy antes de publicar snapshot;
- fornecer snapshot imutável e versionado para cada Execution;
- impedir que escopo mais estreito amplie privilégios além do limite global;
- representar segredos por `SecretReference` opaca e resolvê-los somente na borda autorizada;
- suportar rotação, revogação, rewrap e auditoria sem revelar material secreto;
- falhar fechado quando `APP_MASTER_KEY` obrigatória estiver ausente, inválida ou incompatível.

O sistema NÃO DEVE:

- tratar variável, arquivo ou registro como válido sem schema e policy;
- retornar mapas livres de configuração sensível a consumidores;
- permitir que Workspace ou Agent sobrescreva chave não delegável;
- persistir `APP_MASTER_KEY` no PostgreSQL, Redis, Workspace, Artifact ou código;
- usar segredo como ID, correlação, label, chave Redis ou conteúdo de Event;
- modificar retroativamente snapshot de Execution em andamento;
- reiniciar ou reconfigurar componente com estado incompatível sem fluxo explícito.

## Arquitetura

```text
defaults do catálogo
      │
bootstrap externo / ambiente autorizado
      │
configuração global persistida
      │
configuração do Workspace
      │
configuração do Agent
      ▼
 ConfigurationManager ──> validação / policy / proveniência
      │                           │
      │                           └── SecretResolver por referência
      ▼
ImmutableConfigurationSnapshot por Execution
```

Precedência não significa autoridade ilimitada. Uma fonte posterior só pode substituir chave cujo catálogo permita naquele escopo. Policies e limites globais formam ceiling; Workspace restringe ou escolhe dentro dele; Agent restringe ou especializa dentro de ambos. Requests efêmeros de Execution podem selecionar somente opções explicitamente permitidas e não são nova fonte persistente.

## Fontes e precedência

Da menor para a maior precedência de valor, respeitando delegação:

1. default seguro do catálogo versionado;
2. bootstrap externo autorizado para inicialização;
3. configuração global persistida;
4. configuração do Workspace persistida;
5. configuração do Agent persistida;
6. override efêmero da Execution, apenas para chaves `EXECUTION_OVERRIDABLE` e dentro dos limites resolvidos.

Variáveis de ambiente ou secret files são mecanismos possíveis do bootstrap, não autoridade genérica. Entrada desconhecida, duplicada, vazia indevida ou de escopo ilegal é erro. A origem e a versão escolhidas ficam no snapshot sem registrar valor sensível.

## Dados e classificação

```text
ConfigurationOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

ConfigurationKeyDescriptor<T> {
  key: ConfigurationKey<T>
  value_type: ConfigurationValueType
  allowed_scopes: ConfigurationScope[]
  merge_strategy: REPLACE | INTERSECT | MINIMUM | APPEND_UNIQUE
  default_value: T | null
  sensitivity: PUBLIC | INTERNAL | SENSITIVE | SECRET_REFERENCE
  mutability: BOOT_ONLY | RESTART_REQUIRED | RELOADABLE | EXECUTION_OVERRIDABLE
  validation_rules: ConfigurationValidationRule<T>[]
  policy_ref: ConfigurationPolicyRef
  descriptor_version: Version
}

ConfigurationScope = GLOBAL | WORKSPACE | AGENT | EXECUTION
```

`REPLACE` só é permitido para valor delegável; `INTERSECT` preserva allowlists; `MINIMUM` seleciona o limite mais restritivo; `APPEND_UNIQUE` só adiciona itens autorizados pelo ceiling. Ausência de estratégia explícita rejeita override.

```text
ConfigurationEntry<T> {
  key: ConfigurationKey<T>
  scope: ConfigurationScope
  scope_ref: GlobalRef | WorkspaceId | AgentId
  value: T | SecretReference
  classification: DataClassification
  version: Version
  source: ConfigurationSource
  created_by: ActorRef
  created_execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
  effective_from: Instant
  expires_at: Instant | null
}

ConfigurationSource = CATALOG_DEFAULT | BOOTSTRAP | GLOBAL_STORE |
                      WORKSPACE_STORE | AGENT_STORE | EXECUTION_OVERRIDE

ResolvedConfigurationValue<T> {
  key: ConfigurationKey<T>
  value: T | SecretReference
  effective_scope: ConfigurationScope
  source: ConfigurationSource
  source_version: Version
  descriptor_version: Version
  policy_version: Version
  classification: DataClassification
}
```

Valores `SENSITIVE` podem ser necessários ao Runtime, mas recebem redaction e acesso mínimo. Valores `SECRET_REFERENCE` nunca carregam plaintext. Dados sensíveis de negócio que não sejam configuração permanecem em seus stores próprios; a configuração guarda referência ou policy, não conteúdo.

## Contratos tipados de configuração

```text
interface ConfigurationManager {
  resolve(request: ResolveConfiguration) -> ConfigurationSnapshot
  validate(request: ValidateConfiguration) -> ConfigurationValidationResult
  set(command: SetConfiguration) -> ConfigurationWriteReceipt
  unset(command: UnsetConfiguration) -> ConfigurationWriteReceipt
  inspect(query: InspectConfiguration) -> RedactedConfigurationView
  activate(command: ActivateConfigurationVersion) -> ConfigurationActivationReceipt

  pre: ator e escopos foram autenticados e autorizados
  post: snapshot contém somente chaves registradas, válidas e permitidas
}
```

```text
ResolveConfiguration {
  operation_id: ConfigurationOperationId
  context: ConfigurationOperationContext
  required_keys: ConfigurationKeyRef[]
  execution_overrides: ConfigurationOverride[]
  expected_catalog_version: Version
  purpose: ConfigurationPurpose
}

ConfigurationSnapshot {
  snapshot_id: ConfigurationSnapshotId
  context_scope: ConfigurationContextScope
  values: ResolvedConfigurationValue<ConfigurationValue>[]
  catalog_version: Version
  policy_versions: Version[]
  source_versions: ConfigurationSourceVersion[]
  integrity_ref: IntegrityRef
  created_at: Instant
  valid_until: Instant | null
}

ConfigurationContextScope {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  purpose: Purpose
}
```

Snapshot é imutável, limitado à Execution e purpose e contém apenas as chaves necessárias. Alteração posterior afeta novas resoluções; Execution em andamento só adota versão nova por ponto seguro e comando explícito quando a chave for reloadable. Referência de segredo dentro do snapshot ainda é resolvida no instante do uso.

```text
ValidateConfiguration {
  operation_id: ConfigurationOperationId
  context: ConfigurationOperationContext
  candidate_entries: ConfigurationEntry<ConfigurationValue>[]
  target_scope: ConfigurationScope
  expected_catalog_version: Version
  purpose: ValidationPurpose
}

ConfigurationValidationResult {
  outcome: VALID | INVALID | INCOMPATIBLE | RESTART_REQUIRED
  issues: ConfigurationIssue[]
  normalized_entries: ConfigurationEntry<ConfigurationValue>[]
  catalog_version: Version
  policy_versions: Version[]
}

SetConfiguration {
  operation_id: ConfigurationOperationId
  context: ConfigurationOperationContext
  target_scope: ConfigurationScope
  entry: ConfigurationEntry<ConfigurationValue>
  expected_scope_version: Version
  activation_mode: VALIDATE_ONLY | ACTIVATE_WHEN_SAFE | REQUIRE_RESTART
  change_reason: ChangeReason
  idempotency_key: IdempotencyKey
}

UnsetConfiguration {
  operation_id: ConfigurationOperationId
  context: ConfigurationOperationContext
  target_scope: ConfigurationScope
  key: ConfigurationKeyRef
  expected_scope_version: Version
  change_reason: ChangeReason
  idempotency_key: IdempotencyKey
}

ActivateConfigurationVersion {
  operation_id: ConfigurationOperationId
  context: ConfigurationOperationContext
  target_scope: ConfigurationScope
  scope_ref: GlobalRef | WorkspaceId | AgentId
  candidate_scope_version: Version
  expected_active_version: Version | null
  expected_catalog_version: Version
  activation_mode: ACTIVATE_WHEN_SAFE | REQUIRE_RESTART
  purpose: ActivationPurpose
  idempotency_key: IdempotencyKey
}

ConfigurationActivationReceipt =
  | ConfigurationVersionActivated {
      target_scope: ConfigurationScope
      scope_ref: GlobalRef | WorkspaceId | AgentId
      previous_active_version: Version | null
      active_version: Version
      activation_state: ACTIVE | PENDING_RESTART
      activated_at: Instant
      policy_versions: Version[]
      correlation_id: CorrelationId
    }
  | ConfigurationActivationRejected {
      candidate_scope_version: Version
      reason: NOT_VALIDATED | INCOMPATIBLE | POLICY_DENIED |
              DEPENDENCY_UNAVAILABLE | RESTART_MODE_REQUIRED
    }
  | ConfigurationActivationConflicted {
      expected_active_version: Version | null
      actual_active_version: Version | null
    }
  | ConfigurationActivationIndeterminate {
      operation_id: ConfigurationOperationId
      idempotency_key: IdempotencyKey
    }

pre: actor está autorizado a ativar target_scope e scope_ref para o purpose informado
pre: scope_ref corresponde ao user_id, workspace_id e agent_id aplicáveis do contexto
pre: candidate_scope_version foi validada contra expected_catalog_version e policies vigentes
post: ACTIVE é confirmado com troca versionada e Event na mesma transação conceitual
post: retry com a mesma idempotency_key não ativa novamente nem ignora conflito
```

`unset` revela o próximo valor permitido na cadeia de precedência, mas só depois de validar o snapshot resultante. Um default inseguro ou dependência ausente bloqueia ativação. Mutação e Event usam a fronteira transacional da RFC 601.

## Validação e aplicação

A validação ocorre em quatro camadas:

1. **estrutura:** chave conhecida, tipo, formato, cardinalidade e tamanho;
2. **semântica:** range, unidade, combinação e dependências entre chaves;
3. **escopo e policy:** fonte autorizada, estratégia de merge e ceiling de privilégio;
4. **operacional:** capability disponível, segredo referenciável, compatibilidade e requisito de restart.

Normalização é determinística e não executa código fornecido pelo valor. Configuração inválida nunca vira parcial ativa. Grupos atômicos de chaves são validados e publicados juntos. O componente consumidor verifica `snapshot_id`, integridade, escopo e validade antes do uso.

## Segredos por referência

```text
SecretReference {
  secret_id: SecretId
  secret_version: SecretVersionSelector
  owner_scope: SecretOwnershipScope
  purpose: SecretPurpose
  provider_ref: SecretProviderRef
  expires_at: Instant | null
}

SecretVersionSelector = PINNED(SecretVersion) | CURRENT

SecretResolutionContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  consumer: AuthorizedSecretConsumer
  actor: ActorRef
}

SecretHandle {
  handle_id: EphemeralSecretHandleId
  secret_id: SecretId
  resolved_version: SecretVersion
  consumer: AuthorizedSecretConsumer
  expires_at: Instant
}
```

`SecretReference` não concede leitura. `SecretResolver` reautoriza ownership, consumer, purpose, versão, status e expiração e retorna handle efêmero, não plaintext ao domínio. O adapter injeta o material diretamente na fronteira que autentica com o sistema externo. Handles não são serializáveis, persistíveis ou reutilizáveis por outro consumer.

```text
interface SecretResolver {
  resolve(request: ResolveSecret) -> SecretHandle
  revoke(command: RevokeSecret) -> SecretLifecycleReceipt
  rotate(command: RotateSecret) -> SecretRotationReceipt
  rewrap(command: RewrapSecrets) -> SecretRewrapReceipt

  invariant: nenhum resultado público contém material secreto
  invariant: mutações revalidam ator, ownership, consumer administrativo e purpose
}

ResolveSecret {
  operation_id: SecretOperationId
  context: SecretResolutionContext
  secret_ref: SecretReference
  required_capability: SecretUseCapability
}

RevokeSecret {
  operation_id: SecretOperationId
  context: SecretResolutionContext
  secret_id: SecretId
  expected_active_version: SecretVersion
  effective_at: Instant
  revoke_scope: EXACT_VERSION | ALL_VERSIONS
  reason: SecretRevocationReason
  idempotency_key: IdempotencyKey
}

SecretLifecycleReceipt =
  | SecretRevoked {
      secret_id: SecretId
      revoked_versions: SecretVersion[]
      previous_active_version: SecretVersion
      lifecycle_state: REVOKED | PARTIALLY_REVOKED
      effective_at: Instant
      revoked_at: Instant
      correlation_id: CorrelationId
    }
  | SecretRevocationRejected {
      secret_id: SecretId
      reason: POLICY_DENIED | VERSION_NOT_ACTIVE | DEPENDENCY_BLOCKED |
              INVALID_EFFECTIVE_TIME
    }
  | SecretRevocationConflicted {
      secret_id: SecretId
      expected_active_version: SecretVersion
      actual_active_version: SecretVersion | null
    }
  | SecretRevocationIndeterminate {
      operation_id: SecretOperationId
      idempotency_key: IdempotencyKey
    }

pre: actor e consumer administrativo podem revogar o secret no owner_scope e purpose informados
pre: expected_active_version corresponde à versão durável antes da mutação
post: versão revogada não pode gerar novos handles a partir de effective_at
post: retry com a mesma idempotency_key retorna o mesmo outcome sem duplicar revogação

RotateSecret {
  operation_id: SecretOperationId
  context: SecretResolutionContext
  secret_id: SecretId
  expected_current_version: SecretVersion
  activation_at: Instant
  overlap_duration: Duration
  idempotency_key: IdempotencyKey
}

RewrapSecrets {
  operation_id: SecretOperationId
  context: SecretResolutionContext
  source_key_version: MasterKeyVersion
  target_key_version: MasterKeyVersion
  batch_limit: PositiveInteger
  checkpoint_ref: RewrapCheckpointRef | null
  idempotency_key: IdempotencyKey
}
```

## APP_MASTER_KEY e envelope de proteção

Quando a aplicação armazena secrets, `APP_MASTER_KEY` é root de proteção carregada exclusivamente por bootstrap externo autorizado. Ela:

- não possui default;
- não é gerada silenciosamente em produção;
- não é persistida com ciphertext, banco, Redis, Artifact, Workspace ou backup de dados;
- possui identidade/versão observável sem expor material;
- é usada para unwrap de data-encryption keys ou mecanismo equivalente, não como valor de configuração distribuído;
- nunca é entregue a Agent, Tool, Provider, browser ou frontend;
- deve poder ser rotacionada por processo de rewrap checkpointed e verificável.

Ausência ou falha de unwrap coloca consumidores de segredos em modo indisponível e impede operações dependentes; não tenta chave antiga desconhecida nem inicia com segredo vazio. Backups de dados sem acesso controlado à chave externa não são considerados recuperação completa. Ambientes diferentes usam roots distintas.

## Rotação, revogação e lifecycle de segredos

Rotação cria nova versão, valida seu uso em canal autorizado, ativa em instante explícito e mantém overlap mínimo quando o sistema externo exigir. Novas resoluções usam a versão ativa; handles antigos expiram rapidamente. Revogação bloqueia novas resoluções e sinaliza consumidores, mas não pode desfazer uso externo já realizado.

Rotação de `APP_MASTER_KEY` rewrapa material protegido sem alterar a credencial de negócio. Rotação de secret muda a credencial. Ambas mantêm checkpoint, idempotência, contagens e auditoria. Falha parcial preserva versão conhecida por registro; nenhuma linha tenta "qualquer chave que funcione".

## Separação de dados sensíveis

Categorias são separadas por finalidade e acesso:

| Categoria | Local/forma | Regra |
| --- | --- | --- |
| configuração pública/interna | store de configuração | snapshot tipado e minimizado |
| configuração sensível | store de configuração protegido | redacted em inspeção e telemetria |
| segredo | secret store ou ciphertext envelopado | somente `SecretReference` no domínio |
| conteúdo de negócio sensível | store proprietário/Artifact | não migrar para configuração |
| material temporário resolvido | memória do consumer autorizado | TTL curto, zeroização best effort, nunca persistir |

Prompts, documentos, tokens de sessão, cookies e outputs não viram configuração só por serem reutilizados. Redis pode manter sessão server-side conforme RFC 601, mas não a `APP_MASTER_KEY` nem credencial durável.

## Fluxo normal

1. Catálogo registra descriptor e policy versionados.
2. Alteração autorizada é validada contra tipo, escopo, dependências e ceiling.
3. Entrada e Event são confirmados transacionalmente.
4. Nova Execution resolve somente chaves requeridas em ordem de precedência.
5. Snapshot imutável registra origens e integridade.
6. Ao precisar segredo, consumer apresenta referência e contexto completo.
7. Resolver reautoriza e entrega handle efêmero diretamente à borda.

## Fluxo de falha

- chave desconhecida, tipo inválido ou override ilegal rejeita a alteração;
- conflito de versão não sobrescreve configuração concorrente;
- dependência ausente impede ativação do grupo inteiro;
- snapshot incompatível não é usado parcialmente;
- SecretReference inválida, revogada ou cross-scope falha sem revelar existência;
- `APP_MASTER_KEY` ausente/inválida bloqueia operações dependentes e alerta;
- rotação parcial continua por checkpoint ou reverte ativação de modo explícito;
- falha de reload mantém snapshot anterior conhecido ou exige restart, conforme descriptor.

## Fluxo de cancelamento

Validação cancelada não grava. Mutação cancelada antes do commit não ativa; após commit, a nova versão permanece e o cancelamento impede somente propagação adicional. Resolução cancelada invalida o handle antes do uso quando possível. Rotação ou rewrap deixam de iniciar novos lotes, estabilizam o atual, persistem checkpoint e relatam contagens; nunca descartam chave necessária a registros ainda não migrados.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `ConfigurationChanged` | versão de um escopo foi persistida |
| `ConfigurationActivated` | snapshot ou grupo tornou-se elegível para novas Executions |
| `ConfigurationRejected` | candidato foi recusado por validação ou policy |
| `SecretResolved` | consumer autorizado recebeu handle efêmero |
| `SecretRotated` | nova versão de credencial tornou-se ativa |
| `SecretRevoked` | novas resoluções foram proibidas |
| `SecretRewrapProgressed` | lote de rewrap foi confirmado |
| `MasterKeyUnavailable` | root necessária não pôde ser usada |

Payloads contêm chave categórica ou hash estável quando apropriado, escopo, versões, consumer, outcome, seis campos sensíveis e razão sanitizada. Nunca contêm valor sensível, `SecretReference` completa, ciphertext, handle ou material de chave.

## Segurança

- toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- configuração de escopo menor não amplia allowlist, capability, quota ou acesso de rede;
- snapshots são minimizados, íntegros, imutáveis e limitados à Execution;
- secrets permanecem por referência e são resolvidos no último momento;
- `APP_MASTER_KEY` vive fora dos stores protegidos por ela;
- inspect, diff, erro, Event, trace e métrica aplicam redaction por descriptor;
- acesso a segredo usa consumer allowlist, purpose e TTL;
- rotação e recuperação exigem dual control quando policy determinar;
- entrada de configuração externa é dado não confiável e nunca código executável.

## Observabilidade

Métricas incluem resoluções, validações, rejeições, conflito de versão, snapshot age, reload, restart required, segredo resolvido/negado, handle expirado, rotações, revogações, backlog de rewrap e indisponibilidade de master key. Logs e traces registram IDs, chaves redacted, escopo, versão, provenance e outcome. Auditoria permite saber quem mudou ou usou uma referência, quando e para qual purpose, sem revelar o valor.

## Invariantes

- toda chave ativa existe no catálogo tipado e passou por validação completa.
- precedência é determinística e limitada pelas permissões de escopo.
- Workspace e Agent nunca ampliam o ceiling global.
- cada Execution usa snapshot imutável e versionado.
- segredo nunca aparece como valor de configuração pública.
- `SecretReference` não concede acesso e sempre é reautorizada.
- material secreto não entra em Event, log, trace, métrica, Redis ou Artifact.
- `APP_MASTER_KEY` não é persistida com os dados que protege.
- ausência da master key falha fechada.
- rotação, revogação e rewrap são versionados, idempotentes e auditáveis.
- dados sensíveis permanecem no store proprietário de sua finalidade.

## Extensibilidade

Novas fontes, scopes, secret providers e estratégias de merge podem ser registrados por interfaces versionadas. Cada extensão declara precedência, autoridade, classificação, mutabilidade, validação, rollback e redaction. Feature flags são configuração tipada e não podem substituir autorização.

## Futuro

KMS/HSM, secret stores externos, dynamic credentials, attestação de workload, aprovação multiator e distribuição segura de configuração poderão especializar adapters. Qualquer evolução deve preservar snapshots determinísticos, secrets por referência, separação de dados e `APP_MASTER_KEY` externa.

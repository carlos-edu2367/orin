# RFC 403 — Filesystem

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](401-tool-runtime.md), [RFC 402 — Resource Manager](402-resource-manager.md), [RFC 404 — Terminal](404-terminal.md), [RFC 405 — Browser](405-browser.md)

## Objetivo

Definir o Filesystem Resource e sua porta segura para leitura, listagem, metadados, criação, escrita, movimentação, cópia e remoção. Toda operação ocorre sob a raiz canonicalizada do Workspace autorizado e bloqueia path traversal, caminhos absolutos indevidos, reparse points e escape por symlink, inclusive em condições de corrida.

## Fora de escopo

- escolher sistema operacional, filesystem, biblioteca, protocolo de volume ou armazenamento remoto;
- definir Artifact Storage, versionamento de documentos, sincronização ou backup;
- interpretar conteúdo de arquivo, executar binários ou coordenar múltiplas Tools;
- oferecer acesso geral ao host, diretório pessoal, device, share de rede ou raiz do sistema;
- definir Workspace fisicamente ou permitir que o chamador escolha sua raiz.

## Responsabilidades e não responsabilidades

O Filesystem Resource DEVE:

- resolver a raiz física de um `workspace_id` por porta autorizada e canonicalizá-la antes do uso;
- aceitar somente `WorkspacePath` relativo e normalizado no contrato público;
- resolver cada componente sem seguir escape por symlink, junction, mount ou mecanismo equivalente;
- revalidar containment no momento do efeito e usar operações descriptor-relative ou equivalentes quando disponíveis;
- aplicar permissões, tamanho, quota, tipo de entrada, concorrência e política de overwrite;
- oferecer escrita atômica quando solicitada e explicitar quando o adapter não puder garanti-la;
- devolver conteúdo volumoso por stream limitado ou referência, nunca em Event;
- auditar metadados mínimos da operação e preservar cancelamento.

O Filesystem Resource NÃO DEVE:

- aceitar caminho físico fornecido por Agent, Tool, usuário, página ou Provider;
- expandir `~`, variável de ambiente, drive implícito ou localização derivada de ID;
- permitir `..`, caminho absoluto, UNC, device namespace, alternate data stream ou forma equivalente de escape;
- seguir symlink cuja resolução final ou intermediária saia da raiz;
- acessar banco, Context, Memory ou outro Workspace diretamente;
- tratar validação lexical como prova suficiente de containment físico.

## Arquitetura

```text
Tool atômica
   │ FilesystemOperation + lease
   ▼
Filesystem Port
   ├── WorkspaceRootResolver
   ├── PathPolicy
   ├── CanonicalResolver
   ├── Quota / Permission Policy
   ├── Filesystem Adapter
   └── Audit / Events
          │
          ▼
 raiz canonicalizada do Workspace
```

`WorkspaceRootResolver` é a única fonte da raiz. A porta pública usa caminhos lógicos; a tradução para caminho físico é interna ao adapter e nunca aparece em resultado, log ou Event.

## Dados

```text
FilesystemOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

WorkspacePath {
  segments: SafePathSegment[]
}

SafePathSegment {
  value: Text
  invariant: não vazio, não é '.' nem '..' e não contém separador ou namespace reservado
}
```

`WorkspacePath` é sempre relativo à raiz. Uma string de transporte só se torna `WorkspacePath` depois de parsing e validação específicos da plataforma; normalização Unicode e comparação de case seguem uma política fixa do Workspace.

```text
FilesystemEntry {
  path: WorkspacePath
  kind: FILE | DIRECTORY | SYMLINK
  size_bytes: NonNegativeInteger | null
  version: FileVersion
  modified_at: Instant
  classification: DataClassification
}

FilesystemOperation {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  kind: READ | LIST | STAT | CREATE_DIRECTORY | WRITE | MOVE | COPY | REMOVE
  source: WorkspacePath
  destination: WorkspacePath | null
  limits: FilesystemLimits
  idempotency_key: IdempotencyKey | null
}

FilesystemLimits {
  maximum_bytes: NonNegativeInteger
  maximum_entries: NonNegativeInteger
  maximum_depth: NonNegativeInteger
  timeout: Duration
}
```

## Contratos tipados

```text
interface WorkspaceRootResolver {
  resolve(context: FilesystemOperationContext) -> CanonicalWorkspaceRoot

  pre: user e Workspace estão autorizados para a finalidade
  post: raiz é absoluta, canonicalizada, existente e pertencente ao Workspace
}

CanonicalWorkspaceRoot {
  root_ref: OpaqueRootRef
  workspace_id: WorkspaceId
  identity: FilesystemObjectIdentity
  policy_version: Version
}
```

```text
interface FilesystemPort {
  stat(request: StatRequest) -> FilesystemEntry
  list(request: ListRequest) -> FilesystemEntryPage
  read(request: ReadRequest, sink: ByteSink) -> ReadResult
  create_directory(request: CreateDirectoryRequest) -> FilesystemEntry
  write(request: WriteRequest, source: ByteSource) -> WriteResult
  move(request: MoveRequest) -> MoveResult
  copy(request: CopyRequest) -> CopyResult
  remove(request: RemoveRequest) -> RemoveResult

  pre: request contém contexto completo e lease válido para a operação
  post: todo caminho afetado permanece contido na mesma raiz autorizada
}
```

```text
StatRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  symlink_policy: REJECT | REQUIRE_CONTAINED_TARGET
  expected_version: FileVersion | null
}

ListRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  symlink_policy: REJECT | REQUIRE_CONTAINED_TARGET
  recursive: Boolean
  maximum_depth: NonNegativeInteger
  maximum_entries: NonNegativeInteger
  cursor: DirectoryCursor | null
}

ReadRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  symlink_policy: REJECT | REQUIRE_CONTAINED_TARGET
  expected_version: FileVersion | null
  offset_bytes: NonNegativeInteger
  maximum_bytes: NonNegativeInteger
  timeout: Duration
}

CreateDirectoryRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  create_parents: Boolean
  expected_parent_version: FileVersion | null
  idempotency_key: IdempotencyKey
}

WriteRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  mode: CREATE_NEW | REPLACE | APPEND
  atomicity: REQUIRE_ATOMIC | BEST_EFFORT
  expected_version: FileVersion | null
  maximum_bytes: NonNegativeInteger
  idempotency_key: IdempotencyKey
}

MoveRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  source: WorkspacePath
  destination: WorkspacePath
  expected_source_version: FileVersion
  expected_destination_version: FileVersion | null
  overwrite: NEVER | IF_VERSION_MATCHES
  idempotency_key: IdempotencyKey
}

CopyRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  source: WorkspacePath
  destination: WorkspacePath
  expected_source_version: FileVersion
  expected_destination_version: FileVersion | null
  overwrite: NEVER | IF_VERSION_MATCHES
  maximum_bytes: NonNegativeInteger
  idempotency_key: IdempotencyKey
}

RemoveRequest {
  operation_id: FilesystemOperationId
  context: FilesystemOperationContext
  lease_id: ResourceLeaseId
  path: WorkspacePath
  expected_kind: FILE | DIRECTORY | SYMLINK
  expected_version: FileVersion | null
  recursive: Boolean
  maximum_entries: NonNegativeInteger
  idempotency_key: IdempotencyKey
}
```

Todos os requests públicos incluem `FilesystemOperationContext`; assim, `stat`, `list`, `read`, `create_directory`, `write`, `move`, `copy` e `remove` validam explicitamente usuário, Workspace, Agent, Execution, correlação e finalidade. Cursor, versão ou `operation_id` nunca substituem esse escopo. `MoveRequest` e `CopyRequest` exigem origem e destino na mesma raiz resolvida pelo contexto.

Operação destrutiva exige alvo exato, tipo esperado e controle de versão quando disponível. Remoção recursiva é negada por padrão, nunca pode apontar para `WorkspacePath` vazio e deve enumerar e validar cada entrada dentro da raiz antes e durante o efeito.

```text
interface CanonicalPathResolver {
  resolve_for_read(root: CanonicalWorkspaceRoot, path: WorkspacePath) -> AuthorizedPathHandle
  resolve_parent_for_create(root: CanonicalWorkspaceRoot, path: WorkspacePath) -> AuthorizedParentHandle
  revalidate(handle: AuthorizedPathHandle, expected_identity: FilesystemObjectIdentity) -> RevalidationResult

  invariant: nenhum handle representa objeto fora de root
  invariant: resolução não segue componente que escape por symlink ou equivalente
}
```

## Canonicalização e contenção

A validação normativa ocorre nesta ordem:

1. resolver `workspace_id` para uma raiz confiável sem usar entrada do chamador;
2. canonicalizar a raiz e fixar sua identidade física;
3. parsear caminho lógico, rejeitando forma absoluta, segmento vazio indevido, `.`/`..`, namespace reservado e encoding ambíguo;
4. caminhar a partir do handle da raiz, componente a componente, sem lookup relativo ao diretório de processo;
5. inspecionar symlink, junction, mount e reparse point antes de atravessar;
6. permitir symlink somente quando a política autorizar e o alvo canonicalizado permanecer dentro da mesma raiz; caso incerto, negar;
7. fixar identidade do alvo ou do parent para criação;
8. imediatamente antes de ler ou alterar, revalidar raiz, parent e alvo para impedir troca em corrida;
9. executar por handle relativo seguro ou mecanismo com garantia equivalente;
10. confirmar que resultado e qualquer temporário continuam contidos antes de publicar sucesso.

Comparar prefixo textual não é suficiente. A comparação usa identidade e semântica da plataforma, com separador, case, Unicode, hard links e boundaries de mount tratados pela política do adapter. Hard link para objeto proibido é rejeitado quando o adapter não puder provar ownership e containment.

## Concorrência e efeitos

`expected_version` protege contra overwrite ou remoção concorrente. Escrita `REQUIRE_ATOMIC` usa temporário privado dentro do mesmo diretório autorizado, flush conforme política e rename atômico; falha em garantir isso rejeita antes de substituir. `MOVE` entre raízes é proibido; cópia seguida de remoção é uma Capability composta, não uma operação atômica escondida. Resultados declaram `effect_state` como `NOT_APPLIED`, `APPLIED` ou `UNKNOWN` quando a confirmação for incerta.

## Fluxo normal

1. A Tool envia operação com contexto, lease, limites e caminho lógico.
2. A porta valida ownership, finalidade e permissão específica.
3. A raiz é resolvida e canonicalizada; o caminho é parseado e contido fisicamente.
4. Quota, tamanho, tipo, versão e política de symlink são verificados.
5. O adapter executa por handle seguro, observa cancelamento e contabiliza bytes/entradas.
6. Pós-condições e containment são revalidados.
7. Resultado tipado e Event mínimo são confirmados; handles e temporários são liberados.

## Fluxo de falha

Path traversal, symlink escape, raiz divergente, versão conflitante ou tipo inesperado falham antes do efeito e são auditados como rejeição de política. Falha de leitura não retorna conteúdo de outro caminho. Escrita atômica falha sem substituir o destino quando a confirmação anterior ao rename não ocorreu. Se o estado após efeito for incerto, a operação não é repetida cegamente: reconcilia identidade, versão e tamanho sob a mesma autorização. Temporários são limpos sem atravessar a raiz.

## Fluxo de cancelamento

Antes do efeito, cancelamento encerra sem mudança. Durante stream, interrompe novas leituras ou escritas no próximo limite seguro, fecha handles e remove temporário não publicado. Rename, remove ou outro efeito já confirmado permanece auditável e não é desfeito por alegação de cancelamento. Operação recursiva deixa de iniciar novas entradas, estabiliza a atual e relata resultados parciais por referência quando permitido, sem afirmar sucesso integral.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `FilesystemReadFinished` | leitura ou listagem terminou com outcome explícito |
| `FilesystemEntryCreated` | arquivo ou diretório passou a existir no caminho lógico |
| `FilesystemEntryChanged` | escrita, move ou copy confirmou nova versão |
| `FilesystemEntryRemoved` | entrada deixou de existir no caminho lógico |
| `FilesystemOperationRejected` | operação foi negada por path, ownership ou política |

Payloads usam `operation_id`, operação, `WorkspacePath` sanitizado ou hash quando sensível, versão, contagens, `effect_state` e razão. Conteúdo, caminho físico e bytes nunca entram no Event.

## Segurança

- operações exigem `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose` explícitos;
- a raiz vem de configuração autorizada, não de argumento da Tool;
- path traversal e symlink escape são bloqueados lexical e fisicamente;
- permissões distinguem listar, ler, criar, substituir, mover, copiar e remover;
- arquivos e diretórios aplicam quota, tamanho, profundidade, contagem e classificação;
- nomes e conteúdo externos são dados não confiáveis e não alteram política;
- erros não revelam existência de caminho fora do escopo;
- segredos não são gravados sem mecanismo e classificação explícitos.

## Observabilidade

Métricas incluem operações por tipo/outcome, bytes, entradas, latência, conflitos de versão, violações de traversal, symlink rejeitado, quota, cancelamentos, temporários limpos e efeitos incertos. Logs e traces usam IDs, caminho lógico redacted ou hash, versão e razão categórica; nunca caminho físico ou conteúdo. Auditoria destrutiva registra ator, finalidade, alvo lógico, expectativa e outcome.

## Invariantes

- toda operação é relativa à raiz canonicalizada de exatamente um Workspace;
- caminho fornecido pelo chamador nunca seleciona raiz física;
- `..`, forma absoluta, namespace especial e encoding ambíguo são rejeitados;
- nenhum componente ou symlink pode resolver fora da raiz;
- containment é revalidado no instante do efeito, não apenas antes;
- operação destrutiva nunca usa alvo vazio, amplo ou de tipo desconhecido;
- move atômico não cruza raízes;
- handle e caminho físico não atravessam a porta pública;
- falha, cancelamento e efeito incerto permanecem distinguíveis e auditáveis.

## Extensibilidade

Adapters para plataformas ou volumes diferentes implementam a mesma semântica de raiz, containment, identidade, concorrência e cancelamento. Novas operações só entram se forem atomicamente definíveis; fluxos compostos permanecem em Capabilities. Política pode proibir symlinks integralmente sem enfraquecer o contrato.

## Futuro

Snapshots, quotas por classificação, antivírus, content-addressing e Workspaces remotos poderão especializar a porta. Nenhuma evolução pode aceitar validação somente textual, permitir escape por link ou expor localização física como autoridade.

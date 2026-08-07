# RFC 603 — Workspaces: especificação de desenho

**Data:** 2026-08-07  
**Status:** aprovado para implementação inline no adapter de referência  
**Escopo:** contratos públicos, registro em memória, root adapter de referência, lifecycle, leases, fencing, quotas, cleanup, reconciliação e composição opcional com RFC 601/103.

## Objetivo

Entregar um `WorkspaceManager` completo no escopo de referência do AgentOS. A identidade lógica, ownership, estado e versões ficam atrás de uma porta independente de banco e filesystem. O adapter de root é substituível e nunca recebe uma root escolhida pelo chamador. O resultado público contém somente referências opacas, IDs, estados, versões, quotas, usage e razões categóricas.

## Decisão de arquitetura

O desenho usa cinco fronteiras pequenas:

1. `WorkspaceManagerService` aplica autorização, lifecycle, idempotência, quota, lease, fencing e workflow de cleanup.
2. `WorkspaceRegistry` é a autoridade durável de referência. `InMemoryWorkspaceRegistry` usa mutação copy-on-write sob lock e tombstones para impedir reuso de IDs. `TransactionalWorkspaceRegistry` adapta mudanças e eventos para `TransactionalPersistence` da RFC 601.
3. `WorkspaceRootAdapter` é a porta para provisionar, resolver, inspecionar, quarentenar e limpar uma root. `InMemoryWorkspaceRootAdapter` mantém roots isoladas e handles efêmeros; nenhuma localização física atravessa a porta.
4. `WorkspaceQuotaController` mantém reservas antes do efeito, contabilização após efeito confirmado e estados `CURRENT`, `STALE`, `IN_PROGRESS` e `DIVERGENT`.
5. `WorkspaceEventSink` registra fatos depois da confirmação; na composição transacional, os envelopes entram em `OutboxChange` na mesma transação RFC 601.

O serviço coordena as fronteiras, mas não executa Tools, Agents, Filesystem ou Artifact Storage. Filesystem e Artifacts continuam donos de suas operações; Workspace fornece somente ownership, root binding e política.

## Modelos públicos

Os modelos são dataclasses congeladas, limitadas e sem `repr` sensível:

- `WorkspaceOperationContext` e `CreateWorkspaceContext` com usuário, Workspace quando aplicável, Agent, Execution, correlação, finalidade e ator;
- `WorkspaceRecord`, `WorkspaceSnapshot`, `WorkspaceRootDescriptor`, `WorkspaceQuota` e `WorkspaceUsage`;
- `WorkspaceLease`, `WorkspaceLock`, `QuotaReservation`, `WorkspaceDeletionReceipt` e `WorkspaceReconciliationReceipt`;
- estados exatos da RFC 603 e resultados `NOT_APPLIED`, `APPLIED`, `UNKNOWN`;
- requests para criação, ativação, inspeção, lease, renew/release, transição, reserva/contabilização, delete e reconcile.

`OpaqueWorkspaceRootRef`, `OpaqueRootHandleRef`, `FilesystemObjectIdentity` e `FencingToken` têm representação opaca. Root refs e handles não são caminhos, URLs, drives, UNC, handles nativos ou segredos. `OpaqueRootHandleRef` é deliberadamente não serializável e é aceito somente pelo adapter que o criou, no binding de lease correspondente.

## Lifecycle e invariantes

Criação grava `workspace_id`, `user_id`, quota, configuração, classificação, idempotência e `PROVISIONING` antes de chamar o root adapter. A root provisionada permanece em staging/quarentena até `activate`, que compara versão e identity. Retry com a mesma chave retorna o mesmo ID ou conflito; uma chave diferente nunca recupera outro ownership. Tombstones impedem reuso depois de `DELETED`.

Transições válidas:

| Origem | Destino | Regra |
|---|---|---|
| `PROVISIONING` | `ACTIVE` | root pronta, identity confirmada e versão esperada |
| `PROVISIONING` | `FAILED`/`RECOVERY_REQUIRED` | provisionamento ou root inconclusos |
| `ACTIVE` | `SUSPENDING` | barreira administrativa aceita |
| `SUSPENDING` | `SUSPENDED` | novos leases bloqueados e ativos drenados/revogados |
| `SUSPENDING` | `ACTIVE` | cancelamento antes de revogação efetiva |
| `SUSPENDED` | `ARCHIVING` | política permite arquivamento |
| `ARCHIVING` | `ARCHIVED` | somente leitura confirmada |
| qualquer estado não terminal elegível | `DELETING` | delete confirma versão, root identity e fence |
| `DELETING` | `DELETED` | cleanup e tombstone da root esperada confirmados |
| qualquer estado operacional | `RECOVERY_REQUIRED` | divergência impede ação segura |

`DELETED` é terminal. `DELETING` nunca volta silenciosamente a `ACTIVE`; `ARCHIVED`, `SUSPENDED`, `RECOVERY_REQUIRED` e `FAILED` não aceitam leases normais. Toda mudança incrementa versão, usa expected version e é idempotente.

## Root, canonicalização e containment

O chamador fornece somente `workspace_id` e paths lógicos a subsistemas especializados; nunca root física. Antes de cada lease o Manager resolve a `root_ref`, canonicaliza e compara a identity registrada. O adapter rejeita root vazia/ampla, symlink, junction, mount, reparse point e hard-link ambiguity, e falha fechado quando não consegue provar containment. O Manager revalida state/version/root identity depois da resolução e antes de publicar o lease, fechando a janela de troca.

Cleanup recebe somente `root_ref` e identity esperadas do registro, usa handles relativos e limite de entradas, não segue links e não amplia o alvo quando a root diverge. Uma divergência deixa `RECOVERY_REQUIRED` ou mantém `DELETING`; nunca alegra sucesso.

## Leases, locks e concorrência

Lease exige contexto completo, permissões bounded, budget, expected version, expected root identity, duração positiva e idempotency key. O limite de leases é reservado atomicamente no registro de usage. Renew exige o mesmo binding, expiration esperada, versão, identity e fence; release é idempotente e não reativa o lease.

Lock administrativo é um lease efêmero com `FencingToken` monotônico. O token é necessário em cada mutação administrativa; perder o lock ou receber token antigo rejeita o efeito, mas somente a comparação de versão durável decide a transição. Suspensão/delete bloqueiam novas concessões, revogam ou drenam ativos até deadline e não restauram autorização de forma implícita.

## Quotas e usage

`maximum_bytes`, `maximum_entries`, `maximum_file_bytes`, `maximum_depth`, `maximum_active_leases` e `reserved_bytes` são bounded e validados. `reserve_usage` roda antes do efeito e retorna uma reserva opaca; `record_usage` só aceita efeito confirmado e o mesmo lease/reservation binding. Usage stale ou divergent impede nova reserva; reconciliação usa contagem bounded do root adapter e marca divergência em caso de troca/limite excedido. Artifacts continuam usando a quota própria da RFC 602; a integração expõe somente um hook de agregação, sem duplicar bytes.

## Delete e reconciliação

Delete executa: alvo exato → `DELETING` + fence → bloqueio/drain → manifesto categórico bounded → quarantine quando suportada → cleanup relativo com checkpoints → reconciliação de referências/metadata pelos adapters próprios → tombstone da root esperada → `DELETED`. Falha antes, durante ou depois do efeito retorna receipt explícito; retry usa a mesma operação/fence e não pode apagar outro alvo.

`reconcile` aceita `ROOT`, `USAGE`, `LEASES`, `CLEANUP` ou `ALL`, limite positivo e chave idempotente. Ele é seguro para root ausente, root trocada, órfão, metadata divergente e operação interrompida. Evidência contém apenas contagens, estados, versões, identity opaca e códigos.

## Persistência e eventos

O domínio importa somente `TransactionalPersistence`, `EventEnvelope` e tipos públicos de RFC 601/103. `TransactionalWorkspaceRegistry` serializa somente metadata bounded, root refs/identities opacas, leases administrativos e tombstones; não grava handle vivo nem root física. A transação registra `RecordChange`, auditoria mínima e `OutboxChange` juntos. O adapter in-memory registra eventos pós-fato por sink local.

Eventos emitidos somente quando o fato foi confirmado: `WorkspaceProvisioningStarted`, `WorkspaceActivated`, `WorkspaceSuspended`, `WorkspaceArchived`, `WorkspaceDeletionStarted`, `WorkspaceDeleted`, `WorkspaceRecoveryRequired` e `WorkspaceQuotaExceeded`. Payloads contêm IDs, ownership, versão, policy, execution/correlação, finalidade e razão; não contêm path, root física, handle, nomes do manifesto, conteúdo ou segredo.

## Testes e critérios de aceitação

Os testes cobrem contracts/boundedness, ownership e bootstrap, todas as transições, idempotência/conflict/UNKNOWN, root identity e corrida, links/traversal/raiz fornecida, leases/renew/revoke/fencing, quotas concorrentes/stale/divergent, cleanup parcial e retry, reconcile por escopo, round-trip RFC 601, eventos pós-fato, ausência de vazamento em `repr`/erro/evento e scans de dependências.

O gate só será declarado fechado após `pytest`, `compileall`, scan de imports proibidos, `git diff --check`, teste PostgreSQL opcional condicionado a `AGENTOS_TEST_POSTGRES_DSN`, revisão independente e matriz/closeout com evidência fresca.

## Limitações tecnológicas legítimas

Este gate entrega a semântica completa no adapter de referência. Não implementa serviço de filesystem de produção, volume/container, storage remoto, Redis, transação distribuída, API HTTP, UI, colaboração, backup, VCS, sync, snapshots ou exatamente-uma-vez. Essas limitações não reduzem os contratos de Workspace nem permitem root fornecida pelo chamador.

# RFC 404 — Terminal

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](401-tool-runtime.md), [RFC 402 — Resource Manager](402-resource-manager.md), [RFC 403 — Filesystem](403-filesystem.md)

## Objetivo

Definir o Terminal Resource como sessão persistente, controlada, cancelável, isolada e observável para execução de comandos dentro de um Workspace. A sessão preserva identidade, diretório corrente, processo, status, owner, Workspace e buffer limitado entre comandos autorizados sem se tornar uma Execution, um checkpoint ou fonte de autorização.

## Fora de escopo

- escolher shell, pseudo-terminal, container, sistema operacional ou biblioteca concreta;
- interpretar intenção, compor comandos, executar Agent ou coordenar múltiplas Tools;
- fornecer acesso administrativo irrestrito ao host;
- persistir processo vivo em checkpoint ou garantir sobrevivência a falha de host;
- definir interface visual, protocolo remoto, SSH ou política de pacote;
- substituir Artifact Storage para saída durável.

## Responsabilidades e não responsabilidades

O Terminal Resource DEVE:

- criar e manter sessões identificáveis e persistentes entre invocações autorizadas;
- fixar owner, Workspace, raiz, cwd inicial, ambiente permitido, limites e política na criação;
- executar no máximo um comando foreground por sessão salvo extensão futura explícita;
- manter `pid`, árvore de processos, status e comando corrente sob controle do adapter;
- capturar stdout/stderr em buffer sequenciado, limitado e redigido conforme política;
- permitir inspeção, streaming, entrada interativa autorizada, cancelamento de comando e fechamento;
- isolar filesystem, processo, ambiente, rede e recursos conforme a política efetiva;
- limpar processos filhos e temporários ao fechar, expirar ou perder o lease.

O Terminal Resource NÃO DEVE:

- aceitar PID, cwd físico ou variável de ambiente como autoridade fornecida pelo chamador;
- executar comando fora de `Execution` ou sem lease e finalidade explícita;
- reutilizar sessão entre usuários, Workspaces ou Agents por conveniência;
- permitir que cwd escape da raiz canonicalizada do Workspace;
- expor secrets no comando, buffer, Event, log ou snapshot;
- tratar envio de sinal como prova de término;
- acessar banco, Memory, Context ou Tool Registry diretamente.

## Arquitetura

```text
Tool atômica
   │ TerminalRequest + lease
   ▼
Terminal Port
   ├── Session Registry
   ├── Authorization / Policy
   ├── Workspace Root + cwd resolver
   ├── Process Supervisor
   ├── Bounded Output Buffer
   └── Terminal Adapter / sandbox
                │
                ▼
        processo e filhos isolados
```

Persistência significa que a sessão pode receber mais de um comando durante sua vida autorizada. O estado de processo permanece no Resource adapter; estado durável contém somente snapshot e referências suficientes para auditoria, nunca handle ou pseudo-terminal vivo.

## Dados

```text
TerminalOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

TerminalSession {
  id: TerminalSessionId
  cwd: WorkspacePath
  pid: ProcessId | null
  status: TerminalSessionStatus
  owner: UserId
  workspace: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  buffer: TerminalBuffer
  lease_id: ResourceLeaseId
  current_command_id: TerminalCommandId | null
  policy_version: Version
  created_at: Instant
  last_activity_at: Instant
  expires_at: Instant
}

TerminalSessionStatus = CREATING | READY | RUNNING | EXITED | FAILED | CANCELLED | CLOSED
```

Os campos normativos `id`, `cwd`, `pid`, `status`, `owner`, `workspace` e `buffer` estão sempre presentes no snapshot; `pid` pode ser nulo antes da criação ou após término. `owner` e `workspace` são aliases semânticos para `user_id` e `workspace_id` da sessão, não chaves alternativas.

```text
TerminalBuffer {
  first_sequence: PositiveInteger | null
  last_sequence: PositiveInteger | null
  retained_bytes: NonNegativeInteger
  dropped_bytes: NonNegativeInteger
  maximum_bytes: NonNegativeInteger
  truncation: NONE | HEAD_DROPPED | REDACTED
}

TerminalOutputChunk {
  session_id: TerminalSessionId
  command_id: TerminalCommandId
  sequence: PositiveInteger
  channel: STDOUT | STDERR | CONTROL
  bytes: ByteChunk
  occurred_at: Instant
}

TerminalCommand {
  command_id: TerminalCommandId
  session_id: TerminalSessionId
  context: TerminalOperationContext
  command: SensitiveText
  requested_cwd: WorkspacePath | null
  environment_refs: SecretReference[]
  timeout: Duration
  maximum_output_bytes: NonNegativeInteger
  idempotency_key: IdempotencyKey | null
}
```

Texto do comando é sensível, não é usado como label e pode ser armazenado somente sob política explícita de auditoria redigida. Reexecutar comando é presumido não idempotente; a mesma chave evita submissão duplicada, mas não autoriza retry após efeito incerto.

## Contratos tipados

```text
interface TerminalPort {
  create(request: CreateTerminalSession) -> TerminalSessionSnapshot
  execute(request: ExecuteTerminalCommand) -> TerminalCommandAccepted
  write_input(request: WriteTerminalInput) -> InputWriteResult
  stream(request: StreamTerminalOutput, sink: TerminalOutputSink) -> StreamResult
  inspect(query: AuthorizedTerminalQuery) -> TerminalSessionSnapshot
  request_cancel(request: CancelTerminalCommand) -> CancelTerminalResult
  close(request: CloseTerminalSession) -> CloseTerminalResult

  pre: toda operação possui contexto completo e lease válido
  post: operação não cruza owner, Workspace, Agent ou finalidade autorizados
}
```

```text
CreateTerminalSession {
  request_id: TerminalRequestId
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  initial_cwd: WorkspacePath
  shell_profile: ShellProfileRef
  environment_refs: SecretReference[]
  limits: TerminalLimits
  idempotency_key: IdempotencyKey
}

TerminalLimits {
  session_ttl: Duration
  command_timeout: Duration
  maximum_processes: PositiveInteger
  maximum_memory_bytes: NonNegativeInteger
  maximum_cpu_time: Duration
  maximum_output_bytes: NonNegativeInteger
  network_policy_ref: NetworkPolicyRef
}

WriteTerminalInput {
  request_id: TerminalRequestId
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  session_id: TerminalSessionId
  command_id: TerminalCommandId
  input: SensitiveByteChunk
  end_of_input: Boolean
  input_sequence: PositiveInteger
  idempotency_key: IdempotencyKey
}

StreamTerminalOutput {
  request_id: TerminalRequestId
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  session_id: TerminalSessionId
  command_id: TerminalCommandId | null
  after_sequence: NonNegativeInteger
  maximum_chunks: PositiveInteger
  maximum_bytes: NonNegativeInteger
  timeout: Duration
}

AuthorizedTerminalQuery {
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  session_id: TerminalSessionId
  include_buffer_metadata: Boolean
  include_current_command: Boolean
}

CancelTerminalCommand {
  request_id: TerminalRequestId
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  session_id: TerminalSessionId
  command_id: TerminalCommandId
  reason: CancellationReason
  cancellation_deadline: Instant
  idempotency_key: IdempotencyKey
}

CloseTerminalSession {
  request_id: TerminalRequestId
  context: TerminalOperationContext
  lease_id: ResourceLeaseId
  session_id: TerminalSessionId
  expected_status: TerminalSessionStatus
  reason: CloseReason
  cleanup_deadline: Instant
  idempotency_key: IdempotencyKey
}
```

```text
ExecuteTerminalCommand {
  command: TerminalCommand
  expected_session_status: READY
}

TerminalCommandAccepted {
  command_id: TerminalCommandId
  session_id: TerminalSessionId
  accepted_at: Instant
}

interface TerminalOutputSink {
  emit(chunk: TerminalOutputChunk) -> StreamDisposition
  close(outcome: TerminalCommandOutcome) -> Unit
}

TerminalCommandOutcome =
  | CommandExited { exit_code: Integer, final_cwd: WorkspacePath, output_ref: ResultReference | null, usage: ResourceUsage }
  | CommandFailed { error: TerminalError, effect_state: EffectState, output_ref: ResultReference | null }
  | CommandCancelled { termination_stage: TerminationStage, output_ref: ResultReference | null }
```

```text
interface ProcessSupervisor {
  signal(command_id: TerminalCommandId, signal: COOPERATIVE_CANCEL | INTERRUPT | TERMINATE | KILL) -> SignalReceipt
  await_exit(command_id: TerminalCommandId, deadline: Instant) -> ProcessExitState
  terminate_tree(session_id: TerminalSessionId, deadline: Instant) -> TerminationResult
  reconcile(session_id: TerminalSessionId, context: TerminalOperationContext) -> ProcessTreeSnapshot
}
```

`write_input`, `stream`, `inspect`, `request_cancel` e `close` recebem estruturas completas com `TerminalOperationContext`. Cada operação revalida `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose`, lease, sessão e, quando aplicável, comando; sequência, PID ou posse do `session_id` não concedem autorização.

## Sessão, cwd e buffer

`cwd` é um `WorkspacePath` validado pelas regras da RFC 403. Alteração de diretório feita pelo shell só é aceita no snapshot após resolução canonicalizada; tentativa de escape falha ou termina a sessão conforme política. O processo recebe uma visão de filesystem restrita, ambiente allowlisted e rede negada ou limitada por política.

O buffer é circular ou segmentado, tem sequência monotônica por sessão e limite fixo. Backpressure pode suspender leitura do consumidor, mas nunca permite crescimento ilimitado; excesso é enviado a Artifact autorizado, truncado com marcador explícito ou causa cancelamento conforme política. Output não é comando nem instrução confiável.

## Fluxo normal

1. A Tool solicita sessão com contexto, lease, perfil e limites.
2. A porta valida ownership, finalidade, Workspace root, quotas, secrets por referência e política de rede.
3. O supervisor cria sandbox/processo, fixa `pid`, cwd e ambiente e confirma `READY`.
4. `execute` aceita um comando quando não há foreground ativo e muda para `RUNNING`.
5. Output é sequenciado, limitado e transmitido; entrada interativa exige permissão própria.
6. Após término, exit code, cwd canonicalizado, uso e referência de output são confirmados; a sessão volta a `READY` ou `EXITED` conforme o shell.
7. `close` encerra árvore de processos, limpa temporários, libera lease e confirma `CLOSED`.

## Fluxo de falha

Falha de criação limpa qualquer processo parcial antes de reportar `FAILED`. Comando rejeitado por status, cwd, política ou quota não é enviado ao shell. Perda de conexão com processo produz estado incerto e reconciliação por identidade do processo e sandbox; nunca reexecuta comando automaticamente. Exit code diferente de zero é um outcome explícito do comando, não falha de infraestrutura. Overflow, decoding inválido e perda de chunks ficam marcados no buffer e resultado.

## Fluxo de cancelamento

Cancelamento segue estágios com prazos explícitos: sinal cooperativo, interrupt, terminate da árvore e kill isolado como último recurso. Cada estágio confirma se o processo saiu antes de avançar. Nenhum sinal pode usar PID não reconciliado ou atingir árvore alheia. Após cancelamento, novos inputs e comandos são rejeitados até a sessão estar `READY`, `CANCELLED` ou `CLOSED`. Output e efeitos anteriores permanecem auditáveis; chegada tardia não converte outcome em sucesso.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `TerminalSessionCreated` | sessão isolada ficou pronta |
| `TerminalCommandStarted` | comando autorizado foi aceito pelo processo |
| `TerminalOutputProgressed` | marco de output sequenciado foi disponibilizado |
| `TerminalCommandFinished` | comando terminou com exit, falha ou cancelamento explícito |
| `TerminalSessionClosed` | processo e filhos foram encerrados e limpeza confirmada |
| `TerminalSessionLost` | controle do processo não pôde ser reconciliado |

Eventos incluem IDs, status, exit code quando aplicável, contagens, uso e razão sanitizada. Não incluem texto do comando, output, ambiente, secret, PID do host ou cwd físico.

## Segurança

- toda operação declara `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- sessões são isoladas por owner, Workspace, Agent e lease; reuso entre Executions exige permissão e finalidade explícitas, além de novo lease;
- cwd permanece sob a raiz canonicalizada do Workspace;
- ambiente usa allowlist e secrets por referências efêmeras, nunca interpoladas em logs;
- rede, processos, CPU, memória, tempo, output e número de filhos possuem limites;
- comandos e output são dados sensíveis e não confiáveis;
- PID e handle de terminal não são autoridades públicas;
- fechamento e cancelamento alcançam somente a árvore pertencente à sessão validada.

## Observabilidade

Métricas incluem sessões por status, duração, comandos, exit codes categorizados, latência, bytes, truncamento, backpressure, timeouts, estágios de cancelamento, processos órfãos e falha de cleanup. Logs e traces usam IDs, perfil, finalidade, status e códigos sanitizados. Auditoria registra hash ou resumo redigido do comando somente quando política exigir, jamais secret ou output integral.

## Invariantes

- sessão possui `id`, `cwd`, `pid`, `status`, `owner`, `workspace` e `buffer` observáveis;
- sessão persistente continua subordinada a lease, TTL e ownership;
- no máximo um comando foreground opera por sessão no contrato inicial;
- cwd nunca escapa da raiz canonicalizada do Workspace;
- PID e handles nativos não atravessam a porta pública como autorização;
- output é limitado, sequenciado e não confiável;
- comando não é repetido após estado incerto sem decisão externa explícita;
- cancelamento é controlado, escalonado e restrito à árvore da sessão;
- fechamento confirmado implica ausência de processo filho utilizável.

## Extensibilidade

Perfis de shell, adapters de PTY, sandbox e políticas de rede podem variar atrás das portas. Sessões sem shell interativo podem implementar subconjunto explícito. Extensões não podem enfraquecer ownership, cwd contido, limites, supervisão de árvore ou buffer limitado.

## Futuro

Terminal remoto, snapshots restauráveis, sessões compartilhadas por handoff autorizado e job control avançado poderão ser adicionados. Compartilhamento exigirá contrato próprio de ownership e concorrência; restauração nunca serializará handle vivo nem presumirá que comando incerto é seguro para retry.

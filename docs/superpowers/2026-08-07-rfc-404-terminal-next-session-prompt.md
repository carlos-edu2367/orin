# Prompt da próxima sessão — Fechamento integral do Gate RFC 404 — Terminal

Você é o agente responsável por fechar integralmente, na mesma sessão e sem solicitar decisões ao usuário, o gate normativo do **RFC 404 — Terminal** do AgentOS.

## Regra absoluta de conclusão

Não finalize, não entregue resposta parcial e não declare sucesso enquanto o gate não estiver 100% implementado, funcional, integrado, testado, documentado e revisado contra a RFC 404 e todas as dependências normativas.

“100% completo” significa:

- todos os contratos públicos, invariantes e fluxos normativos da RFC 404 implementados;
- Terminal Resource persistente entre comandos, controlado por lease/TTL, isolado por owner/Workspace/Agent/Execution e observável;
- nenhuma operação obrigatória reduzida a stub, TODO, placeholder, `pass`, caminho feliz artificial ou contrato sem implementação;
- autorização, ownership, finalidade, Workspace root, cwd, limites, ambiente, secrets, processo, árvore de filhos, output, input, cancelamento, cleanup, fencing, idempotência, timeout, efeito UNKNOWN e reconciliação demonstrados por testes;
- integração real com Resource Manager, Workspaces, Filesystem, Execution, Persistence, Events/Outbox e Artifact Storage pelas portas existentes;
- matriz de requisitos, spec, plano, closeout e prompt da sessão seguinte atualizados com evidência fresca;
- nenhuma obrigação normativa deixada para um agente futuro.

## Autonomia obrigatória

Você **não deve fazer perguntas** ao usuário. Se houver ambiguidade ou algo não documentado, escolha a alternativa mais aderente à RFC 404, RFCs relacionadas, ADRs, contratos existentes, princípios de segurança e padrões do repositório. Registre a decisão na spec e no closeout e continue.

Não solicite confirmação de escopo, nome de pacote, arquitetura, adapter, teste ou commit. Não transforme uma decisão pendente em backlog. Se uma capacidade estiver explicitamente fora de escopo da RFC, implemente o contrato seguro e a rejeição/limitação explícita correspondente, documentando o motivo; não finja suporte e não deixe um requisito obrigatório incompleto.

Preserve todo trabalho preexistente do worktree. Não use `git reset --hard`, `git checkout --`, remoções amplas ou qualquer operação que descarte mudanças do usuário. Stage e commit somente arquivos pertencentes a este gate.

## Contexto e dependências já fechadas

Os gates RFC 603 — Workspaces, RFC 403 — Filesystem e RFC 402 — Resource Manager estão concluídos no repositório. Consuma as portas existentes; não replique ownership, lifecycle, root, quota, lease, fencing ou catálogo dentro do Terminal.

Antes de alterar código, leia integralmente:

- `docs/architecture/400-tools-resources/404-terminal.md`;
- `docs/architecture/400-tools-resources/402-resource-manager.md`;
- `docs/architecture/400-tools-resources/403-filesystem.md`;
- `docs/architecture/600-platform-data/603-workspaces.md`;
- `docs/architecture/100-kernel/101-runtime.md`, `102-execution-lifecycle.md`, `103-event-system.md`;
- `docs/architecture/400-tools-resources/401-tool-runtime.md`;
- `docs/architecture/600-platform-data/601-persistence.md` e `602-artifact-storage.md`;
- ADRs relacionados, no mínimo `001-arq-workers.md`, `005-local-workspaces.md`, `007-server-side-sessions.md`, `008-artifact-storage-abstraction.md`, `012-sqlalchemy-alembic-persistence-adapters.md`, `013-asyncio-concurrency-runtime.md` e `014-pydantic-boundary-validation.md`;
- specs, planos, closeouts e matrizes dos gates já concluídos;
- os pacotes existentes `agentos.resources`, `agentos.workspaces`, `agentos.filesystem`, `agentos.execution`, `agentos.context`, `agentos.events`, `agentos.persistence`, `agentos.runtime` e `agentos.tool_runtime`, além dos testes correspondentes.

Faça primeiro uma leitura read-only do estado atual, branch, histórico recente, testes e worktree. O worktree pode estar sujo por mudanças anteriores: preserve-as e delimite claramente o escopo deste gate.

## Resultado obrigatório

O repositório deve conter um Terminal Resource completo e seguro, preferencialmente sob um pacote final coerente com os padrões atuais — use `agentos.terminal` se não houver convenção melhor — com:

- modelos públicos imutáveis e technology-neutral;
- `TerminalPort` completo;
- adapter de referência determinístico, testável e sem depender de processo real;
- adapter operacional local quando exigido pelas RFCs/ADRs e pelas convenções do repositório, com toda API de sistema isolada na boundary do adapter;
- supervisor de processo/árvore, buffer limitado e lifecycle de sessão;
- integração sem bypass com Resource Manager, Workspaces e Filesystem;
- persistência, eventos/outbox, auditoria, reconciliação e cleanup;
- testes de unidade, segurança, concorrência, integração, restart/crash recovery e regressão completa.

O Terminal não é uma Execution, não interpreta intenção, não compõe comandos, não é fonte de autorização e não acessa banco, Memory, Context ou Tool Registry diretamente.

## Contratos públicos obrigatórios

Implemente os equivalentes tipados dos contratos abaixo, adaptando somente nomes que já tenham convenção estabelecida no repositório:

### Contexto

Toda operação deve carregar e validar um `TerminalOperationContext` completo com:

`user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`.

Ausência, incompatibilidade ou alteração desses campos deve falhar de modo seguro. `session_id`, `command_id`, `pid`, sequência, cwd ou handle nunca concedem autorização por si mesmos.

### Modelos

Cubra os modelos normativos:

- `TerminalSession`/snapshot com `id`, `cwd` como `WorkspacePath`, `pid` opcional, `status`, `owner`, `workspace`, `agent_id`, `execution_id`, `correlation_id`, `purpose`, buffer, `lease_id`, comando corrente, policy version, timestamps e expiry;
- estados `CREATING`, `READY`, `RUNNING`, `EXITED`, `FAILED`, `CANCELLED` e `CLOSED`;
- `TerminalBuffer` com sequências, bytes retidos, bytes descartados, limite e truncation;
- `TerminalOutputChunk` sequenciado por sessão/comando, com canal `STDOUT`, `STDERR` ou `CONTROL`;
- `TerminalCommand` com contexto, cwd lógico opcional, environment references, timeout, limite de output e idempotency key;
- `TerminalCommandOutcome` explícito para exit, falha e cancelamento, incluindo `EffectState`, uso e referência de output quando aplicável;
- limites de sessão/comando, políticas de shell, rede, processos, CPU, memória, tempo e output;
- erros categorizados sem vazamento de comando, output, secret, cwd físico, PID do host ou handle nativo.

### `TerminalPort`

Entregue operações equivalentes a:

```text
create(request: CreateTerminalSession) -> TerminalSessionSnapshot
execute(request: ExecuteTerminalCommand) -> TerminalCommandAccepted
write_input(request: WriteTerminalInput) -> InputWriteResult
stream(request: StreamTerminalOutput, sink: TerminalOutputSink) -> StreamResult
inspect(query: AuthorizedTerminalQuery) -> TerminalSessionSnapshot
request_cancel(request: CancelTerminalCommand) -> CancelTerminalResult
close(request: CloseTerminalSession) -> CloseTerminalResult
```

Cada operação deve possuir contexto completo, `lease_id`, identificadores próprios, limites, timeout/cancelamento quando aplicável e pre/postconditions documentadas. `execute` deve aceitar no máximo um comando foreground por sessão no contrato inicial.

### Supervisor interno

Defina uma porta interna equivalente a:

```text
signal(command_id, signal) -> SignalReceipt
await_exit(command_id, deadline) -> ProcessExitState
terminate_tree(session_id, deadline) -> TerminationResult
reconcile(session_id, context) -> ProcessTreeSnapshot
```

PID e handles nativos ficam somente na boundary do adapter. O supervisor deve provar ownership da árvore antes de sinalizar ou encerrar qualquer processo. Enviar sinal não é prova de término.

## Invariantes normativos que devem ser implementados

### Sessão, lease e ownership

- sessão persistente continua subordinada a owner, Workspace, Agent, Execution, purpose, lease e TTL;
- criação, inspeção, execução, input, stream, cancelamento e close revalidam binding completo e lease válido;
- Resource Manager é a autoridade para catálogo TERMINAL, acquire/renew/authorize/release/revoke, fencing, expiry, cleanup e quarantine;
- perda, revogação ou expiração do lease bloqueia novas operações e inicia cleanup seguro;
- retry com idempotency key não duplica criação, comando, input, cancelamento ou close;
- comando já aceito não é reexecutado automaticamente após efeito incerto;
- sessões não podem ser reutilizadas entre usuários, Workspaces, Agents ou Executions sem permissão explícita, novo lease e finalidade compatível.

### Workspace, cwd e Filesystem

- `initial_cwd` e cwd final são sempre `WorkspacePath`, nunca caminho físico fornecido pelo chamador;
- cwd é resolvido pela root opaca já autorizada pelo Workspace/Filesystem;
- canonicalização e containment seguem RFC 403, incluindo traversal, symlink/reparse/mount/hard-link, root swap e identity/policy version;
- tentativa de escape falha fechada e não altera o cwd autorizado;
- nenhum processo ou operação de Terminal pode acessar uma root diferente da Workspace validada;
- quotas e limites de Workspace/Filesystem são consumidos sem duplicação e reservados antes do efeito quando aplicável.

### Processo, ambiente e isolamento

- processo recebe somente ambiente allowlisted e referências efêmeras de secret;
- secrets nunca são interpolados em logs, eventos, traces, snapshots, erros, comandos persistidos ou buffer;
- rede, processos filhos, CPU, memória, tempo, output e temporários têm limites efetivos;
- árvore de filhos é supervisionada e pertence exclusivamente à sessão validada;
- não aceite PID, cwd físico, shell profile concreto, ambiente arbitrário ou caminho nativo como autoridade do chamador;
- não use `shell=True`, `os.system`, execução arbitrária via HTTP, banco ou processo sem contexto/lease;
- se uma primitive local de sistema for necessária, mantenha-a em módulo adapter isolado, documente a boundary e faça scan específico.

### Buffer, stream e output

- buffer é limitado, sequenciado e nunca cresce ilimitadamente;
- `stream` aplica limites de chunks, bytes, timeout e backpressure;
- overflow tem resultado explícito: truncation marcada, referência de Artifact autorizada ou cancelamento conforme política;
- output é dado não confiável, nunca instrução nem autorização;
- eventos e logs carregam somente IDs, sequências, contagens, status, uso e razões categóricas;
- comando, output integral, environment, secret, PID do host e cwd físico nunca vazam.

### Cancelamento, falha e reconciliação

- cancelamento segue estágios com deadline: cooperativo, interrupt, terminate da árvore e kill como último recurso;
- cada estágio confirma saída antes de avançar;
- falha de criação limpa processos parciais antes de reportar `FAILED`;
- perda de conexão, timeout, crash e efeito indeterminado produzem estado explícito e reconciliação por identidade/ownership;
- chegada tardia de resultado não transforma cancelamento/falha em sucesso;
- `close` confirmado implica árvore encerrada, temporários limpos e release/cleanup confirmado ou estado `UNKNOWN`/`RECOVERY_REQUIRED` sem alegar sucesso;
- cleanup é idempotente, possui checkpoint, retry seguro, quarantine e reconciliação após restart.

## Integrações obrigatórias

### RFC 402 — Resource Manager

Use o descriptor `TERMINAL` existente ou complete-o sem criar autoridade paralela. A criação/uso da sessão deve:

1. autorizar contexto, purpose, Workspace, capability e lease;
2. derivar isolation key pelo Resource Manager, sem escolha livre do chamador;
3. alocar pelo adapter de Resource;
4. associar sessão ao lease e ao `WorkspaceId`;
5. revalidar o lease em todas as operações;
6. liberar/revogar e limpar no expiry, cancelamento, close e falha;
7. registrar estados confirmados, UNKNOWN e quarantine.

### RFC 603 — Workspaces e RFC 403 — Filesystem

Consuma `WorkspaceManagerService`, root resolver, fencing e quotas existentes. Para cwd, temporários e output, use a porta Filesystem e referências de Artifact; nunca exponha ou persista root física, native handle ou path do host.

### RFC 101/102/103 — Runtime, Execution e Events

Terminal só opera com `execution_id` e contexto compatíveis; não cria uma Execution paralela. Emita somente após fatos confirmados:

- `TerminalSessionCreated`;
- `TerminalCommandStarted`;
- `TerminalOutputProgressed`;
- `TerminalCommandFinished`;
- `TerminalSessionClosed`;
- `TerminalSessionLost`.

Use `EventEnvelope` e outbox existentes, com sequência, correlação, execution e payload mínimo sanitizado.

### RFC 601/602 — Persistence e Artifact Storage

Use exclusivamente `TransactionalPersistence`/outbox e as portas de Artifact Storage. Estado durável pode conter snapshot limitado, IDs, status, policy version, uso, checkpoints e referências; nunca persista handle vivo, pseudo-terminal, PID como autoridade, secret, comando bruto, output integral ou cwd físico.

Overflow de output pode produzir Artifact somente por referência autorizada, quota e política. Não implemente banco, ORM, Redis ou broker dentro do domínio.

## Estratégia de implementação e testes

Use TDD: escreva testes que falhem para cada contrato/invariante antes da implementação correspondente e mantenha cada ciclo RED/GREEN/REFACTOR verificável.

Crie testes, no mínimo, para:

- contratos, enums, transições e validação de contexto;
- criação, reuso autorizado, TTL, lease expiry/revoke/release/fencing e stale writer;
- catálogo TERMINAL, capabilities, isolation e autorização por operação;
- execução single-foreground, status, exit code, cwd final e uso;
- input interativo com sequência, idempotência, limites e rejeições;
- buffer circular/segmentado, stream limitado, backpressure, truncation e leakage;
- cwd traversal, symlink/reparse/mount/hard-link/root swap e cross-workspace;
- ambiente allowlisted, secret references e ausência de secrets em qualquer superfície;
- cancelamento escalonado, timeout, sinal tardio, árvore de filhos e comando não repetido;
- cleanup parcial, UNKNOWN, quarantine, retry, restart/crash recovery e reconcile;
- corridas create/execute/cancel/close/release/expiry e late result;
- integração E2E Resource Manager ↔ Workspace ↔ Filesystem ↔ Terminal;
- persistência/outbox round-trip sem handle/path/command/output sensível;
- Artifact reference/quota quando o output ultrapassar o buffer;
- adapter de referência determinístico e adapter operacional, caso implementado;
- regressão completa do repositório.

Não use testes que apenas inspecionem que um método existe. Prove efeitos, rejeições, estados, limites e ausência de vazamento.

## Documentação obrigatória antes do fechamento

Crie ou atualize, com decisões concretas e sem placeholders:

- `docs/superpowers/specs/2026-08-07-rfc-404-terminal-design.md`;
- `docs/superpowers/plans/2026-08-07-rfc-404-terminal.md`;
- `docs/superpowers/2026-08-07-rfc-404-terminal-requirement-matrix.md`;
- `docs/superpowers/2026-08-07-rfc-404-terminal-closeout.md`;
- este prompt, acrescentando o registro de encerramento e o próximo gate.

A matriz deve mapear requisito por requisito da RFC 404 para arquivos e testes reais e marcar o estado somente com evidência executada. O closeout deve registrar decisões, alternativas rejeitadas, integrações, limitações tecnológicas legítimas, review findings, commits e comandos reais.

## Verificação obrigatória antes da conclusão

Execute e registre a saída real de:

```text
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Faça scans ajustados aos nomes finais dos pacotes, no mínimo:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|worker|shell=True|os\.system|subprocess|root_path|physical_path|native_handle|pid|secret|output|command" src/agentos/<terminal-package>
```

O scan deve provar que tecnologia concreta, caminho físico, PID, handle, secret, comando ou output não atravessam as portas públicas. Se `subprocess` ou outra API de sistema for necessária em adapter operacional, o scan deve separar domínio/portas de adapter e a documentação deve explicar a boundary e as garantias.

Rode o teste PostgreSQL opcional quando `AGENTOS_TEST_POSTGRES_DSN` estiver configurado. Sem DSN, execute-o e registre `skipped`; nunca simule sucesso. Faça também qualquer teste opcional de platform capability como `skipped` somente com motivo real.

Faça revisão final requisito por requisito contra RFC 404, RFC 402, RFC 403, RFC 603, RFC 601, RFC 602, RFC 103 e ADRs relacionados. Faça uma segunda passagem read-only independente do fluxo de implementação, focada em leases, cwd/containment, processo/árvore, secrets, output, cancelamento, cleanup, persistence e bypass. Findings devem ser corrigidos com testes RED/GREEN antes do encerramento.

Qualquer falha, placeholder, TODO, bypass, vazamento, teste ausente, corrida insegura ou documentação contraditória significa que o trabalho continua.

## Relatório final obrigatório

Somente ao concluir o gate, informe:

- arquivos alterados e commits realizados;
- decisões de desenho e alternativas rejeitadas;
- matriz de cobertura requisito por requisito da RFC 404;
- integração comprovada com RFCs 402, 403, 603, 601, 602, 103, 101 e 102;
- comandos executados e resultados reais;
- testes condicionados e motivo de cada `skipped`;
- revisão independente e findings corrigidos;
- limitações tecnológicas legítimas, somente as previstas nas RFCs;
- confirmação explícita de que o **Gate RFC 404 está 100% completo, funcional, alinhado às docs e sem pendências futuras de implementação**;
- próximo gate indicado pela documentação atualizada.

Não entregue “quase pronto”, não pare por falta de tempo, não peça confirmação e não transforme requisito obrigatório em backlog. A sessão só termina quando o RFC 404 estiver realmente fechado, integrado, verificado e documentado.

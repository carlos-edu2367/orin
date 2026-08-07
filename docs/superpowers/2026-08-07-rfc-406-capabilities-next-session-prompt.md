# Prompt da próxima sessão — Fechamento integral do Gate RFC 406 — Capabilities

Você é o agente responsável por fechar integralmente, na mesma sessão e sem solicitar decisões ao usuário, o gate normativo do **RFC 406 — Capabilities** do AgentOS.

## Regra absoluta de conclusão

Não finalize, não entregue resposta parcial e não declare sucesso enquanto o gate não estiver 100% implementado, funcional, integrado, testado, documentado e revisado contra o RFC 406 e todas as dependências normativas.

“100% completo” significa:

- todos os contratos públicos, invariantes, estados, fluxos, eventos, políticas, limites, retries, checkpoints, child Executions e compensações do RFC 406 implementados;
- Capability Registry versionado, imutável por versão, autorizado, auditável e com bootstrap seguro;
- Capability Runtime composto operando exclusivamente por portas, sem adapter concreto, banco, fila, DSL não especificada ou bypass do Runtime/Tool Runtime;
- `start`, `run`, `resume`, `inspect` e cancelamento com contexto completo, ownership, finalidade, correlação, causalidade, idempotência e versionamento;
- toda Tool invocada pelo Tool Runtime com versão exata, autorização própria, limites, contexto e outcome explícito;
- Execution canônica criada/operada pela porta existente, com mapeamento correto entre `CapabilityRunState` e `ExecutionState`, sem máquina paralela;
- steps determinísticos com dependências, decisão, paralelismo declarado, limite de fan-out, retry, `EffectState`, `UNKNOWN`, reconciliação e compensação explícita;
- child Executions usadas para subtrabalho independente, durável, delegável ou com política própria, sem herdar permissões, segredos ou Context integral;
- checkpoints seguros, referências em vez de payload integral, persistência transacional e outbox sem falso sucesso;
- cancelamento cooperativo propagado a Tools e filhos, compensação somente quando declarada/autorizada e terminal explícito;
- conteúdo de Tool, Agent, Provider, Browser, arquivo e Artifact tratado como dado não confiável, sem expansão de permissões ou argumentos fora do descriptor;
- matriz de requisitos, spec, plano, closeout e este prompt atualizados com evidência fresca;
- nenhuma obrigação normativa deixada para um agente futuro.

## Autonomia obrigatória

Você **não deve fazer perguntas** ao usuário. Se houver ambiguidade, escolha a alternativa mais aderente ao RFC 406, RFCs relacionadas, ADRs, contratos existentes, segurança e padrões do repositório. Registre a decisão na spec e no closeout e continue.

Não solicite confirmação de pacote, representação de programa, scheduler, mecanismo de checkpoint, adapter, fila, banco, child Execution, semântica de retry, política de compensação, teste ou commit. Quando uma tecnologia concreta estiver fora do escopo, implemente a porta technology-neutral e um adapter determinístico mínimo que prove o contrato. Não invente uma DSL, marketplace ou linguagem de workflow: use uma representação tipada, imutável e determinística de `CapabilityStep`/programa que possa ser substituída depois.

Preserve todo trabalho preexistente do worktree. Não use `git reset --hard`, `git checkout --`, remoções amplas ou qualquer operação que descarte mudanças do usuário. Stage e commit somente arquivos pertencentes a este gate.

## Contexto e dependências já fechadas

O gate RFC 405 — Browser está fechado no commit `b551ea8`. Os gates RFC 603 — Workspaces, RFC 403 — Filesystem, RFC 402 — Resource Manager e RFC 404 — Terminal também estão concluídos. Consuma as portas existentes; não replique ownership, lifecycle, lease, fencing, quota, root, Artifact, Persistence, Execution ou Tool Runtime dentro de Capabilities.

Antes de alterar código, leia integralmente:

- `docs/architecture/400-tools-resources/406-capabilities.md`;
- `docs/architecture/400-tools-resources/401-tool-runtime.md`;
- `docs/architecture/400-tools-resources/402-resource-manager.md`;
- `docs/architecture/100-kernel/101-runtime.md`, `102-execution-lifecycle.md` e `103-event-system.md`;
- `docs/architecture/600-platform-data/601-persistence.md` e `602-artifact-storage.md`;
- `docs/architecture/600-platform-data/603-workspaces.md`;
- `docs/architecture/400-tools-resources/405-browser.md` e seu closeout;
- ADRs relacionados, no mínimo `001-arq-workers.md`, `004-playwright-browser-workers.md`, `008-artifact-storage-abstraction.md`, `012-sqlalchemy-alembic-persistence-adapters.md`, `013-asyncio-concurrency-runtime.md` e `014-pydantic-boundary-validation.md`;
- specs, planos, closeouts, matrizes e prompts dos gates já concluídos;
- os pacotes existentes `agentos.execution`, `agentos.runtime`, `agentos.tool_runtime` se existentes, `agentos.resources`, `agentos.events`, `agentos.persistence`, `agentos.artifact_storage`, `agentos.context`, `agentos.memory`, `agentos.browser` e os testes correspondentes.

Faça primeiro uma leitura read-only do branch, histórico, testes e worktree. O worktree pode estar sujo por trabalho anterior: preserve e delimite claramente o escopo deste gate.

## Resultado obrigatório

O repositório deve conter um pacote final coerente — preferencialmente `agentos.capabilities` se não houver convenção melhor — com:

- modelos públicos imutáveis e technology-neutral para contexto, refs, descriptor, limites, registro, run, steps, outcomes, checkpoint, retry, child Execution e compensação;
- `CapabilityRegistry` completo: register, resolve, list e disable, com versões imutáveis, status, permissões, Tools/Capabilities filhas permitidas e bootstrap allowlisted;
- `CapabilityService` completo: start, run, resume, request_cancel e inspect;
- `CapabilityToolPort` que traduza cada passo para RFC 401, sem receber Tool concreta, Registry, adapter, Runtime ou storage;
- `ChildExecutionPort` que crie, inspecione e cancele Executions filhas pela porta da RFC 102;
- `CapabilityStatePort`/checkpoint facade com persistência limitada a IDs, versões, refs, estados, steps, uso, efeitos e timestamps;
- programa de referência determinístico composto por steps tipados, dependências e limites, sem DSL implícita e sem execução arbitrária de código;
- scheduler determinístico para steps prontos, paralelismo declarado e backpressure, sem exceder `maximum_parallel_steps`;
- política de autorização por step calculada como interseção de ator, User, Workspace, Agent, Execution, purpose, descriptor, Tool, Resource, quota e policy;
- outcomes distintos de sucesso, espera, falha, cancelamento, compensação incompleta e efeito `UNKNOWN`;
- eventos `CapabilityStarted`, `CapabilityStepStarted`, `CapabilityStepFinished`, `CapabilityCheckpointCreated`, `CapabilityChildExecutionCreated`, `CapabilityCompensationFinished`, `CapabilityFinished`, `CapabilityFailed` e `CapabilityCancelled` preparados pela outbox após fatos confirmados;
- testes unitários, segurança, concorrência, idempotência, retry/reconcile, checkpoint/restart, child Execution, compensação, cancelamento, integração com Tool Runtime/Execution/Persistence/Events e regressão completa.

Capability não é Tool, não implementa Tool, não chama Tool diretamente, não é Runtime do Kernel, não interpreta intenção, não executa Provider, não acessa Browser/Filesystem/Resource/Artifact/Memory/Context diretamente e não altera a máquina de estados da Execution por caminho paralelo.

## Contratos públicos obrigatórios

Implemente equivalentes tipados dos contratos do RFC 406, adaptando somente nomes já convencionados no repositório.

### Contexto, refs e descriptor

Toda operação de execução deve carregar e validar `CapabilityOperationContext` completo:

`user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`.

Operações administrativas do catálogo devem usar `CapabilityRegistryOperationContext` com `user_id`, `workspace_id`, `agent_id`/`execution_id` ou `administrative_correlation_id`, `correlation_id`, `purpose` e `actor`, mantendo exatamente um entre `execution_id` e `administrative_correlation_id` não nulo. Conhecer `capability_id`, `capability_run_id`, `execution_id`, `step_id` ou ToolRef nunca concede autorização.

`CapabilityRef` deve conter `capability_id` e versão exata. `CapabilityDescriptor` deve conter objetivo/nome/descrição, input/output schemas, `allowed_tools`, `allowed_child_capabilities`, permissions, `CapabilityLimits`, `CapabilityCancellationPolicy`, `compensation_policy` e status `ACTIVE`, `DEPRECATED` ou `DISABLED`. Descriptor publicado é imutável; mudança incompatível cria nova versão.

`CapabilityLimits` deve aplicar efetivamente timeout, `maximum_steps`, `maximum_tool_invocations`, `maximum_child_executions`, `maximum_parallel_steps`, custo e `maximum_resource_usage`.

### Run, step e outcomes

Modele `CapabilityRun` com `capability_run_id`, ref/version, contexto, `input_ref`, `CapabilityRunState`, `state_version`, steps atuais/concluídos, filhos, uso, checkpoint/result refs e timestamps.

Implemente os estados `QUEUED`, `RUNNING`, `WAITING_TOOL`, `WAITING_CHILD`, `PAUSED`, `SUCCEEDED`, `FAILED`, `CANCELLED` e `COMPENSATING` e o mapeamento canônico:

- `QUEUED → Execution.QUEUED`;
- `RUNNING`/`COMPENSATING → Execution.RUNNING`;
- `WAITING_TOOL → Execution.WAITING_TOOL`;
- `WAITING_CHILD → Execution.PAUSED`, somente após checkpoint e filhos aguardados confirmados;
- `PAUSED → Execution.PAUSED`;
- `SUCCEEDED → Execution.COMPLETED`, `FAILED → Execution.FAILED`, `CANCELLED → Execution.CANCELLED`.

`CapabilityStep` deve declarar `step_id`, kind (`TOOL`, `CHILD_EXECUTION`, `DECISION`, `CHECKPOINT`, `COMPENSATION`), dependências, autorização, timeout, retry policy, input bindings e output binding. `CapabilityStepRecord` deve conservar attempt, invocation/child IDs, outcome, result ref, `EffectState` e término.

`CapabilityOutcome` deve distinguir `CapabilitySucceeded`, `CapabilityWaiting` (`TOOL`, `CHILD`, `USER`, `PAUSE`), `CapabilityFailed` com erro/compensation outcome e `CapabilityCancelled` com razão/compensation outcome. Nunca converter parcial, compensação incompleta, cancelamento ou `UNKNOWN` em sucesso.

### Registry e service

Entregue contratos equivalentes a:

```text
CapabilityRegistry:
  register(RegisterCapability) -> RegistrationResult
  resolve(CapabilityRef, CapabilityOperationContext) -> CapabilityDescriptor
  list(AuthorizedCapabilityRegistryQuery) -> CapabilityDescriptor[]
  disable(DisableCapability) -> RegistrationResult

CapabilityService:
  start(StartCapability) -> CapabilityAccepted
  run(RunCapability) -> CapabilityOutcome
  resume(ResumeCapability) -> CapabilityOutcome
  request_cancel(CancelCapability) -> CancelCapabilityResult
  inspect(AuthorizedCapabilityQuery) -> CapabilityRunSnapshot
```

`start` valida descriptor, Task, contexto, ownership, finalidade, input ref e limites e cria uma Execution nova em `QUEUED` pela porta existente. Não executa programa na API. `run` só opera Execution adquirida/elegível e exige `expected_state_version`. `resume` carrega checkpoint seguro e revalida descriptor, ownership, purpose, Tools, children, refs, limites e resultados. Retry depois de terminal cria nova Execution e novo run; não reabre terminal.

### Tool e Child Execution ports

Toda Tool deve ser chamada por uma porta equivalente a:

```text
CapabilityToolPort.invoke(CapabilityToolInvocation) -> ToolInvocationOutcome
```

`CapabilityToolInvocation` carrega run/step IDs, ToolRef com versão exata, contexto completo, argumentos estruturados, idempotency key e limites. A Capability não chama adapter, factory, registry ou Tool diretamente e não duplica `ToolStarted`/`ToolFinished`.

Subtrabalho independente deve usar:

```text
ChildExecutionPort.create(CreateChildExecution) -> ExecutionId
ChildExecutionPort.inspect(AuthorizedChildExecutionQuery) -> ExecutionSnapshot
ChildExecutionPort.request_cancel(CancelChildExecution) -> CommandResult
```

Child Execution recebe contexto mínimo, causalidade e refs autorizadas; não herda permissão ampla, segredo, Context ou Artifact integral. Capability não chama a si mesma nem outra Capability por função interna; composição declarada cria child Execution e respeita profundidade/limites.

## Invariantes normativos obrigatórios

### Autorização e não escalação

- permissão efetiva de cada step é a interseção de ator/User, Workspace/classificação, Agent/Execution, purpose, descriptor/version, Tool/versão e Resource/lease/quota/policy;
- permissão da Capability nunca preautoriza Tool, Resource ou Capability filha;
- mudança de input, redirect, output de modelo, Browser, arquivo, Artifact ou Tool nunca aumenta permissions, purpose, Tool args ou conjunto permitido;
- step não autorizado falha antes do efeito ou aguarda comando externo explícito;
- referências são reautorizadas no resolve e não transferem ownership.

### Planejamento, steps e paralelismo

- somente steps cujas dependências estão confirmadas podem iniciar;
- ciclo de dependência, step duplicado, binding inválido, Capability recursiva e Tool fora da allowlist falham antes do efeito;
- `maximum_steps`, Tool calls, children, paralelismo, tempo, custo e recursos são monotônicos e efetivos;
- paralelismo é declarado, limitado, determinístico e cancelável; não há fan-out implícito;
- decisão interna só escolhe entre branches declarados, não interpreta intenção nem injeta código;
- inputs/outputs volumosos usam refs; checkpoint/Event/log não carregam conteúdo integral.

### Execution e Tool Runtime

- todo run pertence a uma Execution governada pela RFC 102;
- somente `ExecutionControl` confirma mudanças de Execution;
- `WAITING_CHILD` só solicita `Execution.PAUSED` depois de checkpoint e child IDs confirmados;
- retorno de espera solicita `PAUSED → QUEUED`, nunca uma transição paralela;
- cada Tool usa Tool Runtime, ToolRef exata, contexto sensível e autorização própria;
- Capability não cria Execution paralela para esconder passos nem altera a máquina canônica.

### Checkpoint, retry, UNKNOWN e compensação

- checkpoint seguro contém descriptor/version, state, steps concluídos, refs, effects, uso, children e próxima decisão;
- checkpoint nunca contém handle, secret, adapter object, Context/Artifact integral ou payload proprietário;
- idempotency key de step é determinística no run/step/attempt;
- retry só ocorre se Tool declarar idempotência ou houver reconciliação comprovada;
- `EffectState.UNKNOWN` bloqueia retry cego e exige inspect/reconcile;
- compensação é explícita, ordenada, autorizada e composta de Tools próprias;
- compensação não presume rollback global, não apaga Events e falha de compensação permanece visível;
- falha após commit não desfaz Event/outbox e terminal não é reaberto.

### Cancelamento e cleanup

- cancelamento impede novos steps, Tools, children e retries;
- sinal propaga a Tools e filhos ativos conforme policy, aguardando somente deadline seguro;
- compensação em cancelamento só ocorre se declarada, autorizada e dentro do orçamento;
- resultado tardio não converte `CANCELLED` em sucesso;
- filhos não necessários são cancelados/reconciliados e seus IDs permanecem auditáveis;
- run confirma `CANCELLED`, referências parciais autorizadas e Event correspondente sem falso sucesso.

## Integrações obrigatórias

### RFC 401 — Tool Runtime

Use a porta existente para toda invocação, streaming, cancelamento, timeout, idempotência, resultado, uso e Event de Tool. Não replique Tool Registry, Tool authorization, Resource lease ou Tool persistence em Capabilities.

### RFC 101/102/103 — Runtime, Execution e Events

Capability opera sob Execution corrente e não substitui Runtime. Use `ExecutionControl`/fachadas existentes para transições, cancelamento, terminal, causalidade e checkpoints canônicos. Events de Capability só podem ser registrados após fatos confirmados, pela `TransactionalPersistence`/outbox, com IDs, refs, versões, steps, outcomes, uso e razões sanitizadas.

### RFC 601 — Persistence

Use somente `TransactionalPersistence`/outbox. Estado durável pode conter IDs, ownership limitado, descriptor/version, state/version, steps, refs, children, usage, effect states, retry/checkpoint metadata e timestamps. Nunca persista segredo, handle, adapter, código executável arbitrário, Context integral, input/output integral, Tool payload sensível ou autoridade baseada em ID.

### RFC 402/403/602/405

Capabilities apenas encaminham requests autorizados às portas existentes. Não acessam Resource Manager, Filesystem, Artifact Storage ou Browser diretamente. Uma Capability pode coordenar uma Tool Browser/Filesystem/Terminal, mas o acesso efetivo continua sendo validado pelo Tool Runtime e pelo Resource correspondente.

## Estratégia TDD e testes obrigatórios

Use TDD sem exceções: escreva cada teste RED, execute e confirme a falha correta, implemente o mínimo GREEN, execute novamente e só depois refatore. Não escreva produção antes do teste falhar. Não use testes que apenas confirmem que um método existe.

Cubra no mínimo:

- modelos imutáveis, refs, enums, estados, limites e contexto completo;
- registry register/resolve/list/disable, versões imutáveis, status, allowlists, permissions e bootstrap seguro;
- schema/input/output bounded, classificação e ausência de payload/secret integral;
- start criando Execution `QUEUED`, idempotência, conflito e ownership;
- run/resume com Execution elegível, state version, descriptor snapshot e checkpoint;
- mapeamento CapabilityRun ↔ Execution, incluindo `WAITING_CHILD → PAUSED → QUEUED`;
- dependências, ciclo inválido, steps prontos, branches declarados, paralelismo e backpressure;
- Tool Runtime invocado com versão exata, contexto, purpose, grants, limites e sem adapter bypass;
- Tool denial quando Capability permission é ampla mas Tool/Resource permission falta;
- child Execution, causalidade, limites, profundidade, ownership mínimo e ausência de herança de segredo/Context;
- maximum steps/tool calls/children/parallelism/time/cost/resource usage efetivos;
- retry seguro, idempotency key, `UNKNOWN`, inspect/reconcile e late result;
- checkpoints sem handle/secret/payload integral e round-trip por Persistence/outbox;
- falha de step, falha do run, dependências bloqueadas e outcomes explícitos;
- compensação none/explicit, ordem, authorization, falha parcial e ausência de rollback falso;
- cancelamento por run/step/Tool/child, propagação, deadline, cleanup e terminal sem sucesso tardio;
- Events de Capability somente após fatos confirmados, payload mínimo, sequência, correlação e deduplicação;
- concorrência run/resume/cancel/checkpoint, stale writer, duplicate start, retry e child completion;
- restart/crash/reconcile de run, step, child e compensação;
- integração E2E Capability → Execution → Tool Runtime → Tool/Resource → Persistence/Events;
- regressão completa do repositório.

Quando uma dependência opcional não estiver instalada, execute o teste de boundary e registre `skipped` pelo motivo real. Não simule Tool, banco, fila, Runtime ou sucesso de Execution só para fazer o teste passar.

## Documentação obrigatória antes do fechamento

Crie ou atualize, sem placeholders:

- `docs/superpowers/specs/2026-08-07-rfc-406-capabilities-design.md`;
- `docs/superpowers/plans/2026-08-07-rfc-406-capabilities.md`;
- `docs/superpowers/2026-08-07-rfc-406-capabilities-requirement-matrix.md`;
- `docs/superpowers/2026-08-07-rfc-406-capabilities-closeout.md`;
- este prompt, acrescentando registro de encerramento e o próximo gate somente se existir na documentação.

A matriz deve mapear requisito por requisito do RFC 406 para arquivos e testes reais e usar `COVERED` somente com evidência executada. O closeout deve registrar decisões concretas, alternativas rejeitadas, integrações, limitações legítimas, findings corrigidos, commits e comandos reais.

## Verificação obrigatória antes da conclusão

Execute e registre a saída real de:

```text
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Faça scans ajustados ao pacote final, no mínimo:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|subprocess|adapter|database|orm|runtime|tool|capability|execution|checkpoint|child|compensation|secret|handle|payload|input|output|permission|authorization" src/agentos/capabilities
rg -n "ToolRuntime|ToolRegistry|Playwright|Browser|Filesystem|ResourceManager|ArtifactStorage|TransactionalPersistence|EventBus|Runtime|ExecutionControl" src/agentos/capabilities
```

O scan deve provar que Capability não importa nem instancia adapter concreto, banco, ORM, Redis, fila, Runtime interno, Provider, Browser, Filesystem, Resource ou Tool concreta. Falsos positivos — como nomes de portas, tipos públicos e adapters de teste — devem ser explicados no closeout e cobertos por boundary tests.

Execute o teste PostgreSQL opcional quando `AGENTOS_TEST_POSTGRES_DSN` estiver configurado; sem DSN, execute-o e registre `skipped`. Execute qualquer capability/engine opcional e registre `skipped` apenas por motivo real.

Faça uma revisão final requisito por requisito contra RFC 406, RFCs 401, 402, 601, 101, 102 e 103 e ADRs relacionados. Faça uma segunda passagem read-only independente focada em registry/versioning, context/ownership, permission intersection, Tool Runtime boundary, Execution mapping, child inheritance, scheduler/parallelism, limits, checkpoint, retry/UNKNOWN, compensation, cancellation, persistence/outbox, events e bypass. Cada finding deve receber teste RED/GREEN antes do encerramento.

Qualquer falha, TODO, placeholder, `pass`, bypass, falso sucesso, teste ausente, corrida, vazamento, cleanup incompleto ou documentação contraditória significa que o trabalho continua.

## Relatório final obrigatório

Somente ao concluir o gate, informe:

- arquivos alterados e commits realizados;
- decisões de desenho e alternativas rejeitadas;
- matriz de cobertura requisito por requisito do RFC 406;
- integração comprovada com RFCs 401, 402, 601, 101, 102, 103, 403, 602 e 405;
- confirmação de que toda Tool passa pelo Tool Runtime e de que Capability não acessa adapter concreto;
- confirmação de que Execution continua a unidade canônica e de que child Executions não herdam autorização/segredos/Context integral;
- evidências de autorização por interseção, limites, dependências, paralelismo, retries, UNKNOWN, checkpoint, compensação, cancelamento, cleanup e outbox;
- comandos executados e resultados reais;
- testes condicionados e motivo de cada `skipped`;
- revisão independente e findings corrigidos;
- limitações tecnológicas legítimas, somente as previstas no RFC 406;
- confirmação explícita de que o **Gate RFC 406 está 100% completo, funcional, alinhado às docs e sem pendências futuras de implementação**;
- próximo gate somente se estiver definido pela documentação; nunca invente um RFC inexistente.

Não entregue “quase pronto”, não pare por falta de tempo, não peça confirmação e não transforme requisito obrigatório em backlog. A sessão só termina quando o RFC 406 estiver fechado, integrado, verificado e documentado.

## Registro de encerramento desta sessão — a ser preenchido pelo agente executor

Ao fechar o gate, acrescente aqui a evidência real de implementação, testes, decisões, commits, review, limitações legítimas e o próximo gate documentado. Não deixe este registro vazio, genérico ou baseado em intenção.

Próximo gate esperado: identificar exclusivamente a partir da sequência normativa existente após RFC 406; se não houver RFC seguinte documentado, registrar essa ausência como decisão documental, sem criar backlog fictício.

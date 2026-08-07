# RFC 406 — Capabilities: desenho de fechamento

**Data:** 2026-08-07  
**Estado:** desenho executável registrado para fechamento do gate  
**Escopo:** contratos públicos, registry, execução composta, portas, segurança e evidência do RFC 406

## Decisão

Capabilities serão um pacote `agentos.capabilities` composto por quatro limites: modelos imutáveis (`models.py`), portas públicas (`ports.py`), registry versionado (`registry.py`) e serviço/scheduler de execução (`service.py` e `scheduler.py`). A composição não cria um novo Runtime de Kernel, Tool Runtime, banco, fila ou DSL. O programa é uma tupla imutável de `CapabilityStep` tipados, com dependências explícitas, bindings bounded e branches declarados.

O pacote consome `ExecutionControl` e `Execution` existentes. `start` usa `CreateExecution`; `run`, `resume` e cancelamento usam somente os comandos públicos da RFC 102. A máquina `CapabilityRunState` é uma projeção composta e seu mapeamento para `ExecutionState` é validado antes de cada comando. O runtime recebe `CapabilityToolPort`, `ChildExecutionPort`, `CapabilityStatePort` e `CapabilityClock` por injeção; não conhece implementação concreta, registry interno, adapter, banco ou publicador.

## Componentes e fluxo

```text
StartCapability
  -> CapabilityRegistry.resolve
  -> ExecutionControl.create(QUEUED)
  -> CapabilityStatePort.save(run + CapabilityStarted outbox)

Worker/Kernel
  -> CapabilityService.run(expected_state_version)
  -> deterministic ready steps
  -> CapabilityToolPort ou ChildExecutionPort
  -> checkpoint/state/outbox
  -> ExecutionControl.commit/transition
```

`CapabilityStatePort` persiste apenas snapshot allowlisted: IDs, refs, versões, estados, steps, effects, filhos, uso e timestamps. `CapabilityCheckpoint` é uma projeção do mesmo boundary e nunca aceita segredo, handle, objeto, conteúdo integral ou código executável. A implementação `InMemoryCapabilityState` existe somente como adapter de referência de testes; produção continua dependente da porta RFC 601.

## Contratos e invariantes

- `CapabilityOperationContext` exige usuário, workspace opcional, agent, Execution, correlação, finalidade e ator.
- `CapabilityRegistryOperationContext` exige exatamente um entre `execution_id` e `administrative_correlation_id`, com bootstrap allowlisted apenas no catálogo vazio.
- `CapabilityRef` sempre contém versão exata; descriptor e programa publicados são imutáveis. Nova semântica exige nova versão.
- Permissão efetiva de cada step é a interseção de ator/usuário, workspace, agent/Execution, finalidade, descriptor, Tool e Resource/policy. A implementação nega quando qualquer grant obrigatório falta.
- Inputs, outputs e conteúdo externo são refs/valores estruturados bounded. Nenhum valor observado pode expandir allowlists, finalidade, permissões ou argumentos declarados.
- Steps prontos são ordenados por `step_id`, dependências confirmadas são obrigatórias, ciclos/duplicações/bindings inválidos falham antes de efeitos, e `maximum_parallel_steps` é aplicado com backpressure determinístico.
- Retry usa chave `run/step/attempt`; somente `IDEMPOTENT`/`IDEMPOTENT_WITH_KEY` ou reconciliação comprovada permitem nova tentativa. `UNKNOWN` bloqueia retry cego.
- Child Execution recebe refs mínimas, ownership/correlação/causalidade explícitas e não herda grants, segredos ou Context integral.
- Checkpoint ocorre antes de espera; `WAITING_CHILD` só solicita `PAUSED` depois de checkpoint e IDs filhos confirmados. Retomada solicita `PAUSED -> QUEUED`.
- Outcomes são distintos para sucesso, espera, falha, cancelamento, compensação incompleta e efeito `UNKNOWN`; resultado tardio não reabre terminal.
- Events de Capability são outbox entries mínimas no state port após fato composto confirmado; Tool events permanecem responsabilidade do Tool Runtime.

## Integrações

`ExecutionControl` é a integração RFC 102/101. A porta `CapabilityToolPort` espelha o limite RFC 401 sem receber Tool concreta. `ChildExecutionPort` espelha a porta RFC 102 para filhos. `CapabilityStatePort` define a fachada bounded que pode ser composta em `TransactionalPersistence` RFC 601; o pacote não importa tecnologia de persistência. Events usam a forma mínima da RFC 103. Workspace, Resource, Filesystem, Artifact e Browser só aparecem como grants/refs e nunca como imports diretos.

## Alternativas rejeitadas

1. **DSL ou interpretador genérico:** rejeitado por introduzir linguagem não especificada e execução arbitrária; usa-se programa tipado e imutável.
2. **Máquina de Execution paralela:** rejeitada porque criaria divergência com RFC 102; o serviço somente chama `ExecutionControl`.
3. **Capability chamando Tool/adapter diretamente:** rejeitado; a única dependência é `CapabilityToolPort`.
4. **Herança integral de contexto para filhos:** rejeitada por escalação; filhos recebem um `ChildExecutionContext` mínimo e refs autorizadas.
5. **Retry automático de UNKNOWN:** rejeitado por risco de efeito duplicado; exige `reconcile` explícito.
6. **Rollback global em compensação:** rejeitado por não ser garantível; compensação é sequência declarada, autorizada e falível.

## Limitações legítimas

O RFC 401 ainda não possui pacote de runtime no repositório; portanto o gate entrega e testa a porta estrutural `CapabilityToolPort`, sem simular Tool Runtime em produção. O RFC 601 possui portas e adapters existentes; o gate entrega a fachada limitada e o adapter determinístico de testes, sem criar schema ou adapter de banco específico de Capability. A publicação real de outbox permanece responsabilidade do mecanismo RFC 601/103 conectado pela aplicação.

## Teste e revisão

Os testes cobrem contratos, imutabilidade, registry/bootstrap, programa determinístico, dependências/ciclo, paralelismo/backpressure, interseção de grants, Execution mapping, Tool/child boundaries, limits, retry/UNKNOWN, checkpoint, compensation, cancellation, idempotência e scans de imports. A matriz marca `COVERED` somente após execução do teste nomeado e dos comandos finais do briefing.

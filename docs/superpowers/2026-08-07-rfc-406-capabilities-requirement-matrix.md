# Matriz de requisitos — RFC 406 Capabilities

**Data da evidência:** 2026-08-07  
**Critério:** `COVERED` só é usado quando o teste ou comando citado foi executado nesta sessão e retornou o resultado indicado. O pacote é technology-neutral; o adapter de estado em memória é apenas a implementação de referência dos testes.

| Requisito RFC 406 | Implementação | Teste/comando executado | Estado |
|---|---|---|---|
| Contexto completo de execução | `src/agentos/capabilities/models.py:CapabilityOperationContext` | `test_context_is_complete_and_repr_hides_purpose` | COVERED |
| Contexto administrativo e exatamente um vínculo | `CapabilityRegistryOperationContext` | `test_registry.py` + `test_context_is_complete...` | COVERED |
| Ref com versão exata e descriptor imutável | `CapabilityRef`, `CapabilityDescriptor` | `test_models_are_immutable...`, `test_registry_keeps_published_versions_immutable...` | COVERED |
| Input/output schema e valores bounded | `CapabilityDescriptor`, `StructuredValue` | `test_structured_values_are_bounded...` | COVERED |
| Tools e child Capabilities allowlisted | `CapabilityDescriptor`, `_validate_program` | `test_registry...`, lifecycle Tool/child tests | COVERED |
| Limites de tempo, steps, Tool, child, paralelismo, custo e recurso | `CapabilityLimits`, `_ensure_limits`, `_ensure_usage` | `test_tool_invocation_limit...`, `test_timeout_is_enforced...`, contract bounds | COVERED |
| Políticas de cancelamento e compensação | `CapabilityCancellationMode`, `CompensationPolicy` | `test_compensation_and_limits.py` | COVERED |
| Estados do CapabilityRun | `CapabilityRunState` | `test_capability_state_maps_to_canonical_execution` | COVERED |
| Mapeamento canônico para Execution | `execution_state_for_capability_state`, `ExecutionControl` | lifecycle e full regression | COVERED |
| Steps tipados, dependências e bindings | `CapabilityStep`, `CapabilityProgram` | `test_program_rejects_duplicate_steps_and_unknown_dependencies` | COVERED |
| Ciclo, step duplicado e programa inválido antes de efeitos | `DeterministicStepScheduler.validate` | `test_scheduler_rejects_cycles_before_any_step_can_run` | COVERED |
| Seleção determinística e limite de paralelismo | `DeterministicStepScheduler.ready` | `test_ready_steps_are_topological_deterministic_and_bounded` | COVERED |
| Registry register/resolve/list/disable | `InMemoryCapabilityRegistry` | `test_registry.py` | COVERED |
| Bootstrap allowlisted e encerrado após catálogo inicial | `InMemoryCapabilityRegistry.register` | `test_bootstrap_is_allowlisted_once...` | COVERED |
| Idempotência e conflito de registro | registry fingerprints | `test_registry_keeps_published_versions_immutable...`, `test_disable...` | COVERED |
| `start` cria Execution `QUEUED` sem executar | `CapabilityService.start` | `test_start_creates_queued_execution_and_is_idempotent` | COVERED |
| `run` exige versão esperada e Execution adquirida | `CapabilityService.run` | lifecycle tests + stale state test | COVERED |
| `resume` valida checkpoint e reabre somente por `PAUSED -> QUEUED` | `CapabilityService.resume` | `test_child_wait_checkpoints_before_pausing...`, checkpoint test | COVERED |
| `inspect` retorna somente run autorizado | `CapabilityService.inspect`, state scope | `test_inspect_is_scoped_to_the_complete_operation_context` | COVERED |
| Cancelamento terminal e idempotente | `request_cancel`, `_cancel_run` | `test_cancel_propagates_and_late_tool_cancel_cannot_become_success` | COVERED |
| Tool port com ToolRef exata e contexto | `CapabilityToolInvocation`, `CapabilityToolPort` | `test_run_invokes_exact_tool_ref...` | COVERED |
| Capability não instancia Tool/adapter/runtime | `ports.py` e service por DI | `test_capability_package_has_no_concrete...`, integration boundary scan | COVERED |
| Child Execution por porta pública | `ChildExecutionPort`, `CreateChildExecution` | child lifecycle test | COVERED |
| Child sem Context integral/segredo/permissão herdada | `ChildExecutionContext` mínimo | `assert not hasattr(..., "execution_id")` + child lifecycle | COVERED |
| Interseção de autorização por step | `CapabilityAuthorizationPort`/`DefaultCapabilityAuthorization` | `test_step_authorization_is_intersection...` | COVERED |
| Conteúdo externo não expande grants/argumentos | `StructuredValue` e bindings declarados | bounded structured value + authorization denial | COVERED |
| Chave determinística de retry | service `capability:run/step/attempt` | `test_safe_retry_uses_a_new_deterministic_attempt_key` | COVERED |
| Retry só com efeito seguro | `Retryability`, `EffectState` | safe retry + UNKNOWN test | COVERED |
| UNKNOWN bloqueia retry cego | `ToolFailed(effect_state=UNKNOWN)` | `test_unknown_effect_blocks_retry...` | COVERED |
| Checkpoint sem payload/secret/handle e com refs | `CapabilityCheckpoint`, `InMemoryCapabilityState` | checkpoint/outbox test | COVERED |
| Checkpoint e state version com stale writer | `CapabilityStatePort` | `test_state_port_rejects_stale_writer...` | COVERED |
| Outcomes sucesso/espera/falha/cancelamento explícitos | outcome union models | lifecycle, waiting, compensation and limits tests | COVERED |
| Waiting Tool/Child com Execution mapping | `_waiting` | `test_tool_waiting_maps...`, child lifecycle | COVERED |
| Compensação explícita, ordenada e autorizada | `_compensate` | two compensation tests | COVERED |
| Falha de compensação permanece visível | `CompensationOutcome.complete=False` | `test_compensation_failure_never_becomes_success` | COVERED |
| Cancelamento propaga para Tool/children | `CapabilityToolCancel`, `CancelChildExecution` | cancellation lifecycle boundary | COVERED |
| Resultado tardio não altera terminal | terminal immutability in `ExecutionControl` + cancel test | full capability suite | COVERED |
| Events mínimos de Capability e outbox após state change | `CapabilityEvent`, `InMemoryCapabilityState._outbox` | checkpoint/outbox and event-name integration tests | COVERED |
| Tool events não duplicados pela Capability | somente `CapabilityToolPort` | boundary scan and lifecycle call assertions | COVERED |
| Persistência limitada a IDs/refs/versions/states/usage/effects | `CapabilityStatePort`/`CapabilityCheckpoint` | checkpoint and state tests | COVERED |
| Integração com RFC 102/Execution | `ExecutionControl` commands | lifecycle integration tests | COVERED |
| Integração com RFC 401/Tool Runtime | structural `CapabilityToolPort` boundary | lifecycle + boundary scan | COVERED |
| Integração com RFC 601/103 | state/outbox façade and bounded events | checkpoint/outbox tests; no concrete DB | COVERED |
| Regra de não acesso a RFC 402/403/405/602 | no direct imports | integration boundary scan | COVERED |
| Regressão completa | repository test suite | `python -m pytest -q` → 633 passed, 6 skipped | COVERED |
| Compilação e whitespace | repository | `python -m compileall -q src tests`, `git diff --check` | COVERED |

## Skips condicionais

`tests/integration/persistence/test_capability_postgres_optional.py` foi executado e ficou `skipped` porque `AGENTOS_TEST_POSTGRES_DSN` não está configurado. Não há runtime/engine opcional de Capability no repositório para simular; a integração usa a porta estrutural e o teste de boundary.

## Auditoria de falsos positivos no scan

Os termos `execution`, `capability`, `checkpoint`, `child`, `compensation`, `permission`, `tool` e `runtime` aparecem como nomes de contratos públicos normativos. `InMemoryCapabilityState` e `InMemoryCapabilityRegistry` são adapters de referência explicitamente injetados em testes; nenhum adapter concreto de Tool, banco, fila, Runtime do Kernel, Browser, Filesystem, Resource ou Artifact é importado ou instanciado pelo serviço.

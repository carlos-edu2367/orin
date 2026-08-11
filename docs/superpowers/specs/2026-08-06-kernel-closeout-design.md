# AgentOS Kernel Closeout — Especificação agregada

**Estado:** fechamento formal concluído dentro do escopo de referência/in-memory, com limitações de produção explícitas
**Escopo:** `Execution`, `Events/outbox`, `Context`, `Providers/Model Catalog`, `Runtime` e integração com o Agent RFC 201.

## Objetivo e decisão de escopo

Fechar formalmente os cinco subsistemas implementados, confrontando contratos públicos, adapters em memória, integração e requisitos normativos das RFCs 050, 060, 101–104, 201, 501, 502 e 601. O resultado deve distinguir o que é domínio entregue, o que é adapter de teste e o que continua sendo infraestrutura futura.

Não serão criados Orchestrator, Multi-agent, Memory, Blackboard, Tools, Capabilities, Artifact Storage, Workspaces, API, SSE, workers, Scheduler, PostgreSQL, SQLAlchemy, Redis, broker, filesystem, HTTP client ou SDK de Provider.

## Inventário inicial

| Subsistema | Contratos/modelos | Adapter/serviço | Testes existentes | Estado inicial |
| --- | --- | --- | --- | --- |
| Execution | `src/agentos/execution/models.py`, `ports.py`, `events.py` | `control.py`, `in_memory.py` | `tests/unit/execution/*` | parcial: máquina de estados e outbox conceitual presentes; aquisição, sinais e escopo de idempotência precisam de reforço |
| Events | `src/agentos/events/models.py`, `ports.py` | `in_memory.py`, `security.py`, `compat.py` | `tests/unit/events/*`, integração de Execution | parcial: envelope canônico, entrega, archive e replay presentes; query estreita, cursor de outbox e validações precisam de reforço |
| Context | `src/agentos/context/models.py`, `ports.py` | `service.py`, `compat.py` | `tests/unit/context/*` | parcial: pipeline determinístico e efêmero presente; validações de contrato, proveniência e atualização de turno precisam de reforço |
| Providers | `src/agentos/providers/models.py`, `ports.py` | `catalog.py`, `resolver.py`, `provider.py`, `compat.py` | `tests/unit/providers/*` | parcial: catálogo, seleção e normalização presentes; perfil, custo, fallback e validação pré-efeito estão incompletos |
| Runtime | `src/agentos/runtime/models.py`, `ports.py` | `service.py` | `tests/unit/runtime/*` | parcial: loop público e controles presentes; accounting de outcomes, indeterminate, timeout e integração canônica precisam de reforço |
| Agent RFC 201 | `src/agentos/agents/*` | `in_memory.py`, `compat.py` | `tests/unit/agents/*` | completo dentro do escopo atual; preservar fronteira e testes, adicionando somente prova transversal necessária |

## Matriz requisito → implementação → teste → lacuna

| Requisito normativo | Implementação atual | Evidência atual | Lacuna/decisão de fechamento |
| --- | --- | --- | --- |
| Execution: transições, terminais e idempotência | `execution/control.py` + `in_memory.py` | `test_execution_control.py` | manter transições; escopar chave de idempotência por ownership completo e validar contexto/outbox no adapter |
| Execution: aquisição única e ownership | `ExecutionControl.acquire`, versão otimista | lifecycle/runtime tests | reforçar teste de dois workers e preservar `worker_ref` como contrato; lease persistente continua fora de escopo |
| Execution: accounting/outbox atômicos | `CommitExecutionChanges`, `TransactionRequest` | integration + control tests | validar forged transaction e `UNKNOWN`/`inspect_commit`; adapter continua apenas em memória |
| Execution: payload seguro | `execution.events.EventEnvelope` | falha sanitizada parcial | limitar envelope legado e documentar adapter de compatibilidade para o envelope canônico de Events |
| Events: envelope e payload mínimo | `events.models.EventEnvelope`, `security.py` | contract/event tests | validar `event_type` canônico, ownership e bounds; não duplicar o contrato além do adapter legado justificado |
| Events: outbox após commit, cursor/lease, retry | `InMemoryOutboxPublisher` | `test_outbox_publisher.py` | não avançar posição sobre pendência/falha; preservar `event_id`; lease físico continua fora de escopo |
| Events: ao-menos-uma-vez, delivery_id, ACK | `InMemoryEventBus` | event bus tests | adicionar prova de ACK tardio/duplicata e manter dedupe por consumidor |
| Events: ordenação, atraso, lacuna | `_is_ready`, `SequenceGap` | event bus tests | manter bloqueio explícito e cobrir evento atrasado/duplicado/lacuna |
| Events: archive/query/replay autorizado | `InMemoryEventArchive` | archive tests | query deve respeitar Agent/Execution do contexto; IDs desconhecidos não devem vazar `KeyError` tecnológico |
| Context: fontes públicas e ownership | `ContextManagerService` | pipeline tests | validar tipos/proveniência e não introduzir store concreto |
| Context: orçamento, prioridade, dependência determinística | `_allocate`, `_dependency_depths` | pipeline tests | adicionar reserva/limite de categoria e item obrigatório após transformação |
| Context: proveniência, cutoff, sanitização, referência | `_prepare_candidate`, manifest | pipeline tests | exigir/validar campos canônicos e provar que manifest não copia conteúdo |
| Context: apply_turn/cancel/finalize | `apply_turn`, `finalize` | lifecycle tests | cobrir idempotência/conflict e que uso do turno não vira Memory |
| Providers: descriptors/revisions/status | `catalog.py`, frozen models | catalog tests | validar binding/provider, transições e reativação por nova revisão |
| Providers: hard constraints antes de score | `resolver.py` | resolver tests | aplicar perfil/purpose/capabilities completas antes de ranking |
| Providers: seleção determinística e snapshot | `ModelResolverService` | deterministic test | usar custo estimado real, pin de catálogo/policy e snapshot íntegro |
| Providers: fallback explícito sem ampliar escopo | `resolve_fallback` | fallback test | respeitar categorias, tentativas, provider status e budget acumulado; política não materializada permanece limitação explícita |
| Providers: outcomes/indeterminate/stream | `providers.models`, validator | provider tests | validar request pré-efeito, capacidades, imagem/tool/format e preservar outcome indeterminado |
| Runtime: aquisição/ownership/lifecycle | `RuntimeService` | lifecycle/security tests | manter porta única e adicionar prova transversal com Agent config |
| Runtime: Context→Resolver→Provider→Action | `_run_loop` | loop tests | preservar ports e garantir que falhas não virem sucesso |
| Runtime: timeout/budget/usage/checkpoint/recovery | `_pre/post_effect_budget`, checkpoints | limits/recovery tests | accounting de user-input/cancel/indeterminate, timeout pós-efeito e recuperação segura |
| Agent: config version e agente suspenso | `agents.compat`, registry | agent compat/resolution tests | provar integração sem alterar domínio Agent |
| RFC 601: confirmação e outbox conceitualmente atômicas | `ExecutionControl`/in-memory persistence | integration tests | manter adapter como fake de teste; nenhum store concreto será criado |
| Fronteiras | portas e scans existentes | boundary tests | scan final obrigatório deve ter zero matches nos cinco diretórios |

## Arquitetura de fechamento

As correções serão pequenas e atravessarão as portas existentes:

1. `ExecutionControl` continuará sendo a única fachada mutante. A persistência em memória validará contexto completo, versão esperada, outbox correspondente e indeterminação sem alterar a semântica pública.
2. `Events` continuará sendo o contrato canônico. `execution.events` será tratado como envelope legado de compatibilidade e sua conversão para `events.models.EventEnvelope` permanecerá explícita e testada.
3. `ContextManagerService` continuará recebendo somente `ContextSource`, recorder, policy, clock e cancellation injetados.
4. `ModelResolverService` aplicará constraints e preferências públicas do catálogo antes do score e materializará apenas candidates já autorizados.
5. `RuntimeService` continuará sem `EventBus` ou persistence, consolidando todo efeito por `ExecutionControl` e mantendo outcomes distintos.

## Estratégia de testes

Cada correção de produção terá um teste de regressão escrito primeiro, execução RED observada, implementação mínima GREEN e suíte do subsistema. A integração será provada com fakes das portas, nunca com infraestrutura concreta. O Agent RFC 201 receberá apenas testes de integração/compatibilidade necessários para demonstrar `agent_config_version` e bloqueio de Agent não ativo.

## Limitações normativas restantes

- `InMemoryTransactionalPersistence`, `InMemoryEventBus`, `InMemoryEventArchive` e `InMemoryModelCatalog` são adapters de referência/teste; não provam PostgreSQL, broker, lease distribuído, retenção física ou recuperação de produção.
- A política física de outbox, cursor/lease, retry scheduler e durabilidade do catálogo permanecem fora do escopo por RFC 601 e pelas restrições desta sessão.
- Não será implementada política futura de Memory: Context continua efêmero e nenhum `apply_turn` grava Memory.
- Fallback `POLICY` somente poderá ser afirmado como coberto se houver política pública materializada; sem esse port, ele permanece limitação documentada, não será simulado por descoberta ilimitada.

## Critério de aceite

O fechamento só é declarado quando a matriz tiver evidência de implementação/teste para todas as linhas dentro do escopo, as limitações acima estiverem explícitas, os planos anteriores forem atualizados honestamente e as verificações obrigatórias produzirem evidência fresca.

## Status final e evidência fresca (2026-08-06)

| Subsistema | Status final | Limitação explícita |
| --- | --- | --- |
| Execution | completo no contrato público e adapter de referência | lease distribuído e persistência PostgreSQL não implementados |
| Events | completo no envelope, archive, replay, bus e outbox de referência | broker, retenção física e lease de produção não implementados |
| Context | completo no pipeline efêmero, proveniência e `apply_turn` | Memory e store persistente não implementados |
| Providers | completo nos contratos, catálogo, seleção, snapshot, custo acumulado, fallback explícito e validação pré-efeito | `FallbackMode.POLICY` sem policy port materializado permanece rejeitado/documentado; SDKs/network não implementados |
| Runtime | completo no loop público, accounting, timeout pós-efeito e outcomes canônicos | workers, scheduler e persistência de produção não implementados |
| Agent RFC 201 | preservado e completo no escopo original | adapter continua em memória |

Evidência fresca: `python -m pytest -q` → `326 passed, 1 skipped`; `python -m compileall -q src tests` → exit 0; o scan obrigatório de dependências proibidas → exit 1 sem matches; `git diff --check` → exit 0. Nenhuma infraestrutura concreta foi criada. O working tree permanece com alterações intencionais desta linha de trabalho e artefatos anteriores do usuário; nenhum commit foi criado nesta sessão.

As regressões finais cobrem, entre outras, auditoria transacional com escopo e versão, inspeção de commit desconhecido sem contexto opcional, enums/proveniência de Context, idempotência de catálogo por contexto, binding/imagem/tool pré-efeito, rejeição explícita de fallback por política não materializada, custo consumido acumulado no fallback e commit incerto após falha de ação. O próximo trabalho normativo é a implementação de adapters de produção para persistência, outbox/broker, leases, workers/scheduler, retenção e providers concretos; esses itens não fazem parte deste fechamento.

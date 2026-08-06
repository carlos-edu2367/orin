# Task 9 — Relatório

## Status

Concluído e verificado. As três RFCs normativas de Workers, Scheduler e Observabilidade foram criadas exclusivamente em Markdown. Nenhum Worker, fila, Scheduler, backend, schema, configuração executável ou código de produção foi implementado.

## Arquivos

- `docs/architecture/800-operations/801-workers.md`
- `docs/architecture/800-operations/802-scheduler.md`
- `docs/architecture/800-operations/803-observability.md`

## Resumo

- A RFC 801 separa pools `AGENT`, `BROWSER`, `MAINTENANCE` e `SCHEDULER`, define filas particionadas, fairness, admissão, backpressure, concorrência, leases com fencing, retries, quarentena, cancelamento cooperativo e recuperação a partir de estado durável.
- A RFC 801 distingue redelivery de transporte, retry operacional dentro da mesma `Execution` e retry de domínio que cria nova `Execution`; fila, lock, heartbeat e sinal Redis permanecem coordenação efêmera, nunca fonte de verdade.
- A RFC 802 define `FUTURE_EXECUTION`, `SKILL_RECURRENCE`, `WATCHDOG` e `MAINTENANCE`, com agenda e ocorrência duráveis, disparo pelo menos uma vez, materialização idempotente de exatamente uma `Execution` por ocorrência, timezone IANA, política DST, misfire, catch-up e overlap limitados.
- A RFC 802 faz Scheduler detectar e despachar, sem executar carga nem mutar diretamente o domínio; autorização é reavaliada a cada ocorrência e edição não reescreve histórico.
- A RFC 803 define logs estruturados, métricas com cardinalidade controlada, tracing assíncrono, auditoria projetada de Events, uso/custo de modelos com pricing snapshot versionado e reconciliação.
- A RFC 803 define reconstrução integral e autorizada de `Execution` por estado, Events, receipts, manifests e referências, distinguindo evidência confirmada, inferência, lacuna e redaction sem reexecutar efeitos nem expor conteúdo sensível.
- As três RFCs usam contexto sensível completo com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.

## Correções da revisão

- P1 — Scheduler: `ScheduledTarget` virou união discriminada com mapeamento fechado `TASK/SKILL -> AGENT`, `WATCHDOG -> SCHEDULER` e `MAINTENANCE_ROUTINE -> MAINTENANCE`; não existe alvo direto para Browser e Scheduler Worker é explicitamente proibido de iniciar Runtime, Task ou Skill.
- P1 — Estados: máquinas de `Schedule` e `ScheduleOccurrence` agora enumeram transições, owners, estados terminais, operações de pause/resume/cancel/edit/claim/materialize/dispatch/retry/reconcile, versões esperadas e fences contra Worker obsoleto.
- P1 — Custos: o tipo inexistente `ProviderReceiptRef` foi removido. Usage usa `ProviderInvocationId` e `ProviderTerminalRef`, com unicidade `(provider_invocation_id, provider_terminal_ref)`, histórico `(usage_id, version)` e estados explícitos `CONFIRMED`, `ESTIMATED` e `UNAVAILABLE` sem converter ausência em zero.
- P2 — Workers: expiração de `WorkItem` foi formalizada para fila e lease ativo; `ExecutionControl` decide timeout/terminal, reason code é durável e o outcome determina ack, novo attempt, quarentena ou recovery.
- P2 — Despacho: `Dispatch` é a decisão lógica; `DispatchAttempt` identifica cada retry operacional. Redelivery preserva ambos, retry operacional cria apenas novo attempt e retry de domínio cria nova `Execution` e novo dispatch.
- P2 — Timezone: `Schedule.timezone` é a fonte única; cada ocorrência registra versão da base tz, horário local solicitado/efetivo, offset e instante UTC após política DST.
- P1 residual — Reconciliação: `ScheduleReconciliationReceipt` agora discrimina takeover, retorno a `PLANNED`, materialização confirmada, dispatch confirmado/retry, falha, pendência, estado já reconciliado e conflito. Toda reconciliação mutável persiste e devolve fence estritamente maior; materialização/dispatch incertos são inspecionados sem duplicar Execution ou dispatch. `PAUSED -> EXPIRED` foi incluída quando `ends_at` passa.
- P1 residual — Expiração concorrente: `DispatchAttempt` ganhou `version`; `ExpireWorkItem` exige `expected_attempt_version` e, para attempt com lease, `lease_id`/`fencing_token`. As transições por CAS definem quem vence reserve, renewal, ack, release, expiry ou quarantine.
- P2 residual — Roteamento global: `SubmitExecutionWork` agora recebe a união discriminada `DispatchableWork`, cujo mapa fechado vincula Agent, Browser, Maintenance e Scheduler ao único pool permitido. Destino divergente é rejeitado antes da persistência e novamente na materialização da fila.

## Verificações

- 3 de 3 RFCs esperadas presentes; nenhum arquivo não Markdown criado no diretório `800-operations`.
- Seções obrigatórias verificadas: objetivo, fora de escopo, responsabilidades, arquitetura, contratos tipados, dados/persistência, eventos, fluxo normal, falhas/timeout/recovery, cancelamento, segurança, observabilidade, invariantes, extensibilidade e futuro.
- Requisitos da RFC 801 verificados: quatro pools, filas, backpressure, concorrência, retries, locks/leases/fencing, cancelamento, recovery, isolamento e recuperação após perda de coordenação efêmera.
- Requisitos da RFC 802 verificados: futuras, recorrências de Skill, watchdogs, manutenção/limpeza, timezone/DST, semântica de disparo, idempotência, misfire, catch-up, overlap e recovery.
- Requisitos da RFC 803 verificados: logs estruturados, correlação, métricas, tracing, auditoria por eventos, custos de modelo e reconstrução integral de `Execution`.
- Cenários operacionais verificados: fila indisponível, reinício de Worker, Execution órfã, commit/ack incerto, evento duplicado, timeout de Resource, clock drift, telemetria degradada e auditoria pós-incidente.
- Correções verificadas: mapeamento target/pool fechado, máquinas de estado e owners, expected versions/fences, usage por terminal público único, expiração com `ExecutionControl`, separação dispatch/attempt e timezone canônico com evidência local/UTC.
- Achados residuais verificados: receipt de reconciliação por estado, fence crescente no takeover/recovery, `PAUSED -> EXPIRED`, CAS versionado de attempt com lease/fence e união global `work_kind -> target_pool`.
- Campos sensíveis verificados nos três contratos de contexto: `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.
- Links relativos verificados; nenhum destino ausente.
- Nenhum marcador provisório encontrado.
- Nenhum código executável ou arquivo não Markdown adicionado.

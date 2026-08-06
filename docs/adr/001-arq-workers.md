# ADR 001 — ARQ para Workers

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS executa trabalho de longa duração fora da borda de API: `Execution`s de agentes, automação isolada de browser, manutenção, publicação de eventos e agendamento. A API deve apenas traduzir comandos e consultas; não pode executar o Runtime. O sistema também precisa de filas, admissão, backpressure, retries, cancelamento cooperativo, isolamento de pools e recuperação após reinício.

A arquitetura aprovada adota asyncio no Runtime, Redis para coordenação efêmera e quatro pools lógicos: `AGENT`, `BROWSER`, `MAINTENANCE` e `SCHEDULER`. A escolha de Worker precisa ser compatível com esse modelo sem transformar fila, lease ou heartbeat em estado de domínio.

## Decisão

Usar **ARQ** como adapter inicial de execução assíncrona e de materialização de trabalho para os Workers do AgentOS. ARQ usa Redis para filas e deve operar atrás das portas de `DispatchCoordinator` e `WorkQueue`; o Runtime e os domínios não conhecem ARQ diretamente.

ARQ deve materializar somente referências opacas e dados operacionais mínimos. `Execution`, `Dispatch`, `DispatchAttempt`, idempotência, checkpoints, transições, custos e Events continuam duráveis no PostgreSQL. Um Worker recupera a `Execution` por porta autorizada, revalida ownership, versão, cancelamento e fence, e só reconhece trabalho depois de conhecer o resultado durável.

Cada pool recebe processo, credenciais, limites e políticas de isolamento próprios. O pool `BROWSER` é o único autorizado a executar automação de browser; `SCHEDULER` detecta e despacha ocorrências, mas não inicia Runtime nem executa carga de usuário. Redelivery de transporte, retry operacional e retry de domínio mantêm as identidades e os efeitos definidos pela RFC 801.

## Consequências

### Benefícios

- Integra uma fila assíncrona simples ao ecossistema Python/asyncio adotado.
- Separa a API do trabalho pesado e permite dimensionar pools de forma independente.
- Oferece base operacional para prioridades, backpressure, cancelamento e recuperação sem acoplar o Runtime ao transporte de fila.
- Mantém a substituibilidade: outro adapter de fila pode implementar as mesmas portas no futuro.

### Custos e falhas aceitas

- Redis pode ficar indisponível, perder itens efêmeros, entregar em duplicidade, atrasar mensagens ou perder leases durante failover e partições.
- ARQ não fornece exatamente-uma-vez, transação distribuída com PostgreSQL, garantia de ordenação global nem exclusão mútua durável.
- Um ack incerto, um reinício de Worker ou dois detentores aparentes de lease exigem idempotência, versões, fencing, outbox e reconciliação duráveis; não podem ser resolvidos pela fila.
- A operação passa a exigir monitoramento de backlog, idade, starvation, retries, quarantine, leases e capacidade por pool.

### O que esta decisão não resolve

Esta decisão não define funções de Worker, schemas de fila, autoscaling, infraestrutura de Redis, semântica do Scheduler ou ciclo de vida da `Execution`. Também não autoriza fila a decidir autorização, ownership, terminal de `Execution` ou retenção de dados.

## Alternativas consideradas

- **Executar no processo da API:** rejeitada porque mistura transporte e Runtime, bloqueia a borda e não oferece isolamento ou recuperação apropriados.
- **Executar síncronamente no cliente:** rejeitada porque o cliente não é confiável nem é proprietário de estado ou recursos de servidor.
- **Broker diferente desde o início:** adiado. Pode ser adotado por adapter futuro, mas aumenta o custo operacional sem invalidar os contratos de despacho necessários agora.
- **Usar Redis/ARQ como fonte de verdade:** rejeitada porque perda de coordenação não pode apagar ou reescrever estado de domínio.

## Relações com RFCs

- [RFC 102 — Ciclo de vida da Execution](../architecture/100-kernel/102-execution-lifecycle.md) é a autoridade das transições de `Execution`.
- [RFC 103 — Sistema de eventos](../architecture/100-kernel/103-event-system.md) define publicação e deduplicação de Events.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) define a autoridade durável e a outbox conceitual.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) define pools, despacho, retries, fencing e recuperação.
- [RFC 802 — Scheduler](../architecture/800-operations/802-scheduler.md) delimita o trabalho agendado.

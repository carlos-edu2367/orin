# ADR 013 — asyncio como base de concorrência do processo

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

Runtime, adapters e Workers realizam I/O concorrente, streaming, cancelamento cooperativo e deadlines. A concorrência de processo precisa de uma base comum sem transformar task, coroutine ou event loop em estado durável ou contrato de domínio.

## Decisão

Adotar **asyncio** como base inicial de concorrência nas implementações Python. Coroutines, tasks, queues e cancellation de asyncio permanecem detalhes internos de adapters e Workers. Toda operação longa continua vinculada a uma `Execution`, usa deadlines tipados e estabiliza efeitos antes de confirmar terminal. Cancelar uma task não equivale a confirmar `ExecutionState = CANCELLED`; o Runtime aplica a RFC 102 e persiste a decisão pela fronteira da RFC 601.

## Consequências

- A implementação compartilha um modelo coerente para I/O, streaming e cancelamento cooperativo.
- Código bloqueante deve ser isolado em Worker, processo ou adapter apropriado.
- Falhas, cancelamento e timeout de coroutine precisam ser normalizados em outcomes públicos.
- O event loop não é fonte de verdade, scheduler de domínio nem mecanismo de recuperação.

## Alternativas consideradas

- **Threads como modelo primário:** rejeitadas como padrão para o perfil majoritariamente orientado a I/O.
- **Misturar múltiplos runtimes assíncronos no Kernel:** rejeitado por aumentar semânticas de cancelamento e integração.
- **Persistir tasks de asyncio:** rejeitado porque estado de processo é efêmero e não recuperável.

## Relações com RFCs

- [RFC 101 — Runtime](../architecture/100-kernel/101-runtime.md) define o loop sem impor primitivas de linguagem.
- [RFC 102 — Ciclo de vida da Execution](../architecture/100-kernel/102-execution-lifecycle.md) governa cancelamento e terminalidade.
- [RFC 801 — Workers](../architecture/800-operations/801-workers.md) define execução operacional e recuperação.
- [ADR 001 — ARQ para Workers](001-arq-workers.md) escolhe o adapter inicial de filas.


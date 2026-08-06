# ADR 009 — Redis para coordenação efêmera

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

Workers, API e Scheduler precisam trocar sinais de baixa latência para filas, pub/sub, sessões server-side, locks, leases, cancelamento e coordenação temporária. Esses mecanismos aceleram o sistema, mas podem expirar, ser perdidos, duplicados ou divergir durante reinício, failover e partição. O domínio precisa continuar recuperável quando toda a infraestrutura efêmera desaparece.

## Decisão

Usar **Redis** exclusivamente para coordenação efêmera: materialização de fila, pub/sub, sessão server-side, locks e leases, sinais de cancelamento, estado de coordenação — inclusive chaves temporárias de deduplicação — e projeções descartáveis autorizadas. Deduplicação permanece parte desse estado efêmero, não um novo store. Toda chave deve ser namespaced por ambiente e tenancy, ter TTL ou regra explícita de expiração e conter apenas referências opacas e contexto operacional mínimo.

Redis não é fonte de verdade, ledger de auditoria, arquivo histórico, store de segredos duráveis, conteúdo de Artifact ou substituto de transação. A existência, autorização, ownership, versão e terminal de uma entidade são decididos por PostgreSQL ou pela porta durável proprietária. Locks Redis reduzem contenção, mas todas as escritas sensíveis devem revalidar estado, versão e ownership duráveis e usar fencing quando aplicável.

Após perda de Redis, filas, sinais e projeções são recriados de `Execution`s elegíveis, outbox e registros duráveis. Sessões podem ser invalidadas; locks antigos são considerados expirados; um sinal de cancelamento perdido não supera o pedido durável de cancelamento.

## Consequências

### Benefícios

- Oferece coordenação de baixa latência para Workers, clientes e Scheduler sem carregar a base transacional.
- Permite filas, pub/sub, cancelamento cooperativo e sessões com descarte e reconstrução explícitos.
- Mantém adapters de Runtime livres de acesso direto à infraestrutura concreta.

### Custos e falhas aceitas

- A indisponibilidade, evicção, expiração, failover, partição ou perda total de Redis interrompe coordenação e pode invalidar sessões ou atrasar trabalho.
- Pub/sub pode perder notificações; filas podem redeliver; locks podem ter dois detentores aparentes; TTL não equivale a conclusão de domínio.
- Operação exige políticas de TTL, namespacing, isolamento de credenciais, monitoramento de memória, conexões, lag, backlog, leases e cancelamentos.
- Recuperação depende de reconciliação e outbox, podendo introduzir atraso e trabalho repetido deduplicável.

### O que esta decisão não resolve

Redis não torna efeitos externos idempotentes, não fornece atomicidade com PostgreSQL, não garante exclusão mútua durável, não preserva histórico para auditoria e não autoriza acesso a recursos. Também não substitui backup, recuperação do banco ou controles de segurança do store proprietário.

## Alternativas consideradas

- **PostgreSQL para todas as filas, locks e sinais:** rejeitada como padrão inicial por não oferecer a mesma coordenação efêmera de baixa latência e por sobrecarregar a fronteira transacional.
- **Redis como system of record:** rejeitada porque estado recuperável, auditoria e decisões de domínio não podem depender de TTL ou memória efêmera.
- **Broker, cache e gerenciador de sessão distintos desde o início:** adiado; pode ser introduzido por adapters se requisitos operacionais crescerem, preservando as mesmas fronteiras.
- **Locks sem versão ou fencing durável:** rejeitada porque expiração e partições podem produzir escritores obsoletos.

## Relações com RFCs

- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) limita Redis a coordenação efêmera e define recuperação.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) usa estado efêmero autorizado para bindings de stream.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define sessões server-side, revogação e isolamento.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) define filas, leases, locks, cancelamento e fencing.
- [RFC 802 — Scheduler](../architecture/800-operations/802-scheduler.md) depende de coordenação reconstruível para despacho e watchdogs.

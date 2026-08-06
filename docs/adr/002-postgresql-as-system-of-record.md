# ADR 002 — PostgreSQL como system of record

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS precisa preservar a história e o estado autorizador de Agents, `Execution`s, memória, Workspaces, configurações, referências, auditoria e Events mesmo quando processos, filas, caches ou conexões falham. Mudanças de domínio e fatos observáveis associados precisam ter uma fronteira de confirmação comum, com concorrência, idempotência, retenção, backup e recuperação verificáveis.

## Decisão

Usar **PostgreSQL** como a única fonte transacional de verdade para o estado durável de domínio. Ele é a autoridade para existência, ownership, versão e transição das entidades persistentes, e registra o estado e a entrada de outbox do mesmo fato na mesma transação conceitual.

Toda operação que modifica um invariante durável deve revalidar `user_id`, `workspace_id` quando aplicável, versão esperada, estado e propósito. Chamadas a Provider, Artifact Storage, filesystem, Redis ou outros sistemas não ficam abertas em transações longas: usam reserva durável, estados intermediários explícitos, outbox, inspeção e compensação idempotente.

Conteúdo binário e volumoso permanece sob `ArtifactStorage`; Redis permanece somente como coordenação efêmera. Réplicas, índices, caches e projeções podem acelerar leitura, mas não decidem existência, autorização ou versão sem revalidação no store autoritativo.

## Consequências

### Benefícios

- O estado pode ser recuperado e auditado após perda total de Workers, Redis ou projeções derivadas.
- Transações, controle de concorrência, versões e outbox permitem preservar invariantes e publicar fatos sem perder a intenção confirmada.
- Backup, restauração e reconciliação têm uma referência consistente para `Execution`s e demais entidades.

### Custos e falhas aceitas

- O banco exige migrações, índices, monitoramento de conexões, capacidade, backups criptografados, testes de restauração e procedimentos de recuperação.
- Conflitos de serialização, deadlocks, timeouts, commit indeterminado e indisponibilidade são falhas operacionais possíveis; retries só são seguros para comandos idempotentes e orçados.
- Commit e publicação externa não são uma transação distribuída: uma outbox atrasada gera atraso e replay, não confirmação instantânea no consumidor.
- Escalabilidade de escrita, retenção e consultas históricas requerem particionamento, arquivamento ou réplicas futuros, preservando a mesma autoridade.

### O que esta decisão não resolve

PostgreSQL não substitui fila de trabalho, stream para cliente, cache, sessão, lock efêmero, storage de bytes ou Provider. Também não garante por si só entrega exatamente uma vez, sucesso de um efeito externo, disponibilidade multi-região ou que um consumidor tenha processado um Event.

## Alternativas consideradas

- **Redis como banco principal:** rejeitada por ser efêmero e não oferecer a recuperação, auditoria e fronteira transacional requeridas.
- **Event sourcing integral como fonte primária:** adiado; aumenta complexidade de projeções e evolução sem necessidade de substituir o estado versionado e a outbox definidos nesta fase.
- **Banco documental como store principal:** rejeitado nesta decisão porque as relações, transações e concorrência do domínio exigem autoridade relacional consistente.
- **Transação distribuída entre banco e todos os adapters:** rejeitada; é incompatível com Providers e recursos externos. O desenho usa estados explícitos e reconciliação.

## Relações com RFCs

- [RFC 103 — Sistema de eventos](../architecture/100-kernel/103-event-system.md) define Events, outbox e semântica de entrega.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) especifica consistência, retenção, backup e recuperação.
- [RFC 602 — Artifact Storage](../architecture/600-platform-data/602-artifact-storage.md) separa referências duráveis de conteúdo.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) consome projeções autorizadas, sem acesso direto ao store.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) reconstrói coordenação a partir de estado durável.

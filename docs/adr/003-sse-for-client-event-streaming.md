# ADR 003 — SSE para eventos destinados ao cliente

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O cliente precisa acompanhar mudanças de `Execution`s e outros fatos autorizados quase em tempo real, inclusive durante trabalho prolongado. Esse fluxo é de observação: o cliente envia comandos e consultas pela borda de aplicação, enquanto Events de domínio são produzidos pelo Kernel e publicados de modo assíncrono. A interface não pode ler banco, outbox ou broker diretamente, nem deve ganhar capacidade de publicar Events de domínio.

## Decisão

Usar **Server-Sent Events (SSE)** como transporte inicial unidirecional para projeções de Events destinadas ao cliente. O Gateway abre e retoma streams autorizados por uma porta de aplicação; SSE não contém regra de negócio, não recebe comandos e não mantém estado de domínio.

Cada stream deve ter seleção canônica, não vazia e imutável, filtro permitido, `stream_id` opaco, cursor seguro, versão de autorização e epoch de revogação. A entrega é ao-menos-uma-vez e deduplicável por `event_id`; reconexão usa cursor. Cursor anterior ao piso de retenção exige ressincronizar consultando o estado atual e abrir novo stream. O servidor reaplica autorização no recurso concreto, limita buffers, batches e duração, e encerra cliente lento ou revogado com possibilidade segura de retomada.

## Consequências

### Benefícios

- Compatível com o fluxo predominantemente servidor-cliente de acompanhamento de `Execution`.
- Mantém a interface simples, baseada em HTTP, e separa comandos de observação.
- Permite reconexão por cursor e observação de Events sem expor infraestrutura interna.
- A camada de aplicação pode redigir, filtrar e autorizar a projeção antes da entrega.

### Custos e falhas aceitas

- SSE mantém conexões abertas e demanda limites de conexão, buffer, bytes, heartbeats e monitoramento de lag.
- Redes, proxies, reinícios do Gateway e clientes lentos podem interromper, repetir ou atrasar entregas; o cliente deve deduplicar e retomar.
- Não há promessa de exatamente-uma-vez, ordenação global, retenção infinita nem entrega quando o cliente está desconectado além da janela de retenção.
- Revogação e reautorização exigem estado efêmero do binding e fencing de entrega; indisponibilidade desse estado falha fechada para dados protegidos.

### O que esta decisão não resolve

SSE não substitui Event Bus, outbox, auditoria, fila de Workers ou consultas de estado. Não é canal bidirecional, não executa agentes, não confirma que o usuário leu um evento e não transfere a fonte de verdade para o cliente.

## Alternativas consideradas

- **WebSocket:** adiado; oferece bidirecionalidade que não é necessária para observação inicial e amplia o ciclo de vida, autorização e operação da conexão. Pode coexistir em necessidade futura específica.
- **Polling periódico:** rejeitado como mecanismo primário por piorar latência e carga e não oferecer cursor/replay de Events de forma natural.
- **Cliente consumir broker/outbox diretamente:** rejeitado por expor infraestrutura, violar autorização por recurso e contornar projeção segura.
- **Long polling:** rejeitado por complexidade operacional semelhante com experiência de reconexão menos adequada ao stream contínuo.

## Relações com RFCs

- [RFC 103 — Sistema de eventos](../architecture/100-kernel/103-event-system.md) define identidade, correlação, retenção e entrega de Events.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) mantém o estado e a outbox autoritativos.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) especifica binding, cursor, autorização, revogação, backpressure e erros públicos.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define sessão, autorização e revogação.
- [RFC 803 — Observabilidade, auditoria e reconstrução](../architecture/800-operations/803-observability.md) define sinais e auditoria da entrega.

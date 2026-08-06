# ADR 011 — FastAPI como adapter inicial da borda HTTP

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS precisa expor comandos, consultas, sessões e streaming autorizado sem executar o Runtime no processo de transporte. A borda deve validar protocolos e converter entradas para contratos da aplicação, enquanto lifecycle, autorização de domínio, idempotência e persistência permanecem nas RFCs proprietárias.

## Decisão

Adotar **FastAPI** como framework inicial do adapter HTTP. FastAPI fica restrito à borda definida pelas RFCs 701 e 702; tipos de request, dependências, middleware e exceções do framework não atravessam portas públicas nem entram no Kernel. Handlers autenticam o transporte, validam o schema externo, invocam portas da aplicação e traduzem resultados para HTTP/SSE. Trabalho longo é despachado para Workers.

## Consequências

- A implementação ganha integração direta com o ecossistema ASGI e schemas de transporte.
- A borda precisa manter tradução explícita entre DTOs HTTP e tipos públicos de domínio.
- Trocar o framework exige novo adapter, não alteração dos contratos do Runtime.
- FastAPI não decide ownership, estado de `Execution`, retry, transação ou autorização de domínio.

## Alternativas consideradas

- **Framework HTTP próprio:** rejeitado pelo custo e risco sem benefício arquitetural.
- **Executar Runtime nos handlers:** rejeitado por acoplar transporte a trabalho durável.
- **Acoplar tipos FastAPI ao domínio:** rejeitado por impedir testes e substituição da borda.

## Relações com RFCs

- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) define a borda de transporte.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define autenticação e autorização.
- [RFC 101 — Runtime](../architecture/100-kernel/101-runtime.md) proíbe dependência do Kernel em FastAPI.


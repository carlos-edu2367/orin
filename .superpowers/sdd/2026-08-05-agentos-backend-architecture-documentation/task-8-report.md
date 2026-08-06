# Task 8 — Relatório

## Status

Concluído e verificado. As duas RFCs normativas de API/SSE e segurança foram criadas exclusivamente em Markdown. Nenhum endpoint, backend, middleware, schema, configuração executável ou código de produção foi implementado.

## Arquivos

- `docs/architecture/700-api-security/701-api-sse.md`
- `docs/architecture/700-api-security/702-security.md`

## Resumo

- A RFC 701 define o Gateway como adapter sem regra de negócio, com comandos idempotentes e versionados para criar/controlar `Execution`, consultas por projeção autorizada, SSE com cursor opaco, reconexão, retenção, reautorização, backpressure, redaction e mapeamento estável de erros.
- A RFC 701 proíbe explicitamente executar Agent, Tool, Capability ou Runtime na API, acessar diretamente estado/broker/outbox e interpretar aceite de comando como início ou conclusão.
- A RFC 702 define sessão server-side em Redis, cookie opaco `HttpOnly`/`Secure`, CSRF vinculado à sessão, PAT persistido somente como hash, autorização deny-by-default por escopo, auditoria, revogação e isolamento por `user_id`/`workspace_id`.
- A RFC 702 define proteção de segredos com AES-256-GCM, nonce único, AAD vinculada ao ownership, data keys envelopadas e `APP_MASTER_KEY` externa a banco, Redis, Artifact Storage, Workspace, backups de dados, logs e código.
- Ambas as RFCs incluem contextos completos para operações sensíveis com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.

## Correções da revisão

- P1 — SSE: reautorização completa agora ocorre no máximo a cada 30 segundos; invalidação de revogação encerra o stream em até 5 segundos, enquanto um `RevocationEpoch` com fence impede qualquer novo write admitido após o commit da revogação. `StreamAuthorizationVersion` ganhou semântica operacional composta, monotônica, ecoada por leitura e registrada em cada batch.
- P1 — Escopo multi-Execution: `StreamOperationContext`, `AuthorizedStreamScope` e `AuthorizedStreamResource` modelam e auditam o conjunto completo. Cada Execution e descendente é validado individualmente, qualquer falha rejeita toda a abertura e `execution_ids` é uma lista não vazia; vazio nunca significa wildcard.
- P2 — Auditoria crítica: `SecurityAuditGate`, classes de disponibilidade e reservas duráveis agora fazem emissão/revogação de credenciais, mudanças de policy/grant/role, proteção/lifecycle de segredo, `unprotect`, decisões privilegiadas e mutações administrativas falharem fechado quando auditoria obrigatória não está disponível.
- P2 residual — Binding SSE: `StreamOperationContext.resource_selection` passou a ser a fonte canônica única dos recursos e `ClientEventFilter` ficou apenas com projeção de tipos/descendentes. `StreamBinding` preserva seleção, filtro, contexto e digests por `stream_id`; leituras não aceitam filtro/lista novos e rejeitam qualquer tentativa de usar B sob autorização de A.

## Verificações

- 2 de 2 RFCs esperadas presentes; nenhum arquivo não Markdown criado no diretório `700-api-security`.
- Seções obrigatórias verificadas em cada RFC: objetivo, fora de escopo, responsabilidades, arquitetura, dados, contratos tipados, eventos, fluxos normal/falha/cancelamento, segurança, observabilidade, invariantes, extensibilidade e futuro.
- Requisitos da RFC 701 verificados: Gateway adapter, ausência de regra de negócio, criação/controle idempotentes de `Execution`, consultas, SSE, cursor, reconexão, autorização, retenção, erro público e proibição de execução de agentes.
- Requisitos da RFC 702 verificados: sessão Redis, cookie HttpOnly, CSRF, PAT somente hash, escopos, AES-256-GCM, `APP_MASTER_KEY` externa, auditoria, isolamento, revogação e rate/abuse limits.
- Correção SSE verificada: limites explícitos de 30 segundos para reautorização e 5 segundos para encerramento, `StreamAuthorizationVersion`, `RevocationEpoch`, fence anterior a novos writes e falha fechada sem freshness.
- Correção de filtro verificada: contexto multi-recurso, conjunto autorizado/digest auditável, validação individual e lista não vazia sem semântica wildcard.
- Correção de auditoria verificada: classes `REQUIRED_PRECOMMIT`, `REQUIRED_PREDELIVERY` e `REQUIRED_DECISION` cobrem credenciais, policy, segredo e mutações administrativas.
- Correção residual verificada: fonte canônica única em `resource_selection`, ausência de seleção no `ClientEventFilter`, binding imutável por `stream_id`, digest esperado e leitura/fechamento sem novo filtro ou conjunto.
- Campos sensíveis verificados nos dois contratos de contexto: `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.
- Links relativos verificados; nenhum destino ausente.
- Nenhum marcador provisório encontrado.
- Nenhum endpoint, status HTTP, código executável ou arquivo não Markdown adicionado.

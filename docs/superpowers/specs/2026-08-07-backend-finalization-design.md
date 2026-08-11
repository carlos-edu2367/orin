# Design — finalização do backend AgentOS

## Decisão

O backend será composto por adapters de produção em torno dos contratos já existentes. O Kernel e os domínios continuam dependentes de portas; a composição local fica numa camada de bootstrap. PostgreSQL é a única autoridade durável e Redis é exclusivamente coordenação efêmera. Nenhum handler HTTP, worker ou adapter de Provider substitui as decisões de lifecycle de `ExecutionControl` ou a fronteira de commit de `TransactionalPersistence`.

## Componentes

1. `tool_runtime`: contratos imutáveis, registro exato por versão, autorização, leases de Resource, controle de invocação, outbox e adaptadores atômicos para filesystem, terminal, browser e Artifact.
2. `configuration`: `pydantic-settings` para ambiente, catálogo de Providers habilitados, handles de segredo redigidos e validação sanitizada de startup.
3. `providers.http`: um cliente HTTPX por Provider que traduz o contrato público para a API externa, normaliza respostas/stream/erros e nunca publica chaves ou payloads integrais.
4. `workers` e `scheduler`: portas e adapters ARQ/Redis que carregam somente IDs opacos, voltam ao PostgreSQL para toda decisão, e suportam lease, fence, reentrega e reconciliação.
5. `api`: FastAPI com DTOs Pydantic e serviços injetados; autenticação de transporte, CSRF/PAT, paginação e SSE. A camada não importa banco nem Redis.
6. `observability`: registrador estruturado e redigido, métricas de cardinalidade limitada, correlação/tracing e projeções autorizadas de Events.
7. `bootstrap`: composição de produção, CLI e checks; depende de PostgreSQL/Redis reais e recusa configuração incompleta.

## Fluxo ponta a ponta

`POST /executions` valida transporte e chama o serviço de aplicação. A criação durável confirma a Execution e sua outbox. O dispatcher materializa apenas a referência no Redis/ARQ. O worker reabre a Execution autorizada, obtém a seleção de modelo e, quando for solicitado, encaminha a Tool ao Tool Runtime. Cada mudança terminal grava estado e Event na mesma transação; o publicador entrega depois do commit. A projeção SSE lê Events autorizados por cursor opaco e interrompe a conexão quando o epoch de revogação muda.

## Falhas e segurança

Todos os limites são aplicados antes e durante I/O. Cancelamento é um comando durável, revalidado pelo worker; falha de transporte, efeito externo incerto ou commit indeterminado vira resultado categorizado, nunca sucesso implícito. Logs, traces, métricas e envelopes usam IDs e códigos sanitizados; não usam segredos, prompts, respostas, DSNs, SQL, URLs, paths ou cookies.

## Alternativas rejeitadas

- Executar Runtime no FastAPI: viola RFCs 101, 701 e ADR 011.
- Usar Redis/ARQ para estado de domínio: viola RFC 601 e ADRs 001/009.
- SDKs de Provider fora dos adapters: viola RFC 501 e ADR 010.
- Adapter in-memory na composição local: impede a recuperação e a operação exigidas.

## Testes

O comportamento novo recebe teste que falha antes da implementação. Transporte de Provider é testado com `httpx.MockTransport`, exclusivamente como teste do adapter — a composição local usa HTTP real. Integração PostgreSQL/Redis é opt-in, condicionada a serviços reais e documenta explicitamente ausências externas.

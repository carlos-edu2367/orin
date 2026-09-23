# OAuth para servidores MCP remotos — design

**Data:** 2026-09-23
**Estado:** design aprovado, aguardando plano de implementação
**Motivação:** conectar o Orin a servidores MCP que exigem login OAuth (o primeiro alvo é o MCP de conteúdo do Auryly, `https://auryly.com/mcp`) sem colar token à mão.
**Relações:** [RFC 903 — MCP](../../architecture/900-extensibility/903-mcp-future.md), `docs/MCP.md`, [plano de conectores](../plans/2026-08-14-mcp-connectors.md) ("o fluxo OAuth completo é seu próprio plano"), [correções de usabilidade](../plans/2026-08-15-connector-usability-fixes.md) ("Causa 4").

## 1. Problema

Um servidor MCP HTTP que segue o [MCP Authorization spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) responde `401` sem token e espera que o cliente descubra o servidor de autorização, registre-se, faça o fluxo com PKCE e renove o token sozinho. O Orin hoje não faz nada disso:

- **O pacote `agentos/oauth` não está ligado a nada.** PKCE, callback loopback, descoberta e cofre de tokens existem (commit a68c277: "not yet wired into any route or the frontend"), mas só os testes os importam.
- **Não há registro dinâmico de cliente (RFC 7591).** `begin_authorization` exige um `client_id` pronto; servidores MCP como o Auryly só emitem `client_id` via `/register`.
- **A descoberta monta a URL errada.** `discovery.py` acrescenta `/.well-known/oauth-protected-resource` *depois* do caminho (`https://auryly.com/mcp/.well-known/...`); o RFC 9728 insere o sufixo entre host e caminho (`https://auryly.com/.well-known/oauth-protected-resource/mcp`). O cabeçalho `WWW-Authenticate: resource_metadata=` é ignorado.
- **Não há parâmetro `resource` (RFC 8707).** O Auryly valida o recurso do token (`validate_token_resource=True`); sem ele o token é recusado.
- **A única autenticação HTTP é um `Bearer` fixo** montado a partir de um segredo literalmente chamado `token` (`mcp/toolset.py:30`). Não há renovação, nem tratamento de `401`, e `McpServerState.ERROR` nunca é gravado.
- **O catálogo promete e não entrega.** Notion e Sentry dizem "O servidor pede autorização na primeira conexão"; hoje a aprovação recebe `401`, vira `502` e o servidor fica pendente para sempre.

## 2. Objetivo e critérios de sucesso

OAuth genérico, pelo spec do MCP, para qualquer servidor MCP HTTP que o exija. Nada específico do Auryly.

1. Adicionar `https://auryly.com/mcp` pela URL, clicar em **Conectar**, aprovar no navegador e ver o servidor `active` com as ferramentas descobertas, sem configurar `client_id`, segredo ou variável de ambiente.
2. Ferramentas do servidor funcionam nos turnos do agente enquanto o acesso for válido; renovação de token é invisível.
3. Um refresh token nunca é usado duas vezes, mesmo com API e worker renovando ao mesmo tempo.
4. Acesso revogado ou refresh recusado: a ferramenta devolve um erro claro ao agente, o servidor vai para `error` com o motivo e um botão **Reconectar** refaz o login.
5. Servidores sem OAuth (sem credencial ou com `token` fixo) e servidores stdio continuam exatamente como hoje.

## 3. Decisões

| Tema | Decisão |
|---|---|
| Escopo | OAuth do spec do MCP para qualquer servidor HTTP. Google Drive (client_id próprio) fica de fora. |
| Recebimento do código | Rota da própria API: `GET /v1/mcp/oauth/callback`. Sem servidor loopback separado. |
| Estado pendente | No banco (`oauth_pending_authorizations`), 10 min, uso único. Sobrevive a reinício da API. |
| Registro do cliente | DCR como cliente público (`token_endpoint_auth_method: none`); aceita `client_secret` se o servidor emitir. Um registro por servidor, refeito se o `redirect_uri` mudar (porta do Orin mudou). |
| Onde vivem os tokens | Tabela `oauth_tokens` existente, `provider_id = server_id`. |
| Renovação concorrente | Lease no banco (`refresh_lease_until` + `version`) entre processos; lock por servidor dentro do processo. |
| Acesso perdido durante um turno | Erro claro ao agente (`MCP_REAUTH_REQUIRED`) + servidor em `error` + **Reconectar** nas configurações. Sem card de reconexão no chat. |
| Como a tela sabe que terminou | Polling de `GET /v1/mcp/servers/{id}` a cada 2 s (padrão já usado no Orin). |

## 4. Componentes

### 4.1 `agentos/oauth` (genérico; não conhece MCP)

- **`discovery.py`** (reescrito)
  - `discover_protected_resource(resource_url, *, www_authenticate=None, client)`: usa o `resource_metadata` do `WWW-Authenticate` se houver; senão tenta `/.well-known/oauth-protected-resource<caminho>` e depois `/.well-known/oauth-protected-resource`. Exige que o campo `resource` do documento seja igual à URL do servidor (RFC 9728 §3.3). Devolve `ProtectedResourceMetadata(resource, authorization_servers, scopes_supported)`.
  - `discover_authorization_server(issuer, *, client)`: para issuer com caminho, tenta `/.well-known/oauth-authorization-server<caminho>`, `/.well-known/openid-configuration<caminho>` e `<issuer>/.well-known/openid-configuration`; sem caminho, as duas primeiras sem sufixo. Exige `issuer` igual ao consultado (RFC 8414 §3.3), `authorization_endpoint` e `token_endpoint`, e `S256` em `code_challenge_methods_supported`. Devolve `AuthorizationServerMetadata(issuer, authorization_endpoint, token_endpoint, registration_endpoint, revocation_endpoint, scopes_supported)`.
  - `parse_www_authenticate(header)`: extrai `resource_metadata`, `scope` e `error` de um desafio `Bearer`.
- **`registration.py`** (novo): `register_client(metadata, *, redirect_uri, client_name="Orin", client)` → `ClientRegistration(client_id, client_secret, token_endpoint_auth_method)`. Pede `grant_types=[authorization_code, refresh_token]`, `response_types=[code]`, `token_endpoint_auth_method=none`. Erro se o servidor não tiver `registration_endpoint`.
- **`flow.py`** (ampliado): `OAuthProviderConfig` ganha `resource: str | None` e `client_secret: str | None`. `resource` vai no authorize, na troca e no refresh. Com `client_secret`, usa `client_secret_post`. Resposta de erro do token endpoint vira `OAuthGrantRejected` quando `error` é `invalid_grant`/`invalid_client`/`unauthorized_client` (reconexão necessária), e `OAuthFlowError` nos demais casos (falha transitória). A regra de `redirect_uri` loopback continua.
- **`token_store.py`** (ampliado): `save` incrementa `version`; `try_acquire_refresh_lease(user_id, provider_id, version, ttl)` e `release_refresh_lease(...)` (ver §5.3). `delete` continua.
- **`callback_server.py`** (removido), com seus testes: o callback passa a ser rota da API.
- Todas as chamadas HTTP deste pacote recebem um `httpx.Client` construído pelo chamador; nenhuma URL vinda de metadados é chamada sem passar pela política de rede (§7).

### 4.2 `agentos/mcp`

- **`auth.py`** (novo) — `McpOAuth`, a fachada usada pela API e pelo worker:
  - `start(user_id, server_id, *, redirect_uri, challenge=None) -> str`: descoberta, registro (se não houver ou se o `redirect_uri` registrado for outro), grava o pendente, devolve a `authorization_url`.
  - `complete(state, *, code=None, error=None) -> McpServerConfig`: valida e consome o pendente, troca o código, grava os tokens. Não ativa o servidor (isso é do serviço).
  - `token_source(user_id, server_id) -> TokenSource`: objeto com `current() -> str` (renova se vence em < 60 s) e `force_refresh(rejected: str) -> str` (usado após `401`).
  - `forget(user_id, server_id)`: revogação best-effort (RFC 7009, se houver `revocation_endpoint`) e remoção de tokens e registro.
- **`transport_http.py`**: construtor aceita `token_source: TokenSource | None` além de `headers`. Com `token_source`, cada pedido leva `Authorization: Bearer <current()>`; um `401` provoca **uma** chamada a `force_refresh` e uma nova tentativa; um segundo `401` sobe. Sem `token_source`, um `401` lança `HttpUnauthorized(www_authenticate)` (subclasse de `HttpTransportError`) em vez da mensagem genérica.
- **`toolset.py`**: `build_client(config, secrets, *, token_source=None)`. Para `auth_kind=oauth`, o worker passa o `token_source`; o caminho `token` fixo não muda. `McpToolProvider` guarda um `threading.Lock` por `server_id` em volta de `_session` e das chamadas (subagentes compartilham o provider em threads). `McpReauthRequired` vira `ToolOutcome` com código `MCP_REAUTH_REQUIRED` e texto "O acesso a {nome} expirou. Reconecte em Configurações → MCP."
- **`service.py`**:
  - `approve`: se a conexão falhar com `HttpUnauthorized`, grava `auth_kind=oauth`, mantém `pending_approval` e lança `McpAuthorizationRequired` (a API responde `409 mcp_authorization_required`).
  - `activate_after_authorization(user_id, server_id, connect)`: descobre ferramentas com o token e põe o servidor em `active` (mesma gravação de ferramentas do `approve`). Aceita servidores em `pending_approval` ou `error`.
  - `mark_reauth_required(user_id, server_id, reason)`: `state=error`, `state_reason` curto.
  - `active_servers` inclui o `auth_kind` no `McpServerConfig`.
  - `remove` chama `McpOAuth.forget` quando `auth_kind=oauth`.
- **`models.py`**: `McpAuthKind` (`none`, `static`, `oauth`) e campo `auth_kind` em `McpServerConfig`. `static` = tem `secret_names`; `none` = nenhum.

### 4.3 API (`api/gateway.py`)

| Rota | Comportamento |
|---|---|
| `POST /v1/mcp/servers/{id}/approve` | Como hoje; novo erro `409 mcp_authorization_required` (`retryable: false`). |
| `POST /v1/mcp/servers/{id}/oauth/start` | Mutável (CSRF/Origin). Monta o `redirect_uri` a partir de `request.base_url` + `v1/mcp/oauth/callback` e recusa se não for loopback. Aceita servidores em `pending_approval` ou `error` com `auth_kind=oauth`. Resposta: `{authorization_url, expires_at}`. Erros: `422 mcp_oauth_unsupported` (sem descoberta, sem DCR, sem S256), `502 mcp_oauth_unreachable`. |
| `POST /v1/mcp/servers/{id}/oauth/cancel` | Mutável. Apaga pendentes do servidor. `204`. |
| `GET /v1/mcp/oauth/callback?code&state` ou `?error&state` | Não mutável (o `state` é a proteção CSRF). Chama `complete` e depois `activate_after_authorization`. Responde HTML fixo: "Conectado. Pode voltar ao Orin." ou a mensagem de erro. Se o `state` identifica um pendente válido e algo falha depois, grava o motivo em `state_reason`; `state` desconhecido, expirado ou repetido não altera nada. |

O `GET /v1/mcp/catalog` e o resumo do servidor não expõem nada de OAuth além de `auth_kind`. Nenhuma rota devolve token, verifier ou `client_secret`.

### 4.4 Frontend

- `api/mcp.ts`: `auth_kind` no resumo; `startMcpOAuth`, `cancelMcpOAuth`; `approveMcpServer` reconhece `mcp_authorization_required`.
- `features/mcp/useMcpOAuth.ts` (novo hook): inicia, abre a URL com `window.open` (no Electron vai para o navegador do sistema), consulta o servidor a cada 2 s até `active`, `error`/motivo novo, cancelamento ou 10 min.
- `McpApprovalCard`: com `mcp_authorization_required` (ou `auth_kind=oauth`), mostra "Este servidor pede login" e **Entrar com {nome}**; durante a espera, "Aguardando autorização no navegador…" e **Cancelar**; ao fim, sucesso ou o motivo.
- `McpServerCard` em `error`: motivo e **Reconectar** (mesmo hook).

## 5. Fluxos

### 5.1 Conectar

1. Usuário adiciona o servidor (URL, sem credenciais) e clica **Conectar** → `approve`.
2. Descoberta sem credencial recebe `401` → `auth_kind=oauth`, `409 mcp_authorization_required`.
3. Tela chama `oauth/start`. A API: descobre o recurso (a partir do `WWW-Authenticate` capturado numa sondagem sem token) e o servidor de autorização; registra o cliente se preciso; gera `state` e verifier; grava o pendente; devolve a URL com `response_type=code`, `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge`, `code_challenge_method=S256`, `resource`.
4. Escopo pedido: o `scope` do `WWW-Authenticate`, senão `scopes_supported` do recurso, senão nenhum (o servidor aplica o padrão).
5. Usuário aprova no navegador → callback. A API confere o `state` (existe, não expirou, é do servidor), apaga o pendente, troca o código (com verifier e `resource`), grava tokens, descobre ferramentas com o token e ativa o servidor.
6. A tela, consultando o servidor, vê `active` e mostra as ferramentas.

Recusa (`error=access_denied`) ou falha na troca: pendente apagado, servidor mantém o estado anterior, `state_reason` explica. `state` desconhecido, expirado ou repetido: página de erro, nada muda.

### 5.2 Usar (worker; também `test` na API)

`token_source.current()` lê os tokens do banco a cada pedido (SQLite local; assim o worker vê o que a API gravou). Se o token vence em menos de 60 s, renova (§5.3). Um `401` com token → `force_refresh` → nova tentativa única.

### 5.3 Renovar sem reusar o refresh token

1. Ler `(tokens, version)`.
2. `UPDATE oauth_tokens SET refresh_lease_until = agora + 30 s WHERE user_id = ? AND provider_id = ? AND version = ? AND (refresh_lease_until IS NULL OR refresh_lease_until < agora)`.
3. Se atualizou 1 linha: chamar o token endpoint; sucesso → `save` com `version + 1` e lease limpa; falha transitória → liberar a lease e subir `MCP_UNAVAILABLE`; `OAuthGrantRejected` → apagar tokens, `mark_reauth_required`, subir `McpReauthRequired`.
4. Se atualizou 0 linhas: outro processo está renovando (ou já renovou). Reler a cada 250 ms por até 10 s até `version` mudar e usar o token novo; se os tokens sumirem, `McpReauthRequired`; se o tempo acabar, `MCP_UNAVAILABLE`.
5. `force_refresh(rejected)` só renova se o token atual ainda for o `rejected`; se outro já trocou, usa o novo sem renovar.

Dentro do processo, o lock por servidor do `McpToolProvider` serializa subagentes; a lease cobre API × worker.

### 5.4 Reconectar e remover

- **Reconectar:** servidor em `error` → `oauth/start` → mesmo fluxo; `activate_after_authorization` o devolve a `active`.
- **Remover:** `forget` (revogação best-effort, falha não bloqueia) e depois a remoção normal. Como `remove` já faz com `mcp_server_tools`, a remoção apaga explicitamente `mcp_oauth_clients`, `oauth_pending_authorizations` e `oauth_tokens` do servidor na mesma transação; a cascata das FKs é só rede de segurança.

## 6. Dados — migração `0045_mcp_oauth`

- `mcp_servers.auth_kind` `String(16)` NOT NULL, default `none`; linhas existentes com `secret_names` não vazio viram `static`.
- **`mcp_oauth_clients`** (1 por servidor): `server_id` PK e FK para `mcp_servers` com `ON DELETE CASCADE`; `issuer`, `authorization_endpoint`, `token_endpoint`, `revocation_endpoint` (nulo), `resource`, `scope` (nulo); `client_id`; `client_secret_ciphertext` (nulo); `token_endpoint_auth_method`; `redirect_uri`; `created_at`, `updated_at`.
- **`oauth_pending_authorizations`**: `state` PK (String 64); `user_id`; `server_id` FK com cascata; `code_verifier_ciphertext`; `redirect_uri`; `expires_at`; `created_at`. Linhas vencidas são apagadas a cada `start`.
- **`oauth_tokens`** ganha `version` Integer NOT NULL default 0 e `refresh_lease_until` (tz, nulo).
- Tudo espelhado em `persistence/postgres/schema.py`, SQL neutro de dialeto (o runtime local é SQLite; os testes de Postgres continuam passando).

## 7. Segurança

- `state`: 256 bits, uso único, preso a `user_id` e `server_id`, 10 min.
- Verifier e `client_secret` cifrados com o `ProviderSecretCipher`, como os tokens. Tokens e segredos nunca vão para log, evento ou resposta da API.
- **SSRF:** toda URL tirada de metadados (documentos de descoberta, registro, token, revogação) passa por `_public_url(..., resolve_dns=True)` e precisa ser `https`, igual ao transporte MCP. A `authorization_url` aberta no navegador também precisa ser `https`. Sem seguir redirects nas chamadas de servidor.
- Verificações do spec: `resource` do documento igual à URL do servidor; `issuer` igual ao consultado; `S256` anunciado.
- `redirect_uri` sempre montado pela API e sempre loopback; nunca vem do cliente.
- Callback: HTML fixo, mensagens escapadas, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.
- Limite do cifrador (4096 caracteres) mantido; token maior falha com mensagem clara em `state_reason`.
- Antes de fechar: revisão de segurança independente do código OAuth (armazenamento, `redirect_uri`, `state`/PKCE, SSRF), como os planos anteriores exigem.

## 8. Erros

| Situação | Resultado |
|---|---|
| Aprovação recebe `401` | `409 mcp_authorization_required`; card oferece login |
| Servidor sem metadados, sem DCR ou sem S256 | `422 mcp_oauth_unsupported` com o motivo |
| Servidor de autorização fora do ar | `502 mcp_oauth_unreachable` |
| Usuário recusa no navegador | Página de erro; `state_reason` "Autorização recusada" |
| Refresh recusado durante um turno | `MCP_REAUTH_REQUIRED` para o agente; servidor em `error` "Reconexão necessária" |
| Rede falha durante refresh | `MCP_UNAVAILABLE`; tokens mantidos |
| Segundo `401` logo após renovar | `MCP_REAUTH_REQUIRED` e `error` |

## 9. Testes

TDD, um commit por etapa.

- **Unidade (`tests/unit/oauth`, `tests/unit/mcp`, `tests/unit/api`)**, com `httpx.MockTransport`:
  - descoberta: caminho inserido, raiz, `WWW-Authenticate`, fallback OIDC, `resource`/`issuer` divergente, sem S256, URL privada recusada;
  - `parse_www_authenticate`;
  - DCR: público, com `client_secret`, sem `registration_endpoint`;
  - `flow` com `resource` e `client_secret_post`; `OAuthGrantRejected` vs `OAuthFlowError`;
  - lease: dois engines no mesmo arquivo SQLite (API × worker), só um renova, o outro recebe o token novo;
  - transporte: `401` → renova → repete uma vez; segundo `401` sobe; `HttpUnauthorized` sem token;
  - toolset: `MCP_REAUTH_REQUIRED`; lock com subagentes;
  - serviço: `approve` → `McpAuthorizationRequired`; `activate_after_authorization`; `mark_reauth_required`;
  - rotas: start (loopback, não loopback), cancel, callback com sucesso, recusa, `state` expirado, repetido e de outro servidor; nenhum segredo nas respostas;
  - migração e schema.
- **Frontend (Vitest):** estados do `McpApprovalCard` e do `McpServerCard` (pede login, aguardando, cancelado, sucesso, erro, reconectar), `useMcpOAuth` com timers falsos.
- **Integração:** servidor MCP falso com OAuth em processo, volta completa: aprovar → `401` → start → callback → `active` → chamada de ferramenta → token vencido → refresh → `invalid_grant` → `error` → reconectar.
- **Manual:** conectar em `https://auryly.com/mcp` com o Orin instalado e usar uma ferramenta de leitura e uma de escrita.

## 10. Documentação

- RFC 903: "Estado da implementação" passa OAuth e `401` de Adiado para Atendido.
- `docs/MCP.md`: seção "Servers that ask you to sign in".
- Este documento.

## 11. Fora de escopo

- Google Drive e qualquer servidor que exija `client_id` pré-registrado.
- Client ID Metadata Documents (alternativa mais nova ao DCR).
- Card de reconexão dentro do chat.
- Vercel (aceita só clientes pré-aprovados por ela); a entrada do catálogo não muda.
- Step-up de escopo (`insufficient_scope` pedindo novo login com mais escopos).

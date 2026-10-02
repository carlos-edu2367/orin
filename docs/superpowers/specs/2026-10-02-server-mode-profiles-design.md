# Modo servidor com perfis — design (etapa 1 de 3)

**Data:** 2026-10-02
**Estado:** design aprovado em conversa; aguardando revisão da spec escrita
**Motivação:** permitir que qualquer pessoa hospede o Orin na própria VPS e o use pelo navegador (desktop e mobile), com um ou mais perfis protegidos por usuário e senha, cada um com suas conversas, memórias, projetos e chaves.
**Relações:** [ADR 006 — single-user pronto para multi-tenancy](../../adr/006-single-user-multi-tenant-ready.md), [ADR 007 — sessões server-side](../../adr/007-server-side-sessions.md), [runtime loopback local](2026-08-10-local-loopback-runtime-design.md) ("não deve chegar à VPS"), [RFC 702 — Segurança](../../architecture/700-api-security/702-security.md), [RFC 603 — Workspaces](../../architecture/600-platform-data/603-workspaces.md).

## 1. Problema

O Orin só sabe rodar como instalação pessoal:

- **Um único principal fixo.** `LoopbackSecurityService` devolve sempre `user_id="local-user"` (`api/security.py:123-150`) e o gateway exige peer TCP loopback (`api/gateway.py:439-450`). O próprio docstring diz que ele "nunca deve ser composto para uma VPS".
- **Não há contas nem login.** Não existe tabela de usuários nem rota de login/logout/criação de sessão. `PostgresSecurityService` (sessões, CSRF, PATs, rate limit, revogação) existe, é composto quando a confiança loopback está desligada, mas nada cria sessões. As sessões não expiram e o CSRF só exige que a `Origin` exista, sem compará-la a nada.
- **O launcher força localhost.** Host `127.0.0.1` fixo (`launcher/environment.py:101-102`), `proxy_headers=False` (`launcher/internal.py:22-39`), e o frontend só é servido no modo loopback (`bootstrap/production.py:334-335`).
- **O frontend não tem estado de autenticação.** `readBrowserSessionBootstrap` (`frontend/src/api/browserSession.ts`) procura um `<meta name="csrf-token">` que o backend nunca injeta.
- **Estado de arquivo global da instalação.** Workspaces gerenciados (`<data>/workspaces`), índices de busca (`<data>/retrieval/<workspace_id>.db`), plugins (`<data>/plugins`) e `<data>/agent-runtime.json` não são separados por perfil em disco.
- **Capacidades de desktop sem equivalente web.** Seletor nativo de pasta e vínculo de qualquer caminho do host (`local_workspace/paths.py:50-77`), "abrir no app do desktop" (`agentic/file_preview.py:20`), updater pela UI (`gateway.py:1545`), OmniRoute (um processo por instalação).
- **Execução de processos no host.** `run_command` usa `shell=True` e herda `os.environ`, inclusive `AGENTOS_PROVIDER_ENCRYPTION_KEY` (`agentic/agent_tools.py:1532-1584`). MCP stdio, hooks de plugin, terminal e code mode também criam processos no host. Num servidor compartilhado, qualquer perfil leria as chaves de todos.

O que **já está pronto** e não muda: quase toda tabela carrega `user_id` e o gateway passa `principal.user_id` para os stores (ADR 006); provedores já são por usuário (`provider_configurations` único por usuário+provedor); uploads em staging já são separados por dono.

## 2. Decisões de produto (tomadas na conversa)

| Tema | Decisão |
|---|---|
| Público | Projeto open source: qualquer um hospeda a própria instância e acessa pela web. |
| Autenticação | Usuário e senha. Sem OIDC, 2FA ou e-mail nesta etapa. |
| Cadastro | Fechado. O primeiro admin é criado com token de setup; depois o admin cria perfis. |
| Isolamento entre perfis | Real, inclusive para o que o agente executa: **sandbox por perfil** (etapa 2). |
| Chaves de provedor | Cada perfil cadastra e usa as próprias. |
| Tecnologia de implantação e sandbox | **Abordagem A:** Docker Compose (Orin + Caddy com HTTPS automático) e um container de sandbox por perfil, criado pelo Orin via socket-proxy do Docker. Alternativas rejeitadas: bubblewrap nativo (depende da política de user namespaces da distro, persistência ruim de ferramentas) e um usuário Linux por perfil (exige root, frágil). |
| Lançamento | Etapas 1 e 2 saem **na mesma release**. O modo servidor não é publicado com funções travadas. |

## 3. Decomposição

| Etapa | Conteúdo | Spec |
|---|---|---|
| **1 — Modo servidor com perfis** | Modo `server`, contas, login/sessão/CSRF, setup do admin, gestão de perfis, telas de login/setup/admin, layout de dados por perfil, área de arquivos do perfil, download, travas fechadas para o que executa processo, suíte de isolamento. | Esta. |
| **2 — Sandbox por perfil** | Executor que roda processos dentro do container do perfil: `run_command`, terminal, code mode, MCP stdio, hooks de plugin, OmniRoute por perfil, clonar repositório git na área de arquivos. Browser sem acesso à rede do host. | A escrever. |
| **3 — Empacotamento** | Imagem Docker, `docker-compose.yml` com Caddy, socket-proxy, worker com turnos concorrentes entre perfis, aviso de nova versão para o admin, documentação de self-host. | A escrever. |

Mapa de adaptação do que hoje é desktop:

| Hoje | Versão servidor | Etapa |
|---|---|---|
| `run_command`, `terminal.execute`, code mode | Dentro do sandbox do perfil (`docker exec`), workspace montado, sem chaves no ambiente | 2 |
| MCP stdio | Processo do servidor MCP dentro do sandbox; o Orin fala pelo stdin/stdout do `docker exec` | 2 |
| Hooks de plugin | Dentro do sandbox do perfil | 2 |
| OmniRoute | Dentro do sandbox de cada perfil que o ativar; porta alcançável só pelo Orin | 2 |
| Vincular pasta do host + seletor nativo | Área de arquivos do perfil + seletor web restrito a ela + upload de .zip | 1 |
| Clonar código para um projeto | `git clone` executado dentro do sandbox, direto na área de arquivos | 2 |
| "Abrir no app do desktop" | Baixar arquivo (o preview no navegador já existe) | 1 |
| Updater pela UI | Aviso de versão para o admin com o comando `docker compose pull && docker compose up -d` | 3 |

## 4. Etapa 1 — design

### 4.1 Modo de execução

- Nova configuração `ORIN_MODE` com valores `local` (padrão, comportamento atual inalterado) e `server`.
- `ProductionSettings` ganha `ORIN_MODE`, `ORIN_PUBLIC_URL` e `ORIN_TRUSTED_PROXIES`. Validação no boot, falhando com mensagem clara:
  - `server` exige `ORIN_PUBLIC_URL` absoluto (`https://…`; `http://` só com host loopback, para desenvolvimento);
  - `server` proíbe `LOCALHOST_TRUST_ENABLED=true`;
  - `server` exige a chave de criptografia estável (`AGENTOS_PROVIDER_ENCRYPTION_KEY`/`APP_MASTER_KEY`);
  - `server` exige `WEB_DIST_DIR`.
- No modo `server`, `compose_production_services` compõe o novo `SessionSecurityService` (§4.3) e o frontend é montado com `agentos-auth-mode` = `session`.
- `ORIN_TRUSTED_PROXIES` (lista de IPs/CIDRs, padrão vazio): só esses peers têm `X-Forwarded-For`/`X-Forwarded-Proto` respeitados. Uvicorn passa a receber `proxy_headers=True` e `forwarded_allow_ips` a partir dessa lista no modo `server`; no `local` continua `False`.
- O launcher (`orin`) continua só para o modo local nesta etapa. O modo servidor é iniciado com `orin serve`, um comando novo que lê o ambiente, executa migrações e sobe API, worker e scheduler com o mesmo supervisor, sem abrir navegador, sem lock de desktop e com host configurável por `ORIN_BACKEND_HOST`. A etapa 3 usa esse comando como entrypoint do container.

### 4.2 Contas

Tabela nova `users` (migração alembic):

| Coluna | Tipo | Notas |
|---|---|---|
| `user_id` | String(255), PK | `usr_<uuid hex>`; o primeiro admin recebe `local-user` (ver abaixo) |
| `username` | String(64), único | normalizado em minúsculas; `^[a-z0-9][a-z0-9._-]{2,63}$` |
| `display_name` | String(128) | opcional; padrão = username |
| `password_hash` | String(255) | formato `scrypt$n$r$p$salt_b64$hash_b64` |
| `role` | String(16) | `admin` ou `member` |
| `active` | Boolean | desativado não autentica |
| `must_change_password` | Boolean | true para senha provisória |
| `created_at`, `updated_at`, `password_changed_at` | DateTime(tz) | |

- **Hash de senha:** `hashlib.scrypt` da biblioteca padrão (`n=2**15, r=8, p=1`, sal de 16 bytes, saída de 32 bytes), comparado com `hmac.compare_digest`. Parâmetros gravados no hash para permitir aumentar no futuro. Sem dependência nova. Senha com mínimo de 10 caracteres e máximo de 256.
- **Primeiro admin herda `local-user`:** quem migra uma instalação local para o modo servidor mantém conversas, memórias, projetos e chaves sem reescrever `user_id` em dezenas de tabelas. Perfis criados depois recebem `usr_<uuid>`.
- **Setup:** enquanto não existir nenhum usuário, o processo da API gera no boot um token de setup aleatório (32 bytes, base64url), grava só o digest SHA-256 na tabela `instance_setup` (uma linha, com `created_at`) e imprime o token no log com uma linha destacada. O token é regenerado a cada boot até ser consumido. `POST /v1/auth/setup` com token válido cria o admin, apaga o digest e já abre a sessão. Depois disso responde `409 setup_completed`.
- **CLI de resgate:** `orin user create <username> [--admin]` (pede a senha pelo terminal, sem eco) e `orin user reset-password <username>` (gera senha provisória e marca `must_change_password`). Servem para criar o admin sem a tela de setup e para recuperar o acesso quando o único admin esquece a senha.
- **Gestão por admin:** criar perfil (senha provisória gerada pelo servidor, mostrada uma única vez na resposta), desativar/reativar, resetar senha, alterar papel. Regra: a instância nunca fica sem admin ativo (desativar ou rebaixar o último admin responde `409 last_admin`).
- **Perfil desativado:** todas as sessões são revogadas na hora. O scheduler deixa de materializar tarefas agendadas desse perfil e o worker cancela turnos pendentes dele com o motivo `owner_inactive`. Os dados ficam intactos e voltam a ser usados se o perfil for reativado. Excluir perfil está fora do escopo.

### 4.3 Sessão e CSRF

`SessionSecurityService` estende `PostgresSecurityService` (mesma porta `SecurityService`):

- **Sessões:** `security_sessions` ganha `expires_at` e `last_seen_at`. Expiração deslizante de 30 dias: cada request autenticado renova `expires_at`, gravando no máximo uma vez a cada 5 minutos por sessão para não escrever no banco a cada request. Sessão expirada responde `401 authentication_required`.
- **`credential_ref`** de sessão = `session:<session_id>`. Novo método `revoke_user(user_id)` revoga todas as sessões do usuário (desativação, troca e reset de senha). A troca de senha pelo próprio usuário mantém só a sessão atual.
- **Scopes do principal:** `api`, mais `admin` para administradores. Com `must_change_password`, o principal recebe só o scope `password_change`: `authorize` (que exige `api`) recusa tudo, e as rotas `/v1/auth/me`, `/v1/auth/password` e `/v1/auth/logout` aceitam esse scope. Resposta para o resto: `403 password_change_required`.
- **Cookie:** `agentos_session`, `HttpOnly`, `Secure` (exceto com `ORIN_PUBLIC_URL` `http://` loopback), `SameSite=Lax`, `Path=/`, `Max-Age` de 30 dias. O valor é 32 bytes aleatórios em base64url; no banco fica o id como hoje.
- **CSRF:** o token (32 bytes aleatórios) é devolvido no corpo de `login`, `setup` e `me`; o frontend o mantém em memória e envia em `X-CSRF-Token`, como já faz. `validate_csrf` passa a exigir também que `Origin` seja igual à origem de `ORIN_PUBLIC_URL`. No modo `local`, nada muda.
- **Proteção do login:** tabela `auth_login_attempts` (chave = IP ou username, `occurred_at`, `succeeded`). Bloqueio após 5 falhas em 15 minutos por username ou 20 por IP, com bloqueio progressivo (15 min, depois 1 h). Resposta `429 login_locked` com `retry_after`. Usuário inexistente, desativado e senha errada respondem igual (`401 invalid_credentials`). Para não revelar quais usernames existem pelo tempo de resposta, usuário inexistente também passa por uma verificação scrypt contra um hash fixo. O IP do cliente vem de `X-Forwarded-For` apenas se o peer estiver em `ORIN_TRUSTED_PROXIES`.

Rotas novas (todas sob `/v1/auth` e `/v1/admin`):

| Rota | Quem | Efeito |
|---|---|---|
| `GET /v1/auth/me` | qualquer sessão | `{user, csrf_token, capabilities}`; sem usuários na instância → `409 setup_required`; sem sessão → `401` |
| `POST /v1/auth/setup` | anônimo + token | cria o admin e abre sessão |
| `POST /v1/auth/login` | anônimo | abre sessão; corpo `{username, password}` |
| `POST /v1/auth/logout` | sessão | revoga a sessão atual e apaga o cookie |
| `POST /v1/auth/password` | sessão | `{current_password, new_password}`; limpa `must_change_password` e revoga as outras sessões |
| `GET /v1/admin/users` | admin | lista perfis |
| `POST /v1/admin/users` | admin | cria perfil; devolve a senha provisória uma vez |
| `PATCH /v1/admin/users/{id}` | admin | `display_name`, `role`, `active` |
| `POST /v1/admin/users/{id}/reset-password` | admin | nova senha provisória, revoga sessões |

`login`, `setup` e as rotas anônimas exigem `Origin` igual a `ORIN_PUBLIC_URL`, já que ainda não há sessão para o CSRF.

### 4.4 Capacidades da instância

Um objeto imutável `InstanceCapabilities`, construído na composição a partir do modo, é a única fonte de verdade sobre o que está disponível:

| Capacidade | `local` | `server` (etapa 1) |
|---|---|---|
| `shell` (run_command, terminal, code mode com execução) | sim | não |
| `mcp_stdio` | sim | não |
| `plugin_hooks` | sim | não |
| `omniroute` | sim | não |
| `host_folders` (vincular qualquer pasta do host, seletor nativo) | sim | não |
| `profile_files` (área de arquivos do perfil) | não | sim |
| `open_in_desktop_app` | sim | não |
| `ui_updater` | sim | não |
| `user_admin` | não | sim |

- O gateway consulta o objeto antes de executar a rota correspondente: `404 capability_unavailable`.
- O runtime agentic não registra as ferramentas indisponíveis no conjunto oferecido ao modelo, para que ele não tente usá-las (`AgentToolset(enable_terminal=False)`). O diagnóstico automático depois de `write_file`/`edit_file` (que roda linters do projeto via shell, `agentic/agent_tools.py:972-1010`) também fica desligado sem `shell`.
- O worker do MCP recusa iniciar servidores stdio e o motor de hooks não executa hooks quando a capacidade está desligada.
- `/v1/auth/me` expõe o objeto para a UI.
- A etapa 2 troca `shell`, `mcp_stdio`, `plugin_hooks` e `omniroute` para "disponível via sandbox", sem mexer nos pontos de consulta.

### 4.5 Dados em disco por perfil

Novo layout, usado **nos dois modos**, para o que o sandbox da etapa 2 vai montar:

```
<data>/users/<user_id>/
  workspaces/<workspace_id>/   # workspaces gerenciados (antes <data>/workspaces)
  files/                        # área de arquivos do perfil (só modo server usa)
```

- `OrinPaths` ganha `user_root(user_id)`, `user_workspaces(user_id)` e `user_files(user_id)`. `user_root` valida o id (`^[A-Za-z0-9_-]{1,64}$`) e é o único ponto que monta esses caminhos. Quem hoje usa `orin_paths().workspaces` (gateway, `TurnSession`, `ChatWorker`) passa a usar `user_workspaces(user_id)`.
- **Migração de layout** no boot (antes de servir requests), idempotente e com marcador `<data>/.layout-v2`. O conteúdo de `<data>/workspaces/` é movido para `users/local-user/workspaces/`. Se a migração falhar no meio, o boot para com erro e o próximo boot retoma (cada movimento é verificável: destino existe e origem não).
- **Continuam onde estão**, por já serem isolados e por não serem montados no sandbox:
  - staging de uploads: já é separado por dono em `<data>/uploads/staging/<user_id>`;
  - índices de busca `<data>/retrieval/<workspace_id>.db`: só são abertos a partir de um workspace cujo dono já foi verificado, e o id do workspace é aleatório;
  - `<data>/agent-runtime.json`: já é indexado por `user_id` e só é lido no servidor;
  - cache de pacotes de plugin `<data>/plugins/<plugin_id>/<versão>`: é endereçado por conteúdo (digest conferido) e, no modo server, só recebe pacotes públicos via https (§4.7). O que cada perfil instalou e habilitou já fica na tabela `plugins`, por `user_id`.
- `runtime_heartbeats`, a chave de criptografia, o lock de instalação e o log são da instância e continuam globais.

### 4.6 Área de arquivos do perfil (modo server)

Substitui o vínculo de pasta do host:

- **Vínculo de projeto:** no modo `server`, `workspace_roots` só aceita caminhos dentro de `users/<user_id>/files/`, validados no vínculo e revalidados a cada `resolve` (o caminho guardado pode ter ficado fora por symlink). Um vínculo antigo fora da área, vindo de uma instalação local migrada, aparece no projeto como "pasta indisponível no modo servidor", sem apagar o vínculo.
- **Seletor web:** `GET /v1/files?path=<relativo>` lista uma pasta da área (nome, tipo, tamanho, mtime). `POST /v1/files/folders` cria uma pasta. A navegação usa a mesma contenção de caminho de `ConversationWorkspace.resolve` (`agentic/workspace.py:66-89`): resolve symlinks e exige que o resultado fique dentro da raiz.
- **Upload de .zip:** `POST /v1/files/import` extrai o arquivo numa pasta nova da área. Proteções:
  - rejeita entradas absolutas, com `..` ou que resolvam fora do destino (zip slip);
  - ignora symlinks;
  - limites de tamanho total extraído (padrão 2 GiB), número de entradas (padrão 50 000) e razão de compressão (para conter zip bomb);
  - extração para uma pasta temporária e `rename` atômico ao terminar.
- **Download:** a rota de arquivo de conversa já aceita `?disposition=attachment` (`GET /v1/conversations/{id}/files/{path}`); a UI passa a usá-la no lugar de "abrir no app do desktop" no modo `server`. Para a área de arquivos, `GET /v1/files/download?path=…` devolve o arquivo com `Content-Disposition: attachment`. Baixar uma pasta inteira como .zip fica fora desta etapa.
- **Turno com pasta fora da área:** o worker revalida `workspace_root_path` no modo `server`. Se a pasta vinculada estiver fora de `users/<user_id>/files/`, o turno termina com o código `workspace_unavailable` em vez de rodar em outro lugar.

### 4.7 Plugins no modo server

A instalação de plugins continua disponível para cada perfil e registra só o que não executa processo (skills e MCP remoto). Endurecimento do `fetcher` (`plugins/fetcher.py`) no modo `server`:

- a fonte `path` (copiar uma pasta do host) é rejeitada;
- `git clone` aceita apenas URLs `https://`, roda com `GIT_ALLOW_PROTOCOL=https`, `-c protocol.file.allow=never`, sem submódulos (já é o caso);
- o host da URL precisa resolver para endereço público. Reaproveita a política de rede do browser (`browser/security.py`), bloqueando também loopback e rede privada.

### 4.8 Browser do agente no modo server

`NetworkPolicy` (`browser/security.py:30-40`, `browser/conversation_worker.py:286-297`) passa a bloquear loopback no modo `server`, além do que já bloqueia (rede privada, link-local, metadados de nuvem). Assim o agente não alcança a API do Orin nem outros serviços da VPS.

### 4.9 Frontend

- **Bootstrap:** `readBrowserSessionBootstrap` reconhece `agentos-auth-mode=session`. Nesse modo, um `SessionProvider` chama `GET /v1/auth/me` antes de renderizar as rotas:
  - **200:** guarda usuário, CSRF e capacidades em contexto e entrega o token ao `ApiClient`;
  - **401:** vai para `/login?next=<rota>`;
  - **409 `setup_required`:** vai para `/setup`;
  - **403 `password_change_required`:** vai para `/change-password`.
- O `ApiClient` trata `401` em qualquer chamada voltando para `/login?next=<rota atual>`.
- **Telas novas:**
  - `/login`: usuário e senha, erro genérico, aviso de bloqueio com tempo restante;
  - `/setup`: token, username, senha e confirmação;
  - `/change-password`: senha atual, nova senha e confirmação.
  
  Os campos usam `autocomplete` (`username`, `current-password`, `new-password`) e o layout é de coluna única, que funciona em celular. O visual segue os tokens de `DESIGN.md`.
- **Menu do perfil** no rodapé da sidebar, só no modo `session`: inicial do nome, nome de exibição, "Trocar senha" e "Sair".
- **`/settings/users`**, só para admin: lista de perfis com papel e estado, criar perfil (mostra a senha provisória uma vez, com botão copiar), desativar/reativar, resetar senha, alterar papel.
- **Capacidades:** a UI esconde, sem desabilitar, o que a instância não oferece:
  - "Abrir pasta" e vínculo de pasta do host dão lugar ao seletor da área de arquivos e ao upload de .zip;
  - "Abrir no app do desktop" dá lugar a "Baixar";
  - OmniRoute some do catálogo de provedores;
  - o updater some de About;
  - MCP stdio some do formulário de MCP;
  - terminal some.
  
  A decisão vem sempre de `capabilities`, nunca do modo.

### 4.10 Erros

Todos no formato de erro já usado pela API (`error.code`, `category`, `message_key`, `retryable`, `retry_after`):

| Código | HTTP | Quando |
|---|---|---|
| `authentication_required` | 401 | sem sessão, sessão expirada ou revogada |
| `invalid_credentials` | 401 | login errado (usuário inexistente, desativado ou senha errada) |
| `login_locked` | 429 | bloqueio por tentativas; `retry_after` preenchido |
| `setup_required` | 409 | `/me` ou `login` numa instância sem usuários |
| `setup_completed` | 409 | `setup` numa instância que já tem usuário |
| `invalid_setup_token` | 401 | token de setup errado |
| `admin_required` | 403 | rota `/v1/admin/*` sem scope `admin` |
| `password_change_required` | 403 | perfil com senha provisória fora de `/v1/auth/*` |
| `last_admin` | 409 | desativar ou rebaixar o último admin ativo |
| `username_taken` | 409 | criar perfil com username existente |
| `weak_password` | 422 | senha fora dos limites |
| `capability_unavailable` | 404 | função desligada na instância |
| `resource_not_found` | 404 | inclusive recurso de outro perfil (indistinguível) |
| `archive_rejected` | 422 | .zip com caminho inválido ou acima dos limites |

## 5. Testes e critérios de pronto

TDD no padrão atual (`tests/unit`, `tests/integration`, pytest; vitest e Playwright no frontend).

- **Unitários:**
  - hash e verificação scrypt (inclusive parâmetros antigos);
  - política de bloqueio do login;
  - expiração deslizante e escrita espaçada de `last_seen_at`;
  - CSRF com `Origin`;
  - `InstanceCapabilities` por modo;
  - validação de `ProductionSettings` no modo `server`;
  - `OrinPaths.user_root`;
  - extração de .zip (zip slip, symlink, limites).
- **Integração (FastAPI sobre SQLite real):**
  - setup com token (válido, inválido, já concluído);
  - login, logout e `/me`;
  - bloqueio e `retry_after`;
  - senha provisória até a troca;
  - CRUD de perfis por admin e `admin_required` para member;
  - `last_admin`;
  - desativação revogando sessões, cancelando turnos pendentes e pausando agendamentos;
  - migração de layout (inclusive retomada após falha no meio e reexecução sem efeito);
  - vínculo de projeto restrito à área de arquivos;
  - fetcher de plugins recusando `path`, `file://`, `ext::` e host privado;
  - política de rede do browser bloqueando loopback no modo `server`.
- **Suíte de isolamento A×B (critério principal de pronto):**
  - gerada a partir das rotas registradas no app;
  - para cada rota de recurso com id, o perfil B tenta ler, alterar, apagar e (onde houver) assinar o SSE de um recurso do perfil A e precisa receber `404`;
  - listagens de B nunca contêm itens de A;
  - uma rota nova sem caso de isolamento faz o teste falhar até ser classificada (com recurso / sem recurso / só admin);
  - furos encontrados são corrigidos no store, não no gateway (ADR 006).
- **Regressão do modo local:** a suíte atual passa sem alteração de comportamento. O loopback não muda.
- **Frontend:**
  - vitest do bootstrap de sessão, dos redirecionamentos e da renderização por capacidades;
  - Playwright e2e: setup → login → criar perfil → login como membro → troca de senha obrigatória → membro não vê conversas nem projetos do admin.

## 6. Fora de escopo

- **Etapa 2:** sandbox, execução de processos e `git clone` na área de arquivos.
- **Etapa 3:** imagem Docker, compose, HTTPS, concorrência do worker entre perfis, aviso de versão.
- OIDC/login social, 2FA, recuperação de senha por e-mail, cadastro público.
- Excluir perfil, compartilhar conversas ou projetos entre perfis, organizações.
- Cotas e limites de uso por perfil.
- Chave de criptografia por perfil (a chave segue única por instância; a etapa 2 garante que ela nunca chegue ao sandbox).
- PostgreSQL: o modo servidor continua sobre SQLite.

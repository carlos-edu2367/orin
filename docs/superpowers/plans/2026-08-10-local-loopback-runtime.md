# Localhost trusted runtime — Implementation Plan

**Goal:** tornar o AgentOS utilizável numa máquina pessoal sem um emissor de
sessão externo, sem introduzir um bypass reutilizável na VPS.

**Architecture:** `ProductionSettings` habilita explicitamente um adaptador de
segurança local apenas em `local`/`development`. O gateway verifica o IP do
cliente antes de obter o principal desse adaptador. O frontend só reconhece o
modo local a partir de uma variável de build declarada; o modo padrão mantém o
contrato cookie + CSRF já implementado.

## Task 1 — Contrato e segurança do loopback

**Files:** `src/agentos/api/security.py`, `src/agentos/api/gateway.py`,
`src/agentos/bootstrap/production.py`, `tests/unit/api/test_api_asgi.py`,
`tests/unit/bootstrap/test_production.py`.

1. Escrever testes RED para autenticação local aceita em `127.0.0.1`/`::1`,
   rejeitada fora de loopback e desativada por padrão.
2. Criar um principal/adaptador local explícito que preserve autorização,
   rate-limit e idempotência, mas não aceite sessão/cookie como evidência.
3. Adicionar a guarda de endereço no gateway antes de autenticar o principal
   local; preferir `request.client.host`, falhando fechado se ausente.
4. Validar no settings que `LOCALHOST_TRUST_ENABLED` nunca combina com
   ambiente de VPS/produção.
5. Executar os testes unitários backend focalizados.

## Task 2 — Bootstrap web sem regressão da sessão normal

**Files:** `frontend/index.html`, `frontend/src/api/browserSession.ts`,
`frontend/src/api/client.ts`, testes unitários do cliente, Home e Providers.

1. Escrever testes RED: `auth-mode=loopback` gera bootstrap pronto sem CSRF;
   meta ausente continua sendo `missing_csrf`; CSRF continua vencendo no modo
   normal.
2. Publicar a meta de modo através de `VITE_AUTH_MODE`, sem valor padrão
   permissivo.
3. Atualizar o browser client e as telas para aceitarem apenas o bootstrap
   loopback declarado e continuarem bloqueando o modo normal sem CSRF.
4. Executar Vitest e E2E das jornadas provider/conversa em ambos os contratos.

## Task 3 — Operação local repetível

**Files:** `.env.example`, novo `.env.local.example` (se útil), `README.md`,
`docker-compose.yml` e possivelmente scripts de package.

1. Documentar uma sequência PowerShell curta que copia as variáveis, sobe
   dependências, migra banco e inicia os dois processos apenas em loopback.
2. Se necessário, criar perfil Compose para backend/frontend ou scripts npm;
   não publicar portas em interfaces externas nem incluir chaves reais.
3. Manter o caminho futuro de VPS documentado como sessão externa, HTTPS e
   `LOCALHOST_TRUST_ENABLED=false`.

## Task 4 — Verificação

1. Rodar `python -m pytest -q` e as integrações PostgreSQL locais.
2. Rodar `npm test`, `npm run test:e2e`, `npm run test:visual`, `npm run lint`
   e `npm run build`.
3. Fazer smoke test real com os processos locais, validar `/healthz`,
   `/readyz`, provider OpenRouter e criação de conversa sem cookie.
4. Revisar diff por segredos e confirmar que nenhum serviço se anuncia fora de
   `127.0.0.1`.

## Plan self-review

- O bypass depende de duas condições independentes (configuração explícita e
  origem loopback) e é proibido por validação fora do ambiente local.
- Não altera o contrato de sessão para hosts normais e não usa secret/token
  acessível pelo browser.
- O plano inclui operação reproduzível, não apenas testes, e deixa a VPS no
  caminho seguro já existente.

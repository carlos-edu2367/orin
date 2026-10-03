# Modo servidor (em desenvolvimento)

> Ainda não publicado em release. O modo servidor só sai junto com o sandbox por perfil (etapa 2). Até lá, shell, terminal, MCP stdio, hooks de plugin e OmniRoute ficam desligados nele.

O modo servidor roda o Orin numa máquina acessada pelo navegador, com perfis protegidos por usuário e senha. Cada perfil tem as próprias conversas, memórias, projetos, chaves de provedor e área de arquivos.

## Subir para testar

1. Defina a URL pública e, se houver proxy reverso, os IPs dele:

   ```bash
   export ORIN_PUBLIC_URL=https://orin.example.com
   export ORIN_TRUSTED_PROXIES=127.0.0.1
   export ORIN_BACKEND_HOST=127.0.0.1
   ```

   Para testar localmente sem HTTPS, use `ORIN_PUBLIC_URL=http://127.0.0.1:49200`.

2. Rode `orin serve`. Na primeira subida, o log mostra um bloco `ORIN SETUP TOKEN`.
3. Abra `ORIN_PUBLIC_URL` no navegador, cole o token em `/setup` e crie o primeiro admin.
4. Em **Settings → Perfis**, crie os perfis. Cada um recebe uma senha provisória, trocada no primeiro login.

O primeiro admin herda os dados de uma instalação local que já existia no mesmo diretório de dados.

## Recuperar acesso

- `orin user reset-password <usuário>` gera uma senha provisória e encerra as sessões do perfil.
- `orin user create <usuário> --admin` cria um admin sem passar pela tela de setup.

## Onde ficam os dados

- Banco: `<data>/orin.db`
- Workspaces gerenciados: `<data>/users/<id>/workspaces/`
- Área de arquivos: `<data>/users/<id>/files/`

## Segurança

- Sessão em cookie `HttpOnly`, `Secure` e `SameSite=Lax`, válida por 30 dias sem uso.
- Toda alteração exige o token CSRF da sessão e a `Origin` igual a `ORIN_PUBLIC_URL`.
- O login bloqueia temporariamente depois de várias senhas erradas, por usuário e por IP.
- Recursos de outro perfil respondem 404.

# Prompt — Conectores MCP e Plugins funcionam no código mas não na prática

**Use este arquivo inteiro como prompt inicial da próxima sessão.** Trabalhe direto no branch
`main`, sem worktree — decisão explícita do dono do projeto para esta rodada. Backend: Python 3.13
(`.venv/Scripts/python.exe`). Frontend: `frontend/` (Vite + React + TypeScript + Vitest).

## O problema, do ponto de vista do usuário

Os três planos (MCP, Plugins, Settings shell) foram implementados e a suíte de testes passa, mas
o dono do projeto tentou usar de verdade e nada funciona:

1. Tentou instalar `https://github.com/obra/superpowers` (um plugin real, publicado, usado pelo
   próprio Claude Code). O agente respondeu: *"este pacote não contribui com nenhuma
   funcionalidade que eu consiga utilizar"*.
2. Tentou conectar aos MCPs do GitHub e da Vercel, e usar Google Drive/Gmail — nenhum funciona.

**Os testes não capturam isso porque todos os fixtures de teste foram escritos por quem escreveu
o código, seguindo exatamente o schema que o código espera.** Nenhum teste rodou contra um pacote
ou servidor do mundo real. As quatro causas abaixo já foram **confirmadas por reprodução direta**
nesta sessão — não são suspeitas, são fatos verificados no terminal. Comece corrigindo-as; não
gaste tempo re-diagnosticando.

## Causa 1 (CRÍTICA, confirmada) — o parser de skill rejeita todo SKILL.md real

`src/agentos/skills/parser.py:47` exige `{"name", "description", "version"}` no frontmatter de
todo `SKILL.md`. Isso é correto para skills que o **próprio Orin versiona** (criadas via
`create_skill`/`edit_skill`, que têm uma tabela `skill_versions` com semver). Mas
**nenhum plugin real do ecossistema Claude Code declara `version` por skill** — o formato padrão
(usado pelo próprio `superpowers`, e por praticamente todo plugin publicado) só tem `name` e
`description`. Versão é uma propriedade do *plugin inteiro* (`plugin.json`), não de cada skill
dentro dele.

O inspector de plugin (`src/agentos/plugins/inspector.py:29`) reusa esse mesmo parser estrito sem
adaptação, então toda skill de todo plugin real "falha ao analisar" e vira warning silencioso —
resultando em `contribution_count == 0` e a mensagem "não contribui com nada".

**Reprodução (rode isto primeiro, antes de qualquer mudança, para confirmar o estado atual):**

```bash
.venv/Scripts/python.exe -c "
from pathlib import Path
from agentos.skills.parser import parse_skill_file
path = Path(r'C:\Users\Carlos\.claude\plugins\cache\claude-plugins-official\superpowers\5.1.0\skills\brainstorming\SKILL.md')
parse_skill_file(path, include_instructions=False)
"
```
Resultado atual: `SkillParseError: missing required skill frontmatter field: version`.

Se esse caminho de cache local não existir mais na máquina onde você está rodando, clone
`https://github.com/obra/superpowers` para qualquer pasta e aponte para
`skills/brainstorming/SKILL.md` de lá — o arquivo é o mesmo.

**Direção da correção:** não afrouxe `parse_skill_file`/`parse_skill_markdown` — ele é usado por
`agentos/skills/service.py` e pela API de skills do próprio Orin, e ali a exigência de versão é
legítima. Em vez disso, o **inspector de plugin precisa de um caminho de parsing próprio, tolerante
ao formato real do ecossistema**: `version` ausente deve receber um valor sintético (ex.: a versão
do próprio plugin, ou `"1.0.0"`) em vez de rejeitar a skill. Considere extrair a lógica de
frontmatter (`_parse_frontmatter`, `_value`, `_scalar`, `_list` em `parser.py`) para uma função
reutilizável que aceite um conjunto de campos obrigatórios diferente, e chame essa função duas
vezes: uma vez com `required={"name","description","version"}` para o Orin nativo, outra com
`required={"name","description"}` e um default de versão para plugins. Escreva o teste desta
correção **contra o SKILL.md real do superpowers** (copie o conteúdo real para o fixture de teste,
não invente um novo formato) — é a única forma de garantir que não regride de novo silenciosamente.

Depois de corrigir, confirme com o pipeline completo:
```bash
.venv/Scripts/python.exe -c "
from pathlib import Path
import tempfile
from agentos.plugins.sources import resolve_source
from agentos.plugins.fetcher import PluginFetcher
from agentos.plugins.inspector import inspect_plugin_package

source = resolve_source('https://github.com/obra/superpowers')
root = Path(tempfile.mkdtemp(prefix='orin-plugin-test-'))
fetched = PluginFetcher(root).fetch(source)
inspection = inspect_plugin_package(fetched.path, package_digest=fetched.package_digest)
print('skills:', len(inspection.skills), 'is_installable:', inspection.is_installable)
"
```
Esperado depois do fix: `skills: 14 is_installable: True` (ou próximo disso — o número exato de
skills no repo pode mudar com o tempo; o que importa é ser **maior que zero**).

## Causa 2 (CRÍTICA, confirmada) — o timeout do transporte stdio é código morto

`src/agentos/mcp/transport_stdio.py`: o parâmetro `timeout` é armazenado em `self._timeout`
(linha 67) mas **nunca é lido em nenhum outro lugar do arquivo**. `open()` chama
`subprocess.Popen(...)` sem limite de tempo; `send()` chama `process.stdout.readline(...)` de
forma bloqueante, sem limite de tempo. Confirmado por `grep -n timeout` no arquivo — só aparece
nas duas linhas citadas.

Na prática: um servidor MCP `npx`/`uvx` que precisa baixar o pacote na primeira execução (o caso
comum — a maioria dos usuários não tem o pacote em cache) pode levar de segundos a mais de um
minuto. Sem timeout, a chamada trava **indefinidamente**, sem erro, sem log, sem retorno.

**Reprodução:** `npx -y @modelcontextprotocol/server-github` nesta máquina imprime
`GitHub MCP Server running on stdio` só depois de baixar o pacote (com um aviso de que o pacote
está **deprecated** — ver Causa 4). Numa máquina sem cache, ou numa rede lenta, essa espera pode
ultrapassar qualquer expectativa razoável de UI, e hoje nada interrompe.

**Direção da correção:** aplique `self._timeout` de verdade. `subprocess.Popen` não tem timeout
nativo no construtor; a forma correta é ler `stdout` numa thread separada com uma fila
(`queue.Queue`) e usar `queue.get(timeout=self._timeout)` em `send()`, ou usar
`selectors`/`asyncio` para leitura com prazo. Ao estourar o timeout, mate o processo (reusar
`_terminate_process_tree`, já importado) e levante `StdioTransportError` com uma mensagem clara.
Escreva um teste que comprove isso com um subprocesso real que dorme mais que o timeout configurado
e confirme que `send()` levanta dentro de um prazo razoável, não trava o processo de teste.

## Causa 3 (CRÍTICA, confirmada) — as rotas de aprovação bloqueiam o event loop inteiro

Toda rota que faz I/O real e demorado roda de forma **síncrona dentro de uma `async def` do
FastAPI**, sem `run_in_threadpool`/`asyncio.to_thread`:

- `approve_mcp_server`, `test_mcp_server` (`src/agentos/api/gateway.py`) chamam
  `services.mcp.approve(...)`/`.test(...)`, que abrem um processo `npx`/`uvx` ou uma conexão HTTP
  de verdade — de forma bloqueante, direto na função da rota.
- `inspect_plugin` chama `fetcher.fetch()`, que roda `subprocess.run(["git", "clone", ...],
  timeout=120)` — bloqueante, direto na função da rota, por até 120 segundos.

FastAPI/Starlette roda uma `async def` no mesmo event loop de todas as outras requisições e de
todo SSE ativo. Uma chamada bloqueante dentro dela **congela o processo inteiro** — não só aquela
requisição, mas a API inteira, para todas as abas e conversas abertas, pelo tempo que o
subprocesso/git levar. Isso contraria o próprio desenho documentado do projeto (ver README, seção
"Why three local processes": *"uvicorn agentos.api.asgi:app — HTTP + SSE. ... Never calls a
provider."* — o princípio de manter trabalho longo fora do processo da API foi violado aqui).

Do lado do frontend, `frontend/src/api/client.ts` não aplica nenhum timeout/`AbortSignal` por
padrão às chamadas de mutação — então quando o backend trava, o `fetch` do usuário também fica
pendurado sem nunca dar erro. É exatamente o sintoma relatado: clicar em "Conectar" e nada
acontecer, para sempre.

**Direção da correção:**
1. Envolva as chamadas bloqueantes (`services.mcp.approve/.test`, `services.plugins.inspect/.approve`) com `starlette.concurrency.run_in_threadpool` (já é dependência transitiva do FastAPI) nas rotas correspondentes, para que o event loop nunca seja bloqueado por elas.
2. Escreva um teste de regressão que prove isso: dispare uma aprovação lenta (um `connect`/fetcher fake com `time.sleep`) e, **concorrentemente**, confirme que uma segunda requisição simples (ex.: `GET /healthz` ou `GET /v1/mcp/servers`) responde antes da primeira terminar. Use `TestClient` com `httpx.AsyncClient`/duas threads, ou teste isso via `asyncio.gather` num teste `async def` que bata direto no app ASGI.
3. Considerando a arquitetura documentada do projeto, avalie se a solução certa a médio prazo é mover approve/inspect para o worker (como turnos já fazem) em vez de só usar threadpool — mas para esta sessão, threadpool é a correção mínima que resolve o travamento; não expanda o escopo para uma reescrita do worker a menos que o dono peça.

## Causa 4 (gap de produto, confirmado) — GitHub, Vercel, Google Drive e Gmail precisam de OAuth, que não existe

`@modelcontextprotocol/server-github` (a entrada `github` do catálogo,
`src/agentos/mcp/catalog.py`) está **descontinuado** — rodar `npx -y @modelcontextprotocol/server-github`
imprime `npm warn deprecated ... Package no longer supported`. O caminho recomendado hoje pelo
GitHub é o servidor MCP remoto hospedado deles.

Mais fundamental: **GitHub (hospedado), Vercel, Google Drive e Gmail exigem OAuth 2.0
(authorization code + redirect de consentimento)**, não um token estático colado numa caixa de
texto. O plano original de MCP já registrava isso como fora de escopo
(`docs/superpowers/plans/2026-08-14-mcp-connectors.md`, seção de follow-ups: *"OAuth para
servidores HTTP... v1 aceita apenas um bearer token colado pelo usuário; o fluxo OAuth completo é
seu próprio plano"*) — ninguém pegou esse trabalho depois. Sem ele, essas quatro integrações
específicas são **impossíveis** de conectar hoje, não importa quantos bugs de 1–3 sejam
corrigidos.

**Isto não é só um problema de código — leia com atenção antes de começar a implementar:**
Um fluxo OAuth genérico (authorization code + PKCE, callback local em `127.0.0.1`, troca de código
por token, refresh, armazenamento cifrado do refresh token com o mesmo `ProviderSecretCipher` já
usado para credenciais de provider) é implementável e cabe nesta sessão. **Mas fazer Google
Drive/Gmail e Vercel funcionarem de verdade também exige um `client_id` OAuth registrado pelo
dono do Orin no Google Cloud Console e no painel de integrações da Vercel** — isso é uma credencial
de aplicativo que só o dono do projeto pode obter (não é algo que se implementa em código, é um
cadastro externo). **Não invente nem hardcode um client_id.** Se ao chegar nesse ponto o
`client_id` não estiver disponível (variável de ambiente, arquivo de config, ou fornecido pelo
dono na conversa), pare e pergunte ao dono como ele quer prosseguir — implemente o mecanismo
genérico e deixe pronto para receber credenciais, mas não simule ou finja uma integração
funcionando sem elas.

Para o GitHub especificamente, existe uma saída mais simples que não depende de OAuth: o servidor
MCP hospedado do GitHub aceita um **Personal Access Token via header `Authorization: Bearer`** em
alguns modos (verifique a documentação atual deles antes de assumir). Se isso ainda for verdade,
trocar a entrada `github` do catálogo de `transport: stdio` (via npx, deprecated) para
`transport: http` apontando para o endpoint hospedado deles resolve GitHub **sem precisar de
OAuth**, e é um bom primeiro passo de baixo risco antes de atacar o problema de OAuth completo.

**Escopo sugerido para esta sessão nesta causa** (negocie com o dono se achar que é demais para
"one shot" junto com as causas 1–3, que são bugs claros e não негociáveis):
1. Trocar/atualizar a entrada `github` do catálogo para o endpoint hospedado, se ainda aceitar PAT.
2. Implementar o mecanismo genérico de OAuth (broker de autorização, callback loopback, troca de
   token, refresh, storage cifrado) como uma peça nova e testável — sem acoplar a nenhum provider
   específico.
3. Adicionar entradas de catálogo para Vercel e Google Drive/Gmail com `auth: "oauth"` em vez de
   `secrets`, condicionadas à existência de client_id configurado — se não houver client_id
   configurado, a entrada aparece no catálogo mas o botão de conectar explica que falta
   configuração do lado do Orin, em vez de falhar silenciosamente ou travar.

## Ordem de trabalho recomendada

1. **Causa 1** primeiro — é a que bloqueia o caso de uso mais simples e mais visível (instalar um
   plugin conhecido). TDD: teste com o SKILL.md real do superpowers, veja falhar pelo motivo
   documentado acima, corrija, veja passar.
2. **Causa 2** — sem isso, testar a Causa 3 fica difícil de fazer de forma confiável (uma conexão
   MCP real que trava mascara se o threadpool resolveu o problema ou só adiou).
3. **Causa 3** — proteção estrutural contra qualquer I/O lento futuro, não só as duas causas
   acima.
4. **Causa 4** — maior, discuta escopo com o dono se o tempo apertar; pelo menos entregue o
   catálogo do GitHub corrigido e o mecanismo genérico de OAuth, mesmo que Vercel/Google fiquem
   "prontos mas sem credencial" ao final.

## Convenções desta sessão a seguir (não redecidir)

- **TDD**: teste falha pelo motivo certo → implementação mínima → teste passa → commit. Um commit
  por correção, mensagem `fix(plugins): ...` / `fix(mcp): ...`, trailer
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- **Subagente só em ponto crítico**: dispare uma revisão de segurança independente (não de
  implementação) sobre qualquer código novo de OAuth (armazenamento de token, validação de
  `redirect_uri`, geração de `state`/PKCE) antes de considerar a Causa 4 concluída — a mesma
  prática que pegou os dois bugs de segurança reais na sessão de MCP (injeção via `%VAR%` no
  stdio, TOCTOU de DNS rebinding no HTTP). O resto, implemente inline.
- **Rode a suíte inteira antes de declarar qualquer coisa pronta**:
  ```bash
  python -m pytest tests/unit tests/integration -q
  ```
  ```bash
  cd frontend && npx tsc -b --noEmit && npx eslint . --max-warnings=0 && npx vitest run && npm run build
  ```
  A única violação de lint pré-existente e não relacionada é em `RuntimeSettingsPage.tsx:41`
  (confirmada pré-existente via `git stash` numa sessão anterior) — não precisa corrigir a menos
  que o dono peça.
- **Prova, não afirmação**: para cada causa corrigida, rode a reprodução real documentada acima
  (contra o superpowers de verdade, contra um `npx` de verdade) e cole o resultado antes de dizer
  que está corrigido. Este é exatamente o motivo de todo esse handoff existir: a suíte de testes
  ficou verde na sessão anterior e o produto continuava quebrado no uso real.

## Estado no momento deste handoff

**Branch:** `main`
**HEAD:** `0df272d`
**Suítes**: verdes (backend e frontend), mas — como este documento existe para deixar claro —
"verde" aqui não significa "funciona com um plugin ou servidor MCP real". Não confie só na suíte
para declarar as causas 1–4 resolvidas; use as reproduções reais descritas em cada seção.

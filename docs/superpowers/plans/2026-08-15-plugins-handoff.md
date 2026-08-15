# Handoff — Implementar o plano de Plugins

**Data:** 2026-08-15
**Branch:** `main` (trabalho direto no branch, sem worktree — decisão explícita do dono nesta sessão)
**HEAD na entrega:** `073a9ee`
**Suíte no momento do handoff:**
- Backend: `python -m pytest tests/unit tests/integration/test_mcp_end_to_end.py -q` → **1414 passed, 3 skipped**
- Frontend: `npx tsc -b --noEmit && npx vitest run && npm run build` → **297 passed**, typecheck limpo, build ok

## O que fazer nesta sessão

Implementar **integralmente** o plano em
[`docs/superpowers/plans/2026-08-14-plugins.md`](2026-08-14-plugins.md) — 17 tasks, TDD
(RED→GREEN→commit por task), seguindo a skill `superpowers:executing-plans` (ou
`superpowers:subagent-driven-development` se o dono pedir explicitamente subagentes).

**Antes de escrever qualquer código**, pergunte ao dono se quer um worktree isolado ou trabalhar
direto no branch atual — nesta sessão anterior (MCP) a resposta foi "trabalhe direto no main"; não
assuma que vale para esta sessão também.

**Sobre subagentes:** a orientação do dono na sessão de MCP foi "use subagentes somente em locais
críticos, o restante vai inline mesmo". O padrão que funcionou: implementar tudo inline
task-por-task, e disparar **um** subagente de revisão de segurança independente (não de
implementação) sobre o código que faz I/O de rede ou de processo antes de prosseguir. Nesta base de
plugins, o equivalente é `src/agentos/plugins/fetcher.py` (clona repositório git, copia diretório
local, decide o que é "dentro do pacote") e `src/agentos/plugins/activator.py` (registra/desfaz
contribuições em múltiplas portas). Peça ao revisor para provar qualquer bypass de path traversal
ou de symlink-escape com um caso concreto, não apenas apontar teoricamente.

## Por que este plano existe

Há três planos irmãos, todos escritos na mesma sessão de brainstorming
(`docs/superpowers/plans/2026-08-14-mcp-connectors.md`,
`2026-08-14-plugins.md`, `2026-08-14-settings-shell-refactor.md`). O dono pediu para implementar
primeiro o de MCP — **feito, 22 commits, `331d02b`..`073a9ee`** — e agora pediu este handoff para
prosseguir com o de **Plugins**. O terceiro plano (refatoração de Settings) continua **não
implementado**; não o inicie a menos que o dono peça.

## Estado do que já existe (MCP) e por que importa para Plugins

O plano de plugins foi escrito **antes** do código de MCP existir, então algumas premissas dele
sobre a API do `McpServerService` merecem confirmação contra o código real antes de codificar
`PluginActivator` (Task 9) — a mesma lição da sessão de MCP se repete: *planos escritos antes do
código existir contêm suposições que o código real pode não bater 100%.*

Confirmado por leitura direta do código (`src/agentos/mcp/service.py`) no momento deste handoff:

- `McpServerService.propose(command: Mapping) -> dict` aceita **só** estas chaves:
  `user_id, slug, display_name, transport, command, args, url, secret_names, catalog_id,
  tool_allowlist`. Qualquer chave fora dessa lista levanta `McpServiceError`. O
  `PluginActivator` (Task 9 do plano de plugins) **precisa incluir `"user_id"`** no dict que monta
  a partir de um `McpServerContribution` — o plano não mostra o dict completo, só descreve em
  prosa. Não esqueça esse campo.
- `propose()` sempre cria em `state="pending_approval"` — nunca ativa nada sozinho. Isso já é
  exatamente o que o plano de plugins exige ("um servidor MCP de um plugin nunca começa ativo").
- `McpServerService.remove(user_id: str, server_id: str) -> None` — assinatura posicional, bate
  com o `FakeMcpService.remove(self, user_id, server_id)` que o plano de plugins já usa nos testes
  do `PluginActivator`.
- Duas exceções existem além de `McpServiceError` genérica: `McpServerNotFound` (servidor
  inexistente) e `McpConnectionFailed` (só usada por `approve()`/`test()`, que o `PluginActivator`
  não chama). O `PluginActivator` só precisa tratar `McpServiceError` (classe-mãe) num `except`
  amplo para o rollback da Task 9.
- `get(user_id, server_id)` agora devolve também uma lista `tools` (nome + descrição + estado
  `enabled`) — isso não afeta o plano de plugins, que nunca lê esse campo, mas se algum teste
  comparar um dict completo por igualdade em vez de campos específicos, vai quebrar por essa razão.
  Prefira asserções por campo, não por igualdade total do dict.

Confirmado também que **não há drift** nas duas dependências do backend de skills que a Task 5/8
do plano de plugins usa:

- `agentos.skills.parser.parse_skill_file(path, *, include_instructions=True, source=SkillSource.BUILTIN, scope=SkillScope.SYSTEM) -> Skill` — levanta `SkillParseError` (não uma exceção genérica) para um SKILL.md malformado. A Task 5 (inspector) deve capturar `SkillParseError` especificamente ao decidir "skill quebrada vira warning, não crash" — capturar `Exception` bare também funciona mas perde precisão.
- `agentos.skills.models.SkillSource` ainda **não tem** o membro `PLUGIN` — é a Task 8 do plano de
  plugins que o adiciona. Não presuma que já existe.
- `PostgresSkillLibraryService._insert(self, skill: Skill, *, user_id: str | None) -> None` — a
  Task 8 precisa adicionar um parâmetro `plugin_id` a esse método e à coluna nova da tabela
  `skills`, exatamente como o plano descreve.

## Duas armadilhas reais encontradas na sessão de MCP que provavelmente se repetem aqui

Ambas custaram uma rodada de fix na sessão anterior. As Tasks 14/15 do plano de plugins
(`PluginApprovalCard`, `PluginsSection`) são estruturalmente quase idênticas ao que já foi
construído para MCP (`McpApprovalCard`, `McpSection`) — a mesma classe de bug tende a aparecer de
novo se não for evitada de propósito:

1. **`role="tab"` sem um padrão de tablist completo quebra a role ARIA implícita.** Um `<button>`
   com `role="tab"` explícito deixa de ter role `"button"` para fins de
   `getByRole('button', ...)` do Testing Library — e um `role="tab"` sem `tabpanel`/navegação por
   seta é ARIA incompleta de qualquer forma. Prefira `aria-pressed` num `<button>` normal para um
   simples alternador de modo (foi a correção aplicada em `McpServerForm.tsx`).

2. **A regra de lint `react-hooks/set-state-in-effect` rejeita `setState` síncrono no corpo de um
   `useEffect`** (inclusive quando o `useEffect` só chama uma função `useCallback` que por sua vez
   chama `setState` de forma síncrona antes de qualquer `await`/`.then`). O padrão que passa no
   lint deste repo (ver `SchedulesPage.tsx`, e o fix aplicado em `McpSection.tsx`/
   `McpServerForm.tsx`): nunca chame `setLoading(true)`/`setError(null)` como primeira instrução
   síncrona; deixe o estado inicial já nascer `true` quando fizer sentido, e só chame `setState`
   dentro de `.then(...)`/`.catch(...)`/`.finally(...)`. Rode `npx eslint . --max-warnings=0` cedo,
   não só no final — é mais barato corrigir um componente por vez do que descobrir os dois bugs
   juntos no fim.

Rode `npx eslint . --max-warnings=0` no frontend depois de cada componente novo; a única violação
pré-existente e não relacionada que deve continuar aparecendo é em `RuntimeSettingsPage.tsx:41`
(confirmado pré-existente via `git stash`, não é algo para corrigir nesta sessão a menos que o
dono peça).

## Ordem de execução dentro do plano de plugins

O próprio plano já declara isso na seção "Ordem de execução recomendada", mas resumindo: as Tasks
1–12 (backend: modelos, manifest parser, sources, fetcher, inspector, marketplace, migração,
skills-de-plugin, activator, service, tools de agente, rotas HTTP) **não dependem** do plano de
Settings. Só a Task 15 (seção de Plugins na UI de Settings) precisaria dele — e, como não foi
implementado, siga exatamente o padrão que a Task 16 do plano de MCP usou: renderize o conteúdo
dentro do `SettingsPage` atual (`frontend/src/features/settings/SettingsPage.tsx`), adicionando
`'plugins'` ao array `SECTIONS` e uma rota `/settings/plugins` em `frontend/src/app/routes.tsx` —
sem esperar a refatoração do shell. Veja `frontend/src/features/mcp/McpSection.tsx` como exemplo
direto de como isso ficou para MCP.

O card de aprovação de plugin (Task 14) deve reaproveitar as classes CSS `approval-card*` já
criadas em `frontend/src/styles/agentos.css` para o card de MCP — elas foram desenhadas de
propósito para servir aos dois (ver comentário no CSS: "used by both MCP servers and plugins").
Não crie um novo bloco de classes do zero.

## Verificação a cada task

Backend:
```bash
python -m pytest tests/unit/plugins -q
```
Depois de cada task que toca `agent_tools.py`, `gateway.py`, `bootstrap/production.py`, rode
também:
```bash
python -m pytest tests/unit -q
```

Frontend:
```bash
cd frontend && npx tsc -b --noEmit && npx eslint . --max-warnings=0 && npx vitest run
```

Ao final de todas as 17 tasks:
```bash
python -m pytest tests/unit tests/integration -q
cd frontend && npm run build
```

## Convenções já estabelecidas nesta sessão (seguir, não redecidir)

- **Um commit por task**, mensagem no formato `feat(plugins): <o que mudou>` /
  `test(plugins): <...>` / `docs(plugins): <...>`, trailer
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- **RED antes de GREEN sempre**: escreva o teste, rode e confirme que falha pelo motivo esperado
  (`ModuleNotFoundError`/`ImportError`/`TypeError` de argumento faltando), só então implemente.
- **Toda migração nova** deve ser validada com
  `python -c "from sqlalchemy import create_engine; from agentos.persistence.postgres.migrate import upgrade, downgrade; e = create_engine('sqlite:///:memory:'); upgrade(e); downgrade(e, '<revisão anterior>')"`
  — `alembic upgrade head --sql` não funciona neste repo por causa de uma migração antiga
  (`0002_persistence_integrity`) que exige reflexão de tabela em modo batch; use o helper
  `agentos.persistence.postgres.migrate.upgrade(engine)` contra SQLite em memória em vez disso.
- **Teste de schema "boundary"**: `tests/unit/persistence/test_postgres_schema.py` mantém uma
  lista exaustiva de todas as tabelas permitidas. Toda tabela nova precisa ser adicionada a essa
  lista ou o teste falha — não é um teste esquecido, é deliberado.
- **Nunca aceitar valor de segredo como argumento de tool do agente.** O padrão usado em
  `configure_mcp` (rejeitar qualquer `**kwargs` não declarado explicitamente) deve se repetir em
  `install_plugin`/`inspect_plugin` se algum dia um plugin trouxer campos sensíveis — hoje o plano
  de plugins não expõe segredo nenhum diretamente (delega para o fluxo de MCP quando o plugin traz
  `.mcp.json`), então isso já está coberto por construção, mas vale manter esse princípio se o
  desenho mudar.

## Onde estão os três planos e o que cada um cobre

| Plano | Status | Arquivo |
|---|---|---|
| MCP Connectors | ✅ Completo (22 commits) | `2026-08-14-mcp-connectors.md` |
| Plugins | ⬜ Não iniciado — **este handoff** | `2026-08-14-plugins.md` |
| Settings Shell Refactor | ⬜ Não iniciado | `2026-08-14-settings-shell-refactor.md` |

Depois de completar o plano de Plugins, o próximo handoff natural seria o de Settings Shell
Refactor — ele unifica a navegação de Settings (hoje `SECTIONS` cresceu ad-hoc com `'mcp'` e,
depois deste plano, `'plugins'`) e transformaria `ProviderSettingsPage` numa grade de cards. Não
inicie esse terceiro plano nesta sessão a menos que o dono peça explicitamente.

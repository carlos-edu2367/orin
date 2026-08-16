# Handoff — Planejar a Categoria A (hooks/commands de plugins)

**Data:** 2026-08-16
**Branch:** `main` (trabalho direto no branch, sem worktree — decisão explícita do dono na sessão anterior)
**HEAD na entrega:** `d3dfc7a`
**Suíte no momento do handoff:**
- Backend: `python -m pytest tests/unit -q` → **1522 passed, 3 skipped, 1 failed** (a falha é `tests/unit/launcher/test_paths_and_profile.py::test_runtime_profile_uses_the_embedded_release_version`, pré-existente e sem relação com plugins — compara uma versão hardcoded `"0.2.1"` contra `pyproject.toml`, que já está em `0.2.2`; não é algo desta linha de trabalho)
- Frontend: `npx tsc -b && npx vitest run` → **347 passed**, typecheck limpo

## O que fazer nesta sessão

**Só planejamento — não implemente nada.** O objetivo é chegar a um spec aprovado em
`docs/superpowers/specs/` e um plano de implementação em `docs/superpowers/plans/` para a
Categoria A: plugins que só declaram `hooks/`/`commands/` (sem skill, MCP server ou agent), que
hoje são detectados e **completamente ignorados** — nenhum código lê o conteúdo desses diretórios
além de um `Path.exists()`.

Siga o fluxo que este projeto já usa: `superpowers:brainstorming` → (spec escrito, autorrevisado,
commitado, aprovado pelo dono) → `superpowers:writing-plans`. **Não invoque `executing-plans` nem
`subagent-driven-development` nesta sessão** — não é para implementar, é para planejar.

A skill de brainstorming manda decompor um projeto quando ele cobre múltiplos subsistemas
independentes antes de aprofundar em qualquer um. Avalie logo cedo se "motor de hooks" e "motor de
commands" são, na prática, dois subprojetos com pouco código em comum (parecem ser: hooks reagem a
eventos do ciclo de vida do agente, commands são invocações explícitas do usuário — mecanismos de
disparo bem diferentes) — se forem, pergunte ao dono qual entra primeiro, do jeito que a sessão
anterior fez para B vs. A (via `AskUserQuestion`, apresentando os dois lados, não decidindo sozinho).

## Por que este handoff existe

A sessão anterior mapeou duas lacunas no sistema de plugins do Orin, testando a Biblioteca de
Plugins ao vivo (`docs/superpowers/specs/2026-08-16-plugin-library-design.md`):

- **Categoria B** (repositórios sem manifesto — MCP servers "crus"): spec, plano e implementação
  completos nesta mesma sessão. Ver
  [2026-08-16-plugin-library-raw-mcp-install-design.md](../specs/2026-08-16-plugin-library-raw-mcp-install-design.md)
  e [2026-08-16-plugin-library-raw-mcp-install.md](2026-08-16-plugin-library-raw-mcp-install.md).
  13 commits, `500026e`..`d3dfc7a`, tudo direto em `main`.
- **Categoria A** (plugins só-hooks/commands): identificada, mas **deliberadamente adiada** —
  o dono escolheu atacar B primeiro porque a infraestrutura já existia e o escopo era menor. A
  Categoria A foi descrita como "provavelmente um projeto de escopo considerável" já na primeira
  mensagem da sessão anterior, e essa avaliação não foi revista — **é o trabalho desta sessão
  decidir se isso é verdade e, se for, como decompor.**

## O que já sabemos sobre a Categoria A (confirmado por leitura direta do código)

Exemplo real que reproduz o problema:
https://github.com/eugeniughelbur/obsidian-second-brain — manifesto válido
(`.claude-plugin/plugin.json`), só declara `hooks/` e `commands/`, zero skills/MCP/agents.

- `src/agentos/plugins/inspector.py:66-69` — se `hooks/` ou `commands/` existir no pacote, o
  inspector só adiciona um warning de texto (`"O plugin declara hooks; hooks não são suportados e
  não serão ativados."` / `"comandos declarados não são executados; apenas SKILL.md é suportado"`).
  Não há nenhum parsing do conteúdo desses diretórios em lugar nenhum do código — nem nome de
  arquivo, nem schema, nem contagem. É puramente detectado-e-ignorado.
- `src/agentos/plugins/models.py:73-96` — `PluginInspection.contribution_count = len(skills) +
  len(mcp_servers) + len(agents)`; `is_installable = contribution_count > 0`; `requires_approval =
  is_installable`. Hooks/commands nunca entram nessa conta — um plugin só-hooks tem
  `contribution_count == 0`.
- `src/agentos/plugins/activator.py` — exatamente três kinds de contribuição, cada um tratado
  explicitamente: `inspection.skills` → `skill_library.install_plugin_skills(...)`,
  `inspection.mcp_servers` → `mcp_service.propose(...)`, `inspection.agents` →
  `agent_templates.register(...)` (se `agent_templates` foi injetado). Não existe uma quarta
  coleção (`inspection.hooks` ou `inspection.commands`) no modelo nem no ativador — precisaria ser
  criada do zero, incluindo o rollback simétrico em `deactivate()`.
- `src/agentos/plugins/service.py:118-119` — se `not inspection.is_installable`, levanta
  `PluginServiceError("this package contributes nothing Orin can use")`. **Novidade desta sessão:**
  `PluginServiceError` agora aceita um `code=` opcional (adicionado para a Categoria B, ver
  `src/agentos/plugins/service.py:25-27`) e `gateway.py`'s `plugin_service_error` handler já lê
  `exc.code` em vez de devolver sempre `"plugin_operation_rejected"` hardcoded
  (`src/agentos/api/gateway.py`, handler `plugin_service_error`). Ou seja: **o mecanismo para dar
  um código de erro específico para "só contribui hooks/commands, e isso ainda não é suportado" já
  existe e está pronto para reuso** — só falta decidir se essa mensagem genérica merece um código
  próprio (ex.: `plugin_hooks_only`) quando o desenho da Categoria A avançar o suficiente para
  precisar distinguir esse caso na UI.

## O que NÃO sabemos ainda — é o trabalho de exploração desta sessão

A pergunta mais importante, levantada mas **não respondida** na sessão anterior: **quais pontos de
extensão (hook points) existem hoje no runtime do Orin, e eles servem como base para um sistema de
hooks de plugin?**

Uma pista concreta encontrada por grep em `src/agentos/agentic/runtime.py` (chamadas
`self._life(turn, "<state>", ...)`, que repassam para `store.lifecycle(turn, state, **payload)`):
`running`, `context_updated`, `retrying`, `waiting_tool`, `tool_started`, `tool_failed`,
`waiting_user`, `completed`, `context_compacting`, `context_compacted`, `tool_finished`, `failed`,
`cancelled`.

**Atenção: isso é telemetria interna do turno** (para alimentar o timeline que a UI mostra em
tempo real via SSE), **não é necessariamente um ponto de extensão onde um handler de plugin
poderia se registrar e executar código**. Pode ser reaproveitável como base, pode não ser — não dei
esse passo. Também vale conferir `docs/superpowers/specs/2026-08-06-event-system-design.md`: é
sobre um Event System genérico (outbox/event-bus, RFC 103) que parece ser uma camada de
observabilidade/entrega de eventos do sistema, um conceito provavelmente **diferente** de "hook
point que uma skill/plugin declara para reagir a um momento do ciclo de vida" (o equivalente ao que
o Claude Code chama de `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop` etc.
em `hooks/`). Não presuma que um documento responde a pergunta do outro — leia os dois com
ceticismo antes de decidir que "os hook points já existem" ou que "precisam ser inventados do
zero".

Outras perguntas em aberto que o brainstorming precisa resolver, na ordem que provavelmente faz
sentido levantar:

1. **Hooks e commands são um projeto só ou dois?** (ver seção acima — avalie isso antes de
   aprofundar em qualquer um).
2. **Que forma um handler de hook assume no Orin?** O Claude Code usa comandos de shell
   configurados em JSON (`hooks/*.json` ou dentro do `plugin.json`) que recebem contexto por
   stdin/env e podem bloquear a ação via exit code. Isso é compatível com o modelo de execução do
   Orin (que roda como serviço, não como CLI local por sessão)? Vale a pena olhar como
   `src/agentos/agentic/session.py` e `runtime.py` já lidam com processos externos (ex.: MCP
   stdio, terminal RFC-404) para entender que superfície de execução de processo já existe e é
   seguro reaproveitar.
3. **Que forma um "command" assume?** Slash-commands customizados — hoje o roteamento de mensagem
   do usuário para o agente já existe; um command de plugin precisaria interceptar antes desse
   roteamento normal. Onde isso se encaixa na pipeline de chat (`src/agentos/conversations/chat.py`)?
4. **Segurança:** hooks executáveis vindos de um repositório de terceiros (mesmo que aprovado pelo
   usuário) são a superfície de risco mais óbvia deste projeto inteiro — execução de código
   arbitrário no lado do servidor. O desenho precisa decidir sandboxing/permissões antes de
   qualquer implementação. Vale revisar como o Orin já isola execução de ferramentas (RFC-404
   terminal, RFC-405 browser, RFC-406 capabilities — specs em
   `docs/superpowers/specs/2026-08-07-rfc-40{4,5,6}-*-design.md`) como precedente de como este
   projeto já pensa sobre isolamento.

## Convenções desta base de plugins a seguir (não redecidir)

- Specs em `docs/superpowers/specs/YYYY-MM-DD-<topico>-design.md`; planos em
  `docs/superpowers/plans/YYYY-MM-DD-<topico>.md`.
- Um commit por task na fase de implementação (quando essa fase chegar, numa sessão futura),
  mensagem `feat(plugins): ...` / `test(plugins): ...`, trailer `Co-Authored-By: Claude Sonnet 5
  <noreply@anthropic.com>`.
- RED antes de GREEN sempre — mas isso é para a sessão de implementação, não esta.
- Ambiente Python: `.venv/Scripts/python.exe` (não há `pytest` no `python` global do PATH neste
  Windows). Frontend: `cd frontend && npx vitest run` / `npx tsc -b` / `npx eslint . --max-warnings=0`.
- Ao apresentar opções de abordagem (a skill de brainstorming pede 2-3), lidere com a recomendada e
  explique o porquê — mas não decida por conta própria uma pergunta de priorização ou de escopo que
  é do dono; use `AskUserQuestion` como a sessão anterior fez para B vs. A.
- O dono pediu explicitamente, na sessão anterior, para trabalhar direto em `main` sem worktree e
  sem pausar para perguntas de execução — **isso valia para uma sessão de implementação já
  planejada**; não assuma que a mesma autorização vale para pular etapas do brainstorming (perguntas
  de priorização/escopo) nesta sessão de planejamento. Pergunte normalmente, a menos que o dono
  repita a instrução aqui.

## Estado dos documentos relacionados

| Documento | Status | Arquivo |
|---|---|---|
| Plugin Library (busca + instalação) | ✅ Completo | `specs/2026-08-16-plugin-library-design.md`, `plans/2026-08-16-plugin-library.md` |
| Categoria B (MCP cru, sem manifesto) | ✅ Completo | `specs/2026-08-16-plugin-library-raw-mcp-install-design.md`, `plans/2026-08-16-plugin-library-raw-mcp-install.md` |
| Categoria A (hooks/commands) | ⬜ Não iniciado — **este handoff** | (a criar) |

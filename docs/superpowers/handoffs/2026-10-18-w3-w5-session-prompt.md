# Prompt para a próxima sessão — Orin Code: ondas W3, W4 e W5

> Cole tudo abaixo da linha em uma nova sessão do Claude Code aberta em `C:\Users\carlos\Documents\projetos\orin`.

---

Você vai continuar a refatoração do Orin Code para paridade com o Claude Code 2.1.299. As ondas **W0, W1 e W2 já estão prontas** na branch `feat/orin-code-parity-w0-w3` (commits locais, **sem push**). Sua tarefa: entregar **W3, W4 e W5**, uma de cada vez, nesse mesmo fluxo, sem me fazer perguntas (a spec e o fluxo já foram aprovados; se surgir uma decisão real, escolha a opção mais segura/fail-closed, registre no ledger e siga).

## Leia antes de começar

1. Spec mestre (aprovada): `docs/superpowers/specs/2026-10-18-orin-code-claude-code-full-parity-design.md` — W3 = §6, W4 = §7, W5 = §8; também §2 (critérios de aceitação), §13–§17.
2. Planos e ledgers das ondas feitas (o padrão a seguir e as pendências herdadas):
   - `docs/superpowers/plans/2026-10-18-w0-foundations.md` + `...-w0-foundations-ledger.md`
   - `docs/superpowers/plans/2026-10-18-w1-permissions-security.md` + `...-w1-permissions-security-ledger.md`
   - `docs/superpowers/plans/2026-10-18-w2-hooks.md` + `...-w2-hooks-ledger.md`
3. Revisões adversariais: `docs/superpowers/reviews/2026-10-18-w0-review.md`, `-w1-review.md`, `-w2-review.md` (seção "Can defer" de cada uma lista o que cai em W3/W4/W5).
4. Specs de engenharia reversa do Claude Code: `C:\Users\carlos\Downloads\claude-re-specs\` (01 loop, 02 tools/`sdk-tools.d.ts`, 03 permissões/hooks/settings, 04 MCP/skills/subagentes/plugins/compactação/checkpoints). **Clean-room:** nunca copie prompts ou o `sdk-tools.d.ts` para o repo.

## Estado atual (baseline)

- Branch: `feat/orin-code-parity-w0-w3` (continue nela; não faça push; não troque de branch).
- Backend: `uv run pytest -q tests/unit` → **2857 passed, 53 skipped** (~3 min).
- Frontend: `npm --prefix frontend test` → **596 passed** (houve 1 falha intermitente não identificada numa execução; duas reexecuções seguidas passaram), `lint` limpo, `build` ok.
- O que existe: `agentos.agentic.tools.public_names` (nomes Claude Code atrás de `experimental.claudeToolNames`, default off), `agentos.settings` (5 camadas, interop `.claude`, filtro de trust), `agentos.agentic.lifecycle` (33 eventos + bus), `agentos.permissions` (engine, shell analyzer fail-closed, paths protegidos, trust store, auto-mode), `agentos.hooks` (hooks de settings com protocolo Claude Code).

## Fluxo obrigatório por onda (o mesmo usado em W0–W2)

Para **cada** onda (W3, depois W4, depois W5):

1. **Plano (você, modelo principal):** use `superpowers:writing-plans`. Leia o código real que vai mudar antes de escrever. Plano em `docs/superpowers/plans/2026-10-XX-wN-<tema>.md` com: header da skill, Global Constraints, **Deviations** intencionais, File Structure, tarefas TDD com **código completo** (testes e implementação), comandos exatos e saída esperada, e uma última tarefa de regressão + ledger. Faça a autorrevisão do plano (testes que quebram no Windows, ordem de construção de objetos, imports circulares, caminhos de workspace gerenciado vs local). Commite o plano.
2. **Executor (subagente `general-purpose`, model `haiku`, foreground):** executa o plano tarefa a tarefa (RED → GREEN → commit), roda as suítes indicadas, registra desvios no ledger e responde com `git log --oneline <plano>..HEAD`, resultado da suíte completa e desvios. Use o mesmo prompt de executor das ondas anteriores (veja o padrão abaixo).
3. **Revisor (subagente `general-purpose`, model `opus`, foreground):** revisão adversarial negative-first do range do executor; reproduz cada achado com scripts no scratchpad (nunca no repo); escreve `docs/superpowers/reviews/2026-10-XX-wN-review.md` com tabela (ID, severidade, arquivo:linha, problema, reprodução, **fix exato com testes**) e seções "Must fix before W(N+1)" e "Can defer"; commita só esse arquivo. **Se o revisor for interrompido no meio da escrita** (aconteceu na W2: um classificador de segurança cortou o documento), complete você mesmo o documento a partir do que ele já escreveu e do resumo dele, marcando o que você completou.
4. **Corretor (subagente `general-purpose`, model `haiku`, foreground):** aplica todos os "must fix" (e os baratos de "can defer" que você escolher), um commit por achado, TDD, suíte completa + frontend verdes, reexecuta as reproduções da revisão no scratchpad, adiciona a seção "Review fixes" no ledger.
5. **Verificação sua:** rode você mesmo `uv run pytest -q tests/unit` e, se a onda tocou frontend, `npm --prefix frontend test`, `lint`, `build`. **Checagem visual obrigatória** de toda mudança de UI (regra #3 do CLAUDE.md): use o browser embutido; se o fluxo real exigir provedor configurado, faça um harness temporário (`frontend/visual-check.html` + `frontend/src/__visual__/...`) com o CSS real, verifique desktop dark e mobile 375px light, estados inicial/sucesso/erro, **remova o harness** e commite só as correções. Na W1 essa checagem achou 2 bugs de layout que os testes não pegavam.

### Padrão do prompt do executor (adapte o nome do plano)

> You are implementing a written plan in C:\Users\carlos\Documents\projetos\orin (Windows; Git Bash via the Bash tool; Python via `uv run ...`; frontend via `npm --prefix frontend ...`). Branch `feat/orin-code-parity-w0-w3` — do not switch. NEVER `git push`. Plan: <path>. Execute tasks in order with strict TDD (test → FAIL → implement → PASS → commit with the given message + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Copy plan code faithfully; line numbers are approximate. Fix implementation rather than tests; never delete/skip/weaken existing tests; log deviations. Run the suites the plan lists; at the end run the FULL backend suite (timeout 600000 ms) and frontend checks. Reply with the commit list, suite results, every deviation, anything incomplete — 100% honest.

## Escopo de cada onda (spec + pendências herdadas)

### W3 — Loop e contexto (spec §6)
- Tetos do modo Code (`maxTurns`, `maxBudgetUsd`, `safetyMaxToolRounds=500`), recuperação de `max_tokens`, retry com backoff/jitter/`retry-after` + cadeia `fallbackModel` com `PreModelSwitch`/`PostModelSwitch`, fila de input (persistida, injetada entre rodadas de tools; `UserPromptSubmit` na injeção), interrupção preservando texto parcial, mecanismo único de `<system-reminder>`.
- System prompt **clean-room** do modo Code com snapshot por conversa (hash, até `compact_boundary`), `excludeDynamicSystemPromptSections`; **ligar `experimental.claudeToolNames` por padrão no modo Code** junto com o prompt novo e renderizar nomes de tools/campos via `public_name()` em prompts e mensagens de erro (W0-R20).
- Compactação com fronteiras persistidas (`compact_boundary`, `microcompact_boundary`), `/compact [instruções]` (endpoint), reanexo pós-compact (até 5 arquivos, todos, plano), seções padronizadas, `/context` (dados por categoria; UI fica para W7).
- Hierarquia de memória em arquivo (`ORIN.md`/`CLAUDE.md`/`AGENTS.md`, rules com `paths:`, `.local`, imports `@` com profundidade 5 e detecção de ciclo, subdiretórios sob demanda, `claudeMdExcludes`, `InstructionsLoaded` por arquivo), consolidação opt-in `autoDreamEnabled`, `outputStyle`, `effort`.
- Pendências herdadas: W0-R14 (SessionStart com `source` explícito vindo do store) e o resíduo da W2-R6 (SessionStart pode disparar de novo após um primeiro prompt bloqueado); emitir `SessionEnd` e `FileChanged`; aplicar bloqueio de `TaskCompleted` e `ConfigChange`; decidir quais eventos não bloqueantes podem injetar contexto (`PostToolUseFailure`, `SubagentStart`, hoje descartados pelo bus); **decidir W1-R9** (turno não interativo executa `ask` comum — proposta: fail-closed por padrão com opt-in).

### W4 — Ferramentas (spec §7)
- Dividir a god class `AgentToolset` em módulos por tool com `ToolSpec` real (Pydantic `Input`/`Output`, nomes Claude Code canônicos, aliases legados; levar `public_name`/`permission_category` para dentro do registry — W0 deviation 1 e W0-R19 #9). Os nomes internos passam a ser os canônicos; manter aliases para histórico/regras/UI.
- Paridade de cada tool da tabela §7 (Read unificado com dedupe `file_unchanged`, Edit/Write com `structuredPatch`, Grep com `ripgrep` empacotado + fallback, Bash com cwd persistente e `persistedOutputPath`, **`PowerShell` + análise de PowerShell no shell analyzer**, `TaskOutput`/`TaskStop` unificados, `WebFetch` com `prompt`, `WebSearch` com domínios, `ToolSearch`, `Skill`, `CronCreate/Delete/List`, `ScheduleWakeup`, `Monitor`, `PushNotification`) e expor nos schemas públicos as capacidades hoje escondidas (W0-R21).
- `additionalDirectories`/`--add-dir`/`DirectoryAdded` (W1 deviation 4).
- Checkpoints por edição content-addressed, rewind por mensagem com restore seguro (TOCTOU/symlink/junction).
- Remover a armadilha `matches_permission_rule` legada em `code_mode/permissions.py` (nota do corretor da W1).

### W5 — Extensibilidade (spec §8)
- MCP completo (escopos, `.mcp.json` com aprovação + trust, `${VAR}`, sse/ws, resources/prompts/elicitation com `Elicitation`/`ElicitationResult`, limites de saída, `mcp serve`, encerramento stdio com Job Object).
- Skills em disco (todas as fontes, frontmatter completo, substituições, `` !`cmd` `` pelo pipeline de permissões), agentes definidos em arquivo + teams (`SendMessage`, `TeammateIdle`), aliases de modelo, sem escalada de modo.
- Plugins compatíveis com `.claude-plugin` + marketplaces + `userConfig` no cofre; **unificar hooks de plugin no dispatcher da W2** (deviation 1 da W2), hooks em frontmatter de skill/agente, bloqueio de `SubagentStop`.
- LSP por plugin (diagnósticos como `<system-reminder>`).

## Pendências transversais ainda abertas (não esquecer no relatório final)

- W0-R11: redação de segredos em sinks (SSE/atividade/hooks http) → W7. O rótulo de atividade de hook mostra até 120 caracteres do comando.
- W0-R18: política de merge de listas e proveniência por elemento → W7.
- W1-R10 / nota da W1: thread do classificador `auto` em timeout e breaker por turno; `autoMode.model` ignorado pelo provedor HTTP real → W6.
- Reuso de PID no Windows: o kill de árvore da W2 valida por creation time; o Python da Microsoft Store escapa do Job Object (documentado). Não rodamos CI Linux localmente: os caminhos POSIX (symlinks, `killpg`) só foram cobertos por testes, sem execução em Linux.

## Regras do Carlos (CLAUDE.md global) que valem aqui

- Superpowers sempre; TDD; postura adversarial (tente quebrar o que escreveu); avise proativamente de problemas.
- Checagem visual obrigatória de mudanças de UI.
- **Sinceridade radical no final**: liste o que ficou pendente, o que não foi verificado e as limitações.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nunca faça push.

Ao terminar as três ondas, escreva um relatório final (o que foi entregue por onda, resultados de teste, desvios, pendências) e pare.

# Design Spec (Mestre): Orin Code — Paridade Completa com Claude Code 2.1.299 + Extras do Orin

**Data:** 2026-10-18
**Status:** Aprovada por seção no brainstorming (aguardando revisão do documento escrito)
**Versão-base do Orin:** 0.8.3 (`81a30eb`)
**Fonte de engenharia reversa:** pacote `claude-re-specs` (docs 00–09, Claude Code `2.1.299`, `claude.exe` SHA-256 `295b49ec…df2b`), cruzado com o código atual do Orin.
**Relação com specs anteriores:** substitui, onde houver conflito, `2026-10-15-orin-code-claude-parity-design.md` (nomes de modos, origem do system prompt, escopo). O que aquela spec já entregou (modo imutável Chat/Code, `TodoBar`, `PlanApprovalCard`, diffs inline, auto-background) é base, não é refeito.

---

## 0. Decisões tomadas no brainstorming

| # | Decisão | Escolha |
|---|---|---|
| D1 | Superfícies | **UI web/Electron + CLI headless (`orin code -p`, stream-json, subcomandos)**. Sem TUI de terminal. |
| D2 | Formatos de configuração | **Ler `.claude/*`, `CLAUDE.md`, `.mcp.json`, `.claude-plugin` e equivalentes `.orin`/`ORIN.md`; na mesma camada, `.orin` vence; escrita sempre em `.orin`.** |
| D3 | Recursos de nuvem/conta Anthropic | **Equivalentes locais** onde faz sentido; excluir o que é exclusivo de conta Anthropic. |
| D4 | Estratégia | **Abordagem A — strangler incremental em ondas W0–W9**, uma release por onda. Spec mestre (este documento) + **um plano por onda**. |
| D5 | Nomes de ferramentas | **Nomes do Claude Code são canônicos** (`Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`, `Agent`, `TodoWrite`…); snake_case atual vira alias. Tools exclusivas do Orin seguem o mesmo estilo (`Remember`, `Recall`, `SearchCode`…). |
| D6 | CLI headless | **Cliente fino da API local** (um único motor). |

Princípios transversais: clean-room (não copiar prompts nem `sdk-tools.d.ts` da Anthropic para o repo — reimplementar apenas interfaces), fail-closed em segurança, extras do Orin preservados, paridade mensurável por ledger de testes.

---

## 1. Matriz de Gaps (Claude Code 2.1.299 × Orin 0.8.3)

Legenda: ✅ paridade · 🟡 parcial · ❌ ausente.

| Subsistema | Claude Code | Orin hoje (evidência) | Status | Onda |
|---|---|---|---|---|
| Loop agêntico | Até `end_turn`; `--max-turns`/`--max-budget-usd`; recuperação `max_tokens`; cadeia `--fallback-model`; fila de mensagens | `AgenticTurnRuntime.run` (`agentic/runtime.py:293`), tetos 12 iterações/24 ações/300 s (`session.py:774`), 1 retry, fallback só de chaves (`provider_key_fallback.py`) | 🟡 | W3 |
| Snapshot de system prompt | Gravado na 1ª requisição, reutilizado até `compact_boundary` | Só breakpoints de cache Anthropic (`provider_stream.py`) | 🟡 | W3 |
| Compactação | Micro + full com fronteiras, `/compact [instr]`, hooks | Micro 65% / full 82% (`runtime.py:901-1124`), títulos PT, sem fronteira persistida | 🟡 | W3 |
| Ferramentas | 45 (registry tipado) | ~25 equivalentes em god class `AgentToolset` (`agent_tools.py`, 6.176 linhas) | 🟡 | W0/W4 |
| Permissões | 6 modos, deny→ask→allow multi-camada, especificadores ricos, caminhos protegidos | 4 modos (`code_mode/models.py:14`), regras só em `.orin/settings.json` (`code_mode/permissions.py`), **Bash por prefixo — vulnerável** (`permissions.py:140`) | 🟡 | W1 |
| Hooks | 33 eventos, 4 handlers, todas as camadas | 3 eventos, só plugins (`plugins/hooks_manifest.py:17`) | ❌ | W2 |
| Settings | 5 camadas + filtro de segurança do projeto | `data/agent-runtime.json` + `.orin/settings.json` | ❌ | W0 |
| Memória em arquivo | Hierarquia CLAUDE.md + rules + imports + local | `AGENTS.md`/`CLAUDE.md`/`CONVENTIONS.md` só na raiz (`session.py:139`) | 🟡 | W3 |
| MCP | stdio/http/sse/ws, 4 escopos, aprovação `.mcp.json`, resources, prompts, elicitation, `serve` | stdio + http, OAuth, busca lazy (`mcp/`) | 🟡 | W5 |
| Skills | Disco + frontmatter completo + substituições | DB versionado + 8 builtins (`skills/`) | 🟡 | W5 |
| Subagentes | Definidos em arquivo, teams, background | Builtins explore/plan/general/fork + `create_agent` | 🟡 | W5 |
| Plugins | `.claude-plugin`, marketplaces, `userConfig` | Biblioteca própria com inspeção sem execução (`plugins/`) | 🟡 | W5 |
| LSP | Diagnósticos pós-edição via plugins | — | ❌ | W5 |
| Slash commands | ~83 embutidos | Só comandos de plugins (`CommandPicker.tsx`) | ❌ | W7 |
| Checkpoints | Por mensagem, código/conversa, TOCTOU-safe | Por turno (`code_mode/checkpoints.py`) | 🟡 | W4/W7 |
| Headless/SDK | `-p`, stream-json, `control_request`, subcomandos | Inexistente (`launcher/cli.py` só gerencia serviço) | ❌ | W6 |
| UX | Keybindings 27 contextos, fila, `/context`, status line | Shift+Tab, TodoBar, PlanApprovalCard, command palette | 🟡 | W7 |

**Extras do Orin preservados (não regredir):** multiprovedor + fallback de chaves + catálogo de modelos; busca semântica `SearchCode`/`ProjectMap`; memória tipada com aprendizado automático; projetos; agendamentos; geração de mídia; `create_agent`/`ask_agents`; MCP OAuth + lazy search; preview servers; `VerifyProject`; modo servidor multiperfil; Electron; instaladores; leitura de Office/visão.

---

## 2. Ondas, dependências e critérios de aceitação

Cada onda: plano próprio em `docs/superpowers/plans/`, entrega atrás de `experimental.<onda>` até fechar, release `0.9.x` (W0 = `0.9.0`).

| Onda | Escopo | Depende | Critério de aceitação (resumo) |
|---|---|---|---|
| **W0 Fundações** | Tool Registry, Settings 5 camadas, barramento de lifecycle | — | `AgentToolset` removido; todas as tools existentes passam pelo registry com schemas Pydantic; settings efetivos com proveniência; todos os pontos de evento emitidos (sem handlers) |
| **W1 Permissões e segurança** | 6 modos, especificadores, shell AST, caminhos protegidos, diretórios de trabalho, Workspace Trust, `auto`, `dontAsk` | W0 | Corpus adversarial de shell 100% verde; repo malicioso não executa nada antes do trust; ledger dos 6 modos coberto |
| **W2 Hooks** | 33 eventos, 4 handlers, todas as fontes, protocolo, Job Objects | W0, W1 | Cada evento com teste de disparo; exit 0/2/outros e JSON de controle conformes; árvore de processos morta no timeout |
| **W3 Loop e contexto** | Limites, recuperação, fallback, fila, interrupção, prompt snapshot, compactação, memória em arquivo, output styles, effort | W0, W2 | Turno longo sem tetos artificiais; compactação com `compact_boundary` persistido; hierarquia de memória com imports |
| **W4 Ferramentas** | Tools faltantes + paridade de saídas + checkpoints por edição | W0, W1 | Ledger das 45 tools: cada uma `included` com teste ou `excluded:<motivo>` |
| **W5 Extensibilidade** | MCP completo, Skills em disco, agentes em arquivo + teams, plugins compatíveis, LSP | W0–W2 | Repo configurado para Claude Code funciona no Orin sem alteração (fixture de interop) |
| **W6 CLI headless + SDK** | `orin code`, stream-json, `control_request`, subcomandos | W1–W5 | `claude-agent-sdk` oficial (Python) dirige `orin code` sem modificações |
| **W7 UX web** | Slash commands, keybindings, fila, rewind por mensagem, superfícies, settings UIs | W1–W5 | Verificação visual Playwright (desktop/mobile, dark/light, estados ativo/vazio/erro) + axe em toda superfície nova |
| **W8 Equivalentes locais** | Review multiagente, ultraplan, CLI remota, `plugin eval` | W5, W6 | `orin code review` produz achados verificados via `ReportFindings` |
| **W9 Conformidade** | Ledger completo, docs, drift de ADRs, README | todas | Meta-teste do ledger verde; zero item `included` sem teste |

---

## 3. W0 — Fundações

### 3.1 Tool Registry (`src/agentos/agentic/tools/`)

- Um módulo por tool/família: `files/read.py`, `files/edit.py`, `files/write.py`, `files/notebook.py`, `search/glob.py`, `search/grep.py`, `shell/bash.py`, `shell/powershell.py`, `shell/tasks.py`, `web/fetch.py`, `web/search.py`, `planning/plan_mode.py`, `planning/todo.py`, `planning/tasks.py`, `interaction/ask_user.py`, `agents/agent.py`, `git/worktree.py`, `mcp/*.py`, `orin/memory.py`, `orin/search_code.py`, `orin/media.py`, `orin/projects.py`, `orin/skills_admin.py`, `orin/plugins_admin.py`, `orin/preview.py`, `orin/verify.py`.
- Contrato `ToolSpec`:
  - `name` (canônico estilo Claude Code), `aliases` (snake_case antigos), `description`.
  - `Input`/`Output`: modelos Pydantic com **os mesmos nomes de campo** das interfaces públicas do Claude Code. JSON Schema enviado ao provedor gerado do modelo `Input`.
  - `read_only`, `concurrency_safe`, `permission_category` ∈ {`read`, `edit`, `exec`, `network`, `mcp`, `agent`, `interaction`, `meta`}.
  - `validate_input(input, ctx)`, `check_permissions(input, ctx) -> PermissionCheck`, `call(input, ctx) -> Output`, `summarize(input, output) -> ToolSummary` (alimenta a UI), `max_result_chars`.
- Modelo de execução **inalterado**: síncrono, com `ThreadPoolExecutor` para tools `concurrency_safe` (como `runtime.py:1643`). Migração para asyncio está **fora** deste programa.
- Strangler: `AgentToolset` vira fachada que delega ao registry; cada tool migra com seu teste; a fachada é removida ao fim da W0.
- **Legal:** o arquivo `sdk-tools.d.ts` não entra no repo. Testes de contrato listam os nomes de campos esperados (fatos de interface) sem copiar o arquivo.

### 3.2 Settings (`src/agentos/settings/`)

Precedência (maior → menor):

1. **managed:** `%ProgramData%\Orin\managed-settings.json` (POSIX: `/etc/orin/managed-settings.json`) + `managed-settings.d/*.json` (ordem alfabética, ignorando dotfiles).
2. **CLI/sessão:** flags e `--settings <arquivo|json>`.
3. **local:** `.orin/settings.local.json` sobre `.claude/settings.local.json`.
4. **projeto:** `.orin/settings.json` sobre `.claude/settings.json`.
5. **usuário:** `ORIN_CONFIG_DIR/settings.json` sobre `~/.claude/settings.json`.

Regras de merge: escalares sobrescrevem; arrays em `permissions.{allow,ask,deny,additionalDirectories}` são unidos e deduplicados; objetos fazem deep-merge. `EffectiveSettings` (Pydantic) guarda a **camada de origem por chave** (para `/status`, `/config`, editor de permissões).

Filtro de segurança para as camadas projeto/local: ignorar `claudeMd`, caminho de memória, `allowedMcpServers`/`deniedMcpServers`, `apiKeyHelper` e `statusLine` até o Workspace Trust; booleanos que ampliam privilégio só aceitam o valor restritivo; quando managed/CLI/usuário ativam uma restrição, o projeto não a desliga.

Migração: `data/agent-runtime.json` é importado uma vez para a camada de usuário; a UI passa a gravar nela. Escrita atômica (temp + fsync + `os.replace`), lock de arquivo e checagem de mtime (UI e CLI podem escrever concorrentemente). Watcher emite `ConfigChange`.

`--setting-sources user,project,local` e os modos `--bare`/`--safe-mode`/`--restricted` filtram camadas aqui.

### 3.3 Barramento de lifecycle (`src/agentos/agentic/lifecycle.py`)

Eventos tipados (dataclasses) para os 33 pontos de hook + `StatusLine`/`FileSuggestion`. A W0 instrumenta a emissão; a W2 conecta os handlers. O mesmo barramento alimenta a atividade SSE, substituindo emissões ad-hoc (`runtime._hooks`, `runtime._life`, `_RuntimeStore.lifecycle`).

### 3.4 O que não muda

SQLite + migrações Alembic (aplicadas pelo launcher), publisher/worker, SSE, invariante Chat×Code (o modo da conversa é imutável). Chat e Code compartilham o motor; o modo define toolset padrão, prompt e modo de permissão inicial.

---

## 4. W1 — Permissões e segurança

### 4.1 Modos

Canônicos: `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions` (`manual` é alias de `default`). Aliases legados: `ask_permissions→default`, `accept_edits→acceptEdits`, `plan_mode→plan`, `full_autonomy→bypassPermissions`. Migração de DB converte `conversations.permission_mode`.

Ciclo do Shift+Tab: `default → acceptEdits → plan`, mais `auto` se configurado e `bypassPermissions` só com `permissions.allowBypassPermissionsMode` (equivalente a `--allow-dangerously-skip-permissions`). `--restricted` recusa `bypassPermissions`.

### 4.2 Pipeline por chamada de tool (ordem estrita)

1. `validate_input` (schema).
2. Hooks `PreToolUse` (`allow`/`deny`/`ask`, `updatedInput`, `additionalContext`).
3. Regras `deny → ask → allow` (todas as camadas + `--allowedTools`/`--disallowedTools` + concessões da sessão). Um `deny` vence tudo, inclusive allow mais específico e qualquer modo.
4. `ToolSpec.check_permissions` (diretórios de trabalho, caminhos protegidos, análise de shell).
5. Fallback por modo: `bypassPermissions` aprova; `acceptEdits` aprova edições e comandos de arquivo dentro dos diretórios de trabalho fora de caminhos protegidos; `auto` consulta o classificador; `dontAsk` nega; `default` pergunta; `plan` nega mutações (exceto arquivos de plano).
6. Se o resultado for ask: hook `PermissionRequest` → card na UI, `can_use_tool` no SDK, `--permission-prompt-tool`, ou negação se `--permission-prompts none`.

"Sim, não perguntar de novo" grava a regra sugerida na camada **local** (`.orin/settings.local.json`) ou só na sessão, à escolha do usuário. Negações são registradas em `permission_denials` do resultado do turno e disparam `PermissionDenied`.

### 4.3 Especificadores

- `Bash(cmd)`, `Bash(prefix:*)`, `Bash(prefix *)`; `PowerShell(...)` com a mesma gramática.
- `Read(glob)`, `Edit(glob)`, `Write(glob)` com semântica gitignore: `//abs`, `~/home`, `/relativo-ao-arquivo-de-settings`, `./` ou relativo ao cwd. Uma regra `Edit` cobre `Edit`, `Write` e `NotebookEdit`.
- `WebFetch(domain:host)`, `WebSearch`, `Agent(tipo)`, `Skill(nome)`, `mcp__srv`, `mcp__srv__tool`, `mcp__srv__*`.
- Nomes antigos (`run_command(...)`, `edit_file(...)`) continuam aceitos via aliases.

### 4.4 Shell seguro

- Parser AST: `tree-sitter` + `tree-sitter-bash` (MIT) e `tree-sitter-powershell` (MIT). **Não** usar `bashlex` (GPL-3, incompatível com MIT).
- Cada subcomando (`&&`, `||`, `;`, `|`, `&`, nova linha), redirecionamento e substituição (`$()`, crases, `<()`, `>()`) é avaliado individualmente. Allow só se **todos** casarem com allow; qualquer deny → deny; qualquer ask → ask; substituição de comando ou de processo nunca recebe auto-allow.
- **Fail-closed:** falha de parse ou construção não reconhecida → `ask`.
- Preset de regras allow embutido "testes e builds" (por exemplo `pytest`, `npm test`, `npm run build`, `tsc`, `ruff`, `git status|diff|log`), **ligado por padrão para usuários migrados**, removível. Preserva o comportamento atual do `accept_edits` do Orin.
- **Spike obrigatório** como primeira tarefa da W1: validar os wheels do `tree-sitter` dentro do build PyInstaller (Windows e Linux).

### 4.5 Fronteiras

- Diretórios de trabalho = cwd + `permissions.additionalDirectories` + `--add-dir` + `/add-dir` (este último dispara `DirectoryAdded`). Leitura fora deles → ask.
- Caminhos protegidos (sempre ask, mesmo em `acceptEdits`): os dotfiles da lista REA (`.bashrc`, `.bash_profile`, `.envrc`, `.npmrc`, `.mcp.json`, `.claude.json`, `.pre-commit-config.yaml`; lista completa REA na doc 03 §1.3, copiada como constante), `.git/`, configs em `.orin/` e `.claude/` e internos de `ORIN_CONFIG_DIR`/`ORIN_DATA_DIR` (histórico, file-history, snapshots).
- Deny gerenciado vence `bypassPermissions`.
- **Workspace Trust:** na primeira sessão Code em uma pasta cujos configs trazem hooks, MCP de projeto, `apiKeyHelper`, `statusLine`, skills com `` !`cmd` `` ou marketplaces extras, a UI mostra um card de confiança; até aprovar, esses itens ficam inertes. Confiança é guardada por caminho canônico no estado do usuário. Headless: `-p` exige pasta já confiável ou `--trust`.

### 4.6 Modo `auto` (classificador local)

- Usa o "modelo rápido" configurado (`autoMode.model`, padrão: favorito marcado como rápido).
- Regras `autoMode.{environment, allow, soft_deny, hard_deny}`; `hard_deny` é determinístico e avaliado antes do LLM.
- Entrada do classificador: ação proposta + resumo curto do pedido do usuário + ambiente. Saída: `allow` | `deny` | `ask` + razão.
- Falha/timeout do classificador → `ask`. Circuit breaker: 3 negações consecutivas ou 20 na sessão → o modo `auto` passa a perguntar ao usuário até o próximo turno do usuário (configurável em `autoMode.circuitBreaker`).
- Custo contabilizado no uso da sessão.
- Subcomandos `orin code auto-mode config|defaults|reset|critique` e `/auto-mode-setup`.

---

## 5. W2 — Hooks

### 5.1 Fontes

`hooks` das 5 camadas de settings; `hooks/hooks.json` de plugins (restrição de plugin mantida); frontmatter `hooks` de skills e agentes (ativos só durante sua execução); callbacks registrados pelo SDK no `initialize`. Hooks de projeto/local só após Workspace Trust.

### 5.2 Handlers

| Tipo | Execução | Retorno |
|---|---|---|
| `command` | Subprocesso; payload JSON no stdin; env `ORIN_PROJECT_DIR` (+ alias `CLAUDE_PROJECT_DIR`), `ORIN_ENV_FILE` (+ `CLAUDE_ENV_FILE`) em `SessionStart`; `timeout` padrão 60 s; `async`, `statusMessage`, `onFailure` | exit code + stdout/stderr |
| `http` | `POST` do payload para `url`; `headers` com interpolação restrita a `allowedEnvVars`; netpolicy existente (`oauth/netpolicy.py`) para SSRF | JSON de controle no corpo |
| `prompt` | Modelo rápido avalia `prompt` + payload | `{ok, reason}` |
| `agent` | Subagente read-only (`Read`, `Grep`, `Glob`) | `{ok, reason}` |

### 5.3 Protocolo

- Exit `0`: stdout é JSON de controle se for um objeto; em `SessionStart`/`UserPromptSubmit`, texto puro vira contexto.
- Exit `2`: bloqueia; stderr vai para o modelo (`PreToolUse`, `Stop`, `SubagentStop`, `ConfigChange`, `TaskCompleted`) ou para o usuário (`UserPromptSubmit`). `PermissionRequest` decide via `hookSpecificOutput.decision`, não por exit code.
- Outros códigos: não bloqueiam (stderr no log/verbose), exceto `onFailure: "block"`.
- JSON de controle: `continue`, `stopReason`, `suppressOutput`, `systemMessage`, `decision`, `reason`, `hookSpecificOutput` (variantes `PreToolUse`, `PermissionRequest`, `PostToolUse`/`SessionStart`/`UserPromptSubmit`/`SubagentStart`).
- Eventos não bloqueantes (conjunto `MXt`): `Notification`, `SessionStart`, `SessionEnd`, `Setup`, `StopFailure`, `SubagentStart`, `PostToolUseFailure`, `PostCompact`, `PostModelSwitch`, `PermissionDenied`, `WorktreeCreate`, `WorktreeRemove`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `DirectoryAdded`, `MessageDisplay`, `StatusLine`, `FileSuggestion`.
- Agregação: hooks do mesmo evento rodam em paralelo, deduplicados por comando idêntico; decisão final `deny > ask > allow`; múltiplos `updatedInput` → vence o da camada de maior precedência.
- Processos: Windows Job Object com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (via `ctypes`), process group no POSIX; árvore inteira encerrada no timeout.
- Os 3 eventos atuais de plugins (`SessionStart`, `PostToolUse`, `PostCompact`) migram para este motor; hooks de plugin mantêm o timeout declarado (1–30 s) e a execução confinada ao diretório do plugin.

### 5.4 Mapeamento evento → ponto de emissão

| Evento | Ponto no Orin |
|---|---|
| `PreToolUse` | Dispatcher do registry, passo 2 do pipeline (hoje `runtime._run_toolset`, `runtime.py:1417`) |
| `PostToolUse` / `PostToolUseFailure` | Dispatcher, após `ToolSpec.call` (sucesso/erro) |
| `PostToolBatch` | Fim do lote em `_run_toolset` |
| `Notification` | Novo `NotificationService` (W7): ocioso, permissão pendente, pergunta ao usuário |
| `UserPromptSubmit` | `ChatWorker.run` antes de `AgenticTurnRuntime.run` e na injeção de mensagens da fila |
| `UserPromptExpansion` | Novo expansor de slash commands e `@`-menções (W7) |
| `SessionStart` | Criação/retomada da conversa, `/clear`, pós-compactação (`source`: startup, resume, clear, compact) |
| `SessionEnd` | Arquivar/excluir conversa, `/exit`, `/clear`, fim de processo headless |
| `Stop` / `StopFailure` | `runtime.run` em `end_turn` / `runtime._fail` |
| `SubagentStart` / `SubagentStop` | Lançamento/término de subagente (`session.py`, executores de subagente) |
| `PreCompact` / `PostCompact` | Em volta de `runtime._run_compaction` (`runtime.py:980`) e de `/compact` |
| `PreModelSwitch` / `PostModelSwitch` | `/model`, `set_model` do SDK, troca por fallback |
| `PermissionRequest` / `PermissionDenied` | `ToolPermissionBroker` antes de criar o pedido / em toda negação |
| `Setup` | `orin code --init` / `--maintenance` e primeira confiança de um workspace |
| `TeammateIdle` | Mailbox de teams (W5) |
| `TaskCreated` / `TaskCompleted` | Tools `TaskCreate` / `TaskUpdate(status=completed)` |
| `Elicitation` / `ElicitationResult` | Cliente MCP (W5) |
| `ConfigChange` | Watcher de settings (W0) |
| `WorktreeCreate` / `WorktreeRemove` | `code_mode/worktree.py` |
| `InstructionsLoaded` | Carregador de memória em arquivo (W3) |
| `CwdChanged` | `/cd` e mudança de cwd persistente do `Bash` |
| `FileChanged` | Watcher dos arquivos lidos na sessão (modificação externa) |
| `DirectoryAdded` | `/add-dir`, `--add-dir` |
| `MessageDisplay` | Finalização de mensagem do assistente para exibição |
| `StatusLine` / `FileSuggestion` | Comando de status line / provedor de sugestões de `@` (W7) |

---

## 6. W3 — Loop e contexto

### 6.1 Loop (modo Code)

- Remover os tetos de iteração/ações/deadline do modo Code. Encerramento por `end_turn`, interrupção, `maxTurns`, `maxBudgetUsd` ou teto de segurança configurável `safetyMaxToolRounds` (padrão 500 rodadas de tools; ao atingir, o turno pausa e pergunta se continua). Detecção de stall de 180 s mantida. Modo Chat mantém os limites atuais.
- `stop_reason == max_tokens`: se truncou uma `tool_use`, refaz a requisição com `max_tokens` maior (até o máximo do modelo no catálogo); se truncou texto, envia continuação (até 3 vezes).
- Retry com backoff exponencial + jitter respeitando `retry-after`; ordem de recuperação: rotação de chaves (existente) → cadeia `fallbackModel`/`--fallback-model`; o primário volta a ser tentado no próximo turno do usuário. Troca dispara `Pre/PostModelSwitch`.
- Fila de input: mensagens enviadas durante o turno são persistidas (`turn_queue`) e injetadas na próxima fronteira entre rodadas de tools; "enviar agora" = interromper + enviar. `queued_turn_count` no resultado.
- Interrupção: preserva o texto parcial; tool em execução recebe resultado `interrupted by user`.
- Anexos `<system-reminder>`: mecanismo único para mudanças de todo, arquivos alterados externamente, lembrete de plan mode, `additionalContext` de hooks, diagnósticos LSP e memória relevante.
- `--json-schema`: saída estruturada validada com retries (subtipo `error_max_structured_output_retries`).

### 6.2 System prompt

- Texto **próprio** (clean-room) com a mesma estrutura de seções do Claude Code (identidade, tom, uso de ferramentas, tarefas, plan mode, segurança, ambiente) + seções dos extras do Orin. Corrige a premissa da spec 2026-10-15 de "prompt extraído".
- Snapshot por conversa: renderizado na 1ª requisição, persistido com hash, reutilizado até o próximo `compact_boundary` (`systemPromptSnapshot: on|off`).
- `excludeDynamicSystemPromptSections`: cwd, env, git status e data vão para a 1ª mensagem do usuário.
- Cache: breakpoints em system, tools e última mensagem na Anthropic; prefixo estável beneficia o cache automático da OpenAI.

### 6.3 Compactação

- Microcompact: limpa `tool_result` antigos, mantendo intactos os 8 mais recentes (constante atual `RECENT_UNAGED_TOOL_RESULTS`), substituindo os demais por marcador; persiste `microcompact_boundary`.
- Autocompact: limiar = janela do modelo − reserva (saída máxima do modelo + 13.000 tokens de margem), configurável por `autoCompact`/`--autocompact <auto|tokens>`.
- `/compact [instruções]`; `PreCompact` pode adicionar instruções ou bloquear.
- Resumo com seções padronizadas (substitui os títulos em PT atuais); persistir `compact_boundary {trigger, pre_tokens, post_tokens, cumulative_dropped_tokens}`; mensagem de continuação própria.
- Pós-compact: reanexar os arquivos lidos mais recentes (até 5), todos e plano ativo; re-renderizar o snapshot; `PostCompact`.
- Compactação reativa em erro de context-length: mantida.
- `/context`: uso por categoria (system prompt, tools, tools MCP, memória, mensagens, livre).

### 6.4 Memória em arquivo

- Camadas: managed (`%ProgramData%\Orin\ORIN.md` e `CLAUDE.md`) → usuário (`ORIN_CONFIG_DIR/ORIN.md` + `~/.claude/CLAUDE.md`) → projeto, do cwd até a raiz do repo: `ORIN.md`, `CLAUDE.md`, `.orin/ORIN.md`, `.claude/CLAUDE.md`, `AGENTS.md`, `CONVENTIONS.md` → regras `.orin/rules/*.md` e `.claude/rules/*.md` (condicionais por frontmatter `paths:`) → local `ORIN.local.md`/`CLAUDE.local.md`.
- Memória de subdiretório carregada quando um arquivo dele é lido pela primeira vez.
- Imports `@caminho`: profundidade máxima 5, detecção de ciclo.
- `claudeMdExcludes`, hook `InstructionsLoaded`, `/memory` (edição), `/init` (gera `ORIN.md`).
- Auto-memory: a memória tipada em DB do Orin **é** a auto-memory (superior ao `MEMORY.md`). Adiciona-se consolidação em background opt-in (`autoDreamEnabled`) sobre transcripts recentes com o modelo rápido; `/pause-memory` pausa aprendizado e consolidação.

### 6.5 Output styles, effort, sugestões

- `outputStyle`: Default, Explanatory, Learning + custom em `.orin/output-styles/*.md` e `.claude/output-styles/*.md`.
- `effort` low/medium/high/xhigh/max: Anthropic → thinking budget; OpenAI → `reasoning_effort`; demais provedores → ignorado com aviso na UI.
- Sugestão do próximo prompt (opt-in, modelo rápido).

---

## 7. W4 — Ferramentas

| Claude Code | Origem no Orin | Ação |
|---|---|---|
| `Read` | `read_file` + `view_file` + `transcribe_pdf` | Unificar: texto, imagem, PDF (`pages`), `.ipynb`; dedupe `file_unchanged`; Office (docx/xlsx/pptx) como extra |
| `Edit` | `edit_file` | Saída `FileEditOutput` (`structuredPatch`, `originalFile`, `userModified`); batch `edits` mantido como extensão |
| `Write` | `write_file` | Saída com `type: create|update`, patch |
| `NotebookEdit` | `notebook_edit` | Alinhar campos |
| `Glob` | `glob_files` | — |
| `Grep` | `search_files` | `ripgrep` empacotado no instalador; fallback Python |
| `Bash` | `run_command` | cwd persistente, `run_in_background`, `persistedOutputPath`, auto-background de dev server (extra) |
| `PowerShell` | — | Nova no Windows |
| `TaskOutput` / `TaskStop` | `read_process_output` / `stop_process` | Renomear; unificar com subagentes em background |
| `Agent` | `run_subagent` (+ `create_agent`/`ask_agents` extras) | `subagent_type` inclui custom; `isolation: worktree`; `model`; `mode`; `run_in_background` |
| `TodoWrite`, `TaskCreate/Get/List/Update` | existentes | Alinhar campos/saídas |
| `AskUserQuestion` | `ask_user` | Alinhar campos (`header`, `options[].preview`, `multiSelect`) |
| `EnterPlanMode` / `ExitPlanMode` | existentes | `ExitPlanMode` recebe o conteúdo do plano |
| `EnterWorktree` / `ExitWorktree` | existentes | Alinhar |
| `WebFetch` | `fetch_url` | `url` + `prompt`: markdown + resposta do modelo rápido; cache de 15 min |
| `WebSearch` | `web_search` | `allowed_domains`/`blocked_domains` |
| `ToolSearch` | `search_mcp_tools` | Sintaxe `select:` e palavras-chave; base das tools diferidas |
| `Skill` | `use_skill` | Renomear |
| `CronCreate/Delete/List`, `ScheduleWakeup` | scheduler do Orin | Novas sobre `scheduler/` |
| `Monitor` | — | Nova: observa processo/arquivo em background e acorda o agente na condição |
| `PushNotification` | — | Nova: notificação Electron/web + hook `Notification` |
| `ListMcpResources`, `ReadMcpResource`, `ReadMcpResourceDir`, `RefreshMcpTools` | — | Novas (W5) |
| `ReportFindings` | — | Nova (review local, W8) |
| `SendMessage` | — | Nova (teams, W5) |

**Excluídas (produto/conta Anthropic):** `Artifact`, `ClaudeDesign`, `OfferChromeSetup`, `ShowOnboardingRolePicker`, `SendFeedback`, `ReadNotifications`, `RemoteTrigger`, `ProposeGoal`, `Workflow`, `Projects` (o Orin tem tools próprias de projetos).

**Extras do Orin mantidos:** `SearchCode`, `ProjectMap`, `VerifyProject`, `PreviewStart/Stop/Logs`, `Remember`/`Recall`, administração de skills/MCP/plugins, `GenerateImage/Audio/Video`, tools de projeto, `ProposeCodeSession`, `WriteContract`/`ReportVerification` (opcionais).

### 7.1 Checkpoints por edição

- Snapshot content-addressed (sha256) em `ORIN_DATA_DIR/file-history/<sessão>/` antes de cada `Edit`/`Write`/`NotebookEdit`, agrupado por mensagem do usuário. Migra `code_mode/checkpoints.py`.
- Rewind por mensagem: código, conversa ou ambos (`rewind_files` no SDK).
- Restore seguro: realpath dentro dos diretórios de trabalho; recusa reparse points/symlinks e diretórios trocados; confere hash antes de sobrescrever (mitigação TOCTOU, risco #4 da spec REA).
- **Limitação:** mudanças feitas via `Bash` não são rastreadas (paridade com o Claude Code).

---

## 8. W5 — Extensibilidade

### 8.1 MCP

- Escopos e fontes (conflito de nome: local > projeto > usuário): managed (`managed-mcp.json`, `allowedMcpServers`/`deniedMcpServers`); usuário (servidores da UI + `mcpServers` de `~/.claude.json`); projeto (`.mcp.json` na raiz + `.orin/mcp.json`); local (estado por usuário×projeto, incluindo `projects[cwd].mcpServers` de `~/.claude.json`); `--mcp-config`, `--strict-mcp-config`.
- `.mcp.json`: aprovação por servidor (exige trust); `enableAllProjectMcpServers`, `enabledMcpjsonServers`, `disabledMcpjsonServers`; `mcp reset-project-choices`.
- Expansão `${VAR}` e `${VAR:-default}` em `command`, `args`, `env`, `url`, `headers`; variável ausente → servidor em erro, sem quebrar a sessão.
- Transportes: stdio, http (streamable), **sse**, **ws**. Encerramento stdio: fecha stdin → 300 ms → encerra o Job Object.
- Tools `mcp__srv__tool` (nomes sanitizados), diferidas via `ToolSearch` acima de 10% do contexto (`ENABLE_TOOL_SEARCH=auto:<N>`).
- Resources: `@srv:uri` no composer + tools de resource. Prompts → `/mcp__srv__prompt args`. Elicitation → card de formulário + hooks. `list_changed` → atualização do catálogo.
- Saída: aviso acima de 10k tokens, corte em 25k (`MAX_MCP_OUTPUT_TOKENS`); excedente persistido em arquivo.
- OAuth existente mantido; `mcp login|logout`.
- `orin code mcp serve`: expõe `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash` por stdio; só regras allow/deny valem; o que exigiria prompt é negado.

### 8.2 Skills

- Fontes: managed; usuário (`ORIN_CONFIG_DIR/skills`, `~/.claude/skills`); projeto (`.orin/skills`, `.claude/skills`); plugins (`plugin:skill`); legado `commands/*.md` (subpastas → `/dir:nome`); skills em DB versionadas e builtins (extra). Conflito: managed > usuário > projeto > plugin, com aviso.
- Frontmatter: `name`, `description`, `argument-hint`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `model`, `effort`, `context: fork`, `agent`, `hooks`.
- Catálogo no prompt: só nome + descrição, com orçamento; corpo carregado sob demanda.
- Substituições: `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N`, `${ORIN_SESSION_ID}`/`${CLAUDE_SESSION_ID}`, `${ORIN_SKILL_DIR}`/`${CLAUDE_SKILL_DIR}`.
- `` !`cmd` `` passa pelo pipeline de permissões do `Bash`; em skills de projeto, só após trust. `allowed-tools` concede allow temporário durante a skill e nunca vence deny.
- `context: fork` executa via `Agent`. Watcher + `/reload-skills`.

### 8.3 Subagentes e teams

- Fontes: builtins (`general-purpose`, `Explore`, `Plan`, `verifier`), usuário (`ORIN_CONFIG_DIR/agents`, `~/.claude/agents`), projeto (`.orin/agents`, `.claude/agents`), plugins, `--agents` JSON, `create_agent` (DB). Precedência: managed > CLI > projeto > usuário > plugin > builtin.
- Frontmatter: `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `memory`, `background`, `isolation`, `omitClaudeMd`, `hooks`, `effort`, `color`.
- Aliases de modelo: `sonnet`/`opus`/`haiku`/`inherit` resolvidos por `modelAliases` nos settings (padrão: `haiku` → modelo rápido favorito; sem mapeamento → `inherit`).
- Sem escalada: subagente não roda em modo mais permissivo que o pai (exceto pai em `bypassPermissions`).
- Agentes de plugin: `hooks`, `mcpServers`, `permissionMode` ignorados.
- Background: `agentId` + `outputFile`, `task_notification`, `TaskOutput`/`TaskStop`.
- Teams: `team_name`/`name` → teammate com mailbox persistido + tool `SendMessage` + hook `TeammateIdle`; `ask_agents` passa a ser construído sobre isso.
- **Excluído:** `CLAUDE_CODE_COORDINATOR_MODE` (flag interna/experimental da Anthropic).
- `/agents`: UI que cria/edita os arquivos `.md`.

### 8.4 Plugins

- Manifesto `.claude-plugin/plugin.json` e `.orin-plugin/plugin.json`; layout padrão (`skills/`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json`, `.lsp.json`, `output-styles/`, `settings.json` com `agent`).
- Marketplaces (`marketplace.json`) de owner/repo, git, URL ou caminho; escopos user/project/local; `enabledPlugins`; `extraKnownMarketplaces` (do projeto exige trust).
- `userConfig` com `sensitive: true` armazenado no cofre Fernet do Orin (o mesmo das chaves de provedor); substituições `${user_config.X}`, `${ORIN_PLUGIN_ROOT}`/`${CLAUDE_PLUGIN_ROOT}`.
- Mantido: inspeção sem execução e aprovação antes de instalar.

### 8.5 LSP

Cliente LSP por linguagem, declarado por plugins (`.lsp.json`). Após `Edit`/`Write`, diagnósticos entram como `<system-reminder>`. Falha do servidor desativa diagnósticos sem bloquear edições.

---

## 9. W6 — CLI headless e protocolo SDK

### 9.1 Arquitetura

Cliente fino (`src/agentos/cli_code/`): garante o runtime local (sobe em background se necessário, como `orin start --no-browser`), cria/retoma conversa via API, consome SSE e traduz para o protocolo Claude Code. Autenticação por token por execução gravado pelo launcher em `ORIN_RUN_DIR` com ACL restrita ao usuário. Nesta onda, só o perfil local (remoto = W8).

### 9.2 Superfície

- `orin code [prompt]` sem `-p`: abre uma sessão Code na UI web/Electron no cwd.
- `orin code -p` — flags suportados:
  - Formato: `--output-format text|json|stream-json`, `--input-format text|stream-json`, `--include-partial-messages`, `--include-hook-events`, `--replay-user-messages`, `--verbose`.
  - Sessão: `-c/--continue`, `-r/--resume [id]`, `--fork-session`, `--session-id`, `-n/--name`, `--no-session-persistence`, `-w/--worktree [nome]`, `--bg`.
  - Modelo: `--model`, `--fallback-model`, `--effort`, `--provider` (extra).
  - Permissões: `--permission-mode`, `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `--permission-prompts host|none`, `--permission-prompt-tool`, `--allowedTools`, `--disallowedTools`, `--tools`, `--add-dir`, `--trust`.
  - Configuração: `--mcp-config`, `--strict-mcp-config`, `--settings`, `--setting-sources`, `--agents`, `--agent`, `--plugin-dir`.
  - Prompt: `--system-prompt[-file]`, `--append-system-prompt[-file]`, `--system-prompt-snapshot on|off`, `--exclude-dynamic-system-prompt-sections`.
  - Execução: `--json-schema`, `--max-turns`, `--max-budget-usd`, `--autocompact`, `--prompt-suggestions`, `--init`, `--maintenance`.
  - Modos: `--bare`, `--safe-mode`, `--restricted`.
  - Debug: `-d/--debug [filtro]`, `--debug-file`.
  - `--desktop`: abre no Electron.
- **Excluídos:** `--chrome`/`--no-chrome`, `--cloud`, `--teleport`, `--remote-control*`, `--environment`, `--ide`, `--tmux`, `--file`, `--brief`, `--betas`, `--from-pr`, `--ax-screen-reader` (TUI).
- Subcomandos de `orin code`: `mcp` (add, add-json, get, list, remove, login, logout, reset-project-choices, serve); `plugin` (install, uninstall, enable, disable, list, update, validate, details, configure, `marketplace add|list|remove|update`, `eval` na W8); `agents [--json]`, `logs <id>`, `stop <id>`, `rm <id>`, `attach <id>` (stream ao vivo no terminal, mensagens via stdin); `doctor`; `auto-mode config|defaults|reset|critique`; `purge`; `review` (W8); `import claude|codex|gemini|cursor`.
- **Excluídos:** `auth`, `setup-token`, `gateway`, `install`/`update` (já existem em `orin`), `ultrareview` cloud (substituído por `review`).
- Códigos de saída: `0` sucesso, `1` erro.

### 9.3 Protocolo stream-json

- Saída: `system/init` (session_id, cwd, tools, mcp_servers, model, permissionMode, slash_commands, output_style); `assistant`; `user`; `stream_event` sempre no formato de eventos SSE da Anthropic, independentemente do provedor; `system/{compact_boundary, microcompact_boundary, task_started, task_progress, task_notification, hook_*}`; `result` com subtipos `success`, `error_max_turns`, `error_during_execution`, `error_max_budget_usd`, `error_max_structured_output_retries` e campos `duration_ms`, `duration_api_ms`, `num_turns`, `total_cost_usd`, `usage`, `modelUsage`, `permission_denials`, `structured_output`, `errors`.
- Host → harness: `initialize` (hooks do SDK, agents, servidores MCP do SDK), `interrupt`, `set_permission_mode`, `set_model`, `set_max_thinking_tokens`, `rewind_files`, `mcp_status`.
- Harness → host: `can_use_tool`, `hook_callback`, `mcp_message`.
- Tradução: o worker bloqueia em `request_and_wait`; a CLI recebe o evento SSE, emite `control_request`, recebe `control_response` e grava a resolução via API.

### 9.4 Teste de conformidade

O `claude-agent-sdk` oficial (Python, MIT, dependência apenas de dev) dirige `orin code` via `cli_path`. Aceitação da W6 = cenários do SDK (query simples, streaming, `can_use_tool`, hooks do SDK, interrupt, resume) passando sem modificação do SDK.

---

## 10. W7 — Paridade de UX web

### 10.1 Slash commands embutidos (mesmos nomes e aliases)

- Sessão/contexto: `/add-dir`, `/agents`, `/autocompact`, `/btw`, `/clear` (`/reset`, `/new`), `/compact`, `/context`, `/copy`, `/cd`, `/diff`, `/export`, `/rename` (`/name`), `/resume` (`/continue`), `/rewind` (`/checkpoint`, `/undo`), `/branch`, `/fork`, `/subtask`, `/exit` (`/quit`).
- Configuração: `/config` (`/settings`), `/doctor`, `/effort`, `/fast`, `/model`, `/output-style`, `/permissions` (`/allowed-tools`), `/hooks`, `/mcp`, `/plugin` (`/plugins`, `/marketplace`), `/reload-plugins`, `/reload-skills`, `/skills`, `/memory`, `/pause-memory` (`/memory-pause`, `/toggle-memory`), `/keybindings`, `/statusline`, `/theme`, `/color`, `/auto-mode-setup`, `/import`, `/init`.
- Informação: `/help`, `/status`, `/tasks` (`/bashes`), `/usage` (`/cost`, `/stats`), `/version`, `/release-notes`, `/insights`, `/skill-doctor`, `/sandbox` (informa honestamente "sem sandbox de SO").
- Fluxo: `/plan`, `/loop`, `/loops`, `/review`, `/security-review`, `/ultrareview`, `/ultraplan`, `/restart` (`/update`), `/desktop` (`/app`).
- Dinâmicos: skills, commands de plugins, `/mcp__srv__prompt`.
- **Excluídos:** `/login`, `/logout`, `/voice`, `/mobile`, `/chrome`, `/ide`, `/install-github-app`, `/install-slack-app`, `/teleport`, `/remote-env`, `/session` cloud (substituído por URL do modo servidor na W8), `/design-*`, `/cloud-plugins`, `/artifacts`, `/autofix-pr`, `/privacy-settings`, `/passes`, `/stickers`, `/radio`, `/powerup`, `/wellbeing`, `/advisor`, `/focus`, `/upgrade`, `/usage-credits`, `/extra-usage`, `/pro-trial-expired`, `/rate-limit-options`, `/setup-bedrock`, `/setup-vertex`, `/feedback`, `/bug`, `/heapdump`, `/team-onboarding`, `/__remote-workflow`, `/workflow-launch-exec`, `/tui`, `/terminal-setup`, `/scroll-speed`.

### 10.2 Teclado

- Motor de keybindings por contexto, com os contextos aplicáveis à web: Global, Chat, Autocomplete, Confirmation, Transcript, HistorySearch, ModelPicker, EffortSlider, Select, DiffDialog, Tabs, Attachments, Footer, Help, Settings, Task.
- `keybindings.json` em `ORIN_CONFIG_DIR` e `~/.claude/keybindings.json`, mesmo schema (array ou `{ $schema, bindings }`), `null` desativa; validador com erros/avisos.
- Teclas reservadas pelo navegador (`ctrl+t`, `ctrl+w`, `ctrl+n`, `ctrl+tab`, `ctrl+l` e similares) não são capturáveis fora do Electron: defaults diferentes para browser e Electron; validador avisa.
- Padrões: Enter envia; Shift+Enter quebra linha; Esc interrompe; Esc Esc abre o seletor de rewind; Shift+Tab cicla o modo; `ctrl+o` transcript detalhado; `ctrl+r` busca no histórico; `ctrl+t` todos (Electron); `meta+p` model picker; `meta+t` thinking.
- Composer: `!` executa shell direto e a saída entra no contexto; `@` arquivos e resources MCP; `/` comandos; colar imagem anexa.

### 10.3 Superfícies

- Fila de mensagens visível e editável acima do composer + "enviar agora".
- Card de permissão com as 3 opções (sim; sim e não perguntar de novo, mostrando regra e escopo sessão/local; não, com instrução alternativa) e diff para edições.
- Seletor de rewind por mensagem (código/conversa/ambos).
- Painel de tarefas em background (shells, subagentes, teammates).
- Status line (`statusLine` → comando com JSON da sessão no stdin; exige trust).
- Transcript detalhado (thinking, saída de hooks).
- Grade do `/context`.
- Notificações desktop/web (ocioso, permissão pendente).
- Settings: editor de permissões por camada com proveniência, hooks, MCP por escopo, agentes, output styles, keybindings, pastas confiáveis.
- Dívida resolvida aqui: dependências do frontend com `latest` passam a ser fixadas; versão do `frontend/package.json` alinhada.

---

## 11. W8 — Equivalentes locais de nuvem

- `/ultrareview`, `/review`, `/security-review`, `orin code review [alvo]`: revisores em paralelo sobre o diff (lentes: correção, segurança, performance, testes) → passada de verificação que deduplica e confirma → `ReportFindings`. Postar em PR só com `--post`, via `gh`, com confirmação.
- `/ultraplan`: subagentes `Plan` com effort alto + rodada de crítica.
- CLI remota: `orin code` contra perfil do modo servidor (token do perfil); `/session` mostra a URL.
- `orin code plugin eval`: casos com e sem plugin, juiz com modelo rápido, relatório HTML local.

---

## 12. W9 — Conformidade e fechamento

- `tests/parity/parity-matrix.yaml`: cada item do Claude Code (45 tools, 33 hooks + 2 de UI, 6 modos, 5 camadas, flags e subcomandos da CLI, slash commands, mensagens stream-json, contextos de keybinding) com status `included` | `excluded:<motivo>` | `orin-extra` e IDs dos testes. Meta-teste falha se item `included` não tiver teste.
- Docs: `docs/MCP.md`, `docs/PLUGINS.md`, `docs/CREATING_SKILLS.md` atualizados; novo `docs/ORIN_CODE.md` (settings, permissões, hooks, CLI).
- Drift: ADRs 001/002/009 marcados como superados pela realidade SQLite local; README corrigido sobre o browser interno.

---

## 13. Tratamento de erros (transversal)

- Erro de tool → `tool_result` com `is_error` e mensagem acionável; nunca derruba o turno.
- Erros de provedor classificados (retryable, overloaded, auth, context-length, invalid-request) → retry, fallback, compactação reativa ou mensagem ao usuário.
- MCP/LSP/hooks isolados: falha marca o componente como `Failed`, remove suas tools, o turno segue.
- Settings inválido: UI mostra banner (arquivo, linha, camada) e mantém a última versão válida; headless ignora silenciosamente e registra em debug.
- Escritas atômicas (temp + fsync + `os.replace`) para settings, checkpoints e instalação de plugins; lock + checagem de mtime em settings.
- Backup automático de `orin.db` antes de cada migração.

## 14. Segurança (transversal)

Shell AST fail-closed; TOCTOU/symlink em edição e rewind; Job Objects para Bash, MCP, hooks e LSP; token da API local; Workspace Trust; caminhos protegidos; deny gerenciado vence tudo; restrições de plugin; SSRF policy em `WebFetch` e hooks http; conteúdo de tools/web/MCP marcado como não confiável no contexto; redação de segredos estendida a payloads de hooks e eventos. Postura legal: nenhum prompt ou arquivo `.d.ts` da Anthropic entra no repo (clean-room).

## 15. Estratégia de testes (transversal)

- TDD (RED → GREEN → REFACTOR) em cada tarefa dos planos.
- Adversariais: corpus de injeção de shell (bash e PowerShell); corridas de symlink/TOCTOU; fixture de repositório malicioso (hooks, MCP, skills com `` !`cmd` ``) provando que nada executa antes do trust; concorrência (fila durante compactação, interrupção no meio de tool, dois escritores de settings); falhas de I/O (disco cheio simulado, MCP morrendo no meio da chamada, stream do provedor cortado).
- Fixture de interop: repositório configurado só para Claude Code funcionando no Orin.
- Protocolo: `claude-agent-sdk` oficial dirigindo `orin code`.
- Visual: Playwright desktop/mobile, dark/light, estados ativo/vazio/erro em toda superfície nova + axe.
- CI: Windows + Ubuntu (existente).

## 16. Migração e compatibilidade

- Atividade histórica com nomes snake_case renderiza via aliases; regras antigas de `.orin/settings.json` continuam válidas.
- Mudanças de comportamento comunicadas em release notes: `Bash` mais estrito (pode perguntar mais); tetos do modo Code removidos; preset "testes e builds" ligado para quem migra.
- Cada onda atrás de `experimental.<onda>` até estabilizar; depois vira padrão.

## 17. Riscos e limitações conhecidas

1. Escopo de vários meses; W0 e W1 são as ondas de maior risco por mexerem no núcleo.
2. Paridade é garantida no **harness**; o comportamento do **modelo** varia por provedor (effort, cache, recuperação de `max_tokens` em Ollama/OpenRouter diferem da Anthropic).
3. Wheels do `tree-sitter` no build PyInstaller precisam de validação (spike na W1).
4. Job Objects via `ctypes` (`pywin32` não é dependência).
5. Atalhos do Claude Code que o navegador reserva só funcionam no Electron.
6. Mudanças feitas via `Bash` não são cobertas por checkpoints.
7. Sem sandbox de SO (seccomp/bwrap/AppContainer); a proteção é confinamento de caminho + permissões. Um sandbox de SO fica fora deste programa e é candidato a programa futuro.

## 18. Fora de escopo (consolidado)

TUI de terminal; recursos exclusivos de conta/nuvem Anthropic (teleport, cloud sessions, remote-control, créditos/planos, mobile, apps GitHub/Slack, Claude in Chrome, Claude Design, artifacts); `CLAUDE_CODE_COORDINATOR_MODE`; migração do runtime para asyncio; sandbox de SO; as ferramentas listadas como excluídas na seção 7.

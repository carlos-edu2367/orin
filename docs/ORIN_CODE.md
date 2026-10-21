# Orin Code — Guia de Uso, CLI, Permissões, Settings e Hooks

O **Orin Code** é o ambiente de engenharia de software do Orin, disponível tanto na interface gráfica (Web e Electron) quanto pelo terminal (`orin code` / `orin-code`) e via SDK (`claude-agent-sdk`).

Uma conversa no Orin nasce como **Chat** ou como **Code** (invariante imutável por conversa). No Orin Chat, o agente pode propor a abertura de uma sessão Code com `ProposeCodeSession` (exibindo o card interativo **Iniciar no Orin Code** com seleção de pasta, provedor/modelo, modo de permissão e prompt editável).

---

## 1. CLI (`orin code` e `orin-code`)

O comando `orin code` (e o binário de entrada `orin-code`) é um cliente fino sobre o runtime local do Orin: se o Orin ainda não estiver rodando, ele o inicia automaticamente em segundo plano, autentica-se com o token de execução local (`ORIN_RUN_DIR`, restrito ao usuário do SO) e conduz a sessão.

### Abrir sessão na interface
```bash
orin code                                    # abre o Orin apontando para o diretório atual
orin code "Investigue o erro no parser AST"  # cria a sessão Code na pasta atual e abre no navegador
```

### Modo Headless (`-p` / `--print`)
```bash
# Saída em texto puro
orin code -p "Explique a arquitetura deste repositório"

# Saída JSON estruturada ou stream-json (compatível com o protocolo Claude Code)
orin code -p "Rode os testes" --output-format json
orin code -p "Refatore X" --output-format stream-json --include-partial-messages

# Validação de resposta com JSON Schema (até 3 tentativas automáticas)
orin code -p "Extraia os pacotes" --output-format json --json-schema '{"type":"object","properties":{"packages":{"type":"array"}}}'

# Continuidade, retomada e execução em segundo plano
orin code -c -p "Continue a tarefa anterior"
orin code -r <session_id> -p "Adicione testes de regressão"
orin code -p "Execute a suíte completa" --bg
```

Flags principais suportadas em `orin code -p`:
- **Formatos:** `--output-format text|json|stream-json`, `--input-format text|stream-json`, `--include-partial-messages`, `--include-hook-events`, `--replay-user-messages`, `--verbose`.
- **Sessão:** `-c`/`--continue`, `-r`/`--resume [id]`, `--session-id <uuid>`, `-n`/`--name`, `--no-session-persistence`, `--bg`.
- **Modelo e orçamento:** `--model`, `--provider`, `--fallback-model`, `--effort low|medium|high|xhigh|max`, `--max-turns`, `--max-budget-usd`, `--autocompact <auto|tokens>`.
- **Permissões:** `--permission-mode default|acceptEdits|plan|auto|dontAsk|bypassPermissions`, `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `--permission-prompts host|none`, `--permission-prompt-tool stdio`, `--allowedTools`, `--disallowedTools`, `--tools`, `--add-dir`, `--trust`.
- **Configuração e Prompts:** `--settings <arquivo|json>`, `--setting-sources user,project,local`, `--mcp-config`, `--strict-mcp-config`, `--agents <json>`, `--system-prompt`, `--system-prompt-file`, `--append-system-prompt`, `--append-system-prompt-file`, `--system-prompt-snapshot on|off`, `--init`, `--maintenance`.

### Subcomandos da CLI
```bash
# Servidores MCP
orin code mcp list
orin code mcp get <nome>
orin code mcp add <nome> <comando_ou_url> [args...]
orin code mcp add-json <nome> '<json>'
orin code mcp remove <nome>
orin code mcp reset-project-choices
orin code mcp serve                  # expõe Read, Edit, Write, Glob, Grep e Bash via stdio

# Plugins e Marketplaces
orin code plugin list
orin code plugin install <referencia>
orin code plugin uninstall <plugin_id>
orin code plugin enable <plugin_id> | disable <plugin_id>
orin code plugin validate <caminho>
orin code plugin details <plugin_id>
orin code plugin configure <plugin_id> '<json>'
orin code plugin marketplace add <fonte> | list

# Agentes, execuções em segundo plano e diagnóstico
orin code agents [--json]
orin code logs <id> [-f]
orin code attach <id>
orin code stop <id>
orin code rm <id>
orin code auto-mode config | defaults | reset | critique
orin code doctor
orin code purge [--yes]
```

### Uso com o `claude-agent-sdk` oficial
Aponte `cli_path` em `ClaudeAgentOptions` para `orin-code`. O Orin responde ao protocolo de controle (`initialize`, `can_use_tool`, `hook_callback`, `mcp_message`, `interrupt`, `set_permission_mode`, `set_model`, `set_max_thinking_tokens`, `rewind_files`, `mcp_status`) e emite eventos `stream_event` normalizados independentemente do provedor de modelo configurado no Orin.

---

## 2. Settings em 5 Camadas (`.orin` e `.claude`)

O Orin lê configurações tanto nos caminhos nativos `.orin` quanto nos caminhos `.claude` (para interoperabilidade imediata com repositórios configurados para Claude Code). Na mesma camada, `.orin` tem precedência sobre `.claude`, e toda escrita feita pelo Orin ocorre em `.orin`.

Precedência (da maior para a menor):
1. **Managed (`managed`):** `%ProgramData%\Orin\managed-settings.json` (Windows) ou `/etc/orin/managed-settings.json` (Linux) + `managed-settings.d/*.json`.
2. **CLI / Sessão (`cli`):** flags de linha de comando e `--settings`.
3. **Local (`local`):** `.orin/settings.local.json` > `.claude/settings.local.json` (não versionado).
4. **Projeto (`project`):** `.orin/settings.json` > `.claude/settings.json`.
5. **Usuário (`user`):** `<ORIN_CONFIG_DIR>/settings.json` > `~/.claude/settings.json`.

**Filtro de segurança de projeto:** configurações em `project` e `local` não podem desativar restrições impostas por `managed`, `cli` ou `user`, e propriedades sensíveis (`hooks`, servidores `.mcp.json`, `apiKeyHelper`, `statusLine`) permanecem inertes até que a pasta receba **Workspace Trust**.

---

## 3. Modos de Permissão e Segurança de Shell

Na interface, alterne entre os modos com `Shift+Tab` ou no seletor do composer:

| Modo | Comportamento |
| --- | --- |
| `default` | Pede confirmação para edições de arquivos, comandos shell e ferramentas externas. Leituras dentro do workspace são permitidas. |
| `acceptEdits` | Aprova automaticamente edições de arquivo (`Edit`, `Write`, `NotebookEdit`) fora de caminhos protegidos e comandos do preset seguro de testes/builds. |
| `plan` | Somente leitura e pesquisa; bloqueia mutações no código (permitindo apenas escrever o arquivo de plano em `.orin/plans/`) e recusa *wakeups* automáticos. |
| `auto` | Consulta um classificador local (no modelo rápido configurado em `autoMode.model`) combinando regras determinísticas `hard_deny`/`allow` e *circuit breaker* por conversa. |
| `dontAsk` | Aprova o que já possui regra `allow` explícita e nega imediatamente qualquer ação que precisaria de confirmação interativa. |
| `bypassPermissions` | Executa sem prompts interativos (respeitando sempre regras `deny` da camada `managed` e o confinamento de host quando em modo servidor). |

### Regras e Especificadores
Regras são avaliadas na ordem estrita **`deny → ask → allow`**:
- `Bash(pytest:*)`, `Bash(git status)`, `PowerShell(Get-ChildItem *)`
- `Read(src/**)`, `Edit(src/**)`, `Write(docs/**)` (suporta prefixos `//abs`, `~/home`, `/relativo-ao-settings`, `./relativo-ao-cwd`)
- `WebFetch(domain:docs.python.org)`, `WebSearch`, `Agent(Explore)`, `Skill(testing)`, `mcp__servidor__*`

### Análise Sintática de Shell (`tree-sitter`) e Caminhos Protegidos
- Comandos `Bash` e `PowerShell` são decompostos em árvore sintática (`tree-sitter-bash` e `tree-sitter-powershell`). Cada subcomando (`&&`, `||`, `;`, `|`), redirecionamento e substituição (`$()`, crases) é verificado individualmente. Se qualquer parte falhar no parse ou contiver substituição não autorizada, a decisão cai em `ask` (*fail-closed*).
- **Caminhos protegidos:** `.git/`, `.orin/`, `.claude/`, `.env*`, `.bashrc`, `.bash_profile`, `.npmrc`, `.mcp.json`, `.claude.json` e `.pre-commit-config.yaml` nunca recebem auto-aprovação silenciosa em `acceptEdits`.

---

## 4. Hooks (33 Eventos)

Hooks permitem interceptar, validar ou enriquecer o ciclo de vida do agente. Podem ser declarados nas 5 camadas de settings, em `hooks/hooks.json` de plugins, no frontmatter de Skills/Agentes ou via callbacks do SDK.

- **Principais eventos:** `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PostCompact`, `PreModelSwitch`, `PostModelSwitch`, `PermissionRequest`, `PermissionDenied`, `Setup`, `TaskCreated`, `TaskCompleted`, `TeammateIdle`, `Elicitation`, `ElicitationResult`, `ConfigChange`, `WorktreeCreate`, `WorktreeRemove`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `DirectoryAdded`.
- **Tipos de handler:**
  1. `command`: subprocesso recebendo payload JSON via `stdin` (isolado em Job Object no Windows / process group no POSIX; `exit 0` aprova/injeta contexto, `exit 2` bloqueia com razão).
  2. `http`: `POST` JSON submetido à política anti-SSRF do Orin, com interpolação de headers restrita a `allowedEnvVars`.
  3. `prompt`: avaliação rápida por modelo LLM (`{ok, reason}`).
  4. `agent`: subagente verificador read-only (`Read`, `Grep`, `Glob`).

---

## 5. Memória em Arquivo (`ORIN.md` e `CLAUDE.md`)

Em cada sessão, o Orin monta instruções hierárquicas combinando:
1. Camada gerenciada (`%ProgramData%\Orin\ORIN.md` / `/etc/orin/ORIN.md`);
2. Camada do usuário (`<ORIN_CONFIG_DIR>/ORIN.md` e `~/.claude/CLAUDE.md`);
3. Raiz e diretórios do projeto (`ORIN.md`, `CLAUDE.md`, `.orin/ORIN.md`, `.claude/CLAUDE.md`, `AGENTS.md`, `CONVENTIONS.md`);
4. Regras modulares em `.orin/rules/*.md` e `.claude/rules/*.md` (com ativação condicional por glob no frontmatter `paths:`);
5. Instruções locais não versionadas (`ORIN.local.md` / `CLAUDE.local.md`).

Arquivos de memória podem importar outros documentos com `@caminho/relativo.md` (até 5 níveis de profundidade, com detecção de ciclos).

---

## 6. Checkpoints por Edição e Rewind

Antes de qualquer alteração em arquivo por `Edit`, `Write` ou `NotebookEdit`, o Orin grava um snapshot endereçado por conteúdo (`SHA-256`) em `<ORIN_DATA_DIR>/file-history/<session_id>/`, agrupado por mensagem do usuário. Você pode restaurar os arquivos para o estado anterior a qualquer ponto da conversa (pela interface com `Esc Esc` / `/rewind` — escolhendo restaurar apenas código, apenas conversa ou ambos — ou via `rewind_files` no protocolo SDK), com validação anti-TOCTOU e recusa de symlinks/reparse points fora do workspace.

---

## 7. Superfícies Web/Desktop, Slash Commands e Keybindings (W7)

- **Slash commands embutidos:** `/add-dir`, `/agents`, `/autocompact`, `/btw`, `/clear` (`/reset`, `/new`), `/compact`, `/context`, `/copy`, `/cd`, `/diff`, `/export`, `/rename`, `/resume`, `/rewind` (`/checkpoint`, `/undo`), `/branch`, `/fork`, `/subtask`, `/exit`, `/config`, `/doctor`, `/effort`, `/fast`, `/model`, `/output-style`, `/permissions`, `/hooks`, `/mcp`, `/plugin`, `/reload-plugins`, `/reload-skills`, `/skills`, `/memory`, `/pause-memory`, `/keybindings`, `/statusline`, `/theme`, `/color`, `/auto-mode-setup`, `/init`, `/help`, `/status`, `/tasks`, `/usage` (`/cost`), `/version`, `/plan`, `/review`, `/security-review`, além de comandos dinâmicos de Skills, Plugins e Prompts MCP (`/mcp__<servidor>__<prompt>`).
- **Composer avançado:**
  - Prefixar com `!` (ex.: `!git status`) executa um turno de shell direto sem invocar o modelo e registra a saída na conversa.
  - `@caminho` anexa arquivos do workspace e `@servidor:uri` injeta resources de servidores MCP ativos.
  - Mensagens enviadas enquanto um turno está rodando entram na **fila visível e editável** acima do composer (injetadas na próxima fronteira de ferramentas, ou imediatamente ao clicar em **Enviar agora**).
- **Keybindings (`keybindings.json`) e atalhos:** configuráveis em `<ORIN_CONFIG_DIR>/keybindings.json` e `~/.claude/keybindings.json` (ou pelo editor visual em Configurações). Atalhos padrão incluem `Shift+Tab` (ciclar modo de permissão), `Esc` (interromper turno), `Esc Esc` (seletor de rewind), `Ctrl+O` (transcript detalhado com thinking e saída de hooks) e `Ctrl+R` (busca no histórico de prompts).
- **Status line, `/context` e Editores de Configuração:** suporte a comando `statusLine` customizado (após Workspace Trust), grade visual de uso da janela de contexto (`/context`), painel de tarefas em segundo plano e editores dedicados em **Configurações** para permissões por camada, hooks, agentes, estilos de saída, keybindings, MCP por escopo e pastas confiáveis.

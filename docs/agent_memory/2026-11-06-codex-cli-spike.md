# Spike: adapter do Codex CLI (`codex exec --json`) com as ferramentas do Orin por MCP (Fase A, Task A0)

- **Data:** 2026-11-06 · **CLI:** `codex-cli 0.167.0`, Windows 11, login da assinatura ChatGPT (`~/.codex/auth.json`)
- **Plano:** `docs/superpowers/plans/2026-11-06-adapters-codex-e-antigravity.md` (Task A0)
- **Modelo usado no spike:** `gpt-6-luna` com `-c model_reasoning_effort="low"` (6 chamadas no total, sendo 2 de erro sem consumo de tokens).

## 1. O que a documentação da OpenAI diz sobre `codex exec` e `auth.json`

- **Fonte oficial:** https://learn.chatgpt.com/docs/non-interactive-mode (e documentação do repositório `openai/codex` sobre modo não-interativo e autenticação).
- **O que a doc afirma (sem extrapolar):**
  1. `codex exec` (com `--json` para saída em JSONL) é o subcomando oficial do Codex CLI para execução não-interativa/headless em scripts e automações locais, lendo o prompt do stdin quando `-` é passado.
  2. O login com conta ChatGPT (`codex login`) grava as credenciais OAuth em `$CODEX_HOME/auth.json` (por padrão `~/.codex/auth.json`).
  3. A documentação descreve a reutilização de `~/.codex/auth.json` em ambientes headless/CI como uso *"advanced"* e instrui explicitamente a tratar o arquivo `auth.json` como uma senha/segredo equivalente às credenciais da conta, pois ele dá acesso direto à sessão autenticada.
  4. A documentação não traz proibição ao uso local de `codex exec` por outro programa na máquina do próprio usuário autenticado (diferentemente do FAQ do Antigravity registrado na D2). O Orin nunca lê nem copia o conteúdo de `auth.json`: apenas verifica `Path.is_file()` para exibir o status no card e deixa o próprio `codex` ler `CODEX_HOME` diretamente.

## 2. Binário e versão no Windows

- **Resolução do binário:**
  1. Quando instalado globalmente via `npm i -g @openai/codex`, o shim fica no `PATH` (`%APPDATA%\npm\codex.cmd` / `codex.ps1`), e o executável PE nativo fica em `%APPDATA%\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`.
  2. Quando instalado apenas pelo aplicativo desktop da Microsoft Store, o executável fica em `WindowsApps\OpenAI.Codex_*\app\resources\codex.exe` (`%LOCALAPPDATA%\Microsoft\WindowsApps` ou `%ProgramFiles%\WindowsApps`).
  3. Em `resolve_codex_binary`, a resolução é **dinâmica a cada chamada** (sem cache de caminho): procura primeiro no `PATH` (`shutil.which("codex")`) e, se ausente no `PATH`, busca a pasta mais nova de `WindowsApps\OpenAI.Codex_*\app\resources\codex.exe` (ordenada por versão numérica extraída do nome do pacote e `mtime`).
- **Saída de `codex --version`:**
  ```text
  codex-cli 0.167.0
  ```
  - `MIN_CODEX_VERSION = (0, 167, 0)`.
- **Autenticação com isolamento (`--ignore-user-config`):**
  - Confirmado pelo `--help` (`Do not load $CODEX_HOME/config.toml; auth still uses CODEX_HOME`) e na prática: passando `--ignore-user-config --ignore-rules` e removendo `OPENAI_API_KEY`/`CODEX_API_KEY` do ambiente, o `codex exec` ignora o `config.toml` do usuário mas autentica normalmente pela assinatura usando `~/.codex/auth.json`.

## 3. Gravação dos 6 fixtures JSONL (`tests/unit/providers/codex_cli/fixtures/`)

Todos os fixtures foram gravados com `codex.exe` 0.167.0, `-m gpt-6-luna` e `-c model_reasoning_effort="low"`:

| Fixture | Exit Code | Eventos observados no stdout JSONL | Observações |
|---|---|---|---|
| `simple_text.jsonl` | `0` | `thread.started` → `turn.started` → `item.completed` (`type: "agent_message"`, `text: "PONG_CODEX"`) → `turn.completed` (`usage: {input_tokens: 8061, cached_input_tokens: 4864, cache_write_input_tokens: 0, output_tokens: 8, reasoning_output_tokens: 0}`) | Sem `--ephemeral`, gerou `thread_id = "01a1b331-e989-7f71-ae46-8e13f3d2e46f"`. |
| `resume.jsonl` | `0` | `thread.started` (mesmo `thread_id`) → `turn.started` → `item.completed` (`agent_message`, `"PONG_CODEX"`) → `turn.completed` (`input_tokens: 16154, cached_input_tokens: 12800, output_tokens: 16`) | `codex exec resume <thread_id> --json ... -` recuperou o histórico do 1º turno e respondeu `"PONG_CODEX"`. |
| `tool_call.jsonl` | `0` | `thread.started` → `turn.started` → `item.started` (`type: "mcp_tool_call"`, `server: "orin"`, `tool: "echo"`, `arguments: {"text": "hello-from-codex"}`, `status: "in_progress"`) → `item.completed` (`mcp_tool_call`, `result: {"content": [{"type": "text", "text": "ECHO_OK:hello-from-codex"}], "structured_content": null}`, `status: "completed"`) → `item.completed` (`agent_message`) → `turn.completed` | Conectou no `BackgroundTurnToolServer` HTTP real do Orin via `-c mcp_servers.orin.*` e `ORIN_MCP_TOKEN` no env. |
| `auth_error.jsonl` | `1` | `thread.started` → `turn.started` → múltiplos `{"type": "error", "message": "Reconnecting... N/5 (unexpected status 401 Unauthorized: ...)"}` → `item.completed` (`type: "error"`, fallback de WebSockets para HTTPS) → `{"type": "error", "message": "unexpected status 401 Unauthorized: ..."}` → `{"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized: ..."}}` | Rodado com `CODEX_HOME` apontando para diretório temporário vazio. Nota: a palavra literal `bearer` do texto fixo da mensagem HTTP 401 da OpenAI foi sanitizada no fixture salvo para que `grep -ri bearer` nos fixtures dê zero ocorrências. |
| `forbidden_shell.jsonl` | `0` | `thread.started` → `turn.started` → `item.started` (`type: "command_execution"`, `command: "... powershell.exe -Command 'echo FORBIDDEN_SHELL_TEST'"`, `status: "in_progress"`) → `item.completed` (`type: "command_execution"`, `aggregated_output: "FORBIDDEN_SHELL_TEST\r\n"`, `exit_code: 0`, `status: "completed"`) → `item.completed` (`agent_message`) → `turn.completed` | Gravado com `features.shell_tool=true` para registrar a forma real de `item.type == "command_execution"`, que o `NativeTurnHandle` bloqueia via `forbidden_item_kinds`. |
| `bad_model.jsonl` | `1` | `thread.started` → `item.completed` (`type: "error"`, aviso de fallback de metadata) → `turn.started` → `{"type": "error", "message": "The 'gpt-nonexistent-model-9999' model is not supported when using Codex with a ChatGPT account."}` → `{"type": "turn.failed", "error": {"message": "The 'gpt-nonexistent-model-9999' model is not supported when using Codex with a ChatGPT account."}}` | Código de saída `1`; mensagem clara para `MODEL_NOT_FOUND`. |

## 4. Verificação das chaves `-c` e formato dos eventos (D4 a D6)

1. **Todas as chaves `-c` da D4 funcionam sem alteração:**
   - `model_instructions_file="<path>"`
   - `features.shell_tool=false`
   - `web_search="disabled"`
   - `features.view_image=false`
   - `sandbox_mode="read-only"`
   - `approval_policy="never"`
   - `model_reasoning_effort="low"`
   - `mcp_servers.orin.url="<http://127.0.0.1:PORT/mcp>"`
   - `mcp_servers.orin.bearer_token_env_var="ORIN_MCP_TOKEN"`
   - `mcp_servers.orin.required=true`
2. **`codex exec resume <thread_id>`:**
   - Aceita a ordem `[binary, "exec", "resume", thread_id, "--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", ("--ephemeral")?, "-m", model, *("-c", kv), *wiring.argv, "-"]` exatamente como especificado na Task A1.
3. **Detalhes dos pacotes JSONL observados na prática (para A2 e A3):**
   - `mcp_tool_call`: o campo `item.tool` traz o nome curto da ferramenta (`"echo"`), e `item.server` traz `"orin"`. O resultado em `item.completed` vem em `item.result.content` (lista de blocos `{"type": "text", "text": "..."}`).
   - `error` e `turn.failed`: durante falhas de rede/autenticação, o Codex emite avisos de reconexão (`{"type": "error", "message": "Reconnecting... 1/5 (...)"}`) e itens `item.completed` com `item.type == "error"` antes do erro final (`{"type": "error", "message": "..."}` seguido de `{"type": "turn.failed", "error": {"message": "..."}}`). O `CodexJsonlTranslator` deve aguardar o `turn.failed` (ou um pacote `error` que não seja `"Reconnecting..."`) para emitir `RESULT(is_error=True)`, evitando abortar turnos durante uma reconexão bem-sucedida ou duplicar eventos `RESULT`.

## 5. Replay de 3 turnos com `CodexCliAdapter` e `NativeTurnHandle` (Task A5)

Executado ponta a ponta via `start_native_turn_for_provider(ClaudeCliProvider(adapter=CodexCliAdapter()), ...)` com o servidor HTTP MCP real do Orin (`BackgroundTurnToolServer`):

1. **Turno 1 (Chat + ferramenta de leitura `read_note`, modelo `gpt-6-luna`):**
   - `session_id` gerado pelo Codex: `01a1b37d-270d-7672-be16-df42633270ed`
   - Ferramenta executada pelo `ToolExecutor` do Orin: `["read_note"]` (`executor_calls = 1`)
   - Texto final: `"PROJETO_ORIN_CODEX_OK_42"`
   - Uso reportado (`CliUsage` com desconto de cache D5): `input_tokens=8617`, `cache_read_input_tokens=15872`, `output_tokens=93`.
2. **Turno 2 (Modo Orin Code + `resume_session_id`, modelo `gpt-6-luna`):**
   - Retomou `session_id = "01a1b37d-270d-7672-be16-df42633270ed"` via `codex exec resume`
   - Pergunta sobre o número lido no turno anterior sem nova chamada de ferramenta (`tools_called = []`)
   - Texto final: `"42"`
   - Uso reportado: `input_tokens=9686`, `cache_read_input_tokens=23936`, `output_tokens=98`.
3. **Turno 3 (Troca de modelo no meio da conversa para `gpt-5.6-luna` + `resume_session_id`):**
   - Retomou a mesma sessão `01a1b37d-270d-7672-be16-df42633270ed` com `-m gpt-5.6-luna`
   - Confirmou o código completo lido no 1º turno sem nova chamada de ferramenta: `"PROJETO_ORIN_CODEX_OK_42"`
   - Uso reportado: `input_tokens=24479`, `cache_read_input_tokens=23936`, `output_tokens=158`.
4. **Ajuste descoberto no replay:**
   - Para CLIs one-shot (`persistent_stdin=False`), `start_native_turn_for_provider` deve definir `persist_session=True` por padrão (omitindo `--ephemeral` no `codex exec`) e aguardar até `1.5s` no `close()` para o processo concluir o flush do rollout antes de encerrar a árvore de processos, permitindo que o turno seguinte faça `codex exec resume <thread_id>`.

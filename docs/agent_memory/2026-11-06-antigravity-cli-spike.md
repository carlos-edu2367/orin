# Spike: adapter do Antigravity CLI (`agy --input-format stream-json --output-format stream-json`) com as ferramentas do Orin por MCP (Fase C, Task C1)

- **Data:** 2026-11-06 · **CLI:** `agy 1.5.2`, Windows 11, login da assinatura Google (`~/.gemini/antigravity-cli/jetski_state.pbtxt`)
- **Plano:** `docs/superpowers/plans/2026-11-06-adapters-codex-e-antigravity.md` (Task C1)
- **Modelo usado no spike:** `gemini-3.8-flash-low` com `--effort low` (3 chamadas reais de modelo no total: 2 turnos de texto no mesmo processo + 1 turno com chamada MCP; mais 2 chamadas de erro com 0 tokens).

## 1. Binário e versão no Windows (Step 1)

- **Resolução do binário:**
  1. Primeiro no `PATH` (`shutil.which("agy")`).
  2. Se ausente no `PATH`, em `%LOCALAPPDATA%\agy\bin\agy.exe` (local padrão do instalador oficial `https://antigravity.google/cli/install.ps1`).
- **Saída de `agy --version`:**
  ```text
  1.5.2
  ```
  - `MIN_ANTIGRAVITY_VERSION = (1, 1, 1)` (exigido pelo plano para garantir as correções de stdout vazio fora de TTY na `1.0.15` e de erros silenciosos na `1.1.1`).
- **Detecção de login sem ler segredos:**
  - O estado de onboarding/login do `agy` gera `~/.gemini/antigravity-cli/jetski_state.pbtxt`. A checagem de status verifica apenas `Path.is_file()` (rejeitando symlinks com `is_symlink()`), sem abrir ou ler o conteúdo de arquivos de credenciais.
- **Flag de modo headless com `stream-json`:**
  - A partir da `1.1.15`, `--input-format stream-json --output-format stream-json` ativa diretamente o modo print/headless lendo pacotes NDJSON do stdin. **Importante:** não se deve passar `-p` sem argumento ao lado de `--input-format`, pois no parser de flags do Go `-p` consome o flag seguinte como texto do prompt (`Error: -p took "--input-format" as its prompt`).

## 2. System prompt e isolamento de ferramentas via Custom Agent (Steps 2, 3 e 4)

1. **System prompt e `excludeDefaultComponents: true`:**
   - O `agy` lê tanto `AGENTS.md`/`GEMINI.md` na pasta de trabalho quanto agentes customizados em `<workdir>/.agents/agents/<name>.md` quando o diretório contém `.git` e está listado em `trustedWorkspaces`.
   - Ao gravar `<workdir>/.agents/agents/orin.md` (owner-only, removido ao fim do turno) e passar `--agent orin`:
     - `excludeDefaultComponents: true` no frontmatter YAML remove as seções de prompt padrão e desativa o carregamento de componentes embutidos para o modelo (reduzindo o prompt base para ~543 tokens);
     - `inheritMcp: false` impede que o agente herde servidores MCP globais do usuário (`~/.gemini/config/mcp_config.json`);
     - O corpo Markdown de `orin.md` (além de `AGENTS.md` na pasta de trabalho) injeta o system prompt do Orin.
2. **Servidor MCP por turno (`mcpServers` e `_meta.antigravity/alwaysLoad`):**
   - O `agy` conecta ao `BackgroundTurnToolServer` Streamable HTTP do Orin (`http://127.0.0.1:<port>/mcp`) declarado no frontmatter `mcpServers` de `<workdir>/.agents/agents/orin.md` (lista YAML com `name: orin`, `url`, `headers.Authorization`) e em `<workdir>/.agents/mcp_config.json`.
   - Como o `agy` não expande `${ENV}` dentro de `headers.Authorization`, o token Bearer efêmero do turno vai nos arquivos owner-only dentro do diretório temporário do turno e nunca no `argv`; ambos os arquivos são apagados no `close()` do turno.
   - No `TurnToolServer` (`tools/list`), incluir `"_meta": {"antigravity/alwaysLoad": True}` em cada ferramenta (suportado desde `agy 1.3.3`) faz o `agy` expor todas as ferramentas do Orin diretamente ao modelo como `mcp_orin_<tool_name>` sem exigir descoberta lazy via `call_mcp_tool`.
3. **Permissões por sessão sem tocar no `settings.json` global (`--settings`):**
   - O flag `--settings <path_ou_json>` aplica configurações exclusivamente à sessão atual (`Settings for this session only, never saved`).
   - Passando um arquivo JSON owner-only em `--settings <settings_file>` com:
     ```json
     {
       "trustedWorkspaces": ["<workdir>"],
       "allowNonWorkspaceAccess": false,
       "enableTerminalSandbox": true,
       "disableSlashCommands": true,
       "permissions": {
         "allow": ["mcp(orin/*)"],
         "deny": [
           "command(*)",
           "read_file(*)",
           "write_file(*)",
           "read_url(*)",
           "execute_url(*)",
           "unsandboxed(*)"
         ]
       }
     }
     ```
     todas as chamadas `mcp(orin/*)` são aprovadas automaticamente sem prompt interativo e sem `--dangerously-skip-permissions`, enquanto ferramentas embutidas permanecem negadas e o `~/.gemini/antigravity-cli/settings.json` global do usuário nunca é tocado.

## 3. Gravação dos 5 fixtures `stream-json` (`tests/unit/providers/antigravity_cli/fixtures/`)

| Fixture | Exit Code | Eventos observados no stdout JSONL | Observações |
|---|---|---|---|
| `simple_text.jsonl` | `0` | `init` (`conversation_id`, `init.model`, `init.tools`) → `step_update` (`user_input`, `DONE`) → `step_update` (`agent_response`, `ACTIVE`, `text_delta: "PONG_AG"`) → `step_update` (`agent_response`, `ACTIVE`, `text_delta: "Y"`) → `step_update` (`agent_response`, `DONE`, `text_delta: "\n"`, `usage`) → `result` (`status: "SUCCESS"`, `response: "PONG_AGY\n"`, `usage: {input_tokens: 543, output_tokens: 5, thinking_tokens: 0, cache_read_tokens: 0, total_tokens: 548}`) | Confirmou leitura da palavra-chave `PONG_AGY` definida no system prompt de `orin.md`/`AGENTS.md`. |
| `second_turn.jsonl` | `0` | `step_update` (`user_input`, `step_index: 2`, `DONE`) → `step_update` (`agent_response`, `step_index: 3`, `ACTIVE`, `"PONG_AG"`) → `step_update` (`agent_response`, `ACTIVE`, `"Y_T2"`) → `step_update` (`agent_response`, `DONE`, `"\n"`, `usage`) → `result` (`status: "SUCCESS"`, `response: "PONG_AGY_T2\n"`, `num_turns: 2`, `usage`) | Segundo turno enviado pelo mesmo `stdin` persistente (`{"event":"user","message":{"content":[{"type":"text","text":"..."}]}}`); manteve o `conversation_id` e a memória do 1º turno sem novo evento `init`. |
| `tool_call.jsonl` | `0` | `init` → `step_update` (`user_input`) → `step_update` (`agent_response`, `DONE`, `thinking_tokens: 31`) → `step_update` (`step_type: "tool"`, `state: "ACTIVE"`, `tool_name: "mcp_orin_echo"`, `tool_info: {"name": "mcp_orin_echo", "parameters": {"text": "hello-from-agy"}}`) → `step_update` (`step_type: "tool"`, `state: "DONE"`, `tool_name: "mcp_orin_echo"`, `tool_info: {"name": "mcp_orin_echo", "parameters": {"text": "hello-from-agy"}, "output": "ECHO_AGY_OK:hello-from-agy"}`) → `step_update` (`agent_response`, `"ECHO_AGY_OK:hello-from-agy"`) → `result` (`status: "SUCCESS"`) | Executou a ferramenta `echo` no `BackgroundTurnToolServer` real do Orin (`executor_calls == 1`). |
| `bad_model.jsonl` | `1` | `result` (`status: "ERROR"`, `error: "invalid model selection (--model \"gemini-nonexistent-model-9999\" ...): model gemini-nonexistent-model-9999 is not recognized..."`, `finished_reason: "error"`) | Emitido antes do `init`; classificado como `CliErrorKind.MODEL_NOT_FOUND`. |
| `auth_error.jsonl` | `3` | `init` → `step_update` (`user_input`) → `step_update` (`step_type: "error_message"`, `state: "DONE"`) → `result` (`status: "ERROR"`, `error: "... API key not valid ... API_KEY_INVALID ..."`, `finished_reason: "error"`) | Classificado como `CliErrorKind.AUTH`. |

## 4. Detalhes observados para a Task C2 (`AntigravityCliAdapter` e `AntigravityStreamTranslator`)

1. **Inventário `init.tools`:** no pacote `init`, o `agy 1.5.2` lista estaticamente os 60 nomes de ferramentas embutidas do binário (`["ask_custom_permission", ..., "write_to_file"]`), e não os nomes das ferramentas MCP. O `AntigravityStreamTranslator` filtra/valida `init.tools` contra esse conjunto conhecido do `agy` (ou ferramentas `orin/*` / `mcp_orin_*`) e preenche `mcp_servers={"orin": "connected"}` e `api_key_source="none"`. Qualquer execução real (`step_update` com `step_type == "tool"`) cujo `tool_name` não pertença ao servidor MCP `orin` (`mcp_orin_*`, `orin/*`, `mcp__orin__*`) dispara `CliSecurityAbort`.
2. **Acumulação de `usage` no 2º turno:** no `result` do 2º turno no mesmo processo (`second_turn.jsonl`), `result.usage` traz o acumulado da conversa (`input_tokens: 1172 = 543 + 629`), enquanto o último `step_update(agent_response, state="DONE")` do turno traz o `usage` do passo (`input_tokens: 629`). O `AntigravityStreamTranslator` deve calcular o uso do turno a partir dos `step_update` do turno atual (ou da diferença em relação ao `result.usage` do turno anterior quando `step_update` não trouxer `usage`), aplicando `input_tokens = max(0, input_tokens - cache_read_tokens)`, `cache_read_input_tokens = cache_read_tokens`, `output_tokens = output_tokens` e `reasoning_tokens = thinking_tokens`.

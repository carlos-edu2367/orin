# Design Spec: Claude Code CLI (`claude -p`) Bridge no Orin Code (Limites da Assinatura Pro/Max/Team)

**Data:** 2026-10-28  
**Escopo:** `Architectural` (Integração de `claude -p` via assinatura OAuth compartilhando o Harness completo do Orin Code + UI Dual-Mode no Provider `anthropic`)

---

## 1. Contexto e Problema

Usuários com assinatura ativa do Claude (**Claude Pro**, **Claude Max 5x/20x** ou **Team**) autenticados localmente no CLI oficial `claude` (`claude auth login` -> `authMethod: "claude.ai"`) possuem uma franquia de uso baseada em janelas deslizantes de 5 horas (`five_hour`) e 7 dias (`seven_day`).

Atualmente, o provider `anthropic` no Orin Code exige obrigatoriamente uma chave de API (`sk-ant-...` do Anthropic Console) e faz requisições HTTP diretas para `https://api.anthropic.com/v1/messages`, cobrando por token em USD e impedindo o uso da assinatura do Claude Code dentro do Orin Code.

### Restrições Empíricas Verificadas no Binário `claude.exe` (v2.1.309 no Windows 11)
1. **Proibição de `--bare` / `CLAUDE_CODE_SIMPLE=1`:** No `claude.exe` v2.1.309, a flag `--bare` desativa explicitamente a leitura de OAuth e Keychain (`Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings`).
2. **Dreno de 325.655 tokens sem isolamento de plugins/MCPs:** Invocar `claude -p --tools ""` sem desativar customizações carrega hooks `SessionStart`, plugins locais e servidores MCP remotos sincronizados da conta `claude.ai`, gerando `325.655` tokens de criação de cache para uma resposta de 1 palavra. Ao combinar `--safe-mode --strict-mcp-config --disable-slash-commands --no-chrome --tools ""`, o overhead cai para **596 tokens** (redução de **546x**) e ~1,1s de latência, mantendo 100% de autenticação via assinatura OAuth (`apiKeySource: "none"`).
3. **Conflito de precedência com `ANTHROPIC_API_KEY`:** Se o processo do Orin tiver `ANTHROPIC_API_KEY` ou `ANTHROPIC_AUTH_TOKEN` definidos no ambiente (`os.environ`), o `claude.exe` prioriza a chave de API sobre a sessão OAuth e tarifa o Console.
4. **Delay de 3 segundos no `stdin`:** Se o `stdin` do subprocesso for um pipe aberto e não receber dados + `EOF` (ou linha JSON em `--input-format stream-json`), o `claude.exe` aguarda 3.000 ms antes de iniciar a requisição.
5. **Resolução no Windows (`claude.exe` vs `claude.cmd`):** Invocar `claude.cmd` passa por `cmd.exe /c`, impondo limite de 8.191 caracteres, risco de injeção de metacaracteres (`&|<>^%`) e processos filhos órfãos ao cancelar um turno.

---

## 2. Arquitetura Proposta

Seguindo o padrão arquitetural já estabelecido em `src/agentos/provider_catalog/opencode.py` (`Local Bridge` vs `Cloud`), o provider **`anthropic`** passa a operar em **Dual-Mode** controlado pela `base_url`:

1. **Modos de Operação no Provider `anthropic` (`src/agentos/provider_catalog/claude_code.py`):**
   - `DEFAULT_CLAUDE_CLI_BRIDGE_URL = "http://127.0.0.1:18447"` (Modo **Assinatura Claude Code CLI** — sem necessidade de API key no cofre do Orin).
   - `ANTHROPIC_CLOUD_BASE_URL = "https://api.anthropic.com"` / `base_url = None` (Modo **Anthropic Console API Key** — exige `sk-ant-...`).
   - `is_claude_cli_bridge_url(base_url)` identifica quando o provider `anthropic` está configurado para usar o binário local `claude -p`.
   - `PROVIDERS_WITH_BASE_URL` e `PROVIDERS_WITH_OPTIONAL_KEY` passam a incluir `"anthropic"`, mantendo a obrigatoriedade de `api_key` quando `not is_claude_cli_bridge_url(base_url)`.

2. **Resolução Segura do Binário e Isolamento de Ambiente (`claude_code.py`):**
   - `_resolve_claude_binary()` localiza prioritariamente o binário PE nativo `claude.exe` (`%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`, `~/.local/bin/claude.exe`, `%LOCALAPPDATA%\Programs\claude\claude.exe`) antes de usar `shutil.which("claude")`.
   - `build_isolated_claude_env()` clona `os.environ` removendo `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `CLAUDE_CODE_SIMPLE`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDECODE` e `CLAUDE_CODE_ENTRYPOINT`.
   - `check_claude_cli_status()` executa `[claude_bin, "auth", "status"]` com ambiente isolado e valida `loggedIn == True` e `authMethod == "claude.ai"`.

3. **Catálogo de Modelos e Teste de Conexão (`ClaudeCodeModelCatalogClient`):**
   - No modo CLI Bridge, `ClaudeCodeModelCatalogClient.list_models()` verifica o status de autenticação OAuth do CLI e retorna os modelos suportados pela assinatura (`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5-20251001`) com metadados completos de contexto, visão, thinking e tools.
   - `POST /v1/providers/anthropic/test` valida a sessão do `claude auth status` (no modo CLI) ou a chamada `/v1/models` (no modo Cloud).

4. **Streaming de Inferência e Ponte de Ferramentas no `HTTPProviderStreamTransport` (`provider_stream.py`):**
   - Quando `self._provider == "anthropic"` e `is_claude_cli_bridge_url(self._base_url)`, `HTTPProviderStreamTransport.stream(request)` delega para `stream_claude_code_bridge(...)`.
   - O system prompt do Orin + definições de ferramentas (`<orin_tool_call>{"name": "...", "arguments": {...}}</orin_tool_call>`) são gravados em um arquivo temporário UTF-8 passado via `--system-prompt-file` (evitando o limite de 32 KB de linha de comando do Windows) e removido no `finally:`.
   - O histórico de mensagens do turno é serializado e enviado via `stdin` no formato `--input-format stream-json` (`{"type": "user", "message": {"role": "user", "content": ...}}\n`), fechando `proc.stdin` imediatamente para eliminar o delay de 3s.
   - O stdout NDJSON (`--output-format stream-json --verbose --include-partial-messages`) é lido em tempo real via fila não-bloqueante:
     - `thinking_delta` -> `NormalizedStreamItem(kind=StreamKind.THINKING, text=...)`
     - `text_delta` -> processado pelo extrator incremental de `<orin_tool_call>`, emitindo `StreamKind.TEXT` para o texto visível e `StreamKind.TOOL_CALL` para cada ferramenta invocada.
     - `rate_limit_event` -> monitora `unifiedWindows.five_hour` e `unifiedWindows.seven_day`.
     - `result` -> popula `StreamUsage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) e encerra com `StreamKind.FINISH` (ou levanta `ProviderError` legível caso `is_error == True` ou o limite de 5h da assinatura tenha sido atingido).

5. **Interface no Frontend (`AnthropicSetup.tsx`, `ProviderDetail.tsx`, `useProviderState.ts`):**
   - O painel de configuração da **Anthropic** ganha um seletor de ambiente (`Assinatura Claude Code (CLI Local)` vs `Chave de API (Anthropic Console)`), permitindo testar a conexão OAuth local, salvar sem chave de API e atualizar o catálogo de modelos.

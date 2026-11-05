# Spike: loop nativo do `claude -p` com as ferramentas do Orin por MCP (Fase 2, Tarefa 1)

- **Data:** 2026-11-05 · **CLI:** Claude Code 2.1.309, Windows 11, login `claude.ai` (OAuth da assinatura)
- **Spec:** `docs/superpowers/specs/2026-11-05-claude-assinatura-nativa-e-orin-code-design.md` (R2.1)
- **Plano:** `docs/superpowers/plans/2026-11-05-fase-2-adapter-claude-cli-nativo.md` (Tarefa 1)
- **Custo:** 20 chamadas, cerca de US$ 0,04 em equivalente de API (Haiku 5.5 em quase tudo; Opus 5.5 em uma).

## Como foi rodado

Ambiente isolado em toda chamada: sem `ANTHROPIC_*`, `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`; com
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`; cwd numa pasta vazia. Comando base:

```
claude -p --restricted --strict-mcp-config --tools "" --disable-slash-commands --no-chrome
  --permission-prompts none --input-format stream-json --output-format stream-json --verbose
  --include-partial-messages [--mcp-config <json> --allowedTools "mcp__orin__*"] --model ... --max-turns N
```

Mensagens pelo stdin, uma por linha: `{"type":"user","message":{"role":"user","content":...}}`. Servidor MCP descartável
(Streamable HTTP, respostas JSON, `http.server` da stdlib) em `127.0.0.1:8765`, com `Authorization: Bearer <token>`.

## Resultados

| Item | Resultado | Evidência | Decisão |
|---|---|---|---|
| 1. OAuth com `--restricted` | ✅ | init: `apiKeySource: "none"`, `permissionMode: "default"`, `tools: []`; resposta "ok" | `--restricted` isola e ainda usa a assinatura. O `permissionMode` do `~/.claude/settings.json` do usuário **não** vaza. `--max-turns` aceito sem aviso. |
| 2. MCP conectado | ✅ | init: `mcp_servers: [{"name":"orin","status":"connected"}]`; `tools` só com `mcp__orin__*` | Caminho nativo viável. |
| 2. Id do `tool_use` | ✅ | `tools/call` → `params._meta = {"claudecode/toolUseId": "toolu_…", "progressToken": 2}` | Correlação exata, sem heurística. |
| 2. Token errado | ⚠️ | init: `status: "failed"`; a execução **continua sem ferramentas e sem erro** | O adapter precisa ler o init e, se o servidor `orin` não está `connected`, abortar antes de qualquer saída e cair no protocolo de texto. |
| 3. Parada em cada `tool_use` | ✅ | stream: `assistant(tool_use)` → `user(tool_result "A")` → `assistant(tool_use)` → `user(tool_result "AB")` → `assistant(text)`; o servidor recebeu `echo("AB")` só depois de responder `echo("A")` | Fim do lote cego. |
| 3. `--max-turns 1` | ✅ | a ferramenta **roda**, depois `result.subtype = "error_max_turns"`, `is_error: true`, exit 1 | O runner trata `error_max_turns` depois de a ferramenta já ter executado. |
| 3b. Ferramenta longa | ⚠️ | sem variável: `tool_result = "The operation timed out."` aos **60 s**; com `MCP_TOOL_TIMEOUT=3600000` a chamada de 90 s terminou | **Sempre** passar `MCP_TOOL_TIMEOUT` (ms) = tempo restante do turno. |
| 3c. Paralelismo | ✅ | `slow_echo` com `annotations.readOnlyHint: true`: 3 chamadas chegaram em 0,4 s e terminaram juntas (~5 s); `slow_echo_rw` sem anotação: em série, 5 s cada (15 s) | A própria CLI paraleliza as ferramentas somente leitura e serializa as que escrevem. As 3 chamadas paralelas vêm na **mesma** mensagem (`message.id` igual). |
| 4. Imagem no `tool_result` | ✅ | `{"type":"image","mimeType":"image/png"}` do MCP chegou ao modelo, que respondeu "vermelha" | Imagens ficam dentro do `tool_result`, presas à chamada. |
| 5. Thinking longo | ✅ | Haiku 5.5 `--effort max`: 9.400 tokens de thinking em 35 s, 29 `thinking_delta` (conteúdo vazio), maior silêncio no stdout **1,8 s**; durante uma ferramenta longa a CLI emite uma linha a cada 30 s | Não há silêncio longo. Os `thinking_delta` vazios servem para mostrar "pensando…". |
| 6a. `--session-id` / `--resume` | ✅ funciona, ❌ inadequado | lembrou "abacaxi"; arquivo em `~/.claude/projects/<slug do cwd>/<uuid>.jsonl` | O slug vem do cwd. Com cwd = repositório do usuário, as conversas do Orin apareceriam no `/resume` do Claude Code dele. **Não usar.** |
| 6b. stdin persistente | ✅ | mesmo processo, `--no-session-persistence`, `--max-turns 1`: a 2ª mensagem lembrou "4721" da 1ª; um `result` por mensagem | Continuidade por processo vivo, sem gravar sessão. |
| 7. Cache do texto em blocos | ✅ | 10 blocos (~12,3k tokens): A gravou 12.316; B (mesmos blocos + novos) leu **11.914 (97%)** | `TEXT_CACHE_OK = true`. |
| 7. Controle: um bloco só | ✅ confirma a causa | A gravou 12.323; B gravou 12.353 e leu **0** | O formato atual (histórico achatado num texto só) zera o cache. |

## Constantes para as próximas tarefas

```python
ISOLATION_FLAGS = ("--restricted", "--strict-mcp-config")
NATIVE_LOOP_VALIDATED = True
IDLE_TIMEOUT_SECONDS = 180                     # max(180, 3 × 1,8 s); pausado durante ferramenta em execução
MCP_TOOL_TIMEOUT_ENV = "MCP_TOOL_TIMEOUT"      # ms; padrão da CLI corta em 60 s
TOOL_USE_ID_META_KEY = "claudecode/toolUseId"  # em params._meta do tools/call
CLI_PARALLEL_MCP = True
CLI_PARALLEL_NEEDS_READONLY_HINT = True        # tools/list deve anotar readOnlyHint nas ferramentas somente leitura
PERSISTENT_STDIN_OK = True
SESSION_BRANCH = "B"                           # sem --session-id/--resume; processo vivo por conversa
SESSION_FILES_DIR = "~/.claude/projects/<slug do cwd>"  # motivo de não usar o ramo A
TEXT_CACHE_OK = True                           # ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET sem padrão de 200k
VALIDATED_CLI_RANGE = (">=2.1.309", "<2.2")
```

## Decisões

1. **Itens 1, 2 e 3 passaram → `NATIVE_LOOP_VALIDATED = True`.** Seguir todas as tarefas da Fase 2.
2. **Agrupador da Tarefa 9, ajustado ao que a CLI faz.** A CLI já manda junto as chamadas somente leitura de uma
   mensagem e em série as que escrevem; uma chamada de escrita só chega depois do resultado da anterior. Esperar as N
   chamadas da mensagem travaria quando houver escrita. Regra: o agrupador junta as chamadas que chegam numa janela
   curta (`BATCH_GATHER_SECONDS = 0.1`) depois da primeira, todas da mesma mensagem, e nunca espera por chamadas de
   ferramentas de escrita. O `tools/list` do `TurnToolServer` anota `readOnlyHint: true` exatamente nas ferramentas que
   `toolset.is_read_only` aceita.
3. **Servidor MCP precisa ser checado no init.** `status != "connected"` para `orin` → `NativeLoopUnavailable` antes
   de qualquer saída (o teste `test_unconnected_mcp_raises_native_unavailable_before_output` da Tarefa 8 cobre).
4. **`MCP_TOOL_TIMEOUT`** vai sempre no ambiente do processo, com o tempo restante do turno em ms.
5. **Sessão (Tarefa 11): ramo B.** Um processo por conversa enquanto ela está ativa, com as mensagens seguintes pelo
   mesmo stdin. Ao reabrir o processo, o histórico vai como blocos (append-only). Nunca `--session-id`/`--resume`.
6. **Protocolo de texto (Tarefa 4/6):** histórico em vários blocos, sem orçamento de 200k por padrão; a janela é a do
   catálogo.
7. **Item 6c (prompt de sistema no `--resume`) e 7b (`cache_control` explícito)** não foram rodados: o ramo A foi
   descartado e o cache já passa sem `cache_control` explícito.

# Spike: Claude Code CLI `notifications/tools/list_changed` Support (Fase 3, Tarefa 11)

- **Data:** 2026-11-06
- **CLI testada:** Claude Code `2.1.309` (`C:\Users\User\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`), Windows 11, login `claude.ai` (OAuth da assinatura)
- **Modelo:** `claude-haiku-5-5`
- **Flags de isolamento:** `--restricted --strict-mcp-config --tools "" --disable-slash-commands --no-chrome --permission-prompts none --no-session-persistence`
- **Ambiente:** `build_isolated_claude_env()` + `merge_with_system_env()`, `cwd` numa pasta temporária vazia.

---

## Chamada 1 — Sonda mínima (`ToolSearch` -> `SecretTool`)

### Comando executado
```powershell
C:\Users\User\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe -p `
  --restricted --strict-mcp-config --tools "" `
  --disable-slash-commands --no-chrome --permission-prompts none --no-session-persistence `
  --input-format stream-json --output-format stream-json --verbose --include-partial-messages `
  --mcp-config C:\Users\User\AppData\Local\Temp\orin-spike-t11-b8v7taxn\mcp.json `
  --allowedTools mcp__orin__* --model claude-haiku-5-5 --max-turns 4
```

### Sequência de requisições HTTP recebidas no servidor MCP
1. `POST /mcp` (`id="server-discover-probe-1"`, `method="server/discover"`, `Accept: application/json, text/event-stream`)
2. `POST /mcp` (`id=0`, `method="initialize"`, `Accept: application/json, text/event-stream`) -> `200 application/json` (`capabilities.tools.listChanged = true`).
3. `POST /mcp` (`method="notifications/initialized"`) -> `202`.
4. `GET /mcp` (`Accept: text/event-stream`) -> `405`.
5. `POST /mcp` (`id=1`, `method="tools/list"`) -> `200 application/json` contendo apenas `ToolSearch`.
6. `POST /mcp` (`id=2`, `method="tools/call"`, `name="ToolSearch"`, `arguments={"query": "select:SecretTool"}`, `Accept: application/json, text/event-stream`) -> `200 text/event-stream`:
   ```text
   event: message
   data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}

   event: message
   data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"..."}],"isError":false}}
   ```
7. `POST /mcp` (`id=3`, `method="tools/list"`, `Accept: application/json, text/event-stream`) -> enviado imediatamente pela CLI após receber `notifications/tools/list_changed`, recebendo `["ToolSearch", "SecretTool"]`.
8. `POST /mcp` (`id=4`, `method="tools/call"`, `name="SecretTool"`, `arguments={"x": "42"}`) -> executado na rodada seguinte, retornando `"SECRET_42_OK"`.
9. Resultado final: `exit_code = 0`, `stop_reason = "end_turn"`, `cache_read_input_tokens = 6176`, `cache_creation_input_tokens = 3422`, `total_cost_usd = 0.00081876`.

---

## Chamada 2 — `TurnToolServer` + `RuntimeToolExecutor` + `AgentToolset` real (`select:WebFetch` + `https://example.com`)

### Comando executado
```powershell
C:\Users\User\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe -p `
  --restricted --strict-mcp-config --tools "" `
  --disable-slash-commands --no-chrome --permission-prompts none --no-session-persistence `
  --input-format stream-json --output-format stream-json --verbose --include-partial-messages `
  --mcp-config C:\Users\User\AppData\Local\Temp\orin-spike-webfetch-la1bkfav\mcp.json `
  --allowedTools mcp__orin__* --model claude-haiku-5-5 --max-turns 4
```
Prompt enviado pelo stdin: `"use ToolSearch com select:WebFetch e depois chame WebFetch em https://example.com"`.

### Resultado observado
- `executor.list_tools()` inicial: **12 ferramentas** do núcleo (`has_WebFetch = False`).
- A CLI chamou `mcp__orin__ToolSearch` com `{"query": "select:WebFetch"}`.
- `RuntimeToolExecutor` detectou `discovered_tool_names == {'fetch_url'}` e marcou `ToolCallResult.tools_changed = True`.
- `TurnToolServer` respondeu ao `POST tools/call` com `Content-Type: text/event-stream` contendo `notifications/tools/list_changed` seguido da resposta JSON-RPC.
- A CLI chamou imediatamente `POST tools/list` (`[executor.list_tools] called! count=13, has_WebFetch=True`).
- Na sequência do mesmo processo, a CLI invocou `mcp__orin__WebFetch` em `https://example.com`, recebeu o conteúdo HTTP 200 de `example.com` e concluiu com `exit_code = 0` (`cache_read_input_tokens = 20776`).

---

## Decisão

- **`ClaudeCliAdapter.capabilities.tool_list_changed = True`**
- No caminho nativo (`NativeTurnRunner`), `allow_deferral` segue `provider.adapter.capabilities.tool_list_changed` (`True` para o Claude CLI 2.1.309), publicando apenas o núcleo inicial (< 4.000 `tools_tokens` também no caminho nativo) e atualizando a lista de ferramentas sob demanda via `notifications/tools/list_changed` em SSE no `POST tools/call`.

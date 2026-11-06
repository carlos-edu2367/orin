# Spike: Claude Code CLI `notifications/tools/list_changed` Support

- **Data:** 2026-11-05
- **CLI testado:** Claude Code `2.1.309` (`claude.exe`)
- **Modelo:** `claude-haiku-5-5`

## Comando executado

```powershell
claude.exe -p `
  --restricted `
  --strict-mcp-config `
  --tools "" `
  --disable-slash-commands `
  --no-chrome `
  --permission-prompts none `
  --no-session-persistence `
  --input-format stream-json `
  --output-format stream-json `
  --verbose `
  --include-partial-messages `
  --mcp-config <mcp.json> `
  --allowedTools mcp__orin__* `
  --model claude-haiku-5-5 `
  --max-turns 4
```

Executado em diretório temporário vazio (`ensure_isolated_cwd`) com `TurnToolServer` anunciando `"capabilities": {"tools": {"listChanged": true}}`.

## Sequência de requisições HTTP recebidas no `TurnToolServer`

1. `POST /mcp` (`id=1`, `method="initialize"`, `Accept: application/json, text/event-stream`) -> `200 application/json` (`capabilities.tools.listChanged = true`).
2. `POST /mcp` (`method="notifications/initialized"`) -> `202`.
3. `POST /mcp` (`id=2`, `method="tools/list"`) -> `200 application/json` contendo apenas `ToolSearch`.
4. `POST /mcp` (`id=3`, `method="tools/call"`, `name="ToolSearch"`, `Accept: application/json, text/event-stream`) -> `200 text/event-stream`:
   ```text
   event: message
   data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}

   event: message
   data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}],"isError":false}}
   ```
5. `POST /mcp` (`id=4`, `method="tools/list"`) -> enviado imediatamente pelo CLI antes da próxima chamada ao modelo, recebendo `["ToolSearch", "ping_discovered"]`.
6. `POST /mcp` (`id=5`, `method="tools/call"`, `name="ping_discovered"`) -> executado no turno seguinte com `exit_code=0` e `cache_read_input_tokens=6176`.

## Decisão

- **`ClaudeCliAdapter.capabilities.tool_list_changed = True`**
- No caminho nativo (`NativeTurnRunner`), `allow_deferral` segue `provider.adapter.capabilities.tool_list_changed`. Quando `ToolSearch` (ou qualquer chamada no lote) descobre novas ferramentas (`discovered_tool_names` ou `active_mcp_tool_names` cresce), `ToolCallResult.tools_changed = True` faz o `TurnToolServer` responder ao `POST tools/call` com `text/event-stream` incluindo `notifications/tools/list_changed` antes do resultado JSON-RPC, fazendo o `claude -p` reconsultar `tools/list` imediatamente sem reiniciar o processo e preservando o cache de prefixo.

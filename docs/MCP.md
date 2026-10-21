# MCP Connectors

Orin connects to MCP (Model Context Protocol) servers so the agent can use
tools, resources, and prompts published by local or remote servers alongside its
built-in capabilities, and can also expose Orin's own file/shell tools as an MCP
server via `orin code mcp serve`.

## What is supported

- **Tools with lazy loading and on-demand search (`ToolSearch`).** A connected
  server's `tools/list` results are namespaced `mcp__<server-slug>__<tool-name>`
  so they never collide with built-in tools or other servers. There is no fixed
  256-tool ceiling (`MAX_TOOLS_PER_SERVER` was removed; pagination detects
  repeated cursors up to `MAX_TOOL_PAGES = 1000`). To protect the context window
  against large MCP servers, schemas are lazy-loaded on demand through
  `ToolSearch` (`search_mcp_tools`), `inspect_mcp_server`, and `call_mcp_tool`
  (or deferred automatically when tool definitions exceed the context budget).
- **Resources, Prompts, and Elicitation.**
  - **Resources:** `ListMcpResources`, `ReadMcpResource`, `ReadMcpResourceDir`,
    and `@server:uri` mentions in the composer; binary blobs validate base64 and
    report unreadable content cleanly.
  - **Prompts:** discovered prompts are exposed as `/mcp__<server>__<prompt>`
    commands.
  - **Elicitation & Dynamic Refresh:** `Elicitation` / `ElicitationResult`
    lifecycle hooks and `RefreshMcpTools` (plus `notifications/tools/list_changed`).
- **Four client transports + SDK relay:**
  - `stdio`: local subprocess (`npx`, `uvx`, `node`, `python`, `python3`, `uv`,
    `deno`, or `bun`), isolated in a Windows Job Object / POSIX process group so
    child processes always terminate when the server closes.
  - `http`: remote HTTPS endpoint speaking streamable HTTP.
  - `sse`: remote Server-Sent Events MCP endpoint.
  - `ws`: WebSocket (`wss://`) MCP endpoint.
  - `sdk`: in-process control-protocol relay (`mcp_message`) for SDK MCP servers
    registered by `claude-agent-sdk` hosts driving `orin code`.
- **Four configuration scopes:**
  1. `managed`: `managed-mcp.json`, plus `allowedMcpServers` / `deniedMcpServers`
     policy enforcement;
  2. `user`: servers configured in **Settings → MCP** (stored in SQLite) and
     `mcpServers` in `~/.claude.json`;
  3. `project`: `.mcp.json` at the repo root and `.orin/mcp.json` (requires
     **Workspace Trust** and per-server approval);
  4. `local`: per-user × per-project state and CLI `--mcp-config` /
     `--strict-mcp-config`.
  - Environment expansion `${VAR}` and `${VAR:-default}` is supported across
    `command`, `args`, `env`, `url`, and `headers`.
- **Browser automation via Playwright MCP:** **Settings → Navegador** installs
  and activates the official `@playwright/mcp` (`npx -y @playwright/mcp@latest`)
  in one click using the curated `playwright` catalog entry.
- **`orin code mcp` CLI & MCP server mode:**
  - Manage servers from the terminal with `orin code mcp list|get|add|add-json|remove|reset-project-choices`.
  - Run `orin code mcp serve` to expose Orin's `Read`, `Edit`, `Write`, `Glob`,
    `Grep`, and `Bash` tools over `stdio` to external MCP hosts (enforcing
    `allow`/`deny` rules and refusing any operation that would require an
    interactive prompt).

## How a connection gets approved

A connection never activates itself without user consent:

1. **Propose** — the agent (via `configure_mcp`), a `.mcp.json` project file, or
   the user (via **Settings → MCP → Adicionar servidor** or **Settings →
   Navegador**) registers a server in `pending_approval`. A proposal carries
   only credential *names*, never secret values.
2. **Approve** — the user supplies any required credential value in the chat's
   approval card or in Settings (and grants Workspace Trust for project-scoped
   `.mcp.json` servers). Approval tests the connection (`initialize` and
   `tools/list`) and caches the discovered descriptors on success.
3. **Use (Lazy-Loaded)** — at turn start, the system prompt includes a compact
   summary of active MCP servers and tool counts. The agent uses `ToolSearch`
   (`search_mcp_tools`), `inspect_mcp_server`, or `call_mcp_tool` to search by
   name, description, or JSON schema properties and activate only the tools
   needed for the current turn.

## Where credentials live

A credential value is never accepted as a tool argument: `configure_mcp`
rejects any keyword it doesn't explicitly declare (`secret_names` — names
only). At approval, the values are combined into one JSON object and
encrypted with `AGENTOS_PROVIDER_ENCRYPTION_KEY`, the same
`ProviderSecretCipher` already used for provider API keys
(`src/agentos/persistence/provider_secrets.py`). No endpoint, log, or event
ever returns a decrypted value; only credential *names* are ever part of a
public response.

## Servers that ask you to sign in (OAuth 2.1)

A remote server that follows the MCP Authorization spec answers the first
connection with `401`. Approval then returns `409 mcp_authorization_required`
and the card offers **Sign in** instead of credential fields:

1. `POST /v1/mcp/servers/{id}/oauth/start` discovers the server's
   authorization server (RFC 9728/8414), registers Orin dynamically
   (RFC 7591) for the redirect `http://127.0.0.1:<port>/v1/mcp/oauth/callback`,
   and returns the authorization URL (PKCE S256, `resource` per RFC 8707).
2. The system browser shows the server's consent page.
3. `GET /v1/mcp/oauth/callback` checks the single-use `state` (10 minutes),
   exchanges the code, stores the tokens encrypted, discovers the tools and
   activates the server.

Tokens live in `oauth_tokens`, encrypted with the same cipher as every other
credential. Before each request the token is read from the database and
renewed when it expires within 60 seconds; a `401` triggers one renewal and
one retry. A lease on the token row makes sure the API and the worker never
spend the same refresh token twice. If the server refuses the renewal, the
tool call fails with `MCP_REAUTH_REQUIRED`, the server moves to `error`, and
**Reconnect** in Settings runs the same sign-in again. Removing the server
revokes the grant when the server advertises a revocation endpoint.

## The stdio launcher allowlist and process cleanup

Only `npx`, `uvx`, `node`, `python`, `python3`, `uv`, `deno`, and `bun` may be
launched (`src/agentos/mcp/transport_stdio.py`). The command runs with
`shell=False` and an explicit, minimal environment (`PATH`, `SystemRoot`,
plus only that server's own credentials) — the worker's own environment is
never inherited.

On Windows, `npx`/`uvx` resolve to `.cmd`/`.bat` shims, which the OS loader
re-invokes through `cmd.exe` regardless of `shell=False`. `%` and `^` are
forbidden alongside POSIX shell metacharacters (`;&|`$><`) in the command,
every argument, *and* every credential value. When a `stdio` session closes,
Orin closes `stdin`, waits 300 ms, and terminates the entire Windows Job Object
/ POSIX process group (plus any recorded descendant PIDs) so child processes
never leak.

## The HTTP / SSE / WebSocket network policy

Remote MCP endpoints must resolve to a public address — the same `_public_url`
policy enforced across Orin's outbound network tools (loopback, private, and
link-local ranges are refused). This check re-runs immediately before *every*
request to mitigate DNS-rebinding races.

## Adding a server to the curated catalog

The catalog (`src/agentos/mcp/catalog.py`) is what lets the agent *explain* a
connection instead of guessing one. Each entry is a `McpCatalogEntry`
declaring what the server does, its transport, how it's launched (or its URL),
and which credentials the user needs (`McpSecretRequirement.how_to_obtain`).
`list_mcp_catalog`, **Settings → MCP**, and **Settings → Navegador** (for
`catalog_id="playwright"`) read from it directly.

## When a server fails

- **Approval fails:** the row stays `pending_approval`, `state_reason` records
  why, and the gateway route returns `502`.
- **A configured server is unreachable at turn time:** the worker logs the
  failure and runs the turn without that server's tools — a broken MCP server
  never blocks the turn from starting.
- **An individual tool call fails:** the model gets a failed `ToolOutcome`
  (`MCP_TOOL_ERROR` or `MCP_UNAVAILABLE`) and can react to it like any other
  tool failure. Outputs above `MAX_MCP_OUTPUT_TOKENS` (25k tokens) are truncated
  and persisted to disk so oversized payloads never overflow the prompt.
- **A signed-in server stops accepting Orin:** the tool call returns
  `MCP_REAUTH_REQUIRED` and the server moves to `error`.

## Removing a server

Disabling (**Settings → MCP**, or `PUT .../enabled`) moves the row to `disabled`
and stops publishing its tools while keeping the cache and encrypted credential.
Removing (`DELETE` or `orin code mcp remove`) deletes the row and its cached
tools entirely.

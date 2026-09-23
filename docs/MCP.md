# MCP Connectors

Orin connects to MCP (Model Context Protocol) servers so the agent can use
tools a remote or local server publishes, alongside its built-in ones. This
document is the pragmatic implementation notes; the normative design is
[RFC 903](architecture/900-extensibility/903-mcp-future.md) and the
implementation plan is
[docs/superpowers/plans/2026-08-14-mcp-connectors.md](superpowers/plans/2026-08-14-mcp-connectors.md).

## What is supported

- **Tools only.** A connected server's `tools/list` results become native
  tools, namespaced `mcp__<server-slug>__<tool-name>` so they can never
  collide with a built-in tool or with another server's tool of the same
  name.
- **Two transports**: `stdio` (a local subprocess, launched via `npx`, `uvx`,
  `node`, `python`, `python3`, `uv`, `deno`, or `bun`) and `http` (a remote
  HTTPS endpoint speaking streamable HTTP or SSE).
- **Resources and prompts are not implemented.** RFC 903 requires each MCP
  surface to map to a proprietary local port before it can be enabled — no
  such mapping exists yet for resources or prompts, so a server that only
  offers those has nothing usable in Orin today.

## How a connection gets approved

A connection never activates itself. The flow is always:

1. **Propose** — the agent (via `configure_mcp`) or the user (via **Settings
   → MCP → Adicionar servidor**) creates a server row in `pending_approval`.
   A proposal carries only credential *names*, never values.
2. **Approve** — the user supplies any required credential value, either in
   the chat's approval card or inline on the server's card in Settings. Only
   this step opens a real connection: the server answers `initialize` and
   `tools/list`, and only on success does the row become `active` and the
   discovered tools get cached.
3. **Use** — from then on, the turn's tool set is built entirely from the
   cache. No network call happens just to list an approved server's tools; a
   session to the server itself opens lazily, the first time the model
   actually calls one of its tools, and closes with the turn.

A failed approval leaves the server `pending_approval` with a recorded
`state_reason` — nothing partially activates.

## Where credentials live

A credential value is never accepted as a tool argument: `configure_mcp`
rejects any keyword it doesn't explicitly declare (`secret_names` — names
only). At approval, the values are combined into one JSON object and
encrypted with `AGENTOS_PROVIDER_ENCRYPTION_KEY`, the same
`ProviderSecretCipher` already used for provider API keys
(`src/agentos/persistence/provider_secrets.py`). No endpoint, log, or event
ever returns a decrypted value; only credential *names* are ever part of a
public response.

## Servers that ask you to sign in

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

Every URL taken from OAuth metadata follows the HTTP network policy below.

## The stdio launcher allowlist

Only `npx`, `uvx`, `node`, `python`, `python3`, `uv`, `deno`, and `bun` may be
launched (`src/agentos/mcp/transport_stdio.py`). The command runs with
`shell=False` and an explicit, minimal environment (`PATH`, `SystemRoot`,
plus only that server's own credentials) — the worker's own environment is
never inherited.

`shell=False` is not the whole story on Windows: `npx`/`uvx` resolve to
`.cmd`/`.bat` shims, which the OS loader transparently re-invokes through
`cmd.exe` regardless of `shell=False`. That means anything on the resulting
command line is subject to `cmd.exe`'s own operator and `%VAR%` expansion
rules. `%` and `^` are forbidden alongside the POSIX shell metacharacters
(`;&|`$><`) in the command, every argument, *and* every credential value —
a value containing a forbidden character is refused before the process ever
starts, rather than risking it being reinterpreted by the shim.

## The HTTP network policy

An `http` server must resolve to a public HTTPS address — the same
`_public_url` policy the agent's own `fetch_url` tool already enforces
(loopback, private, and link-local ranges are refused). This check re-runs
immediately before *every* request, not only once when the server is first
configured: a server the operator doesn't control DNS for could otherwise
pass validation once and then repoint its own domain at a private address
for the actual connection (a DNS-rebinding race). Per-request re-checking
closes the "validate once, reuse forever" version of that gap.

**Known residual limitation:** the connection still isn't pinned to the IP
address that was validated — httpx re-resolves DNS independently a few
milliseconds after the policy check. Fully closing this needs a
transport-level IP pin (a custom `httpcore` `NetworkBackend`), which is a
larger change than this pass covers. This is the same class of risk the
project's README already discloses for SSRF hardening generally.

## Adding a server to the curated catalog

The catalog (`src/agentos/mcp/catalog.py`) is what lets the agent *explain* a
connection instead of guessing one. Each entry is a `McpCatalogEntry`
declaring: what the server does, its transport, how it's launched (or its
URL), and — critically — exactly which credential the user needs to fetch
and where from (`McpSecretRequirement.how_to_obtain`). Add a new entry there;
`list_mcp_catalog` and **Settings → MCP** both read from it directly, no
other wiring needed.

## When a server fails

- **Approval fails** (the server didn't answer, or rejected the credential):
  the row stays `pending_approval`, `state_reason` records why, and the
  gateway route returns `502`. The user can retry with a different value.
- **A configured server is unreachable at turn time:** the worker logs the
  failure and simply runs the turn without that server's tools — a broken
  MCP configuration never blocks the turn from starting.
- **An individual tool call fails:** the model gets a normal failed
  `ToolOutcome` (`MCP_TOOL_ERROR` for a server-side refusal,
  `MCP_UNAVAILABLE` for a transport/protocol failure) and can react to it
  like any other tool failure.
- **A signed-in server stops accepting Orin** (grant revoked or expired): the
  tool call returns `MCP_REAUTH_REQUIRED` with a message telling the user to
  reconnect in Settings → MCP, and the server moves to `error`.

## Removing a server

Disabling (**Settings → MCP**, or the API's `PUT .../enabled`) is reversible:
the row moves to `disabled` and its tools stop being published, but the
cache and encrypted credential stay in place. Removing deletes the row and
its cached tools entirely; the chat and Settings UI both require an explicit
second confirmation before this happens.

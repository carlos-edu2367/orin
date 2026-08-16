# Plugin library raw MCP install design

## Intent

Most repositories the Plugin Library's web search surfaces (mem0, vllm, generic MCP servers) are plain MCP servers published without a `.claude-plugin/plugin.json` manifest — they were never meant to be a "Claude plugin," just an MCP server any client can point at. Today the library treats every result the same way: clicking "Instalar" always goes through `PluginFetcher`, which rejects anything without a manifest before it even reaches inspection. This design lets the Biblioteca recognize that case up front and offer a second path — "Adicionar como servidor MCP" — that infers a launch command from the repository's own config files and feeds the existing MCP propose/approve flow (`POST /v1/mcp/servers`, already used identically by plugin activation) instead of forcing a manifest that will never exist.

This is one of two independent gaps mapped in live testing of the Plugin Library (see [2026-08-16-plugin-library-design.md](2026-08-16-plugin-library-design.md)); the other — plugins that declare only `hooks/`/`commands/` with no skill/MCP/agent contribution — is a separate, larger subproject (a hooks/commands execution engine) deliberately deferred and out of scope here.

## Scope

In scope: a proactive, rate-limit-aware check of whether a library entry has an installable plugin manifest; a best-effort command/transport inference step that clones a repository and reads its own structured config files; a new frontend dialog that surfaces the inferred launch config for review and feeds it into the existing MCP propose/approve flow; propagating the specific "no manifest" failure reason through the existing install path so it can degrade gracefully into the same fallback.

Out of scope: a hooks/commands execution engine (Category A, separate subproject); parsing free-text sources (README prose, docs sites) for inference — only structured `mcp.json`/`smithery.json`/`package.json`/`pyproject.toml` are read; inferring secret *values* (only secret *names*, same as the existing manual MCP form — values are always supplied at approval); authenticated/elevated-rate-limit GitHub access (a `GITHUB_TOKEN` env var is a plausible future addition, not built here); any change to how installed plugins with a valid manifest are inspected, approved, or activated.

## Backend

### Manifest presence probe (discovery-time)

`src/agentos/plugins/manifest_probe.py` adds `GithubManifestProbe`, an `httpx`-based client mirroring the shape of `GithubRepositorySearchClient` (`github_search.py`) — same headers, same keyless/unauthenticated GitHub REST access, same `close()` contract.

`probe(owner: str, repo: str) -> bool | None` issues one call to `GET https://api.github.com/repos/{owner}/{repo}/contents` (root listing) and returns:
- `True` if a `.claude-plugin` directory or a root `plugin.json` file appears in the listing (matches the two locations `PluginFetcher`/`inspect_plugin_package` already check).
- `False` if the listing was retrieved but neither is present.
- `None` on any `httpx.HTTPError` (404, network failure, and — critically — 403/429 rate-limiting). `None` always means "couldn't determine," never "no manifest."

### Discovery integration

`PluginDiscoveryService` (`discovery.py`) gains an optional `manifest_probe: Any = None` constructor argument, wired in `bootstrap/production.py` and `workers/chat.py` the same way `search_client` already is (constructed for real in production, `None` in tests disables the check).

`PluginLibraryEntry` gains a field: `installable_kind: str` (`"plugin" | "mcp_raw" | "unknown"`).

- **Registry-origin entries**: always `"plugin"`. These come from `marketplace.json` indexes that are already pre-vetted; no probe call is spent on them. This alone cuts probe volume roughly in half versus checking every result.
- **Web-origin entries**: `_web_entries` calls `manifest_probe.probe(owner, repo)` once per result after the existing allow-list filter (so probes are never spent on URLs that would be rejected anyway). `True` → `"plugin"`, `False` → `"mcp_raw"`, `None` (probe disabled, or rate-limited/failed) → `"unknown"`.

A new in-memory cache — `_manifest_cache: dict[str, tuple[bool | None, datetime]]`, keyed by normalized repo URL, `MANIFEST_CACHE_TTL = timedelta(hours=24)` — sits alongside (not inside) the existing 15-minute entries cache. Manifest presence changes far less often than search results, so this cache amortizes probe cost across discovery loads and users far more aggressively than the entries cache does. A cached `None` (rate-limited) is also honored for its TTL rather than retried every load, so a rate-limit event doesn't turn into a retry storm.

Worst case this adds up to ~5–10 GitHub API calls per uncached discovery load (bounded by `DISCOVERY_QUERIES`' existing `limit=5` per query); best case (cache warm) adds zero. A probe failure never raises — it degrades the entry to `"unknown"` and the list renders normally.

`PluginService.discover_library` passes `installable_kind` through in the dict it already builds for each entry.

### Command inference (on-demand)

`PluginFetcher` (`fetcher.py`) gains a context-manager method, `fetch_raw(self, source: PluginSource)`, that performs the same clone + `_validate` (size/file-count/symlink guards) as `fetch()` but:
- Never requires or reads a manifest.
- Never copies into `self.root` — it yields the `Path` to the cloned repository while still inside its `TemporaryDirectory`, and the directory is removed on context exit. Nothing from an uninstallable repository is persisted.

`src/agentos/plugins/mcp_inference.py` adds:

```python
@dataclass(frozen=True, slots=True)
class McpLaunchGuess:
    display_name: str
    transport: str | None       # "stdio" | "http" | None
    command: str | None
    args: tuple[str, ...]
    url: str | None
    secret_names: tuple[str, ...]
    confidence: str              # "structured" | "none"

def infer_mcp_launch(path: Path, *, suggested_name: str) -> McpLaunchGuess: ...
```

Reads, in priority order, stopping at the first usable signal:

1. **`mcp.json` / `smithery.json`** at repo root — the closest thing to a purpose-built descriptor. Looks for an `mcpServers` map (Claude Desktop config shape) and takes its first entry, or a flat `{command, args, url, env}` shape. `env` object keys (if present) become `secret_names`. Whichever of `command`/`url` is present sets `transport` to `stdio`/`http` respectively.
2. **`package.json`** — presence of a `bin` field plus a `name` field is read as "this is an npm-published CLI," inferring `command="npx", args=("-y", <package name>)`, `transport="stdio"` — this is the standard invocation documented by npm-published MCP servers, not a literal reading of the `bin` path.
3. **`pyproject.toml`** — presence of `[project.scripts]` (or `[tool.poetry.scripts]`) plus `[project].name` is read the same way for the Python ecosystem's equivalent convention: `command="uvx", args=(<project name>,)`, `transport="stdio"`.
4. **Nothing usable found** → `confidence="none"`, `transport=None`, `command=None`, `url=None`, `args=()`, `secret_names=()`, `display_name=suggested_name` (from the repo name, same fallback `resolve_source` already produces for the manual install dialog).

`http`/SSE inference is limited to signal 1 (`mcp.json`/`smithery.json`) — there's no npm/PyPI-equivalent convention to read a URL from for signals 2–3, so those two always resolve to `stdio` when they match.

`PluginService.infer_mcp_launch(self, *, source_url: str) -> dict[str, Any]`:

```python
def infer_mcp_launch(self, *, source_url: str) -> dict[str, Any]:
    source = resolve_source(source_url)
    if source.kind != "git":
        raise PluginServiceError("only a public GitHub repository can be added as an MCP server", code="plugin_invalid_source")
    try:
        with self.fetcher.fetch_raw(source) as path:
            guess = infer_mcp_launch(path, suggested_name=source.suggested_name)
    except FetchRejected as error:
        raise PluginServiceError(str(error), code="plugin_fetch_failed") from error
    return asdict(guess)
```

### API routes

`POST /v1/plugins/library/infer-mcp` in `gateway.py`, alongside the other `/v1/plugins/*` routes:

- Request model `PluginInferMcpRequest(_RequestModel)`: `source_url: str = Field(min_length=1, max_length=2048)`.
- `principal_for(request, mutable=True)`, `action="plugins.library.infer_mcp"`, `purpose="plugins.inspect"` — same category and CSRF posture as `POST /v1/plugins/inspect`, since this is conceptually the same thing (fetch and analyze a remote repository) applied to a different outcome shape.
- `run_in_threadpool`, since cloning is blocking I/O, same as `inspect_plugin`.
- Returns the `McpLaunchGuess` dict as JSON. Writes nothing to the database.

No new route is needed for creation — the frontend takes the guess, lets the user edit it, and calls the existing `POST /v1/mcp/servers` (propose) and `POST /v1/mcp/servers/{id}/approve`.

### Error code propagation for the "no manifest" case

Even with the proactive probe, an `"unknown"` entry (probe skipped or rate-limited) can still be clicked as "Instalar" and fail at fetch time for the same reason a probed `"mcp_raw"` entry would — the frontend needs to tell that specific failure apart from every other install failure so it can offer the same fallback instead of just erroring out.

`PluginServiceError` gains an optional `code` attribute, defaulting to the value the gateway already hardcodes today:

```python
class PluginServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_operation_rejected") -> None:
        super().__init__(message)
        self.code = code
```

In `service.py::inspect()`, the `except Exception` block that currently collapses every non-`PluginServiceError` failure into `PluginServiceError("plugin could not be inspected")` special-cases the manifest failure:

```python
except Exception as error:
    if isinstance(error, PluginServiceError):
        raise
    if isinstance(error, FetchRejected) and "no valid manifest" in str(error):
        raise PluginServiceError(str(error), code="plugin_no_manifest") from error
    raise PluginServiceError("plugin could not be inspected") from error
```

`gateway.py`'s exception handler stops hardcoding the code and reads it off the exception:

```python
@app.exception_handler(PluginServiceError)
async def plugin_service_error(_: Request, exc: PluginServiceError) -> JSONResponse:
    return _error(409, "CONFLICT", exc.code, retryable=False)
```

This is additive and backward compatible — every existing `PluginServiceError(...)` call site keeps producing `"plugin_operation_rejected"` since none of them pass `code=`.

## Frontend

### Data layer

`frontend/src/api/plugins.ts`:
- `PluginLibraryEntry` gains `installable_kind: 'plugin' | 'mcp_raw' | 'unknown'`, parsed with the same enum-validation pattern `origin` already uses.
- New `inferMcpLaunch(client, sourceUrl)` calling `POST /v1/plugins/library/infer-mcp`, returning a typed `McpLaunchGuess` (`{ display_name, transport, command, args, url, secret_names, confidence }`), reusing `mcpTransport`-style parsing from `api/mcp.ts` for the transport field (nullable here, unlike `McpServerSummary.transport`).

`PluginInstallDialog.tsx`'s `runInspection` catch block currently discards the caught error entirely. It changes to inspect it: if the error is an `ApiError` (from `api/errors.ts`) with `code === 'plugin_no_manifest'`, call a new optional `onNoManifest?: () => void` prop instead of (or in addition to) setting the generic error, so a caller can swap into the fallback dialog. All other failure codes keep today's generic `"Não foi possível inspecionar este plugin."` message unchanged.

### UI

`PluginLibrarySection.tsx`'s card action becomes conditional on `entry.installable_kind`:
- `"plugin"` → **Instalar**, unchanged (`PluginInstallDialog`).
- `"mcp_raw"` → **Adicionar como servidor MCP**, opens the new `McpFromRepoDialog`.
- `"unknown"` → **Instalar** as today; if that dialog's `onNoManifest` fires, the card closes `PluginInstallDialog` and opens `McpFromRepoDialog` with the same `source_url` instead of leaving the user on a dead-end error.

New `frontend/src/features/plugins/McpFromRepoDialog.tsx`:
- On mount, calls `inferMcpLaunch(client, sourceUrl)` and shows a loading state (matching `PluginInstallDialog`'s busy pattern).
- Renders a form with the same fields as `McpServerForm`'s manual mode (`displayName`, `transport` select, `command`/`args` or `url` depending on transport, `secretNames`), pre-filled from the guess and fully editable — the user always reviews before anything is created, mirroring how `PluginInstallDialog` never activates without an explicit confirm.
- If `confidence === 'none'`, the form opens with only `display_name` filled and an inline note: "Não foi possível detectar automaticamente o comando de execução — preencha manualmente."
- On submit, calls the existing `createMcpServer` (`api/mcp.ts`) — no new mutation is introduced. On success, renders the existing `McpApprovalCard` inline (same props shape `McpServerCard.tsx` already passes it) so the user enters secret values and approves — or declines, which deletes the pending server — without leaving the Biblioteca.
- Closing the dialog before submitting discards the guess; nothing was ever written.

No changes to `PluginCard.tsx`, `McpServerCard.tsx`, `McpServerForm.tsx`, or the propose/approve backend path itself.

## Error handling

- Manifest probe fails, times out, or is rate-limited → entry is `"unknown"`, never blocks or errors the list.
- Inference clone fails (unreachable repo, exceeds size/file budget) → `McpFromRepoDialog` shows the same generic "não foi possível" presentation style `PluginInstallDialog` already uses; the dialog stays open so the user can retry or fall back to filling the form manually themselves.
- Inference finds nothing conclusive → blank (but usable) form with an inline note, never an error state.
- Propose/approve failures inside `McpFromRepoDialog` → unchanged, handled by the existing `McpApprovalCard`/`McpServerForm` error states it reuses verbatim.
- `"unknown"`-kind entries that fail install specifically for lack of a manifest → caught via the new `plugin_no_manifest` code and redirected into `McpFromRepoDialog` instead of surfaced as a dead-end error.

## Testing

- Backend:
  - `tests/unit/plugins/test_manifest_probe.py` (new) — `.claude-plugin` present, `plugin.json` present, neither present, HTTP error → `None`.
  - `tests/unit/plugins/test_discovery.py` (extended) — `installable_kind` assignment per origin, registry entries skip the probe, 24h cache hit vs. expiry, a cached `None` is honored (not retried) within its TTL.
  - `tests/unit/plugins/test_mcp_inference.py` (new) — fixtures for `mcp.json`/`smithery.json` (stdio and http shapes, `env` → `secret_names`), `package.json` with/without `bin`, `pyproject.toml` with/without `[project.scripts]`, priority ordering when multiple files are present, and the `confidence="none"` fallback when none match.
  - `tests/unit/plugins/test_fetcher.py` (extended) — `fetch_raw` clones without a manifest, validates size/symlink the same as `fetch`, and cleans up its temp directory on exit.
  - `tests/unit/plugins/test_service.py` (or wherever `PluginService.inspect`/`infer_mcp_launch` is covered, extended) — `code="plugin_no_manifest"` propagates specifically for that `FetchRejected` message and not others; `infer_mcp_launch` rejects non-git sources with `plugin_invalid_source`.
- Frontend:
  - `PluginLibrarySection.test.tsx` (extended) — button choice per `installable_kind`, `"unknown"` → fallback redirect on `plugin_no_manifest`.
  - `McpFromRepoDialog.test.tsx` (new) — loading state, structured pre-fill, `confidence="none"` blank-form note, submit wiring into `createMcpServer`, inline `McpApprovalCard` render and approve/decline.
  - `PluginInstallDialog.test.tsx` (extended) — `onNoManifest` fires only for `plugin_no_manifest`, generic error message unchanged for every other failure code.

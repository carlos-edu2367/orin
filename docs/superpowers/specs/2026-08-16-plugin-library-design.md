# Plugin library design

## Intent

Let a user discover MCP-compatible plugins from within Settings > Plugins without leaving the app or knowing a repository URL in advance. The library combines known plugin marketplaces with a live web search, and installing a discovered plugin reuses the existing inspect/approve flow rather than introducing a second install path.

## Scope

In scope: a new discovery source that merges structured marketplace data with web search results, a read endpoint to fetch it, and a new "Biblioteca" sub-tab in the Plugins settings screen that lists results and installs them through the existing flow.

Out of scope: ranking/relevance tuning beyond simple dedup, user-authored search queries (the list is automatic, not query-driven), persisting discovery results to disk, and any change to how installed plugins are inspected, approved, or activated.

## Backend

### Discovery service

`src/agentos/plugins/discovery.py` adds `PluginDiscoveryService`, constructed with the existing marketplace registry and an optional web search client.

It combines two sources into a single list of `PluginLibraryEntry` (`name`, `description`, `source_url`, `origin: "registry" | "web"`):

- **Registry**: reuses `marketplace.py`'s `DEFAULT_MARKETPLACES` and any marketplaces the user has added via `add_marketplace`, parsed with the existing `parse_marketplace()`. Every entry here is already structured and requires no extra validation.
- **Web search**: uses a new `GithubRepositorySearchClient` (`plugins/github_search.py`), which calls GitHub's public repository search API unauthenticated — no API key of any kind is required, unlike the Brave-backed `agentic/web_search.py` used by the conversational agent's search tool (a separate, unrelated feature left untouched). The service issues a small, fixed set of topic-qualified queries (`topic:mcp-server`, `topic:claude-plugin`), and keeps only results whose URL passes the same public-GitHub allow-list `sources.py::resolve_source` already enforces for manual installs. The client is always constructed in production; `search_client=None` (used only in tests) disables the web source, in which case the service still returns registry results and the response is marked so the frontend can show a quiet "web search unavailable" note instead of an error.

Results from both sources are deduplicated by normalized repository URL (registry entries win on conflict, since they're pre-vetted). The merged list is cached in memory per-process with a short TTL (15 minutes). A `refresh=True` argument bypasses the cache and re-runs both sources synchronously.

This service does not fetch, clone, or inspect any repository — it only surfaces candidates. Fetching/inspection still happens exclusively through the existing `PluginFetcher` + inspector path when the user chooses to install one.

### API route

`GET /v1/plugins/library?refresh=<bool>` in `gateway.py`, placed alongside the other `/v1/plugins/*` routes. Follows the same conventions already used there:

- `run_in_threadpool`, since the web search call is blocking I/O.
- `services.security.check_rate_limit(principal, action="plugins.library", ...)` before running.
- Returns `{ entries: PluginLibraryEntry[], web_search_available: bool }`.

No new route is needed for install — the frontend drives the existing `POST /v1/plugins/inspect` and `POST /v1/plugins/{plugin_id}/approve` routes with the `source_url` taken from the chosen library entry.

## Frontend

### Data layer

`frontend/src/api/plugins.ts` gains `fetchPluginLibrary(refresh?: boolean)` returning the entries and `web_search_available` flag, typed to mirror the new backend response.

### UI

`PluginsSection.tsx` gains two sub-tabs: **Instalados** (the existing list, stats, and "Instalar plugin" dialog — unchanged) and **Biblioteca** (new).

A new `PluginLibrarySection.tsx`:

- Calls `fetchPluginLibrary()` on mount (no query field — the list is automatic per the approved design).
- Renders one card per entry: name, description, an origin badge ("Registro" / "Web"), and an **Instalar** button. If `web_search_available` is `false`, shows a small inline note above the list rather than blocking it.
- A top-level **Atualizar** button calls `fetchPluginLibrary(true)` and replaces the list, with its own loading state independent of the initial load.
- Clicking **Instalar** on a card calls the existing inspect flow with the entry's `source_url` (reusing the same request `PluginInstallDialog` already makes) and opens the existing approval UI on success, so the user reviews contributions before activation exactly as they do today. Inspection failures (e.g. the repo turns out not to be a valid plugin) surface the same error presentation `PluginInstallDialog` already has.

No changes to `PluginCard.tsx`, `PluginApprovalCard.tsx`, or the approve/activate backend path.

## Error handling

- Web search unconfigured or failing: library still returns registry results; frontend shows a non-blocking note, never an error state, since registry-only is a valid steady state.
- Marketplace fetch failing (e.g. a user-added marketphace repo is unreachable): that marketplace's entries are simply omitted from the merged list; does not fail the whole request (matches how `PluginService.search` already tolerates individual marketplace failures).
- Install (inspect/approve) failures: unchanged, handled by existing `PluginInstallDialog` error states.

## Testing

- Backend: `tests/unit/plugins/test_discovery.py` (new) — merge/dedup ordering, registry-only fallback when web search is unconfigured, cache hit vs. `refresh=True` bypass, allow-list filtering of web results. Style follows `tests/unit/plugins/test_fetcher.py` and `test_inspector.py`.
- Frontend: `frontend/tests/unit/PluginLibrarySection.test.tsx` (new) — loading/empty/error-note states, card rendering with origin badge, Instalar wiring into the inspect flow, Atualizar re-fetch. Style follows `PluginApprovalCard.test.tsx`.
- No changes required to existing plugin tests since the installed-plugins flow and its tests are untouched.

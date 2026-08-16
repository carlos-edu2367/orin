# Plugin Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Biblioteca de Plugins" sub-tab to Settings > Plugins that automatically surfaces MCP-compatible plugins from known marketplaces plus a live web search, and lets the user install one directly from a card through the existing inspect/approve flow.

**Architecture:** A new `PluginDiscoveryService` (backend) merges the already-existing `PluginService.search("")` marketplace scan with results from a Brave web search client (reused from `agentic/web_search.py`), deduping by normalized GitHub URL and caching the merged list in memory for 15 minutes. A new `GET /v1/plugins/library` route exposes it. The frontend adds a `PluginLibrarySection` that fetches this route on mount and an `Atualizar` button that forces a refresh; clicking `Instalar` on a card opens the existing `PluginInstallDialog` pre-filled with the entry's source, so it runs the same inspect → approve flow used for manual installs today.

**Tech Stack:** Python (FastAPI, httpx, SQLAlchemy) backend; React/TypeScript frontend; pytest and Vitest for tests.

Spec: `docs/superpowers/specs/2026-08-16-plugin-library-design.md`

---

## File Structure

- Create: `src/agentos/plugins/discovery.py` — `PluginLibraryEntry` dataclass and `PluginDiscoveryService` (merge/dedup/cache logic, no I/O of its own beyond the injected search client).
- Modify: `src/agentos/plugins/service.py` — `PluginService` gains an optional `search_client` constructor param, owns a `PluginDiscoveryService`, exposes `discover_library()`.
- Modify: `src/agentos/bootstrap/production.py` — wires `search_client_from_environment()` into the `PluginService` construction.
- Modify: `src/agentos/api/gateway.py` — new `GET /v1/plugins/library` route.
- Modify: `frontend/src/api/plugins.ts` — `PluginLibraryEntry`/`PluginLibraryResult` types and `fetchPluginLibrary()`.
- Modify: `frontend/src/features/plugins/PluginInstallDialog.tsx` — optional `initialReference` prop that auto-runs inspection on mount.
- Create: `frontend/src/features/plugins/PluginLibrarySection.tsx` — the library list UI.
- Modify: `frontend/src/features/plugins/PluginsSection.tsx` — adds the Instalados/Biblioteca tabs.
- Modify: `frontend/src/styles/agentos.css` — tab and library-card styles.
- Test: `tests/unit/plugins/test_discovery.py` (new), `tests/unit/plugins/test_service.py` (append), `tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py` (append), `frontend/tests/unit/pluginsApi.test.ts` (append), `frontend/tests/unit/PluginLibrarySection.test.tsx` (new), `frontend/tests/unit/PluginsSection.test.tsx` (append).

---

### Task 1: `PluginDiscoveryService` — merge, dedup, cache

**Files:**
- Create: `src/agentos/plugins/discovery.py`
- Test: `tests/unit/plugins/test_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
import httpx

from agentos.agentic.web_search import SearchResult
from agentos.plugins.discovery import DISCOVERY_QUERIES, PluginDiscoveryService

REGISTRY = [{"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo"}]


class FakePluginService:
    def __init__(self, entries):
        self._entries = entries
        self.search_calls = 0

    def search(self, query):
        self.search_calls += 1
        return self._entries


class FakeSearchClient:
    def __init__(self, results_by_query=None, *, error=False):
        self._results_by_query = results_by_query or {}
        self._error = error

    def search(self, query, *, limit=5):
        if self._error:
            raise httpx.HTTPError("boom")
        return self._results_by_query.get(query, [])


def test_registry_only_when_no_search_client():
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=None)
    entries, web_available = discovery.entries()
    assert web_available is False
    assert [e.origin for e in entries] == ["registry"]
    assert entries[0].source_url == "https://github.com/obra/superpowers.git"


def test_merges_web_results_and_registry_wins_on_conflict():
    results = {
        DISCOVERY_QUERIES[0]: [SearchResult("Superpowers", "https://github.com/obra/superpowers", "web desc")],
        DISCOVERY_QUERIES[1]: [SearchResult("Other MCP", "https://github.com/acme/other-mcp", "another plugin")],
    }
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=FakeSearchClient(results))
    entries, web_available = discovery.entries()
    assert web_available is True
    by_url = {e.source_url: e for e in entries}
    assert by_url["https://github.com/obra/superpowers.git"].origin == "registry"
    assert by_url["https://github.com/acme/other-mcp.git"].origin == "web"
    assert len(entries) == 2


def test_web_search_failure_keeps_registry_results_and_stays_available():
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=FakeSearchClient(error=True))
    entries, web_available = discovery.entries()
    assert web_available is True
    assert [e.origin for e in entries] == ["registry"]


def test_results_are_cached_until_refresh():
    plugin_service = FakePluginService(REGISTRY)
    discovery = PluginDiscoveryService(plugin_service, search_client=None)
    discovery.entries()
    discovery.entries()
    assert plugin_service.search_calls == 1
    discovery.entries(refresh=True)
    assert plugin_service.search_calls == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/plugins/test_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.plugins.discovery'`

- [ ] **Step 3: Write the implementation**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .sources import SourceRejected, resolve_source

CACHE_TTL = timedelta(minutes=15)
DISCOVERY_QUERIES = (
    '"mcp server" plugin.json github',
    'claude code plugin marketplace github',
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PluginLibraryEntry:
    name: str
    description: str
    source_url: str
    origin: str  # "registry" | "web"


class PluginDiscoveryService:
    """Surfaces installable plugin candidates; never fetches or clones anything itself."""

    def __init__(self, plugin_service: Any, *, search_client: Any = None) -> None:
        self._plugin_service = plugin_service
        self._search_client = search_client
        self._cache: tuple[list[PluginLibraryEntry], bool] | None = None
        self._cache_at: datetime | None = None

    def entries(self, *, refresh: bool = False) -> tuple[list[PluginLibraryEntry], bool]:
        if not refresh and self._cache is not None and self._cache_at is not None and _now() - self._cache_at < CACHE_TTL:
            return self._cache
        registry = self._registry_entries()
        web, web_available = self._web_entries()
        merged = self._merge(registry, web)
        self._cache, self._cache_at = (merged, web_available), _now()
        return self._cache

    def _registry_entries(self) -> list[PluginLibraryEntry]:
        entries: list[PluginLibraryEntry] = []
        for item in self._plugin_service.search(""):
            try:
                source = resolve_source(str(item["reference"]))
            except SourceRejected:
                continue
            url = source.url or str(item["reference"])
            entries.append(PluginLibraryEntry(str(item["name"]), str(item.get("description") or ""), url, "registry"))
        return entries

    def _web_entries(self) -> tuple[list[PluginLibraryEntry], bool]:
        if self._search_client is None:
            return [], False
        entries: list[PluginLibraryEntry] = []
        for query in DISCOVERY_QUERIES:
            try:
                results = self._search_client.search(query, limit=5)
            except httpx.HTTPError:
                # A configured client that failed this round is still "available" —
                # the frontend note is only for the no-client case.
                continue
            for result in results:
                try:
                    source = resolve_source(result.url)
                except SourceRejected:
                    continue
                if source.kind != "git" or not source.url:
                    continue
                entries.append(PluginLibraryEntry(source.suggested_name or result.title, result.snippet, source.url, "web"))
        return entries, True

    @staticmethod
    def _merge(registry: list[PluginLibraryEntry], web: list[PluginLibraryEntry]) -> list[PluginLibraryEntry]:
        seen: dict[str, PluginLibraryEntry] = {}
        for entry in registry:
            seen[entry.source_url.casefold()] = entry
        for entry in web:
            seen.setdefault(entry.source_url.casefold(), entry)
        return list(seen.values())


__all__ = ["PluginDiscoveryService", "PluginLibraryEntry"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/plugins/test_discovery.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/discovery.py tests/unit/plugins/test_discovery.py
git commit -m "feat(plugins): add PluginDiscoveryService merging registry and web results"
```

---

### Task 2: Wire discovery into `PluginService`

**Files:**
- Modify: `src/agentos/plugins/service.py`
- Test: `tests/unit/plugins/test_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/plugins/test_service.py`:

```python
def test_discover_library_returns_registry_entries(tmp_path):
    service = _service(tmp_path)
    library = service.discover_library()
    assert library["web_search_available"] is False
    assert library["entries"][0]["name"] == "superpowers"
    assert library["entries"][0]["origin"] == "registry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_service.py::test_discover_library_returns_registry_entries -v`
Expected: FAIL with `AttributeError: 'PluginService' object has no attribute 'discover_library'`

- [ ] **Step 3: Implement**

In `src/agentos/plugins/service.py`, add the import next to the existing relative imports (after `from .marketplace import parse_marketplace`):

```python
from .discovery import PluginDiscoveryService
```

Change the constructor (currently `def __init__(self, engine: Engine, *, plugin_root: Path, skill_library, mcp_service, fetcher=None, activator=None) -> None:`) to:

```python
    def __init__(self, engine: Engine, *, plugin_root: Path, skill_library, mcp_service, fetcher=None, activator=None, search_client=None) -> None:
        self.engine, self.plugin_root = engine, Path(plugin_root).resolve()
        self.skill_library, self.mcp_service = skill_library, mcp_service
        self.fetcher = fetcher or PluginFetcher(self.plugin_root)
        self.activator = activator or PluginActivator(skill_library=skill_library, mcp_service=mcp_service)
        self.discovery = PluginDiscoveryService(self, search_client=search_client)
```

Add a new method (place it next to `search`, after its closing line):

```python
    def discover_library(self, *, refresh: bool = False) -> dict[str, Any]:
        entries, web_available = self.discovery.entries(refresh=refresh)
        return {
            "entries": [{"name": e.name, "description": e.description, "source_url": e.source_url, "origin": e.origin} for e in entries],
            "web_search_available": web_available,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/plugins/test_service.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/service.py tests/unit/plugins/test_service.py
git commit -m "feat(plugins): expose discover_library on PluginService"
```

---

### Task 3: Wire the web search client into bootstrap

**Files:**
- Modify: `src/agentos/bootstrap/production.py`

- [ ] **Step 1: Add the import**

Add near the other `agentos.*` imports (after the `from agentos.projects import PostgresProjectStore` line):

```python
from agentos.agentic.web_search import search_client_from_environment
```

- [ ] **Step 2: Pass the client into `PluginService`**

Change the existing line:

```python
        plugins=PluginService(engine, plugin_root=orin_paths().data / "plugins", skill_library=skill_library, mcp_service=mcp_service),
```

to:

```python
        plugins=PluginService(engine, plugin_root=orin_paths().data / "plugins", skill_library=skill_library, mcp_service=mcp_service, search_client=search_client_from_environment()),
```

- [ ] **Step 3: Verify the app still imports cleanly**

Run: `python -c "import agentos.bootstrap.production"`
Expected: no output, exit code 0

- [ ] **Step 4: Commit**

```bash
git add src/agentos/bootstrap/production.py
git commit -m "feat(plugins): wire the web search client into PluginService at bootstrap"
```

---

### Task 4: `GET /v1/plugins/library` route

**Files:**
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py`, add a `discover_library` method to `SlowPlugins` (after the existing `approve` method):

```python
    def discover_library(self, *, refresh=False):
        time.sleep(SLOW_SECONDS)
        return {"entries": [], "web_search_available": False}
```

Then append a new test at the end of the file:

```python
def test_plugin_library_does_not_block_a_concurrent_simple_request():
    app = _app(plugins=SlowPlugins())

    async def slow_call(client):
        return await client.get("/v1/plugins/library", headers=_headers())

    async def fast_call(client):
        return await client.get("/v1/plugins", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow /library to finish — the event loop is blocked"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py::test_plugin_library_does_not_block_a_concurrent_simple_request -v`
Expected: FAIL with a 404 response (route does not exist yet)

- [ ] **Step 3: Implement the route**

In `src/agentos/api/gateway.py`, add this route immediately after `list_plugins` (right before the `@app.post("/v1/plugins/inspect")` line):

```python
    @app.get("/v1/plugins/library")
    async def get_plugin_library(request: Request, refresh: bool = False) -> JSONResponse:
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="plugins.library", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.library", resource_id=None, purpose="plugins.read")
        # Web search is blocking I/O just like plugin inspection, so keep it off the event loop.
        result = await run_in_threadpool(_require_port(services.plugins).discover_library, refresh=refresh)
        return JSONResponse(_jsonable(result))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/gateway.py tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py
git commit -m "feat(plugins): add GET /v1/plugins/library route"
```

---

### Task 5: Frontend API client for the library

**Files:**
- Modify: `frontend/src/api/plugins.ts`
- Test: `frontend/tests/unit/pluginsApi.test.ts`

- [ ] **Step 1: Write the failing test**

In `frontend/tests/unit/pluginsApi.test.ts`, add `fetchPluginLibrary` to the existing import line:

```ts
import { approvePlugin, fetchPluginLibrary, inspectPlugin, listPlugins, removePlugin } from '../../src/api/plugins'
```

Append a new test inside the `describe('plugins api', ...)` block:

```ts
  it('parses the plugin library response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [{ name: 'superpowers', description: 'd', source_url: 'https://github.com/obra/superpowers.git', origin: 'registry' }], web_search_available: false }))
    const library = await fetchPluginLibrary(client(fetchImpl))
    expect(library.entries[0].origin).toBe('registry')
    expect(library.web_search_available).toBe(false)
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run frontend/tests/unit/pluginsApi.test.ts`
Expected: FAIL with `SyntaxError` / `does not provide an export named 'fetchPluginLibrary'`

- [ ] **Step 3: Implement**

In `frontend/src/api/plugins.ts`, add types after `MarketplaceEntry`:

```ts
export type PluginLibraryEntry = { name: string; description: string; source_url: string; origin: 'registry' | 'web' }
export type PluginLibraryResult = { entries: PluginLibraryEntry[]; web_search_available: boolean }
```

Add the fetch function after `addMarketplace`:

```ts
export function fetchPluginLibrary(client: ApiClient, refresh = false, signal?: AbortSignal): Promise<PluginLibraryResult> { return client.request({ path: '/v1/plugins/library', query: { refresh: refresh || undefined }, signal, parse: parseLibrary }) }
```

Add the parsers after `parseMarketplaces`:

```ts
function libraryEntry(value: unknown): PluginLibraryEntry { const data = record(value); const origin = text(data.origin); if (origin !== 'registry' && origin !== 'web') throw invalidResponseError(); return { name: text(data.name), description: text(data.description ?? ''), source_url: text(data.source_url), origin } }
function parseLibrary(value: unknown): PluginLibraryResult { const data = record(value); if (!Array.isArray(data.entries)) throw invalidResponseError(); return { entries: data.entries.map(libraryEntry), web_search_available: data.web_search_available === true } }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/tests/unit/pluginsApi.test.ts`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/plugins.ts frontend/tests/unit/pluginsApi.test.ts
git commit -m "feat(plugins): add fetchPluginLibrary to the plugins API client"
```

---

### Task 6: `PluginInstallDialog` accepts a pre-filled reference

**Files:**
- Modify: `frontend/src/features/plugins/PluginInstallDialog.tsx`

There is no dedicated test file for this component today (it is exercised indirectly through `PluginsSection.test.tsx` and, after Task 7, through `PluginLibrarySection.test.tsx`); this task is a small, behavior-preserving change verified by those existing/upcoming suites, so no new test file is added here.

- [ ] **Step 1: Extract the shared inspection call and accept `initialReference`**

Replace the full contents of `frontend/src/features/plugins/PluginInstallDialog.tsx` with:

```tsx
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { approvePlugin, inspectPlugin, type PluginInspectionResult } from '../../api/plugins'
import type { ApiClient } from '../../api/client'

export function PluginInstallDialog({ client, onClose, onInstalled, initialReference }: { client: ApiClient; onClose: () => void; onInstalled: () => void; initialReference?: string }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [reference, setReference] = useState(initialReference ?? '')
  const [inspection, setInspection] = useState<PluginInspectionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    dialogRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])
  async function runInspection(value: string) { setBusy(true); setError(null); try { setInspection(await inspectPlugin(client, value)) } catch { setError('Não foi possível inspecionar este plugin.') } finally { setBusy(false) } }
  useEffect(() => { if (initialReference) void runInspection(initialReference) }, [initialReference])
  async function inspect(event: FormEvent) { event.preventDefault(); if (!reference.trim()) return; void runInspection(reference.trim()) }
  async function install() { if (!inspection) return; setBusy(true); try { await approvePlugin(client, inspection.plugin_id); onInstalled() } catch { setError('Não foi possível instalar o plugin.') } finally { setBusy(false) } }
  return <div className="plugin-dialog__backdrop"><div className="plugin-dialog" role="dialog" aria-modal="true" aria-labelledby="plugin-dialog-title" tabIndex={-1} ref={dialogRef}><header className="plugin-dialog__head"><div><span className="plugin-dialog__eyebrow">PLUGIN INSTALLER</span><h2 id="plugin-dialog-title">Instalar plugin</h2></div><button type="button" className="button--quiet" onClick={onClose}>Fechar</button></header><p className="plugin-dialog__lede">Inspecione a origem primeiro. A instalação só acontece depois da sua aprovação.</p><form className="plugin-dialog__form" onSubmit={(event) => void inspect(event)}><label htmlFor="plugin-reference">URL, owner/repo ou nome<input id="plugin-reference" value={reference} onChange={(event) => setReference(event.target.value)} placeholder="ex.: github.com/acme/meu-plugin" /></label><button type="submit" className="button button--primary" disabled={busy || !reference.trim()}>{busy ? 'Inspecionando…' : 'Inspecionar origem'}</button></form>{inspection && <div className="plugin-dialog__preview"><div className="plugin-dialog__preview-head"><span className="plugin-dialog__preview-icon" aria-hidden="true">✦</span><div><h3>{inspection.display_name}</h3><p>{inspection.author || 'Autor não informado'} · <code>v{inspection.version}</code></p></div></div><p className="plugin-dialog__description">{inspection.description || 'Sem descrição disponível.'}</p><dl className="plugin-dialog__facts"><div><dt>Contribuições</dt><dd>{inspection.contribution_count}</dd></div><div><dt>Avisos</dt><dd className={inspection.warnings.length > 0 ? 'has-warning' : undefined}>{inspection.warnings.length}</dd></div></dl>{inspection.warnings.length > 0 && <ul className="plugin-dialog__warnings">{inspection.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}<button type="button" className="button button--primary" onClick={() => void install()} disabled={busy}>{busy ? 'Instalando…' : 'Confirmar instalação'}</button></div>}{error && <p className="plugin-dialog__error" role="alert">{error}</p>}</div></div>
}
```

- [ ] **Step 2: Run the existing suite that exercises this component**

Run: `npx vitest run frontend/tests/unit/PluginsSection.test.tsx`
Expected: PASS (unchanged — `initialReference` is optional and defaults to today's behavior)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/plugins/PluginInstallDialog.tsx
git commit -m "feat(plugins): let PluginInstallDialog auto-inspect a pre-filled reference"
```

---

### Task 7: `PluginLibrarySection` component

**Files:**
- Create: `frontend/src/features/plugins/PluginLibrarySection.tsx`
- Test: `frontend/tests/unit/PluginLibrarySection.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { PluginLibrarySection } from '../../src/features/plugins/PluginLibrarySection'
import { ApiClient } from '../../src/api/client'

function response(body: unknown) { return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }) }

it('lists library entries and opens the install dialog', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [{ name: 'Other MCP', description: 'd', source_url: 'https://github.com/acme/other-mcp.git', origin: 'web' }], web_search_available: true }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Other MCP')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Instalar' }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
})

it('shows a note when web search is unavailable', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [], web_search_available: false }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText(/Busca na web indisponível/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run frontend/tests/unit/PluginLibrarySection.test.tsx`
Expected: FAIL — module `../../src/features/plugins/PluginLibrarySection` does not exist

- [ ] **Step 3: Implement**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { fetchPluginLibrary, type PluginLibraryEntry } from '../../api/plugins'
import type { ApiClient } from '../../api/client'
import { PluginInstallDialog } from './PluginInstallDialog'

const ORIGIN_LABEL: Record<PluginLibraryEntry['origin'], string> = { registry: 'Registro', web: 'Web' }

export function PluginLibrarySection({ client, onInstalled }: { client: ApiClient; onInstalled: () => void }) {
  const [entries, setEntries] = useState<PluginLibraryEntry[]>([])
  const [webAvailable, setWebAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const load = useCallback((refresh: boolean) => {
    (refresh ? setRefreshing : setLoading)(true)
    return fetchPluginLibrary(client, refresh)
      .then((result) => { setEntries(result.entries); setWebAvailable(result.web_search_available); setError(null) })
      .catch(() => setError('Não foi possível carregar a biblioteca de plugins.'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [client])
  useEffect(() => { void load(false) }, [load])
  return <div className="plugin-library">
    <div className="plugin-library__head">
      <p className="plugin-library__lede">Plugins compatíveis com MCP encontrados em marketplaces conhecidos e na web.</p>
      <button type="button" className="button button--secondary" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? 'Atualizando…' : 'Atualizar'}</button>
    </div>
    {!webAvailable && <p className="plugin-library__note">Busca na web indisponível no momento — mostrando apenas o registro conhecido.</p>}
    {loading && entries.length === 0 && <div className="plugins-section__loading" aria-label="Carregando biblioteca"><span /><span /><span /></div>}
    {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar a biblioteca</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => void load(false)}>Tentar novamente</button></div>}
    <div className="plugin-library__list" aria-label="Plugins disponíveis para instalação">
      {entries.map((entry) => <article className="plugin-library-card" key={entry.source_url}>
        <div className="plugin-library-card__head">
          <span className="plugin-library-card__icon" aria-hidden="true">✦</span>
          <div className="plugin-library-card__identity"><strong>{entry.name}</strong><span className={`plugin-library-card__badge is-${entry.origin}`}>{ORIGIN_LABEL[entry.origin]}</span></div>
        </div>
        <p className="plugin-library-card__description">{entry.description || 'Sem descrição disponível.'}</p>
        <button type="button" className="button button--primary" onClick={() => setInstalling(entry.source_url)}>Instalar</button>
      </article>)}
      {!loading && !error && entries.length === 0 && <p className="plugin-library__empty">Nenhum plugin encontrado no momento.</p>}
    </div>
    {installing && <PluginInstallDialog client={client} initialReference={installing} onClose={() => setInstalling(null)} onInstalled={() => { setInstalling(null); onInstalled() }} />}
  </div>
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/tests/unit/PluginLibrarySection.test.tsx`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/plugins/PluginLibrarySection.tsx frontend/tests/unit/PluginLibrarySection.test.tsx
git commit -m "feat(plugins): add the plugin library list UI"
```

---

### Task 8: Wire the tabs into `PluginsSection`

**Files:**
- Modify: `frontend/src/features/plugins/PluginsSection.tsx`
- Test: `frontend/tests/unit/PluginsSection.test.tsx`

- [ ] **Step 1: Write the failing assertion**

Append to the existing test in `frontend/tests/unit/PluginsSection.test.tsx` (inside the `it(...)` body, right before the closing `})`):

```ts
; expect(screen.getByRole('tab', { name: 'Biblioteca' })).toBeInTheDocument()
```

So the full line becomes:

```ts
it('lists installed plugins and offers installation', async () => { const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([{ plugin_id:'demo',version:'1.0.0',display_name:'Demo',description:'d',author:'a',homepage:null,state:'active',warnings:[],contribution_count:1 }]), { status:200, headers:{'Content-Type':'application/json'} })); render(<MemoryRouter><PluginsSection client={new ApiClient({fetchImpl,maxAttempts:1})} /></MemoryRouter>); expect(await screen.findByText('Demo')).toBeInTheDocument(); expect(screen.getByRole('button', { name:'Instalar plugin' })).toBeInTheDocument(); expect(screen.getByRole('tab', { name: 'Biblioteca' })).toBeInTheDocument() })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run frontend/tests/unit/PluginsSection.test.tsx`
Expected: FAIL — no element with role `tab` named `Biblioteca`

- [ ] **Step 3: Implement the tabs**

Replace the full contents of `frontend/src/features/plugins/PluginsSection.tsx` with:

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { listPlugins, type PluginSummary } from '../../api/plugins'
import { SettingsSection } from '../settings/SettingsSection'
import { PluginCard } from './PluginCard'
import { PluginInstallDialog } from './PluginInstallDialog'
import { PluginLibrarySection } from './PluginLibrarySection'

export function PluginsSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [plugins, setPlugins] = useState<PluginSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)
  const [tab, setTab] = useState<'installed' | 'library'>('installed')
  const refresh = useCallback(() => listPlugins(apiClient).then((value) => { setPlugins(value); setError(null) }).catch(() => setError('Não foi possível carregar os plugins.')).finally(() => setLoading(false)), [apiClient])
  useEffect(() => { void refresh() }, [refresh])
  const activeCount = plugins.filter((plugin) => plugin.state === 'active').length
  const warningCount = plugins.reduce((count, plugin) => count + plugin.warnings.length, 0)
  return <SettingsSection
    eyebrow="EXTENSÕES / PLUGINS"
    title="Plugins"
    lede="Pacotes declarativos são inspecionados antes de qualquer contribuição ser ativada."
    actions={<button type="button" className="button button--primary plugins-section__install" onClick={() => setDialog(true)}><span className="plugins-section__install-mark" aria-hidden="true">+</span>Instalar plugin</button>}
  >
    <div className="plugins-section">
      <section className="plugins-section__overview" aria-label="Resumo dos plugins">
        <div className="plugins-section__overview-copy">
          <span className="plugins-section__signal" aria-hidden="true">EXTENSION REGISTRY</span>
          <h2>Amplie o workspace com segurança</h2>
          <p>Cada plugin passa por inspeção antes de entrar no ambiente. Revise as contribuições e mantenha somente o que está em uso.</p>
        </div>
        <dl className="plugins-section__stats">
          <div><dt>Instalados</dt><dd>{plugins.length}</dd></div>
          <div><dt>Ativos</dt><dd>{activeCount}</dd></div>
          <div><dt>Avisos</dt><dd className={warningCount > 0 ? 'has-warning' : undefined}>{warningCount}</dd></div>
        </dl>
      </section>

      <div className="plugins-section__tabs" role="tablist" aria-label="Seções de plugins">
        <button type="button" role="tab" aria-selected={tab === 'installed'} className={`plugins-section__tab ${tab === 'installed' ? 'is-active' : ''}`} onClick={() => setTab('installed')}>Instalados</button>
        <button type="button" role="tab" aria-selected={tab === 'library'} className={`plugins-section__tab ${tab === 'library' ? 'is-active' : ''}`} onClick={() => setTab('library')}>Biblioteca</button>
      </div>

      {tab === 'installed' && <>
        {loading && plugins.length === 0 && <div className="plugins-section__loading" aria-label="Carregando plugins"><span /><span /><span /></div>}
        {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar os plugins</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => { setLoading(true); void refresh() }}>Tentar novamente</button></div>}
        <div className="plugins-section__list" aria-label="Plugins instalados">
          {plugins.map((plugin) => <PluginCard key={plugin.plugin_id} plugin={plugin} client={apiClient} onChanged={() => void refresh()} />)}
          {!loading && !error && plugins.length === 0 && <div className="plugins-section__empty"><span className="plugins-section__empty-icon" aria-hidden="true">✦</span><div><h2>Nenhum plugin instalado</h2><p>Instale um pacote para adicionar Skills, servidores MCP ou agentes ao seu workspace.</p></div><button type="button" className="button button--secondary" onClick={() => setDialog(true)}>Explorar um plugin</button></div>}
        </div>
      </>}

      {tab === 'library' && <PluginLibrarySection client={apiClient} onInstalled={() => void refresh()} />}
    </div>
    {dialog && <PluginInstallDialog client={apiClient} onClose={() => setDialog(false)} onInstalled={() => { setDialog(false); void refresh() }} />}
  </SettingsSection>
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/tests/unit/PluginsSection.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/plugins/PluginsSection.tsx frontend/tests/unit/PluginsSection.test.tsx
git commit -m "feat(plugins): add the Instalados/Biblioteca tabs to Settings > Plugins"
```

---

### Task 9: Styles

**Files:**
- Modify: `frontend/src/styles/agentos.css`

- [ ] **Step 1: Add the new rules**

Insert the following block right after the line `.plugins-section__error p { margin: 4px 0 0; color: var(--muted); font-size: 11px; }` (and before `.plugin-dialog__backdrop { ... }`):

```css
.plugins-section__tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); }
.plugins-section__tab { padding: 9px 14px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); font: 12px var(--mono); cursor: pointer; }
.plugins-section__tab.is-active { border-bottom-color: var(--signal); color: var(--text); }
.plugin-library { display: grid; gap: 14px; }
.plugin-library__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.plugin-library__lede { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.55; }
.plugin-library__note { margin: 0; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgb(var(--orin-ink-rgb) / .18); color: var(--faint); font-size: 11px; }
.plugin-library__list { display: grid; gap: 12px; }
.plugin-library__empty { margin: 0; color: var(--muted); font-size: 12px; }
.plugin-library-card { display: grid; gap: 10px; padding: 16px 18px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--raised); }
.plugin-library-card__head { display: flex; align-items: center; gap: 12px; }
.plugin-library-card__icon { display: grid; place-items: center; flex: 0 0 auto; width: 34px; height: 34px; border: 1px solid rgb(var(--orin-accent-rgb) / .32); border-radius: 10px; background: rgb(var(--orin-accent-rgb) / .1); color: var(--signal); font-size: 17px; }
.plugin-library-card__identity { display: flex; align-items: center; gap: 8px; }
.plugin-library-card__badge { padding: 3px 8px; border: 1px solid var(--line); border-radius: 99px; color: var(--mono-readable); font: 9px var(--mono); letter-spacing: .04em; }
.plugin-library-card__badge.is-web { border-color: rgb(var(--orin-accent-rgb) / .36); color: var(--signal); }
.plugin-library-card__description { margin: 0 0 0 46px; color: var(--muted); font-size: 12px; line-height: 1.55; }
```

- [ ] **Step 2: Verify the frontend still builds**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles/agentos.css
git commit -m "style(plugins): add styles for the library tabs and cards"
```

---

### Task 10: Full-suite check and manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the backend plugin tests**

Run: `python -m pytest tests/unit/plugins tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py -v`
Expected: PASS

- [ ] **Step 2: Run the frontend unit tests**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 3: Manual verification in the browser**

Start the app's dev server (per the project's existing `run` workflow), open Settings > Plugins, click the **Biblioteca** tab, and confirm:
- The list loads automatically without any query input.
- Each card shows a name, description, and an origin badge ("Registro" or "Web").
- Clicking **Instalar** on a card opens the install dialog already showing the inspected plugin (no need to retype a URL).
- Clicking **Atualizar** re-fetches the list without navigating away from the tab.
- If `AGENTOS_SEARCH_API_KEY` is not set in the running environment, the "Busca na web indisponível" note is visible and the registry entries (e.g. `superpowers`) still render.

Take a screenshot of the Biblioteca tab for the record.

- [ ] **Step 4: No commit for this task** — it is verification-only; any bug found here should be fixed by amending the relevant task above, not by adding ad hoc code at the end.

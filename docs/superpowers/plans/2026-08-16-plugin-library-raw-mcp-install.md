# Plugin Library Raw MCP Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Plugin Library's "Biblioteca" tab offer manifest-less GitHub repositories (plain MCP servers) an "Adicionar como servidor MCP" path — proactively flagged at discovery time, with a best-effort inferred launch command — instead of rejecting them the way the plugin-manifest install path does today.

**Architecture:** A new `GithubManifestProbe` checks (once per web result, 24h-cached, registry entries skipped) whether a repository has a plugin manifest, tagging each `PluginLibraryEntry` with `installable_kind`. A new `PluginFetcher.fetch_raw` clones a repository without requiring a manifest; a new `mcp_inference.py` reads `mcp.json`/`smithery.json`/`package.json`/`pyproject.toml` from that clone to guess a launch command. The guess feeds a new frontend dialog that lets the user review/edit it before calling the existing MCP propose/approve endpoints — no new persistence path is introduced.

**Tech Stack:** Python 3.13 (FastAPI, SQLAlchemy, httpx), React + TypeScript (Vitest, Testing Library).

**Spec:** [2026-08-16-plugin-library-raw-mcp-install-design.md](../specs/2026-08-16-plugin-library-raw-mcp-install-design.md)

---

## File Structure

Backend (`src/agentos/plugins/`, `src/agentos/api/`, `src/agentos/bootstrap/`):
- `manifest_probe.py` (new) — `GithubManifestProbe`, one GitHub Contents API call per repo, `bool | None`.
- `mcp_inference.py` (new) — `McpLaunchGuess`, `infer_mcp_launch(path, suggested_name)`, pure filesystem parsing, no I/O.
- `discovery.py` (modified) — `PluginLibraryEntry.installable_kind`, probe wiring, 24h manifest cache.
- `fetcher.py` (modified) — clone logic factored into `_clone_into`; new `fetch_raw` context manager reuses it without persisting or requiring a manifest.
- `service.py` (modified) — `PluginServiceError.code`; `inspect()` propagates `plugin_no_manifest`; new `infer_mcp_launch()`; `discover_library()` passes `installable_kind` through; `__init__` gains `manifest_probe`.
- `api/gateway.py` (modified) — `PluginInferMcpRequest`; `POST /v1/plugins/library/infer-mcp`; exception handler reads `exc.code` instead of a hardcoded string.
- `bootstrap/production.py` (modified) — wires `GithubManifestProbe()` into the production `PluginService`.

Frontend (`frontend/src/`):
- `api/plugins.ts` (modified) — `PluginLibraryEntry.installable_kind`; new `McpLaunchGuess` type and `inferMcpLaunch()`.
- `features/plugins/PluginInstallDialog.tsx` (modified) — new optional `onNoManifest` prop, fired on the `plugin_no_manifest` error code.
- `features/plugins/McpFromRepoDialog.tsx` (new) — infers, shows an editable form, submits through the existing `createMcpServer`, then the existing `McpApprovalCard`.
- `features/plugins/PluginLibrarySection.tsx` (modified) — conditional card action by `installable_kind`, fallback wiring from `onNoManifest` into `McpFromRepoDialog`.

No new CSS: `McpFromRepoDialog` reuses `.plugin-dialog__backdrop`/`.plugin-dialog` (from `PluginInstallDialog`) and `.mcp-server-form__manual` field markup (from `McpServerForm`), both already styled in `frontend/src/styles/agentos.css`.

---

### Task 1: Propagate the "no manifest" reason through `PluginServiceError`

**Files:**
- Modify: `src/agentos/plugins/service.py:25-27` (class `PluginServiceError`), `src/agentos/plugins/service.py:16-22` (imports), `src/agentos/plugins/service.py:94-100` (`inspect()`)
- Modify: `src/agentos/api/gateway.py:361-363` (`plugin_service_error` handler)
- Test: `tests/unit/plugins/test_service.py`
- Test: `tests/unit/api/test_plugin_routes.py`

- [ ] **Step 1: Write the failing backend test**

Add to `tests/unit/plugins/test_service.py`:

```python
def test_inspect_reports_a_distinct_code_when_the_repository_has_no_manifest(tmp_path):
    service = _service(tmp_path)
    no_manifest_repo = tmp_path / "no-manifest"
    no_manifest_repo.mkdir()
    (no_manifest_repo / "README.md").write_text("just a readme", encoding="utf-8")
    try:
        service.inspect(user_id="u1", reference=str(no_manifest_repo))
    except PluginServiceError as error:
        assert error.code == "plugin_no_manifest"
    else:
        raise AssertionError("expected inspect() to reject a repository with no manifest")


def test_other_inspect_failures_keep_the_generic_code(tmp_path):
    service = _service(tmp_path)
    try:
        service.inspect(user_id="u1", reference="not-a-real/repo-shape!!!")
    except PluginServiceError as error:
        assert error.code == "plugin_operation_rejected"
    else:
        raise AssertionError("expected inspect() to reject a malformed reference")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_service.py -k no_manifest_or_generic_code -v`

This selector matches nothing yet since the test names don't contain that substring — instead run:

Run: `python -m pytest tests/unit/plugins/test_service.py::test_inspect_reports_a_distinct_code_when_the_repository_has_no_manifest tests/unit/plugins/test_service.py::test_other_inspect_failures_keep_the_generic_code -v`

Expected: FAIL — `AttributeError: 'PluginServiceError' object has no attribute 'code'`.

- [ ] **Step 3: Add the `code` attribute and propagate it in `inspect()`**

In `src/agentos/plugins/service.py`, change the import line (currently `from .fetcher import PluginFetcher`) to:

```python
from .fetcher import FetchRejected, PluginFetcher
```

Replace the `PluginServiceError` class:

```python
class PluginServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_operation_rejected") -> None:
        super().__init__(message)
        self.code = code
```

Replace the `except Exception` block inside `inspect()` (currently):

```python
        except Exception as error:
            if isinstance(error, PluginServiceError):
                raise
            raise PluginServiceError("plugin could not be inspected") from error
```

with:

```python
        except Exception as error:
            if isinstance(error, PluginServiceError):
                raise
            if isinstance(error, FetchRejected) and "no valid manifest" in str(error):
                raise PluginServiceError(str(error), code="plugin_no_manifest") from error
            raise PluginServiceError("plugin could not be inspected") from error
```

- [ ] **Step 4: Run the backend test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_service.py::test_inspect_reports_a_distinct_code_when_the_repository_has_no_manifest tests/unit/plugins/test_service.py::test_other_inspect_failures_keep_the_generic_code -v`

Expected: PASS

- [ ] **Step 5: Write the failing gateway test**

Add to `tests/unit/api/test_plugin_routes.py`, and add a new `Plugins` case for the error path. Update the `Plugins` class's `inspect` method and add a new test:

```python
class RejectingPlugins(Plugins):
    def inspect(self, **kwargs):
        from agentos.plugins.service import PluginServiceError
        raise PluginServiceError("plugin package has no valid manifest", code="plugin_no_manifest")


def test_plugin_inspect_route_surfaces_the_no_manifest_code():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=RejectingPlugins())))
    response = client.post("/v1/plugins/inspect", json={"reference": "acme/no-manifest"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_no_manifest"
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `python -m pytest tests/unit/api/test_plugin_routes.py::test_plugin_inspect_route_surfaces_the_no_manifest_code -v`

Expected: FAIL — `assert 'plugin_operation_rejected' == 'plugin_no_manifest'`.

- [ ] **Step 7: Make the gateway handler read the code off the exception**

In `src/agentos/api/gateway.py`, replace:

```python
    @app.exception_handler(PluginServiceError)
    async def plugin_service_error(_: Request, __: PluginServiceError) -> JSONResponse:
        return _error(409, "CONFLICT", "plugin_operation_rejected", retryable=False)
```

with:

```python
    @app.exception_handler(PluginServiceError)
    async def plugin_service_error(_: Request, exc: PluginServiceError) -> JSONResponse:
        return _error(409, "CONFLICT", exc.code, retryable=False)
```

- [ ] **Step 8: Run both test files to verify everything passes**

Run: `python -m pytest tests/unit/plugins/test_service.py tests/unit/api/test_plugin_routes.py -v`

Expected: PASS (all tests, including the pre-existing ones)

- [ ] **Step 9: Commit**

```bash
git add src/agentos/plugins/service.py src/agentos/api/gateway.py tests/unit/plugins/test_service.py tests/unit/api/test_plugin_routes.py
git commit -m "fix(plugins): propagate the no-manifest reason through PluginServiceError"
```

---

### Task 2: `GithubManifestProbe`

**Files:**
- Create: `src/agentos/plugins/manifest_probe.py`
- Test: `tests/unit/plugins/test_manifest_probe.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/plugins/test_manifest_probe.py`:

```python
import httpx

from agentos.plugins.manifest_probe import GithubManifestProbe


def test_probe_finds_a_dotted_plugin_directory():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/has-manifest/contents"
        return httpx.Response(200, json=[
            {"name": ".claude-plugin", "type": "dir"},
            {"name": "README.md", "type": "file"},
        ])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "has-manifest") is True


def test_probe_finds_a_root_plugin_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "plugin.json", "type": "file"}])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "has-manifest") is True


def test_probe_returns_false_when_neither_is_present():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "README.md", "type": "file"}, {"name": "src", "type": "dir"}])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "no-manifest") is False


def test_probe_returns_none_on_http_error():
    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))))
    assert probe.probe("acme", "rate-limited") is None


def test_probe_returns_none_on_a_malformed_body():
    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"not": "a list"}))))
    assert probe.probe("acme", "weird") is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_manifest_probe.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.plugins.manifest_probe'`

- [ ] **Step 3: Implement `GithubManifestProbe`**

Create `src/agentos/plugins/manifest_probe.py`:

```python
"""Checks, with a single request, whether a public GitHub repository has an
installable Claude plugin manifest (``.claude-plugin/plugin.json`` or a root
``plugin.json``) — the same two locations ``PluginFetcher``/
``inspect_plugin_package`` already look for. Keyless and unauthenticated,
same as ``github_search.py``; a failed or rate-limited check returns
``None`` rather than raising, since "couldn't tell" must never be confused
with "confirmed absent."
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

PROBE_TIMEOUT_SECONDS = 15
DEFAULT_ENDPOINT = "https://api.github.com/repos"


class GithubManifestProbe:
    def __init__(self, client: httpx.Client | None = None, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=PROBE_TIMEOUT_SECONDS)
        self._owns_client = client is None

    def probe(self, owner: str, repo: str) -> bool | None:
        try:
            response = self._client.get(
                f"{self._endpoint}/{owner}/{repo}/contents",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "orin-plugin-discovery",
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError:
            return None
        except (TypeError, ValueError):
            return False
        return self._has_manifest(body)

    @staticmethod
    def _has_manifest(body: Any) -> bool:
        if not isinstance(body, list):
            return False
        for item in body:
            if not isinstance(item, Mapping):
                continue
            if item.get("name") == ".claude-plugin" and item.get("type") == "dir":
                return True
            if item.get("name") == "plugin.json" and item.get("type") == "file":
                return True
        return False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["DEFAULT_ENDPOINT", "GithubManifestProbe"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_manifest_probe.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/manifest_probe.py tests/unit/plugins/test_manifest_probe.py
git commit -m "feat(plugins): add a keyless GitHub manifest presence probe"
```

---

### Task 3: Wire `installable_kind` into plugin discovery

**Files:**
- Modify: `src/agentos/plugins/discovery.py`
- Modify: `src/agentos/plugins/service.py` (`__init__`, `discover_library`)
- Test: `tests/unit/plugins/test_discovery.py`
- Test: `tests/unit/plugins/test_service.py`

- [ ] **Step 1: Write the failing discovery tests**

Add to `tests/unit/plugins/test_discovery.py` (add this import at the top alongside the existing ones: `from datetime import UTC, datetime, timedelta`):

```python
class FakeManifestProbe:
    def __init__(self, result_by_repo=None, *, error=False):
        self._result_by_repo = result_by_repo or {}
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def probe(self, owner, repo):
        self.calls.append((owner, repo))
        if self._error:
            return None
        return self._result_by_repo.get(f"{owner}/{repo}")


def test_registry_entries_are_always_plugin_kind_and_skip_the_probe():
    probe = FakeManifestProbe({"obra/superpowers": False})
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=None, manifest_probe=probe)
    entries, _ = discovery.entries()
    assert entries[0].installable_kind == "plugin"
    assert probe.calls == []


def test_web_entries_are_tagged_plugin_or_mcp_raw_from_the_probe():
    results = {
        DISCOVERY_QUERIES[0]: [SearchResult("Has Manifest", "https://github.com/acme/has-manifest", "d")],
        DISCOVERY_QUERIES[1]: [SearchResult("No Manifest", "https://github.com/acme/no-manifest", "d")],
    }
    probe = FakeManifestProbe({"acme/has-manifest": True, "acme/no-manifest": False})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    entries, _ = discovery.entries()
    by_url = {e.source_url: e for e in entries}
    assert by_url["https://github.com/acme/has-manifest.git"].installable_kind == "plugin"
    assert by_url["https://github.com/acme/no-manifest.git"].installable_kind == "mcp_raw"


def test_web_entries_are_unknown_kind_when_the_probe_is_disabled_or_fails():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    no_probe = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=None)
    entries, _ = no_probe.entries()
    assert entries[0].installable_kind == "unknown"

    failing_probe = FakeManifestProbe(error=True)
    with_failing_probe = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=failing_probe)
    entries, _ = with_failing_probe.entries()
    assert entries[0].installable_kind == "unknown"


def test_manifest_probe_result_is_cached_for_24_hours():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    probe = FakeManifestProbe({"acme/web-thing": True})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    discovery.entries(refresh=True)
    discovery.entries(refresh=True)
    assert probe.calls == [("acme", "web-thing")]


def test_manifest_probe_cache_expires_after_24_hours():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    probe = FakeManifestProbe({"acme/web-thing": True})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    discovery.entries(refresh=True)
    stale_url = "https://github.com/acme/web-thing.git"
    discovery._manifest_cache[stale_url] = (discovery._manifest_cache[stale_url][0], datetime.now(UTC) - timedelta(hours=25))
    discovery.entries(refresh=True)
    assert probe.calls == [("acme", "web-thing"), ("acme", "web-thing")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_discovery.py -v`

Expected: FAIL — `TypeError: PluginDiscoveryService.__init__() got an unexpected keyword argument 'manifest_probe'`

- [ ] **Step 3: Add `installable_kind`, the probe, and its cache to `discovery.py`**

Replace the top of `src/agentos/plugins/discovery.py` (imports and constants) with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .sources import SourceRejected, resolve_source

CACHE_TTL = timedelta(minutes=15)
MANIFEST_CACHE_TTL = timedelta(hours=24)
DISCOVERY_QUERIES = (
    "topic:mcp-server stars:>=3",
    "topic:claude-plugin stars:>=3",
)
```

Replace `PluginLibraryEntry`:

```python
@dataclass(frozen=True, slots=True)
class PluginLibraryEntry:
    name: str
    description: str
    source_url: str
    origin: str  # "registry" | "web"
    installable_kind: str  # "plugin" | "mcp_raw" | "unknown"
```

Replace the `__init__` and `entries`/`_registry_entries`/`_web_entries` methods:

```python
class PluginDiscoveryService:
    """Surfaces installable plugin candidates; never fetches or clones anything itself."""

    def __init__(self, plugin_service: Any, *, search_client: Any = None, manifest_probe: Any = None) -> None:
        self._plugin_service = plugin_service
        self._search_client = search_client
        self._manifest_probe = manifest_probe
        self._cache: tuple[list[PluginLibraryEntry], bool] | None = None
        self._cache_at: datetime | None = None
        self._manifest_cache: dict[str, tuple[bool | None, datetime]] = {}

    def entries(self, *, refresh: bool = False, query: str | None = None) -> tuple[list[PluginLibraryEntry], bool]:
        needle = (query or "").strip()
        if needle:
            registry = self._registry_entries(needle)
            web, web_available = self._web_entries((needle,), limit=8)
            return self._merge(registry, web), web_available
        if not refresh and self._cache is not None and self._cache_at is not None and _now() - self._cache_at < CACHE_TTL:
            return self._cache
        registry = self._registry_entries("")
        web, web_available = self._web_entries(DISCOVERY_QUERIES, limit=5)
        merged = self._merge(registry, web)
        self._cache, self._cache_at = (merged, web_available), _now()
        return self._cache

    def _registry_entries(self, needle: str) -> list[PluginLibraryEntry]:
        entries: list[PluginLibraryEntry] = []
        for item in self._plugin_service.search(needle):
            try:
                source = resolve_source(str(item["reference"]))
            except SourceRejected:
                continue
            if source.kind != "git" or not source.url:
                continue
            entries.append(PluginLibraryEntry(str(item["name"]), str(item.get("description") or ""), source.url, "registry", "plugin"))
        return entries

    def _web_entries(self, queries: tuple[str, ...], *, limit: int) -> tuple[list[PluginLibraryEntry], bool]:
        if self._search_client is None:
            return [], False
        entries: list[PluginLibraryEntry] = []
        for query in queries:
            try:
                results = self._search_client.search(query, limit=limit)
            except httpx.HTTPError:
                continue
            for result in results:
                try:
                    source = resolve_source(result.url)
                except SourceRejected:
                    continue
                if source.kind != "git" or not source.url:
                    continue
                kind = self._probe_kind(source.url)
                entries.append(PluginLibraryEntry(source.suggested_name or result.title, result.snippet, source.url, "web", kind))
        return entries, True

    def _probe_kind(self, repo_url: str) -> str:
        if self._manifest_probe is None:
            return "unknown"
        cached = self._manifest_cache.get(repo_url)
        if cached is not None and _now() - cached[1] < MANIFEST_CACHE_TTL:
            has_manifest = cached[0]
        else:
            owner_repo = repo_url.removeprefix("https://github.com/").removesuffix(".git")
            owner, _, repo = owner_repo.partition("/")
            has_manifest = self._manifest_probe.probe(owner, repo) if owner and repo else None
            self._manifest_cache[repo_url] = (has_manifest, _now())
        if has_manifest is True:
            return "plugin"
        if has_manifest is False:
            return "mcp_raw"
        return "unknown"

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

- [ ] **Step 4: Run the discovery test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_discovery.py -v`

Expected: PASS (all tests, including the pre-existing ones from before this task)

- [ ] **Step 5: Write the failing service test for `installable_kind` passthrough**

Add to `tests/unit/plugins/test_service.py`:

```python
def test_discover_library_passes_installable_kind_through(tmp_path):
    service = _service(tmp_path)
    library = service.discover_library()
    assert library["entries"][0]["installable_kind"] == "plugin"
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_service.py::test_discover_library_passes_installable_kind_through -v`

Expected: FAIL — `KeyError: 'installable_kind'`

- [ ] **Step 7: Wire `manifest_probe` through `PluginService` and pass `installable_kind` through `discover_library`**

In `src/agentos/plugins/service.py`, replace the `__init__` signature and discovery construction:

```python
    def __init__(self, engine: Engine, *, plugin_root: Path, skill_library, mcp_service, fetcher=None, activator=None, search_client=None, manifest_probe=None) -> None:
        self.engine, self.plugin_root = engine, Path(plugin_root).resolve()
        self.skill_library, self.mcp_service = skill_library, mcp_service
        self.fetcher = fetcher or PluginFetcher(self.plugin_root)
        self.activator = activator or PluginActivator(skill_library=skill_library, mcp_service=mcp_service)
        self.discovery = PluginDiscoveryService(self, search_client=search_client, manifest_probe=manifest_probe)
```

Replace `discover_library`:

```python
    def discover_library(self, *, refresh: bool = False, query: str | None = None) -> dict[str, Any]:
        entries, web_available = self.discovery.entries(refresh=refresh, query=query)
        return {
            "entries": [{"name": e.name, "description": e.description, "source_url": e.source_url, "origin": e.origin, "installable_kind": e.installable_kind} for e in entries],
            "web_search_available": web_available,
        }
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_service.py -v`

Expected: PASS (all tests)

- [ ] **Step 9: Run the full plugins test suite to check for regressions**

Run: `python -m pytest tests/unit/plugins/ -v`

Expected: PASS (all tests)

- [ ] **Step 10: Commit**

```bash
git add src/agentos/plugins/discovery.py src/agentos/plugins/service.py tests/unit/plugins/test_discovery.py tests/unit/plugins/test_service.py
git commit -m "feat(plugins): tag library entries with a proactive installable_kind"
```

---

### Task 4: `PluginFetcher.fetch_raw` — clone without a manifest

**Files:**
- Modify: `src/agentos/plugins/fetcher.py`
- Test: `tests/unit/plugins/test_fetcher.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/plugins/test_fetcher.py`:

```python
from agentos.plugins.fetcher import FetchRejected


def _repo_without_manifest(root):
    root.mkdir(parents=True)
    (root / "README.md").write_text("no manifest here", encoding="utf-8")
    (root / "package.json").write_text('{"name": "demo-mcp", "bin": "./cli.js"}', encoding="utf-8")
    return root


def test_fetch_raw_clones_a_repository_with_no_manifest_and_cleans_up(tmp_path):
    source_dir = _repo_without_manifest(tmp_path / "raw-src")
    fetcher = PluginFetcher(tmp_path / "plugins")
    source = resolve_source(str(source_dir))
    captured_path = None
    with fetcher.fetch_raw(source) as path:
        captured_path = path
        assert (path / "package.json").is_file()
        assert not (path / ".claude-plugin").exists()
    assert not captured_path.exists()
    assert not (tmp_path / "plugins" / "demo-mcp").exists()


def test_fetch_raw_still_enforces_the_size_and_symlink_guards(tmp_path):
    source_dir = tmp_path / "raw-src"
    source_dir.mkdir()
    fetcher = PluginFetcher(tmp_path / "plugins", max_files=0)
    (source_dir / "file.txt").write_text("x", encoding="utf-8")
    source = resolve_source(str(source_dir))
    try:
        with fetcher.fetch_raw(source):
            pass
    except FetchRejected:
        pass
    else:
        raise AssertionError("expected fetch_raw to enforce the file-count budget")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_fetcher.py -v`

Expected: FAIL — `AttributeError: 'PluginFetcher' object has no attribute 'fetch_raw'`

- [ ] **Step 3: Extract `_clone_into` and add `fetch_raw`**

In `src/agentos/plugins/fetcher.py`, add `from contextlib import contextmanager` to the imports (alongside the existing `dataclass` import).

Replace the body of `fetch()` from the `with tempfile.TemporaryDirectory(...)` line through the `.git` removal (currently lines 32–58, ending right before `manifest_path = staging / ".claude-plugin" / "plugin.json"`) — i.e. replace:

```python
    def fetch(self, source: PluginSource) -> FetchedPlugin:
        with tempfile.TemporaryDirectory(prefix="orin-plugin-") as temporary:
            staging = Path(temporary) / "package"
            if source.kind == "path":
                self._validate(Path(source.path or ""))
                shutil.copytree(Path(source.path or ""), staging, symlinks=False)
            elif source.kind == "git":
                command = ["git", "clone", "--depth", "1", "--no-tags", "--recurse-submodules=no", "--config", "core.symlinks=false"]
                if source.ref:
                    command += ["--branch", source.ref]
                command += [source.url or "", str(staging)]
                try:
                    subprocess.run(command, check=True, shell=False, timeout=self.timeout, capture_output=True, text=True)
                except (OSError, subprocess.SubprocessError) as error:
                    raise FetchRejected("plugin repository could not be fetched") from error
                if source.subdirectory:
                    selected = staging / source.subdirectory
                    if not selected.is_dir() or not selected.resolve().is_relative_to(staging.resolve()):
                        raise FetchRejected("plugin subdirectory escapes the repository")
                    package = Path(temporary) / "selected"
                    shutil.copytree(selected, package, symlinks=False)
                    shutil.rmtree(staging, ignore_errors=True)
                    staging = package
            else:
                raise FetchRejected("source must be resolved to a git or path source before fetching")
            git_dir = staging / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir, ignore_errors=True)
            manifest_path = staging / ".claude-plugin" / "plugin.json"
```

with:

```python
    def fetch(self, source: PluginSource) -> FetchedPlugin:
        with tempfile.TemporaryDirectory(prefix="orin-plugin-") as temporary:
            staging = self._clone_into(source, temporary)
            manifest_path = staging / ".claude-plugin" / "plugin.json"
```

(the rest of `fetch()` — manifest parsing through `return FetchedPlugin(destination, digest, manifest)` — is unchanged).

Immediately after `fetch()` (before `def _validate`), add:

```python
    @contextmanager
    def fetch_raw(self, source: PluginSource):
        """Clone-only variant of ``fetch`` for repositories with no plugin manifest.

        Applies the same clone and validation guards as ``fetch`` but never reads
        a manifest and never persists into ``self.root`` — the yielded path is
        only valid for the lifetime of this context, long enough to read a
        handful of config files before the clone is discarded.
        """
        with tempfile.TemporaryDirectory(prefix="orin-plugin-raw-") as temporary:
            staging = self._clone_into(source, temporary)
            self._validate(staging)
            yield staging

    def _clone_into(self, source: PluginSource, temporary: str) -> Path:
        staging = Path(temporary) / "package"
        if source.kind == "path":
            self._validate(Path(source.path or ""))
            shutil.copytree(Path(source.path or ""), staging, symlinks=False)
        elif source.kind == "git":
            command = ["git", "clone", "--depth", "1", "--no-tags", "--recurse-submodules=no", "--config", "core.symlinks=false"]
            if source.ref:
                command += ["--branch", source.ref]
            command += [source.url or "", str(staging)]
            try:
                subprocess.run(command, check=True, shell=False, timeout=self.timeout, capture_output=True, text=True)
            except (OSError, subprocess.SubprocessError) as error:
                raise FetchRejected("plugin repository could not be fetched") from error
            if source.subdirectory:
                selected = staging / source.subdirectory
                if not selected.is_dir() or not selected.resolve().is_relative_to(staging.resolve()):
                    raise FetchRejected("plugin subdirectory escapes the repository")
                package = Path(temporary) / "selected"
                shutil.copytree(selected, package, symlinks=False)
                shutil.rmtree(staging, ignore_errors=True)
                staging = package
        else:
            raise FetchRejected("source must be resolved to a git or path source before fetching")
        git_dir = staging / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir, ignore_errors=True)
        return staging
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_fetcher.py -v`

Expected: PASS (all tests, including the pre-existing digest test)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/fetcher.py tests/unit/plugins/test_fetcher.py
git commit -m "feat(plugins): add PluginFetcher.fetch_raw for manifest-less repositories"
```

---

### Task 5: `mcp_inference.py` — best-effort launch command inference

**Files:**
- Create: `src/agentos/plugins/mcp_inference.py`
- Test: `tests/unit/plugins/test_mcp_inference.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/plugins/test_mcp_inference.py`:

```python
import json

from agentos.plugins.mcp_inference import infer_mcp_launch


def test_infers_stdio_from_mcp_json(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"demo": {"command": "python", "args": ["-m", "demo"], "env": {"API_KEY": ""}}}}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "python"
    assert guess.args == ("-m", "demo")
    assert guess.secret_names == ("API_KEY",)
    assert guess.confidence == "structured"


def test_infers_http_from_smithery_json(tmp_path):
    (tmp_path / "smithery.json").write_text(json.dumps({"url": "https://mcp.example.com/v1"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "http"
    assert guess.url == "https://mcp.example.com/v1"
    assert guess.command is None
    assert guess.confidence == "structured"


def test_infers_npx_from_package_json_bin_field(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "@acme/demo-mcp", "bin": "./cli.js"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "npx"
    assert guess.args == ("-y", "@acme/demo-mcp")
    assert guess.confidence == "structured"


def test_package_json_without_a_bin_field_is_not_a_signal(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "not-a-cli"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.confidence == "none"


def test_infers_uvx_from_pyproject_scripts(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-mcp"\n\n[project.scripts]\ndemo-mcp = "demo_mcp:main"\n', encoding="utf-8",
    )
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "uvx"
    assert guess.args == ("demo-mcp",)
    assert guess.confidence == "structured"


def test_mcp_json_takes_priority_over_package_json(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({"command": "node", "args": ["server.js"]}), encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"name": "demo-mcp", "bin": "./cli.js"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.command == "node"


def test_returns_a_blank_guess_when_nothing_matches(tmp_path):
    (tmp_path / "README.md").write_text("just docs", encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.display_name == "demo-repo"
    assert guess.transport is None
    assert guess.command is None
    assert guess.url is None
    assert guess.args == ()
    assert guess.secret_names == ()
    assert guess.confidence == "none"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_mcp_inference.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.plugins.mcp_inference'`

- [ ] **Step 3: Implement `mcp_inference.py`**

Create `src/agentos/plugins/mcp_inference.py`:

```python
"""Best-effort inference of an MCP server's launch command from its own
repository config — only structured files are read (``mcp.json``/
``smithery.json``, ``package.json``, ``pyproject.toml``); free-text sources
like README prose are deliberately not parsed, since a wrong guess there
would be presented to the user as if it were reliable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class McpLaunchGuess:
    display_name: str
    transport: str | None  # "stdio" | "http" | None
    command: str | None
    args: tuple[str, ...]
    url: str | None
    secret_names: tuple[str, ...]
    confidence: str  # "structured" | "none"


def infer_mcp_launch(path: Path, *, suggested_name: str) -> McpLaunchGuess:
    guess = _from_mcp_config(path, suggested_name) or _from_package_json(path, suggested_name) or _from_pyproject(path, suggested_name)
    if guess is not None:
        return guess
    return McpLaunchGuess(suggested_name, None, None, (), None, (), "none")


def _from_mcp_config(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    for filename in ("mcp.json", "smithery.json"):
        candidate = path / filename
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entry = _first_server_entry(data)
        if entry is None:
            continue
        command = entry.get("command")
        url = entry.get("url")
        env = entry.get("env")
        secret_names = tuple(str(key) for key in env.keys()) if isinstance(env, dict) else ()
        if isinstance(command, str) and command.strip():
            args = tuple(str(item) for item in entry.get("args") or ())
            return McpLaunchGuess(suggested_name, "stdio", command.strip(), args, None, secret_names, "structured")
        if isinstance(url, str) and url.strip():
            return McpLaunchGuess(suggested_name, "http", None, (), url.strip(), secret_names, "structured")
    return None


def _first_server_entry(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and servers:
        first = next(iter(servers.values()))
        return first if isinstance(first, dict) else None
    if "command" in data or "url" in data:
        return data
    return None


def _from_package_json(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    candidate = path / "package.json"
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("bin"):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    return McpLaunchGuess(suggested_name, "stdio", "npx", ("-y", name), None, (), "structured")


def _from_pyproject(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    candidate = path / "pyproject.toml"
    if not candidate.is_file():
        return None
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    project = data.get("project") if isinstance(data, dict) else None
    poetry = ((data.get("tool") or {}).get("poetry") if isinstance(data, dict) else None) or {}
    scripts = (project or {}).get("scripts") if isinstance(project, dict) else None
    if not scripts and not poetry.get("scripts"):
        return None
    name = str((project or {}).get("name") or poetry.get("name") or "").strip()
    if not name:
        return None
    return McpLaunchGuess(suggested_name, "stdio", "uvx", (name,), None, (), "structured")


__all__ = ["McpLaunchGuess", "infer_mcp_launch"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_mcp_inference.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/mcp_inference.py tests/unit/plugins/test_mcp_inference.py
git commit -m "feat(plugins): infer an MCP launch command from structured repo config"
```

---

### Task 6: `PluginService.infer_mcp_launch`

**Files:**
- Modify: `src/agentos/plugins/service.py`
- Test: `tests/unit/plugins/test_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/plugins/test_service.py` (the file already imports `json` at the top, reuse it):

```python
def test_infer_mcp_launch_reads_package_json_from_the_clone(tmp_path):
    service = _service(tmp_path)
    source_dir = tmp_path / "raw-repo"
    source_dir.mkdir()
    (source_dir / "package.json").write_text(json.dumps({"name": "demo-mcp", "bin": "./cli.js"}), encoding="utf-8")
    guess = service.infer_mcp_launch(source_url=str(source_dir))
    assert guess["command"] == "npx"
    assert guess["args"] == ["-y", "demo-mcp"]
    assert guess["confidence"] == "structured"


def test_infer_mcp_launch_rejects_a_non_git_source(tmp_path):
    service = _service(tmp_path)
    try:
        service.infer_mcp_launch(source_url="not-a-git-source")
    except PluginServiceError as error:
        assert error.code == "plugin_invalid_source"
    else:
        raise AssertionError("expected a marketplace-name source to be rejected")


def test_infer_mcp_launch_reports_a_fetch_failure(tmp_path):
    service = _service(tmp_path)
    try:
        service.infer_mcp_launch(source_url="https://github.com/this-owner-does-not-exist-orin-test/this-repo-does-not-exist")
    except PluginServiceError as error:
        assert error.code == "plugin_fetch_failed"
    else:
        raise AssertionError("expected fetching a nonexistent repository to fail")
```

Note: `test_infer_mcp_launch_reports_a_fetch_failure` performs a real network call against a repository chosen to not exist; this mirrors how `test_fetcher.py`'s existing tests use local paths for the happy path and is acceptable here since it only needs `git clone` to fail, not succeed. If the test suite runs without network access, mark it to skip explicitly — see Step 3b below.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/plugins/test_service.py -k infer_mcp_launch -v`

Expected: FAIL — `AttributeError: 'PluginService' object has no attribute 'infer_mcp_launch'`

- [ ] **Step 3: Implement `infer_mcp_launch` on `PluginService`**

In `src/agentos/plugins/service.py`, add `from dataclasses import asdict` to the imports (near the top, alongside `from datetime import UTC, datetime`).

Change the import line to also bring in the inference function:

```python
from .mcp_inference import infer_mcp_launch as _infer_mcp_launch
```

(add this alongside the other relative imports at the top of the file, e.g. next to `from .fetcher import FetchRejected, PluginFetcher`).

Add a new method to `PluginService`, placed after `discover_library`:

```python
    def infer_mcp_launch(self, *, source_url: str) -> dict[str, Any]:
        source = resolve_source(source_url)
        if source.kind != "git":
            raise PluginServiceError("only a public GitHub repository can be added as an MCP server", code="plugin_invalid_source")
        try:
            with self.fetcher.fetch_raw(source) as path:
                guess = _infer_mcp_launch(path, suggested_name=source.suggested_name)
        except FetchRejected as error:
            raise PluginServiceError(str(error), code="plugin_fetch_failed") from error
        return asdict(guess)
```

- [ ] **Step 3b: Guard the network-dependent test**

At the top of `test_infer_mcp_launch_reports_a_fetch_failure`, add a skip guard so CI environments without outbound network access don't fail spuriously:

```python
def test_infer_mcp_launch_reports_a_fetch_failure(tmp_path):
    import socket
    try:
        socket.create_connection(("github.com", 443), timeout=3).close()
    except OSError:
        import pytest
        pytest.skip("no network access to GitHub in this environment")
    service = _service(tmp_path)
    try:
        service.infer_mcp_launch(source_url="https://github.com/this-owner-does-not-exist-orin-test/this-repo-does-not-exist")
    except PluginServiceError as error:
        assert error.code == "plugin_fetch_failed"
    else:
        raise AssertionError("expected fetching a nonexistent repository to fail")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/plugins/test_service.py -k infer_mcp_launch -v`

Expected: PASS

- [ ] **Step 5: Run the full plugins suite to check for regressions**

Run: `python -m pytest tests/unit/plugins/ -v`

Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/agentos/plugins/service.py tests/unit/plugins/test_service.py
git commit -m "feat(plugins): add PluginService.infer_mcp_launch"
```

---

### Task 7: `POST /v1/plugins/library/infer-mcp`

**Files:**
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/api/test_plugin_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/api/test_plugin_routes.py`, extending the fake `Plugins` class with an `infer_mcp_launch` method and adding a test:

```python
class Plugins:
    def list(self, user_id): return [{"plugin_id":"demo"}]
    def inspect(self, **kwargs): return {"plugin_id":"demo","state":"pending_approval"}
    def approve(self, **kwargs): return {"plugin_id":"demo","state":"active"}
    def set_enabled(self, **kwargs): return {"plugin_id":"demo","state":"disabled"}
    def remove(self, **kwargs): return {"removed":True}
    def list_marketplaces(self, user_id): return []
    def add_marketplace(self, **kwargs): return {"name":"community"}
    def discover_library(self, *, refresh=False, query=None): return {"entries": [], "web_search_available": refresh, "query_seen": query}
    def infer_mcp_launch(self, *, source_url): return {"display_name": "demo-mcp", "transport": "stdio", "command": "npx", "args": ["-y", "demo-mcp"], "url": None, "secret_names": [], "confidence": "structured", "source_url_seen": source_url}


def test_infer_mcp_route_forwards_the_source_url():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    response = client.post("/v1/plugins/library/infer-mcp", json={"source_url": "https://github.com/acme/demo-mcp.git"})
    assert response.status_code == 200
    body = response.json()
    assert body["command"] == "npx"
    assert body["source_url_seen"] == "https://github.com/acme/demo-mcp.git"
```

(This replaces the existing `class Plugins:` definition in the file — add the `infer_mcp_launch` method to it in place, rather than defining a second class, so the other tests in the file keep using the same fake.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/api/test_plugin_routes.py::test_infer_mcp_route_forwards_the_source_url -v`

Expected: FAIL — 404 (route does not exist yet)

- [ ] **Step 3: Add the request model and route**

In `src/agentos/api/gateway.py`, add a new request model near `PluginReferenceRequest` (around line 215):

```python
class PluginInferMcpRequest(_RequestModel):
    source_url: str = Field(min_length=1, max_length=2048)
```

Add the route immediately after `inspect_plugin` (after the block ending at line 966, before `@app.post("/v1/plugins/{plugin_id}/approve")`):

```python
    @app.post("/v1/plugins/library/infer-mcp")
    async def infer_mcp_from_repository(payload: PluginInferMcpRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="plugins.library.infer_mcp", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="plugins.library.infer_mcp", resource_id=None, purpose="plugins.inspect")
        # Cloning a repository to read its config files is blocking I/O, same as plugin inspection.
        result = await run_in_threadpool(_require_port(services.plugins).infer_mcp_launch, source_url=payload.source_url)
        return JSONResponse(_jsonable(result))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/api/test_plugin_routes.py -v`

Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/gateway.py tests/unit/api/test_plugin_routes.py
git commit -m "feat(api): add POST /v1/plugins/library/infer-mcp"
```

---

### Task 8: Wire `GithubManifestProbe` into production

**Files:**
- Modify: `src/agentos/bootstrap/production.py`

- [ ] **Step 1: Add the import**

In `src/agentos/bootstrap/production.py`, find the existing import of `GithubRepositorySearchClient` and add a matching import directly below it:

```python
from agentos.plugins.manifest_probe import GithubManifestProbe
```

- [ ] **Step 2: Pass it into the production `PluginService`**

Replace line 277:

```python
        plugins=PluginService(engine, plugin_root=orin_paths().data / "plugins", skill_library=skill_library, mcp_service=mcp_service, search_client=GithubRepositorySearchClient()),
```

with:

```python
        plugins=PluginService(engine, plugin_root=orin_paths().data / "plugins", skill_library=skill_library, mcp_service=mcp_service, search_client=GithubRepositorySearchClient(), manifest_probe=GithubManifestProbe()),
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "import agentos.bootstrap.production"`

Expected: no output, exit code 0 (a syntax/import error would print a traceback)

- [ ] **Step 4: Run the full backend plugins and api test suites to check for regressions**

Run: `python -m pytest tests/unit/plugins/ tests/unit/api/test_plugin_routes.py -v`

Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/bootstrap/production.py
git commit -m "feat(plugins): wire GithubManifestProbe into the production plugin service"
```

---

### Task 9: Frontend data layer — `installable_kind` and `inferMcpLaunch`

**Files:**
- Modify: `frontend/src/api/plugins.ts`
- Test: `frontend/tests/unit/PluginLibrarySection.test.tsx` (fixture updates only, wired in Task 12)

- [ ] **Step 1: Write a focused failing test for parsing**

There is no dedicated `plugins.ts` unit test file today (parsing is covered indirectly through component tests). Add one: create `frontend/tests/unit/api-plugins.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { fetchPluginLibrary, inferMcpLaunch } from '../../src/api/plugins'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('plugins api parsing', () => {
  it('parses installable_kind on library entries', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
      entries: [{ name: 'demo', description: 'd', source_url: 'https://github.com/acme/demo.git', origin: 'web', installable_kind: 'mcp_raw' }],
      web_search_available: true,
    }))
    const result = await fetchPluginLibrary(new ApiClient({ fetchImpl, maxAttempts: 1 }))
    expect(result.entries[0].installable_kind).toBe('mcp_raw')
  })

  it('rejects an invalid installable_kind', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
      entries: [{ name: 'demo', description: 'd', source_url: 'https://github.com/acme/demo.git', origin: 'web', installable_kind: 'bogus' }],
      web_search_available: true,
    }))
    await expect(fetchPluginLibrary(new ApiClient({ fetchImpl, maxAttempts: 1 }))).rejects.toThrow()
  })

  it('parses an inferred MCP launch guess', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toContain('/v1/plugins/library/infer-mcp')
      expect(JSON.parse(String(init?.body))).toEqual({ source_url: 'https://github.com/acme/demo.git' })
      return response({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
    })
    const guess = await inferMcpLaunch(new ApiClient({ fetchImpl, maxAttempts: 1 }), 'https://github.com/acme/demo.git')
    expect(guess).toEqual({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
  })

  it('parses a launch guess with no signal found', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ display_name: 'demo', transport: null, command: null, args: [], url: null, secret_names: [], confidence: 'none' }))
    const guess = await inferMcpLaunch(new ApiClient({ fetchImpl, maxAttempts: 1 }), 'https://github.com/acme/demo.git')
    expect(guess.transport).toBeNull()
    expect(guess.confidence).toBe('none')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/unit/api-plugins.test.ts`

Expected: FAIL — `installable_kind` undefined / `inferMcpLaunch is not a function`

- [ ] **Step 3: Update `plugins.ts`**

In `frontend/src/api/plugins.ts`, replace the `PluginLibraryEntry` type:

```typescript
export type PluginLibraryEntry = { name: string; description: string; source_url: string; origin: 'registry' | 'web'; installable_kind: 'plugin' | 'mcp_raw' | 'unknown' }
```

Add a new type below `PluginLibraryResult`:

```typescript
export type McpLaunchGuess = { display_name: string; transport: 'stdio' | 'http' | null; command: string | null; args: string[]; url: string | null; secret_names: string[]; confidence: 'structured' | 'none' }
```

Add a new exported function alongside `fetchPluginLibrary`:

```typescript
export function inferMcpLaunch(client: ApiClient, sourceUrl: string, intent = client.createMutationIntent()): Promise<McpLaunchGuess> { return client.request({ path: '/v1/plugins/library/infer-mcp', method: 'POST', body: { source_url: sourceUrl }, intent, parse: parseLaunchGuess }) }
```

Replace `libraryEntry`:

```typescript
function libraryEntry(value: unknown): PluginLibraryEntry { const data = record(value); const origin = text(data.origin); if (origin !== 'registry' && origin !== 'web') throw invalidResponseError(); return { name: text(data.name), description: text(data.description ?? ''), source_url: text(data.source_url), origin, installable_kind: installableKind(data.installable_kind) } }
function installableKind(value: unknown): PluginLibraryEntry['installable_kind'] { if (value === 'plugin' || value === 'mcp_raw' || value === 'unknown') return value; throw invalidResponseError() }
```

Add the launch-guess parser at the end of the file (alongside the other parse helpers):

```typescript
function parseLaunchGuess(value: unknown): McpLaunchGuess {
  const data = record(value)
  return {
    display_name: text(data.display_name),
    transport: nullableTransport(data.transport),
    command: data.command === null || data.command === undefined ? null : text(data.command),
    args: array(data.args ?? []),
    url: data.url === null || data.url === undefined ? null : text(data.url),
    secret_names: array(data.secret_names ?? []),
    confidence: confidenceValue(data.confidence),
  }
}
function nullableTransport(value: unknown): 'stdio' | 'http' | null { if (value === null || value === undefined) return null; if (value === 'stdio' || value === 'http') return value; throw invalidResponseError() }
function confidenceValue(value: unknown): 'structured' | 'none' { if (value === 'structured' || value === 'none') return value; throw invalidResponseError() }
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/unit/api-plugins.test.ts`

Expected: PASS

- [ ] **Step 5: Fix the existing library fixtures for the now-stricter parser**

The stricter `libraryEntry()` parser requires `installable_kind` on every entry; the two pre-existing fixtures in `frontend/tests/unit/PluginLibrarySection.test.tsx` that build entry objects inline don't have it yet. In that file, add `, installable_kind: 'plugin'` to:

- The entry object in `'lists library entries and opens the install dialog pre-filled and inspected'` (currently `{ name: 'Other MCP', description: 'd', source_url: sourceUrl, origin: 'web' }`).
- The entry object in `'lets the user search by a free-text query'` (currently `{ name: 'obsidian-second-brain', description: 'd', source_url: 'https://github.com/acme/obsidian-second-brain.git', origin: 'web' }`).

- [ ] **Step 6: Run the plugin-related frontend tests to confirm nothing regressed**

Run (from `frontend/`): `npx vitest run tests/unit/PluginLibrarySection.test.tsx tests/unit/PluginsSection.test.tsx tests/unit/api-plugins.test.ts`

Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/plugins.ts frontend/tests/unit/api-plugins.test.ts frontend/tests/unit/PluginLibrarySection.test.tsx
git commit -m "feat(plugins-ui): add installable_kind and inferMcpLaunch to the plugins API layer"
```

---

### Task 10: `PluginInstallDialog` — `onNoManifest` fallback

**Files:**
- Modify: `frontend/src/features/plugins/PluginInstallDialog.tsx`
- Create: `frontend/tests/unit/PluginInstallDialog.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/PluginInstallDialog.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginInstallDialog } from '../../src/features/plugins/PluginInstallDialog'
import { ApiClient } from '../../src/api/client'

function errorResponse(code: string, status = 409): Response {
  return new Response(JSON.stringify({ error: { code, category: 'CONFLICT', message_key: code, correlation_id: 'c1', retryable: false, retry_after: null } }), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('PluginInstallDialog', () => {
  it('calls onNoManifest and does not show the generic error when inspection fails with plugin_no_manifest', async () => {
    const onNoManifest = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_no_manifest'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/no-manifest.git" onClose={vi.fn()} onInstalled={vi.fn()} onNoManifest={onNoManifest} />)

    await waitFor(() => expect(onNoManifest).toHaveBeenCalledOnce())
    expect(screen.queryByText('Não foi possível inspecionar este plugin.')).not.toBeInTheDocument()
  })

  it('shows the generic error for any other failure code, even with onNoManifest provided', async () => {
    const onNoManifest = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_operation_rejected'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/broken.git" onClose={vi.fn()} onInstalled={vi.fn()} onNoManifest={onNoManifest} />)

    expect(await screen.findByText('Não foi possível inspecionar este plugin.')).toBeInTheDocument()
    expect(onNoManifest).not.toHaveBeenCalled()
  })

  it('shows the generic error as before when onNoManifest is not provided', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_no_manifest'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/no-manifest.git" onClose={vi.fn()} onInstalled={vi.fn()} />)

    expect(await screen.findByText('Não foi possível inspecionar este plugin.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/unit/PluginInstallDialog.test.tsx`

Expected: FAIL — first test times out / `onNoManifest` never called (the catch block currently discards the error entirely)

- [ ] **Step 3: Update `PluginInstallDialog.tsx`**

Replace the full file `frontend/src/features/plugins/PluginInstallDialog.tsx`:

```typescript
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { approvePlugin, inspectPlugin, type PluginInspectionResult } from '../../api/plugins'
import { ApiError } from '../../api/errors'
import type { ApiClient } from '../../api/client'

export function PluginInstallDialog({ client, onClose, onInstalled, initialReference, onNoManifest }: { client: ApiClient; onClose: () => void; onInstalled: () => void; initialReference?: string; onNoManifest?: () => void }) {
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
  async function runInspection(value: string) {
    setBusy(true); setError(null)
    try {
      setInspection(await inspectPlugin(client, value))
    } catch (caught) {
      if (onNoManifest && caught instanceof ApiError && caught.code === 'plugin_no_manifest') { onNoManifest(); return }
      setError('Não foi possível inspecionar este plugin.')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => { if (initialReference) void runInspection(initialReference) }, [initialReference])
  async function inspect(event: FormEvent) { event.preventDefault(); if (!reference.trim()) return; void runInspection(reference.trim()) }
  async function install() { if (!inspection) return; setBusy(true); try { await approvePlugin(client, inspection.plugin_id); onInstalled() } catch { setError('Não foi possível instalar o plugin.') } finally { setBusy(false) } }
  return <div className="plugin-dialog__backdrop"><div className="plugin-dialog" role="dialog" aria-modal="true" aria-labelledby="plugin-dialog-title" tabIndex={-1} ref={dialogRef}><header className="plugin-dialog__head"><div><span className="plugin-dialog__eyebrow">PLUGIN INSTALLER</span><h2 id="plugin-dialog-title">Instalar plugin</h2></div><button type="button" className="button--quiet" onClick={onClose}>Fechar</button></header><p className="plugin-dialog__lede">Inspecione a origem primeiro. A instalação só acontece depois da sua aprovação.</p><form className="plugin-dialog__form" onSubmit={(event) => void inspect(event)}><label htmlFor="plugin-reference">URL, owner/repo ou nome<input id="plugin-reference" value={reference} onChange={(event) => setReference(event.target.value)} placeholder="ex.: github.com/acme/meu-plugin" /></label><button type="submit" className="button button--primary" disabled={busy || !reference.trim()}>{busy ? 'Inspecionando…' : 'Inspecionar origem'}</button></form>{inspection && <div className="plugin-dialog__preview"><div className="plugin-dialog__preview-head"><span className="plugin-dialog__preview-icon" aria-hidden="true">✦</span><div><h3>{inspection.display_name}</h3><p>{inspection.author || 'Autor não informado'} · <code>v{inspection.version}</code></p></div></div><p className="plugin-dialog__description">{inspection.description || 'Sem descrição disponível.'}</p><dl className="plugin-dialog__facts"><div><dt>Contribuições</dt><dd>{inspection.contribution_count}</dd></div><div><dt>Avisos</dt><dd className={inspection.warnings.length > 0 ? 'has-warning' : undefined}>{inspection.warnings.length}</dd></div></dl>{inspection.warnings.length > 0 && <ul className="plugin-dialog__warnings">{inspection.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}<button type="button" className="button button--primary" onClick={() => void install()} disabled={busy}>{busy ? 'Instalando…' : 'Confirmar instalação'}</button></div>}{error && <p className="plugin-dialog__error" role="alert">{error}</p>}</div></div>
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/unit/PluginInstallDialog.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/plugins/PluginInstallDialog.tsx frontend/tests/unit/PluginInstallDialog.test.tsx
git commit -m "feat(plugins-ui): let PluginInstallDialog signal a no-manifest failure"
```

---

### Task 11: `McpFromRepoDialog`

**Files:**
- Create: `frontend/src/features/plugins/McpFromRepoDialog.tsx`
- Create: `frontend/tests/unit/McpFromRepoDialog.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/McpFromRepoDialog.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { McpFromRepoDialog } from '../../src/features/plugins/McpFromRepoDialog'
import { ApiClient } from '../../src/api/client'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type FetchInit = Parameters<typeof fetch>[1]
type Route = { method: string; pattern: RegExp; respond: (init?: FetchInit) => Response }

function routedFetch(routes: Route[]) {
  const calls: Array<[string, FetchInit]> = []
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push([url, init])
    const route = routes.find((item) => item.method === method && item.pattern.test(url))
    if (!route) throw new Error(`Unhandled request: ${method} ${url}`)
    return route.respond(init)
  })
  return { fetchImpl, calls }
}

describe('McpFromRepoDialog', () => {
  it('pre-fills the form from a structured inference guess', async () => {
    const { fetchImpl } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: ['API_KEY'], confidence: 'structured' }) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    expect(await screen.findByDisplayValue('demo-mcp')).toBeInTheDocument()
    expect(screen.getByDisplayValue('npx')).toBeInTheDocument()
    expect(screen.getByDisplayValue('-y demo-mcp')).toBeInTheDocument()
    expect(screen.getByDisplayValue('API_KEY')).toBeInTheDocument()
    expect(screen.queryByText(/preencha manualmente/)).not.toBeInTheDocument()
  })

  it('opens a blank form with a note when inference finds nothing', async () => {
    const { fetchImpl } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: null, command: null, args: [], url: null, secret_names: [], confidence: 'none' }) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    expect(await screen.findByText(/preencha manualmente/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('demo-mcp')).toBeInTheDocument()
    expect(screen.getByLabelText('Comando')).toHaveValue('')
  })

  it('submits the (possibly edited) guess and shows the approval card', async () => {
    const { fetchImpl, calls } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: [], confidence: 'structured' }) },
      { method: 'POST', pattern: /\/v1\/mcp\/servers$/, respond: () => json({ server_id: 's1', slug: 'demo-mcp', display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: [], catalog_id: null, state: 'pending_approval', state_reason: '', protocol_version: '', tool_count: 0, tools: [] }, 201) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    await screen.findByDisplayValue('demo-mcp')
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))

    await waitFor(() => {
      const proposeCall = calls.find(([url, init]) => url.endsWith('/v1/mcp/servers') && init?.method === 'POST')
      expect(proposeCall).toBeTruthy()
      expect(JSON.parse(String(proposeCall?.[1]?.body))).toMatchObject({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'] })
    })
    expect(await screen.findByRole('button', { name: 'Conectar' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/unit/McpFromRepoDialog.test.tsx`

Expected: FAIL — module does not exist

- [ ] **Step 3: Implement `McpFromRepoDialog.tsx`**

Create `frontend/src/features/plugins/McpFromRepoDialog.tsx`:

```typescript
import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { inferMcpLaunch, type McpLaunchGuess } from '../../api/plugins'
import { approveMcpServer, createMcpServer, deleteMcpServer, type McpServerDetail, type McpTransport } from '../../api/mcp'
import { McpApprovalCard } from '../conversations/McpApprovalCard'

export function McpFromRepoDialog({ client, sourceUrl, onClose, onAdded }: { client: ApiClient; sourceUrl: string; onClose: () => void; onAdded: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [guess, setGuess] = useState<McpLaunchGuess | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [transport, setTransport] = useState<McpTransport>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [secretNames, setSecretNames] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [created, setCreated] = useState<McpServerDetail | null>(null)

  useEffect(() => {
    dialogRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    let active = true
    setLoading(true)
    inferMcpLaunch(client, sourceUrl)
      .then((result) => {
        if (!active) return
        setGuess(result)
        setDisplayName(result.display_name)
        setTransport(result.transport ?? 'stdio')
        setCommand(result.command ?? '')
        setArgs(result.args.join(' '))
        setUrl(result.url ?? '')
        setSecretNames(result.secret_names.join(', '))
      })
      .catch(() => { if (active) setLoadError('Não foi possível analisar este repositório automaticamente.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [client, sourceUrl])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setSubmitError(null)
    try {
      const detail = await createMcpServer(client, {
        display_name: displayName,
        transport,
        command: transport === 'stdio' ? command.trim() || undefined : undefined,
        args: transport === 'stdio' ? args.split(/\s+/).filter(Boolean) : undefined,
        url: transport === 'http' ? url.trim() || undefined : undefined,
        secret_names: secretNames.split(',').map((item) => item.trim()).filter(Boolean),
      })
      setCreated(detail)
    } catch {
      setSubmitError('Não foi possível adicionar o servidor.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="plugin-dialog__backdrop">
      <div className="plugin-dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-from-repo-title" tabIndex={-1} ref={dialogRef}>
        <header className="plugin-dialog__head">
          <div><span className="plugin-dialog__eyebrow">SERVIDOR MCP</span><h2 id="mcp-from-repo-title">Adicionar como servidor MCP</h2></div>
          <button type="button" className="button--quiet" onClick={onClose}>Fechar</button>
        </header>
        <p className="plugin-dialog__lede">Este repositório não tem um manifesto de plugin — vamos tentar detectar como executá-lo como um servidor MCP comum.</p>

        {created ? (
          <McpApprovalCard
            server={{ server_id: created.server_id, display_name: created.display_name, transport: created.transport, secret_names: created.secret_names, catalog_id: created.catalog_id }}
            active
            onApprove={async (secrets) => { await approveMcpServer(client, created.server_id, secrets); onAdded() }}
            onDecline={async () => { await deleteMcpServer(client, created.server_id); onAdded() }}
          />
        ) : loading ? (
          <p>Analisando repositório…</p>
        ) : (
          <form className="mcp-server-form__manual" onSubmit={(event) => void submit(event)}>
            {loadError && <p role="alert">{loadError}</p>}
            {!loadError && guess?.confidence === 'none' && <p className="plugin-library__note">Não foi possível detectar automaticamente o comando de execução — preencha manualmente.</p>}
            <label>Nome<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
            <label>
              Transporte
              <select value={transport} onChange={(event) => setTransport(event.target.value as McpTransport)}>
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </label>
            {transport === 'stdio' ? (
              <>
                <label>Comando<input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="npx" /></label>
                <label>Argumentos<input value={args} onChange={(event) => setArgs(event.target.value)} placeholder="separados por espaço" /></label>
              </>
            ) : (
              <label>URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://" /></label>
            )}
            <label>Credenciais necessárias<input value={secretNames} onChange={(event) => setSecretNames(event.target.value)} placeholder="separadas por vírgula" /></label>
            <button type="submit" disabled={submitting}>{submitting ? 'Adicionando…' : 'Adicionar'}</button>
            {submitError && <p role="alert">{submitError}</p>}
          </form>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/unit/McpFromRepoDialog.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/plugins/McpFromRepoDialog.tsx frontend/tests/unit/McpFromRepoDialog.test.tsx
git commit -m "feat(plugins-ui): add McpFromRepoDialog for manifest-less repositories"
```

---

### Task 12: Wire it into `PluginLibrarySection`

**Files:**
- Modify: `frontend/src/features/plugins/PluginLibrarySection.tsx`
- Modify: `frontend/tests/unit/PluginLibrarySection.test.tsx`

- [ ] **Step 1: Write the new failing tests**

(The pre-existing fixtures already carry `installable_kind: 'plugin'` from Task 9, so this task only adds new tests.)

Add to `frontend/tests/unit/PluginLibrarySection.test.tsx`:

```typescript
it('offers "Adicionar como servidor MCP" for an mcp_raw entry instead of Instalar', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
    entries: [{ name: 'Raw MCP', description: 'd', source_url: 'https://github.com/acme/raw-mcp.git', origin: 'web', installable_kind: 'mcp_raw' }],
    web_search_available: true,
  }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Raw MCP')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Adicionar como servidor MCP' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Instalar' })).not.toBeInTheDocument()
})

it('falls back to the MCP dialog when an unknown-kind entry turns out to have no manifest', async () => {
  const sourceUrl = 'https://github.com/acme/unknown-kind.git'
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes('/v1/plugins/inspect')) {
      return new Response(JSON.stringify({ error: { code: 'plugin_no_manifest', category: 'CONFLICT', message_key: 'plugin_no_manifest', correlation_id: 'c1', retryable: false, retry_after: null } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
    }
    if (String(input).includes('/v1/plugins/library/infer-mcp')) {
      return response({ display_name: 'unknown-kind', transport: 'stdio', command: 'npx', args: ['-y', 'unknown-kind'], url: null, secret_names: [], confidence: 'structured' })
    }
    return response({ entries: [{ name: 'unknown-kind', description: 'd', source_url: sourceUrl, origin: 'web', installable_kind: 'unknown' }], web_search_available: true })
  })
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Instalar' }))
  expect(await screen.findByRole('heading', { name: 'Adicionar como servidor MCP' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run tests/unit/PluginLibrarySection.test.tsx`

Expected: FAIL — the two new tests fail (no "Adicionar como servidor MCP" button exists yet); the pre-existing tests still pass since their fixtures were already fixed in Task 9.

- [ ] **Step 3: Update `PluginLibrarySection.tsx`**

Replace the full file `frontend/src/features/plugins/PluginLibrarySection.tsx`:

```typescript
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { fetchPluginLibrary, type PluginLibraryEntry } from '../../api/plugins'
import type { ApiClient } from '../../api/client'
import { PluginInstallDialog } from './PluginInstallDialog'
import { McpFromRepoDialog } from './McpFromRepoDialog'

const ORIGIN_LABEL: Record<PluginLibraryEntry['origin'], string> = { registry: 'Registro', web: 'Web' }

function githubUrl(sourceUrl: string): string { return sourceUrl.replace(/\.git$/, '') }

export function PluginLibrarySection({ client, onInstalled }: { client: ApiClient; onInstalled: () => void }) {
  const [entries, setEntries] = useState<PluginLibraryEntry[]>([])
  const [webAvailable, setWebAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [addingMcpFor, setAddingMcpFor] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const load = useCallback((refresh: boolean, q: string) => {
    (refresh ? setRefreshing : setLoading)(true)
    return fetchPluginLibrary(client, refresh, q)
      .then((result) => { setEntries(result.entries); setWebAvailable(result.web_search_available); setError(null) })
      .catch(() => setError('Não foi possível carregar a biblioteca de plugins.'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [client])
  useEffect(() => { void load(false, '') }, [load])
  function search(event: FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    setActiveQuery(trimmed)
    void load(true, trimmed)
  }
  function clearSearch() {
    setQuery('')
    setActiveQuery('')
    void load(false, '')
  }
  return <div className="plugin-library">
    <div className="plugin-library__head">
      <p className="plugin-library__lede">Plugins compatíveis com MCP encontrados em marketplaces conhecidos e na web.</p>
      <button type="button" className="button button--secondary" onClick={() => void load(true, activeQuery)} disabled={refreshing}>{refreshing ? 'Atualizando…' : 'Atualizar'}</button>
    </div>
    <form className="plugin-library__search" onSubmit={search}>
      <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar plugins por nome ou palavra-chave…" aria-label="Buscar na biblioteca de plugins" />
      <button type="submit" className="button button--secondary">Buscar</button>
      {activeQuery && <button type="button" className="button--quiet" onClick={clearSearch}>Limpar</button>}
    </form>
    {activeQuery && <p className="plugin-library__active-query">Resultados para "{activeQuery}"</p>}
    {!webAvailable && <p className="plugin-library__note">Busca na web indisponível no momento — mostrando apenas o registro conhecido.</p>}
    {loading && entries.length === 0 && <div className="plugins-section__loading" aria-label="Carregando biblioteca"><span /><span /><span /></div>}
    {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar a biblioteca</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => void load(false, activeQuery)}>Tentar novamente</button></div>}
    <div className="plugin-library__list" aria-label="Plugins disponíveis para instalação">
      {entries.map((entry) => <article className="plugin-library-card" key={entry.source_url}>
        <div className="plugin-library-card__head">
          <span className="plugin-library-card__icon" aria-hidden="true">✦</span>
          <div className="plugin-library-card__identity"><strong>{entry.name}</strong><span className={`plugin-library-card__badge is-${entry.origin}`}>{ORIGIN_LABEL[entry.origin]}</span></div>
        </div>
        <p className="plugin-library-card__description">{entry.description || 'Sem descrição disponível.'}</p>
        <div className="plugin-library-card__actions">
          <a className="button button--quiet" href={githubUrl(entry.source_url)} target="_blank" rel="noreferrer">Ver no GitHub <span aria-hidden="true">↗</span></a>
          {entry.installable_kind === 'mcp_raw'
            ? <button type="button" className="button button--primary" onClick={() => setAddingMcpFor(entry.source_url)}>Adicionar como servidor MCP</button>
            : <button type="button" className="button button--primary" onClick={() => setInstalling(entry.source_url)}>Instalar</button>}
        </div>
      </article>)}
      {!loading && !error && entries.length === 0 && <p className="plugin-library__empty">Nenhum plugin encontrado no momento.</p>}
    </div>
    {installing && <PluginInstallDialog key={installing} client={client} initialReference={installing} onClose={() => setInstalling(null)} onInstalled={() => { setInstalling(null); onInstalled() }} onNoManifest={() => { setAddingMcpFor(installing); setInstalling(null) }} />}
    {addingMcpFor && <McpFromRepoDialog key={addingMcpFor} client={client} sourceUrl={addingMcpFor} onClose={() => setAddingMcpFor(null)} onAdded={() => { setAddingMcpFor(null); onInstalled() }} />}
  </div>
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run tests/unit/PluginLibrarySection.test.tsx`

Expected: PASS (all tests, including the pre-existing ones)

- [ ] **Step 5: Run the full frontend test suite to check for regressions**

Run (from `frontend/`): `npx vitest run`

Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/plugins/PluginLibrarySection.tsx frontend/tests/unit/PluginLibrarySection.test.tsx
git commit -m "feat(plugins-ui): offer the MCP fallback from the Biblioteca card"
```

---

## Final Verification

- [ ] **Run the full backend suite**

Run: `python -m pytest tests/unit -v`

Expected: PASS (all tests)

- [ ] **Run the full frontend suite**

Run (from `frontend/`): `npx vitest run`

Expected: PASS (all tests)

- [ ] **Manual smoke check (optional but recommended before merging)**

Start the app, open Settings → Plugins → Biblioteca, and confirm: a `mcp_raw`-kind result shows "Adicionar como servidor MCP"; opening it shows a loading state then a pre-filled (or blank-with-note) form; submitting shows the inline approval card; declining removes the pending server; a `plugin`-kind result still installs exactly as before.

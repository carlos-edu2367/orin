# Ollama Cloud Catalog Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the model picker never serves a stale Local Ollama catalog after switching to Cloud, while preserving the official direct Cloud model IDs returned by `https://ollama.com/api/tags`.

**Architecture:** Keep one `ollama` provider and derive Local/Cloud from its normalized base URL. A provider reconfiguration invalidates the persisted catalog timestamp; the catalog repository exposes rows only when the provider is enabled and the catalog has been refreshed after that configuration. Cloud discovery remains sourced exclusively from the remote `/api/tags` and `/api/show` endpoints; model names are not filtered by a `:cloud` suffix because the direct Cloud API returns valid IDs such as `gemma4:31b`.

**Tech Stack:** Python, FastAPI service boundaries, SQLAlchemy, pytest, React/TypeScript, Vitest.

## Global Constraints

- Do not store or print API credentials.
- Do not change Local Ollama behavior or replace the official Cloud API model IDs with synthetic names.
- Preserve tenant scoping by `(user_id, provider)` and existing encrypted credential handling.
- Production code changes require a failing regression test first.

---

### Task 1: Invalidate a provider catalog when its configuration changes

**Files:**
- Modify: `src/agentos/persistence/postgres/provider_configuration.py:96-104`
- Modify: `src/agentos/persistence/postgres/provider_models.py:68-85`
- Test: `tests/unit/persistence/test_provider_configuration.py`
- Create: `tests/unit/persistence/test_provider_models.py`

- [x] **Step 1: Write the failing tests**

Add a test proving reconfiguring an existing provider sets `catalog_refreshed_at` to `None`, and a repository test proving rows are hidden while that timestamp is null or the provider is disabled.

- [x] **Step 2: Run the focused tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/persistence/test_provider_configuration.py tests/unit/persistence/test_provider_models.py -q`

Expected: the reconfiguration assertion observes the previous timestamp and the repository returns the stale model row.

- [x] **Step 3: Implement the minimal invalidation**

Set `catalog_refreshed_at=None` in the existing-row update in `PostgresProviderConfigurationAdapter.configure`. In `PostgresProviderCatalogRepository.list`, join the provider configuration scope and require `enabled == True` and `catalog_refreshed_at IS NOT NULL` before returning catalog records.

- [x] **Step 4: Run the focused tests and verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/persistence/test_provider_configuration.py tests/unit/persistence/test_provider_models.py -q`

Expected: all focused tests pass and stale Local rows are invisible after a Local-to-Cloud reconfiguration until the Cloud refresh completes.

---

### Task 2: Lock Cloud discovery to the remote Ollama API contract

**Files:**
- Modify: `tests/unit/provider_catalog/test_ollama.py`
- Modify: `tests/unit/persistence/test_provider_configuration.py`
- Modify: `src/agentos/persistence/postgres/provider_configuration.py` only if the regression reveals setup still probes an arbitrary catalog model

- [x] **Step 1: Write the failing regression test**

Cover that a Cloud catalog is fetched from `https://ollama.com/api/tags` and retains an official direct-API ID such as `gemma4:31b`; do not assert a `:cloud` suffix. Cover that configuration refresh uses the Cloud base URL after saving.

- [x] **Step 2: Run the focused tests and verify the regression behavior**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog/test_ollama.py tests/unit/persistence/test_provider_configuration.py -q`

Expected: the tests document the current official Cloud naming contract and fail only if implementation changes accidentally route discovery to Local or rewrite model IDs.

- [x] **Step 3: Keep the minimal production behavior**

Do not add a suffix-based Cloud filter. The base URL is the source of truth: Local uses the configured local URL, Cloud uses `https://ollama.com`.

- [x] **Step 4: Run the focused tests again**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog/test_ollama.py tests/unit/persistence/test_provider_configuration.py -q`

Expected: all tests pass.

---

### Task 3: Verify end-to-end contracts

**Files:**
- Modify: `docs/agent_memory/2026-08-12-ollama-cloud-probe-root-cause.md`

- [x] **Step 1: Run backend regression suites**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog tests/unit/persistence tests/unit/workers/test_chat.py tests/unit/api/test_api_asgi.py -q`

- [x] **Step 2: Run frontend tests and build**

Run: `npm test -- --run` in `frontend`, then `npm run build` in `frontend`.

- [x] **Step 3: Run repository hygiene checks**

Run: `git diff --check` and inspect `git status --short`; confirm no credential appears in the diff.

- [x] **Step 4: Record the final technical result**

Update the existing memory with the catalog invalidation behavior and the fact that direct Cloud IDs are sourced from the remote endpoint, without recording any key or response secret.

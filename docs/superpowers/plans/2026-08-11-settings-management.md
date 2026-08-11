# Settings Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a central, minimal Settings architecture backed by scoped management APIs and safe OmniRoute lifecycle control.

**Architecture:** A Settings shell owns global navigation and composes the existing Provider and Skill surfaces. New memory and runtime endpoints expose only persisted, scoped state. OmniRoute lifecycle is isolated in a process manager so HTTP/UI code never owns OS processes directly.

**Tech Stack:** React, React Router, Motion, FastAPI, SQLAlchemy/PostgreSQL, Pydantic, Vitest, pytest.

## Global Constraints

- Chat state and streaming remain independent of Settings navigation.
- Global and project settings use different routes and never mix project data.
- No provider secret is returned by an API.
- OmniRoute never stops an externally owned process and startup is bounded.
- New behavior is covered with a failing test before production code.

---

### Task 1: Runtime lifecycle persistence and process manager

**Files:**
- Create: `src/agentos/omniroute/process_manager.py`
- Modify: `src/agentos/api/gateway.py`, `src/agentos/bootstrap/production.py`, `src/agentos/persistence/postgres/schema.py`
- Test: `tests/unit/omniroute/test_process_manager.py`, `tests/unit/api/test_omniroute_runtime_api.py`

**Interfaces:**
- Produces `OmniRouteProcessManager.status()`, `start()`, `stop()`, `restart()`, and persisted `auto_start` runtime configuration.

- [ ] **Step 1: Write failing tests** for external detection, owned startup, failed health checks, persisted auto-start, and forbidden stop of external instances.
- [ ] **Step 2: Run pytest** and confirm these tests fail because the lifecycle API and manager do not exist.
- [ ] **Step 3: Implement the bounded manager, repository mapping, startup scheduling, and authenticated runtime endpoints.**
- [ ] **Step 4: Run the targeted tests** and confirm the lifecycle semantics pass.

### Task 2: Scoped memory management API

**Files:**
- Modify: `src/agentos/api/gateway.py`, `src/agentos/projects/store.py`, `src/agentos/persistence/postgres/agent_memory.py`
- Test: `tests/unit/api/test_memory_management_api.py`, `tests/unit/projects/test_project_memory_management.py`

**Interfaces:**
- Produces paginated `GET/PATCH/DELETE /v1/memories` and project-scoped equivalents with scope, query, cursor, and owner filters.

- [ ] **Step 1: Write failing tests** that assert global search pagination, project isolation, edit, delete, and details metadata.
- [ ] **Step 2: Run pytest** and confirm absent endpoints fail.
- [ ] **Step 3: Implement scoped query and mutation paths using existing stores and persistence contracts.**
- [ ] **Step 4: Run targeted tests** and confirm each operation uses only its requested scope.

### Task 3: Settings navigation and management surfaces

**Files:**
- Create: `frontend/src/features/settings/SettingsPage.tsx`, `frontend/src/features/memory/MemoryPage.tsx`, `frontend/src/api/memory.ts`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/components/CommandPalette.tsx`, `frontend/src/features/conversations/ChatPage.tsx`, `frontend/src/features/projects/ProjectPage.tsx`, `frontend/src/styles/agentos.css`
- Test: `frontend/tests/unit/SettingsPage.test.tsx`, `frontend/tests/unit/MemoryPage.test.tsx`, `frontend/tests/unit/ChatPage.test.tsx`

**Interfaces:**
- Consumes global Settings routes and memory APIs from Tasks 1–2.
- Produces `/settings/*`, `/projects/:projectId/memory`, Settings quick access, and scoped memory actions.

- [ ] **Step 1: Write failing component tests** for chat-to-settings preserving the chat route, central navigation, memory scope switching, search, deletion, and project isolation.
- [ ] **Step 2: Run Vitest** and confirm the new expected controls/routes are missing.
- [ ] **Step 3: Implement a restrained Settings shell and compose the existing Provider/Skills screens without duplicating them; add Memory and project-memory views.**
- [ ] **Step 4: Run targeted component tests** and confirm all requested flows pass.

### Task 4: OmniRoute UI, documentation, and end-to-end verification

**Files:**
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`, `frontend/src/api/providers.ts`, `docs/OMNIROUTE.md`, `README.md`
- Test: `frontend/tests/unit/ProviderSettingsPage.test.tsx`, `frontend/tests/e2e/settings-navigation.spec.ts`

**Interfaces:**
- Consumes the lifecycle status/configuration endpoints from Task 1.
- Produces a persistent auto-start toggle, ownership-aware controls, and user-safe status/failure messages.

- [ ] **Step 1: Write failing UI and browser-flow tests** for the auto-start toggle, external instance restrictions, Chat → Settings → OmniRoute, and Settings → Memory → Back.
- [ ] **Step 2: Run the tests** and confirm the controls and routes fail before implementation.
- [ ] **Step 3: Implement the OmniRoute state panel, update documentation, and add non-blocking startup feedback.**
- [ ] **Step 4: Run Python tests, frontend tests, lint, build, browser checks, and the local stack; leave it running.**

## Self-review

- All acceptance areas map to a task: navigation (3–4), memory (2–3), skills/providers reuse (3), OmniRoute lifecycle (1 and 4), documentation and end-to-end validation (4).
- The plan uses existing route and persistence boundaries; it does not introduce a second Skills or Providers implementation.
- Process ownership is explicit and prevents external process termination.

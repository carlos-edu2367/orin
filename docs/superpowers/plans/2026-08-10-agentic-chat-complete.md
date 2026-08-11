# Agentic Chat Complete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the current durable text chat into a production-composed AgentOS agent that can safely call tools, create and delegate agents, operate artifacts/filesystem/terminal/browser capabilities, and render an auditable animated activity timeline in chat.

**Architecture:** Keep the HTTP gateway as a thin, authorized boundary. A durable conversation turn is executed by an agentic runtime loop in workers; the loop resolves a model, handles provider text/tool-call streams, authorizes every action through `ToolRuntimeService`, delegates through the multi-agent coordinator, and emits a single conversation activity projection. PostgreSQL remains the source of truth, Redis/ARQ remains dispatch coordination, and the browser consumes typed snapshots plus at-least-once activity events.

**Tech Stack:** Python 3.12/3.13, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis, ARQ, httpx, existing AgentOS domain ports, React/TypeScript, Motion (`motion/react`, the current framer-motion-compatible package), Three.js, `@react-three/fiber`, `@react-three/drei`, Vitest, Playwright, axe-core.

## Global Constraints

- Preserve user-owned worktree changes; never use `git reset --hard`, broad deletion, or checkout to discard work.
- Every new mutation is authenticated, owner-scoped, idempotent, bounded, and redacted; API keys, raw prompts, raw tool arguments, command output, cookies, and provider payloads never enter public events or logs.
- PostgreSQL is authoritative for conversation, execution, tool, delegation, artifact, and activity state; Redis is only coordination.
- The chat worker must never execute arbitrary model-produced Python or shell; all effects go through declared Tool Runtime adapters, leases, policy, and limits.
- The local profile remains loopback-only. Browser network policy, filesystem workspace jail, terminal command policy, artifact classification, and multi-agent ownership are fail-closed.
- Do not call a capability “implemented” from a unit fake alone. Each capability needs a real Postgres/Redis integration test and a Playwright browser test against the running local app.
- Use deterministic provider fixtures for automated tests and one real configured provider smoke test; do not require a live paid provider for every test.
- Keep the existing `motion/react` dependency unless a deliberate migration to `framer-motion` is justified; do not install both for the same animation system.

## Current Reality and Explicit Gaps

- `POST /v1/conversations` and the ARQ worker are functional for plain provider text streaming.
- `src/agentos/workers/chat.py` currently sends only `messages` to the provider and extracts text deltas; it has no tool schema, tool-call loop, tool registry, artifact bridge, browser bridge, terminal bridge, or delegation loop.
- `compose_production_services()` leaves `agents`, `capabilities`, `tools`, `workspaces`, `artifacts`, and `memories` as unavailable ports. The generic routes exist but are not usable.
- Multi-agent event persistence exists, but there is no production composition of `MultiAgentCoordinatorService`, no collaboration query, and no delegation HTTP contract.
- Tool and multi-agent event bridges into `ClientEventStream` exist, but no real gateway/worker path produces those events for a user.
- Browser, terminal, filesystem, resource, and artifact domain implementations exist mostly as ports/reference adapters and are not composed into the chat runtime.
- The current chat UI renders messages and polling snapshots, not a typed tool/delegation activity timeline or expandable agent transcript.

## Target Contracts (freeze these before implementation)

1. `AgenticTurnRuntime.run(turn_id: str) -> None` owns one durable turn and may iterate provider → action → result until a terminal assistant message, failure, cancellation, or user wait.
2. `AgentActionRequest` contains `action_id`, `turn_id`, `agent_id`, `kind`, `tool_ref` or `delegation_ref`, typed input, deadline, policy context, and idempotency key. It never contains an unbounded opaque command.
3. `AgentActivityEvent` contains `event_id`, `conversation_id`, `turn_id`, `execution_id`, `agent_id`, optional `parent_agent_id`, `event_type`, `sequence`, `summary`, sanitized structured payload, visibility, and `created_at`. Public payloads contain references and bounded summaries, not secrets or full untrusted content.
4. Supported activity types include `turn.started`, `assistant.delta`, `assistant.completed`, `tool.requested`, `tool.started`, `tool.progressed`, `tool.finished`, `artifact.created`, `filesystem.read`, `filesystem.write`, `terminal.started`, `terminal.output_summary`, `browser.started`, `browser.navigated`, `browser.artifact`, `agent.created`, `agent.message_sent`, `agent.message_received`, `delegation.created`, `delegation.waiting`, `delegation.completed`, `delegation.failed`, `turn.waiting_user`, `turn.completed`, and `turn.failed`.
5. `GET /v1/conversations/{id}` returns messages plus an authorized `activities` projection; `GET /v1/conversations/{id}/events` returns replayable activity events with an opaque cursor and resync semantics. A separate artifact endpoint returns metadata and short-lived authorized download/read grants.
6. A provider adapter exposes capabilities (`streaming`, `tool_calls`, `structured_output`, `vision`, `max_context`) and normalizes OpenAI-compatible/OpenRouter and Anthropic tool-call deltas into one internal stream.
7. Tool descriptors are versioned, immutable, schema-validated, policy-tagged, and registered in a production composition root. Tool execution returns a typed result plus a sanitized public summary and optional artifact references.
8. Delegation is explicit: a parent agent creates a child agent/run with an authorized model/profile, scoped context, budget, deadline, and collaboration policy; the parent enters a durable wait and resumes only after a child terminal result or failure.

---

## Phase 0 — Baseline, harness, and dependency graph

**Files:**
- Modify: `README.md`, `.env.local.example`, `docker-compose.yml`
- Create: `docs/agentic/TOOL_CAPABILITY_MATRIX.md`, `docs/agentic/E2E_RUNBOOK.md`
- Test: `tests/integration/agentic/test_environment_smoke.py`

- [ ] Record current worktree, Python/Node versions, dependency health, Alembic head, and running services before code changes.
- [ ] Add a deterministic provider test server/transport fixture that can emit text, tool-call deltas, malformed calls, retries, cancellation, and multiple agents without internet access.
- [ ] Add local startup documentation for API, publisher, ARQ worker, frontend, Postgres, Redis, and Playwright browsers.
- [ ] Add a smoke test that creates a conversation, waits for a terminal state, reads activities, and asserts no secret-like field appears in the response.
- [ ] Run the baseline backend unit suite, integration suite against real Postgres/Redis, frontend unit suite, frontend build, and Playwright smoke before proceeding.

## Phase 1 — Durable agentic activity and conversation projections

**Files:**
- Create: `src/agentos/agentic/models.py`, `src/agentos/agentic/events.py`
- Create: `src/agentos/persistence/postgres/agentic_activity.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0017_agentic_activity.py`
- Modify: `src/agentos/persistence/postgres/schema.py`, `src/agentos/conversations/chat.py`
- Modify: `src/agentos/api/contracts.py`, `src/agentos/api/gateway.py`
- Test: `tests/unit/agentic/test_activity_contracts.py`, `tests/integration/agentic/test_activity_postgres.py`

- [ ] Write failing tests for event type validation, per-conversation ordering, owner isolation, idempotent event insertion, redaction, and cursor replay.
- [ ] Add the migration/table and SQLAlchemy adapter for `conversation_activity_events` with indexes on `(conversation_id, id)`, `(user_id, created_at)`, and `(turn_id, sequence)`.
- [ ] Add a projector that joins execution, tool, multi-agent, browser, terminal, filesystem, and artifact facts into the typed chat activity shape without exposing raw payloads.
- [ ] Extend conversation GET/list DTOs with bounded activity summaries and add an authorized conversation event replay route.
- [ ] Add a pagination/resync contract: stale/invalid cursor returns the existing `cursor_invalid` envelope, and the UI can recover from a fresh snapshot.
- [ ] Verify with real Postgres that two users cannot read or replay each other’s activity, including child agent and artifact events.

## Phase 2 — Provider streaming and the real agentic loop

**Files:**
- Create: `src/agentos/agentic/runtime.py`, `src/agentos/agentic/provider_stream.py`, `src/agentos/agentic/action_loop.py`
- Modify: `src/agentos/provider_catalog/ports.py`, `src/agentos/provider_catalog/http.py`, `src/agentos/providers/compat.py`, `src/agentos/workers/chat.py`
- Test: `tests/unit/agentic/test_runtime_loop.py`, `tests/integration/agentic/test_provider_tool_loop.py`

- [ ] Define normalized provider stream items for text deltas, tool-call deltas, usage, finish reasons, rate limits, and provider errors.
- [ ] Implement OpenRouter/OpenAI-compatible streaming with declared JSON-schema tools and Anthropic tool-call normalization; reject unsupported model capabilities before dispatch.
- [ ] Replace the direct text-only worker path with `AgenticTurnRuntime`, preserving durable claim/heartbeat/terminal transitions.
- [ ] Implement the loop: start turn → stream assistant text/tool calls → validate complete call → authorize/execute action → append sanitized result → continue model context → terminal response.
- [ ] Add bounded history/context compaction, maximum action count, wall-clock deadline, token/cost budget, cancellation checks, retry policy, and indeterminate recovery.
- [ ] Ensure malformed/unknown/duplicate tool calls become auditable failed actions, never arbitrary execution or silent success.
- [ ] Prove a deterministic tool-call turn produces `WAITING_TOOL`, `RUNNING`, `ToolStarted`, `ToolFinished`, and final assistant content in order.

## Phase 3 — Production Tool Runtime composition

**Files:**
- Create: `src/agentos/tool_runtime/production.py`, `src/agentos/tool_runtime/catalog.py`
- Modify: `src/agentos/bootstrap/production.py`, `src/agentos/tool_runtime/registry.py`, `src/agentos/tool_runtime/runtime.py`
- Test: `tests/integration/agentic/test_tool_runtime_composition.py`, `tests/unit/tool_runtime/test_production_catalog.py`

- [ ] Compose a real registry with immutable descriptors for filesystem, artifact, terminal, browser, and delegation actions; expose only tools allowed by the selected agent policy.
- [ ] Wire `ToolRuntimeService` to Postgres activity sink, resource manager, idempotency, leases, authorization, cancellation, limits, and audit events.
- [ ] Add a provider-facing tool schema projection that contains name/description/input schema only; never pass internal handles, paths, credentials, or policy internals to the model.
- [ ] Add a typed action-result projection with summary, status, error category, and artifact references; keep raw output private unless an authorized read endpoint is used.
- [ ] Verify every registered tool is reachable through the runtime and that an unregistered or cross-owner tool is denied.

## Phase 4 — Workspaces, filesystem, and artifacts

**Files:**
- Create/modify: `src/agentos/workspaces/production.py`, `src/agentos/filesystem/production.py`, `src/agentos/artifact_storage/production.py`
- Modify: `src/agentos/bootstrap/production.py`, `src/agentos/tool_runtime/adapters.py`, `src/agentos/api/gateway.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0018_agentic_artifacts.py`
- Test: `tests/integration/agentic/test_filesystem_artifact_tools.py`, `tests/e2e/agentic_filesystem.spec.ts`

- [ ] Compose a workspace-rooted filesystem adapter with canonical path validation, symlink escape prevention, quotas, atomic writes, version checks, and owner/workspace scope.
- [ ] Compose artifact metadata, staging, commit, quarantine, checksum, classification, retention, and authorized read/download grants.
- [ ] Add tools for list/stat/read/create-directory/write/remove and artifact create/inspect/read; return summaries such as “Leu `src/app.py`” or “Criou `report.pdf`” with links only when authorized.
- [ ] Add public artifact metadata and short-lived download/read routes with content-type and size limits.
- [ ] Test path traversal, oversized writes, classification violations, stale versions, artifact ownership, resumable reads, and browser-visible download.

## Phase 5 — Terminal capability

**Files:**
- Create/modify: `src/agentos/terminal/production.py`, `src/agentos/workers/terminal.py`
- Modify: `src/agentos/tool_runtime/adapters.py`, `src/agentos/bootstrap/production.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0019_terminal_runs.py`
- Test: `tests/integration/agentic/test_terminal_tool.py`, `tests/e2e/agentic_terminal.spec.ts`

- [ ] Compose terminal sessions through resource leases and a worker boundary; the chat worker must never run shell commands in its own process.
- [ ] Add command policy (allowed executable set, blocked metacharacters/redirects, cwd inside workspace, timeout, output byte cap, process-tree cancellation, environment redaction).
- [ ] Stream only bounded sanitized output summaries into activity events; store full output as a classified artifact when allowed.
- [ ] Support create/execute/read-output/cancel/close with idempotency and durable terminal state.
- [ ] Prove a browser test where the model requests a safe command, the UI shows “Executou `...`”, output is available through an authorized artifact/detail panel, and a forbidden command is denied visibly.

## Phase 6 — Browser capability

**Files:**
- Create/modify: `src/agentos/browser/production.py`, `src/agentos/workers/browser.py`
- Modify: `src/agentos/browser/playwright_adapter.py`, `src/agentos/tool_runtime/adapters.py`, `src/agentos/bootstrap/production.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0020_browser_runs.py`
- Test: `tests/integration/agentic/test_browser_tool.py`, `tests/e2e/agentic_browser.spec.ts`

- [ ] Compose Playwright only in the browser worker; keep browser types outside the domain and enforce URL/redirect/network policy before navigation.
- [ ] Add tools for open session/page, navigate, inspect title/DOM, screenshot, cookies metadata, and close; redact cookies and sensitive page data.
- [ ] Persist page/session state and artifact references; emit browser activity summaries such as “Abriu `example.test`” and “Capturou screenshot”.
- [ ] Add deterministic local test pages for navigation, DOM extraction, screenshot artifact, denied external URL, redirect denial, and timeout recovery.
- [ ] Run the actual Chromium Playwright test with screenshots and inspect the resulting UI, not only mocked browser tests.

## Phase 7 — Agents, profiles, collaboration, and delegation

**Files:**
- Create: `src/agentos/agents/production.py`, `src/agentos/multi_agent/production.py`, `src/agentos/multi_agent/queries.py`
- Modify: `src/agentos/multi_agent/service.py`, `src/agentos/bootstrap/multi_agent.py`, `src/agentos/bootstrap/production.py`, `src/agentos/conversations/chat.py`, `src/agentos/agentic/action_loop.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0021_agentic_collaboration.py`
- Modify: `src/agentos/api/gateway.py`, `src/agentos/api/contracts.py`
- Test: `tests/integration/agentic/test_agent_delegation.py`, `tests/e2e/agentic_delegation.spec.ts`

- [ ] Implement durable Postgres adapters for agent identity/version/profile, collaboration grants, delegation, agent messages, waits, child executions, and result references; reuse existing domain ports rather than bypassing them.
- [ ] Add authorized endpoints for agent profiles, collaboration graph, delegation detail, and child activity, with owner/workspace scoping and no raw private prompts.
- [ ] Add explicit internal actions `agent.create`, `agent.message`, `agent.delegate`, `agent.wait`, and `agent.collect`; make them visible to the model only when policy allows.
- [ ] Make the parent enter a durable wait state and resume after child completion/failure/cancellation. Enforce maximum depth, fan-out, budget, deadline, and no unauthorized cross-user delegation.
- [ ] Persist sender/recipient IDs and sanitized message summaries; make full agent transcript accessible only through an explicit “expandir detalhes” authorization path.
- [ ] Prove a deterministic parent → two children → child results → parent synthesis flow, including one child failure and cancellation.

## Phase 8 — Chat activity API and frontend state model

**Files:**
- Create: `frontend/src/features/conversations/activityTypes.ts`, `activityReducer.ts`, `activitySummary.ts`, `ActivityTimeline.tsx`, `ActivityDetails.tsx`
- Modify: `frontend/src/api/conversations.ts`, `frontend/src/api/client.ts`, `frontend/src/features/conversations/ChatPage.tsx`
- Test: `frontend/tests/unit/activityTimeline.test.tsx`, `frontend/tests/unit/activityReducer.test.ts`, `frontend/tests/e2e/agentic-chat.spec.ts`

- [ ] Add typed client parsers for activities, tool results, delegation edges, agent messages, artifacts, and event cursors; reject malformed public payloads.
- [ ] Replace one-second full polling with snapshot + cursor-based activity replay and reconnect/resync; keep polling as a bounded fallback for degraded mode.
- [ ] Group consecutive low-level events into concise timeline cards: “Usou 3 ferramentas”, “Usou navegador”, “Leu `README.md`”, “Enviou mensagem ao agente X”, “Esperando resposta de X”.
- [ ] Keep the normal transcript compact; each card has an accessible expand control that reveals the authorized detail transcript, tool names/status, agent-to-agent messages, and artifact links.
- [ ] Render explicit states for queued, working, waiting for tool, waiting for agent, waiting for user, retrying, failed, cancelled, and completed; never infer state from animation alone.
- [ ] Add optimistic send only for the user bubble; reconcile all assistant/tool/agent state from server events and dedupe by `event_id`.

## Phase 9 — Visual system, motion, and Three.js orchestration view

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx`, `frontend/src/features/conversations/ConversationComposer.tsx`, `frontend/src/styles/*`
- Create: `frontend/src/features/conversations/AgenticActivityCard.tsx`, `AgentConversationThread.tsx`, `AgenticGraphScene.tsx`, `activityMotion.ts`
- Reuse/modify: `frontend/src/features/agents/agentGraphProjection.ts`, `frontend/src/features/agents/OrchestrationScene.tsx`, `frontend/src/features/agents/AgentRail.tsx`
- Test: `frontend/tests/unit/AgenticGraphScene.test.tsx`, `frontend/tests/e2e/agentic-visual.spec.ts`, `frontend/tests/visual/agentic-chat.spec.ts`

- [ ] Preserve the current calm dark visual language while adding clear hierarchy for user text, assistant text, tool cards, agent cards, waits, and artifacts.
- [ ] Use Motion for enter/exit/layout transitions, staggered activity appearance, progress shimmer, reduced-motion alternatives, and stable height transitions; animations must not alter semantic state.
- [ ] Use Three.js/R3F for an optional expandable collaboration scene only after the 2D timeline is correct. Nodes represent agents, edges represent delegation/message flow, and tool/resource glyphs are derived solely from projected events.
- [ ] Keep the scene lazy-loaded and feature-gated for performance; render a 2D fallback when WebGL is unavailable or reduced motion is requested.
- [ ] Add keyboard navigation, focus management, live-region summaries, contrast checks, screenshot baselines, and mobile layout tests.
- [ ] Validate the actual browser with Playwright screenshots at desktop/mobile, with/without reduced motion, with long tool output, nested delegation, and reconnect banners.

## Phase 10 — Reliability, security, and operational hardening

**Files:**
- Modify: `src/agentos/api/gateway.py`, `src/agentos/agentic/*`, `src/agentos/workers/*`, `src/agentos/bootstrap/production.py`
- Create: `tests/integration/agentic/test_security_boundaries.py`, `tests/integration/agentic/test_recovery.py`
- Modify: `README.md`, `docs/agentic/TOOL_CAPABILITY_MATRIX.md`, `docs/frontend/BACKEND_CAPABILITY_MATRIX.md`

- [ ] Add rate limits and budget accounting per user/agent/turn/provider/tool; expose only bounded usage summaries.
- [ ] Test worker crash after claim, provider timeout, Redis outage, duplicate publish, duplicate tool request, stale lease, browser crash, terminal kill, child failure, and replay after reconnect.
- [ ] Ensure watchdogs produce explicit recoverable activity and never leave a turn silently queued.
- [ ] Add structured sanitized logs and correlation IDs while proving secrets and raw prompt/tool payloads do not appear.
- [ ] Add migrations/backup/rollback documentation and verify `alembic upgrade head` on a fresh database and an existing `0016` database.

## Phase 11 — Release gate and evidence

- [ ] Run the full Python unit suite and all Postgres/Redis integration tests.
- [ ] Run `npm run lint`, `npm run test`, `npm run build`, `npm run test:e2e`, and `npm run test:visual`.
- [ ] Run the browser directly against the running local stack and manually verify: plain chat, filesystem read/write, artifact creation/download, safe terminal command, browser navigation/screenshot, two-agent collaboration, nested activity expansion, reconnect/resync, cancellation, and error recovery.
- [ ] Capture screenshots/videos for the major flows and inspect them for clipping, unreadable states, broken WebGL, excessive motion, and inaccessible controls.
- [ ] Verify every capability in `TOOL_CAPABILITY_MATRIX.md` is marked `implemented` only with a real backend integration test and a browser test.
- [ ] Review the diff for secret leakage, unbounded payloads, fake-only paths, unavailable production ports, and stale docs.
- [ ] Perform a two-stage code review after each delegated task and one final independent review before claiming completion.
- [ ] Update README/runbook with exact startup, migration, worker, browser dependency, and troubleshooting commands.

## Definition of Done

The work is complete only when a real user can ask the selected model to perform a safe multi-step task, observe the assistant thinking/state transitions, see tools and artifacts summarized in chat, expand details to inspect authorized tool/agent messages, watch a parent delegate to child agents and synthesize their results, use terminal/filesystem/browser through policy-controlled workers, reconnect without losing or duplicating events, and recover from cancellation/provider/tool/worker failures. Unit tests alone, mock-only browser tests, static UI fixtures, or a successful text-only response do not satisfy this gate.


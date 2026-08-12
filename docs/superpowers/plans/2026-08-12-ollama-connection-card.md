# Ollama Connection Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Ollama provider setup into a distinctive connection card while preserving its current Local/Cloud behavior and API contract.

**Architecture:** Keep the existing `OllamaSetup` component and event handlers, adding semantic presentation wrappers and state classes. Put all visual treatment in the existing provider stylesheet so other providers and backend contracts remain unchanged.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library, Vite.

## Global Constraints

- Preserve Local/Cloud mode behavior, URL defaults, Cloud key requirement, loading/disabled states, and accessibility labels.
- Do not change API routes, persistence, authorization, or backend behavior.
- Keep the change focused on the Ollama setup surface.

### Task 1: Lock the new visual structure with a regression test

**Files:**
- Modify: `frontend/tests/unit/ProviderSettingsPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Add a test in `describe('ProviderSettingsPage Ollama setup', ...)` that renders the default local state and asserts the new semantic regions exist: `ollama-flow__overview`, `ollama-flow__connection`, and `ollama-flow__footer`.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `npm.cmd test -- --run frontend/tests/unit/ProviderSettingsPage.test.tsx` from the repository root's `frontend` directory.
Expected: FAIL because the new regions do not exist yet.

### Task 2: Implement the premium connection-card structure and styling

**Files:**
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Modify: `frontend/src/styles/index.css`

- [ ] **Step 1: Add semantic wrappers and state presentation**

Wrap the mode selector and explanatory copy in `ollama-flow__overview`, the URL/key fields in `ollama-flow__connection`, and the enable/actions area in `ollama-flow__footer`. Add a compact `ollama-flow__status` badge tied to the existing connection state without changing behavior.

- [ ] **Step 2: Add responsive CSS**

Create the card treatment with a stronger header, two-column field grid where space allows, segmented Local/Cloud control, quiet dividers, clear focus states, and mobile stacking. Scope all new selectors under `.ollama-flow`.

- [ ] **Step 3: Run the focused test**

Run: `npm.cmd test -- --run frontend/tests/unit/ProviderSettingsPage.test.tsx`.
Expected: PASS with all Ollama and provider tests green.

### Task 3: Validate and document the durable decision

**Files:**
- Create: `docs/agent_memory/2026-08-12-ollama-connection-card.md`

- [ ] **Step 1: Run frontend validation**

Run `npm.cmd test`, `npm.cmd run build`, and `npm.cmd run lint` in `frontend`.

- [ ] **Step 2: Review the diff and record the decision**

Confirm only the Ollama presentation, its focused test, plan, and memory documentation changed. Record that the API contract and Local/Cloud semantics were intentionally preserved.

- [ ] **Step 3: Commit and push**

Stage all intended changes, commit with `feat(web): redesign Ollama connection setup`, then push `main` to `origin`.

# Orin Visual Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Replace the visible AgentOS identity with Orin's logo, name, and violet visual palette without changing layouts, navigation, or animations.

**Architecture:** Add a shared brand token stylesheet and a small reusable `Brand` component. Keep internal `agentos.*` storage keys, API names, environment variables, and backend identifiers unchanged while updating visible copy, document metadata, and primary UI colors.

**Tech Stack:** React, TypeScript, Vite, CSS custom properties, Vitest, Testing Library.

## Global Constraints

- Preserve existing layouts, spacing, responsive behavior, and motion behavior.
- Preserve internal AgentOS identifiers and compatibility-sensitive storage keys.
- Keep semantic danger, warning, success, and informational colors distinct from the Orin accent.
- Use the supplied Orin logo asset in the visible brand component.

### Task 1: Shared brand tokens and identity contract

**Files:**
- Create: `frontend/src/config/brand.ts`
- Create: `frontend/src/styles/theme.css`
- Create: `frontend/tests/unit/brandIdentity.test.ts`
- Modify: `frontend/src/main.tsx`

- [x] Write a failing test asserting the visible brand name is `Orin`, the browser title uses `Orin`, and the theme exposes violet accent tokens.
- [x] Run the focused test and confirm it fails because the shared brand contract does not exist.
- [x] Add `BRAND_NAME`, `BRAND_SHORT_NAME`, and `BRAND_LOGO_PATH` constants, plus `theme.css` tokens for graphite surfaces, violet accent, lilac highlight, text, muted text, and semantic states.
- [x] Import `theme.css` before component styles in `main.tsx`.
- [x] Run the focused test and confirm it passes.

### Task 2: Reusable Orin brand component

**Files:**
- Create: `frontend/src/components/Brand.tsx`
- Create: `frontend/tests/unit/Brand.test.tsx`
- Add: `frontend/public/orin-logo.png`

- [x] Write a component test asserting the reusable brand renders an accessible Orin link/text and the supplied logo path.
- [x] Copy the supplied logo into `frontend/public/orin-logo.png` and implement `Brand` with the existing `brand`, `brand__mark`, and `brand__word` classes so layouts remain unchanged.
- [x] Run the focused component test and confirm it passes.

### Task 3: Replace visible identity references

**Files:**
- Modify: `frontend/src/app/Home.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/features/executions/ExecutionPage.tsx`
- Modify: `frontend/src/features/projects/ProjectPage.tsx`
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/skills/SkillsPage.tsx`
- Modify: `frontend/index.html`

- [x] Replace repeated visible brand markup with `Brand` while preserving existing links and aria labels.
- [x] Change only visible copy and document metadata from AgentOS to Orin; retain internal keys and API terminology.
- [x] Run the existing navigation, settings, project, skills, and chat tests and fix only identity-related expectations.

### Task 4: Apply the Orin palette without layout changes

**Files:**
- Modify: `frontend/src/styles/index.css`
- Modify: `frontend/src/styles/agentos.css`

- [x] Replace primary hard-coded graphite/lime values with shared theme tokens or token-derived rgba values.
- [x] Update the brand mark, focus ring, primary buttons, active states, scrollbar hover, agent glow, and links to use the violet accent.
- [x] Keep semantic danger/warn/success/info colors unchanged except for token centralization.
- [x] Run the full frontend test suite and production build.

### Task 5: Final verification

- [x] Run `npm test -- --run` in `frontend`.
- [x] Run `npm run build` in `frontend`.
- [x] Run `npm run lint` and report any pre-existing failures without broad unrelated cleanup.
- [x] Inspect the final diff for accidental layout, animation, or internal identifier changes.

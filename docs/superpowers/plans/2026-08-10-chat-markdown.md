# Chat Markdown Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Markdown sent by the LLM as structured, readable assistant messages in the chat while keeping user messages as plain text.

**Architecture:** Add a focused `MarkdownMessage` React component backed by `react-markdown` and `remark-gfm`. `ChatPage` selects that component only for assistant messages; user messages keep the existing text rendering. Markdown HTML is not enabled, so model content remains constrained to the supported Markdown AST.

**Tech Stack:** React, TypeScript, `react-markdown`, `remark-gfm`, Vitest, Testing Library, Vite.

## Global Constraints

- Assistant messages only receive Markdown rendering.
- Raw HTML from model output must not be rendered.
- Existing chat behavior, activity timeline, optimistic user echo, and streaming reconciliation must remain unchanged.
- Verify with focused unit tests, full frontend tests, build, and lint.

---

### Task 1: Add Markdown renderer dependency and failing coverage

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/src/features/conversations/MarkdownMessage.tsx`
- Create: `frontend/tests/unit/MarkdownMessage.test.tsx`

**Interfaces:**
- Produces `MarkdownMessage({ content: string }): JSX.Element`.

- [ ] **Step 1: Add the runtime dependencies**

Install `react-markdown` and `remark-gfm` in `frontend`.

- [ ] **Step 2: Write the failing component test**

Cover headings, bold text, GFM task/list markup, fenced code, and raw HTML staying absent from the rendered DOM.

- [ ] **Step 3: Run the focused test and verify the expected failure**

Run: `npm test -- --run tests/unit/MarkdownMessage.test.tsx` from `frontend`.

Expected: the test fails because `MarkdownMessage` is not implemented yet.

### Task 2: Implement and integrate assistant Markdown rendering

**Files:**
- Modify: `frontend/src/features/conversations/MarkdownMessage.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/tests/unit/ChatPage.test.tsx`

**Interfaces:**
- `MarkdownMessage` accepts the assistant content string and renders Markdown with GFM, without raw HTML.

- [ ] **Step 1: Implement the minimal renderer**

Use `ReactMarkdown` with `remarkPlugins={[remarkGfm]}` and no `rehype-raw` plugin. Give the root a stable `markdown-message` class.

- [ ] **Step 2: Integrate by role**

Keep user content inside the existing plain-text `<p>`. For assistant messages, render `MarkdownMessage` while retaining empty-content placeholders and retry notices.

- [ ] **Step 3: Add ChatPage regression coverage**

Use an assistant snapshot containing `**Arquivos**\n\n- hello.txt` and assert the visible bold heading/list item are structured, while the user message remains plain text.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `npm test -- --run tests/unit/MarkdownMessage.test.tsx tests/unit/ChatPage.test.tsx` from `frontend`.

### Task 3: Style Markdown content and verify the frontend

**Files:**
- Modify: `frontend/src/styles/agentos.css`

- [ ] **Step 1: Add readable Markdown styles**

Style paragraphs, headings, lists, links, inline code, fenced code, blockquotes, tables, and horizontal rules under `.markdown-message`, preserving wrapping on long content and the existing dark visual language.

- [ ] **Step 2: Run all frontend unit tests**

Run: `npm test -- --run` from `frontend`.

Expected: all tests pass.

- [ ] **Step 3: Run build and lint**

Run: `npm run build` and `npm run lint` from `frontend`.

Expected: both commands exit with code 0 and report no errors.

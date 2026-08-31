---
name: React SPA
description: Build or extend a Vite React TypeScript single-page app with conventional structure and mechanical verification.
version: 1.0.0
tags: [react, vite, typescript, frontend, spa]
capabilities: [scaffold_react_spa, implement_react_ui, verify_frontend]
when_to_use: [building a React SPA, adding a React screen or route, repairing a Vite React TypeScript app]
when_not_to_use: [editing a server-only API, maintaining a Next.js app]
requires_tools: [read_file, write_file, run_command, verify_project, verify_frontend]
---

# React SPA

## Workflow

1. For a new app, use `verify_project` with `scaffold: "vite-react-ts"`; do not hand-write the initial package manifest.
2. Keep page components under `src/pages`, reusable UI under `src/components`, and route/data concerns separate from presentational components.
3. Add routing only when the request has distinct navigable pages; ensure every declared route has an intentional fallback state.
4. Install dependencies before importing them. Prefer TypeScript types at component boundaries and accessible native controls before custom widgets.
5. Run `verify_project` after implementation, then start the dev server and use `verify_frontend` for each requested route.

## Validation

Require fresh evidence from `npm run build`, `npx tsc --noEmit`, `npm run lint` when configured, and rendered routes. Do not claim a visual result from source inspection alone.

---
name: Next.js App Router
description: Build or extend a Next.js TypeScript app using App Router conventions, server/client boundaries, and verification.
version: 1.0.0
tags: [nextjs, react, typescript, frontend, app-router]
capabilities: [scaffold_nextjs, implement_nextjs_route, verify_frontend]
when_to_use: [creating a Next.js application, adding an App Router page, fixing a Next.js frontend]
when_not_to_use: [building a Vite SPA, editing a backend-only service]
requires_tools: [read_file, write_file, run_command, verify_project, verify_frontend]
---

# Next.js App Router

## Workflow

1. For a new app, call `verify_project` with `scaffold: "next-app"` before adding product code.
2. Use `app/` routes, keep server components as the default, and add `"use client"` only at interaction boundaries.
3. Put route-local loading, error, and empty states next to the route. Do not fetch browser-only data in a server component by accident.
4. Keep shared UI and utilities out of a route unless they are truly route-specific; preserve TypeScript and ESLint conventions created by the generator.
5. Verify the build and render each affected route with the running development server.

## Validation

Use `verify_project` for detected typecheck, lint, build, and test commands. Use `verify_frontend` for the primary route and every newly added route.

---
name: Python FastAPI Service
description: Build or extend a typed FastAPI service with clear routing, validation, and mechanical checks.
version: 1.0.0
tags: [python, fastapi, api, backend, testing]
capabilities: [scaffold_fastapi, implement_api_endpoint, verify_python_service]
when_to_use: [creating a FastAPI service, adding an HTTP endpoint, fixing a Python API]
when_not_to_use: [building a browser-only frontend, maintaining a Node API]
requires_tools: [read_file, write_file, run_command, verify_project]
---

# Python FastAPI Service

## Workflow

1. For a new service, use `verify_project` with `scaffold: "fastapi-service"` to establish dependencies before writing endpoints.
2. Keep routers, request/response models, business logic, and persistence boundaries separate; use Pydantic models at the HTTP boundary.
3. Return explicit status codes and write focused tests for success plus the most important invalid input or not-found path.
4. Prefer project-configured tools over inventing commands; use the recipe detected by `verify_project`.

## Validation

Run `verify_project` after changes. Report the actual lint, typecheck, and test status; if a command is not configured, say that rather than treating it as passed.

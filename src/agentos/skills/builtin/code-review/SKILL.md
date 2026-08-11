---
name: Code Review
description: Review a change for correctness, regressions, security boundaries, tests, and maintainability.
version: 1.0.0
tags:
  - code
  - review
  - quality
  - security
capabilities:
  - review_change
  - identify_regression
when_to_use:
  - reviewing a diff
  - pull request
  - proposed implementation
when_not_to_use:
  - the user asks to implement without reviewing a change
requires_tools:
  - read_file
  - run_command
---

# Code Review

## Workflow

1. Read the requested outcome and the changed code in context.
2. Trace data flow and authorization boundaries before commenting on style.
3. Look for behaviour that is incorrect, unsafe, untested or incompatible.
4. Report actionable findings ordered by impact, with evidence and a precise location.

## Validation

Do not invent defects. State when a test or runtime path could not be verified.

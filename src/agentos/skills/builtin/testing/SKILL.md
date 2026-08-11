---
name: Testing
description: Design focused tests, observe a failing case, implement the smallest change, and verify it.
version: 1.0.0
tags:
  - testing
  - quality
  - regression
capabilities:
  - write_test
  - verify_change
when_to_use:
  - adding a feature
  - fixing a defect
  - preventing a regression
when_not_to_use:
  - the task only asks for a prose explanation
requires_tools:
  - read_file
  - run_command
---

# Testing

## Workflow

1. Identify one observable behaviour and write a focused test.
2. Run it and confirm it fails for the missing behaviour.
3. Make the minimal implementation change.
4. Re-run the focused test, then the relevant suite.

## Validation

Report the exact command and result. Do not claim success without fresh evidence.

---
name: Systematic Debugging
description: Investigate software failures with evidence, falsifiable hypotheses, and a verified regression test.
version: 1.0.0
tags:
  - debugging
  - testing
  - code
capabilities:
  - diagnose_failure
  - isolate_cause
when_to_use:
  - a test fails
  - an endpoint returns an error
  - behaviour regressed
when_not_to_use:
  - only a style review is requested
dependencies:
  skills:
    - testing
requires_tools:
  - read_file
  - run_command
---

# Systematic Debugging

## Workflow

1. Reproduce the failure and preserve the observed error.
2. Inspect the narrowest relevant logs, code, configuration and recent change.
3. Form one falsifiable hypothesis and test it before changing unrelated code.
4. Add a regression test, implement the smallest correction, and rerun it.

## Validation

Verify both the original failure path and the affected test suite. State unresolved uncertainty explicitly.

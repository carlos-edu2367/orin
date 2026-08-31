---
name: Frontend Accessibility
description: Validate and improve accessible frontend interactions, semantics, keyboard flow, and responsive rendering.
version: 1.0.0
tags: [accessibility, a11y, frontend, react, ux]
capabilities: [audit_frontend_accessibility, verify_frontend]
when_to_use: [building or changing a frontend form, navigation, dialog, interactive control, or responsive screen]
when_not_to_use: [working on a backend-only task, making prose-only changes]
requires_tools: [read_file, write_file, verify_frontend]
---

# Frontend Accessibility

## Workflow

1. Start with semantic HTML: headings in order, buttons for actions, links for navigation, labels for inputs, and meaningful image alternatives.
2. Ensure focus is visible and the full affected flow is usable with keyboard controls; do not replace native controls without a concrete need.
3. Preserve readable contrast, touch targets, and layout at narrow and wide viewports. Include clear loading, empty, and error states where data can vary.
4. Render every changed route in the isolated browser and inspect the resulting hierarchy and interactive elements.

## Validation

Use `verify_frontend` with all affected routes and report what it observed. Pair it with the stack's mechanical build/typecheck/lint evidence where available.

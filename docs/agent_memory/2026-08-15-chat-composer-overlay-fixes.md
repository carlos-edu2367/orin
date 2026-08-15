# Chat composer overlay fixes

## Context

The chat's return-to-latest action was rendered in the same lower overlay area
as the composer. The composer container captured pointer interaction when the
action was hovered, so the action could not be clicked reliably while the user
was reading older activity.

## Decision

- Keep the action in the chat viewport, but place it right-aligned above the
  composer with `z-index: 30` and a 12px vertical gap.
- Reduce the mobile offset while preserving that gap and horizontal margins.
- Do not render the composer while the overview route is open; closing the
  overview restores the normal composer.

## Validation

- Focused and full frontend unit suites passed.
- Production frontend build passed.
- In the local browser, the overview showed no composer and restored it after
  closing; the return-to-latest action was visibly above the composer and a
  real click returned the chat to the bottom.

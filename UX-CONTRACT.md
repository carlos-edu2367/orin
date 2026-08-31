# UX contract

## Code mode lifecycle

1. A request enters Code mode when the user enables `Code` or the server
   classifies a clearly technical request. A user can opt out for a turn.
2. With the default preference, Orin writes a plan under `.orin/plans/` and
   asks through the existing user-question flow before code or commands run.
3. After approval, activity updates preserve the five lifecycle stages.
4. Automated tests are required for implementation work. Frontend changes also
   require browser validation when a browser capability is available.
5. A failed check sends work back to correction. A genuine decision or a
   blocked external dependency produces an explicit waiting/blocked card.
6. Completion is shown only after the recorded delivery state; caveats remain
   visible instead of being silently treated as success.

## Autonomy and side effects

- `approval_required` is the default.
- `code_autonomy` and `full_autonomy` are global user preferences, not project
  settings. They can be changed in Runtime settings.
- Commits are allowed by the workflow. Pushes and pull requests require an
  explicit user request unless full autonomy was selected. Production deploys
  always require confirmation.

## Notifications and monitoring

- Long work is monitored in the background when enabled.
- Desktop notifications are opt-in and only announce completion, a block, or a
  required decision. The activity timeline remains the source of detail.
- Notification delivery failure must never change task state.

## Activity in chat

- A continuous sequence of ordinary tools in the same turn and agent is one
  expandable activity line. Its title follows the latest human-readable action;
  its count and expanded view retain every individual call and failure.
- Approval requests and browser captures remain independent cards because they
  have an action or visual evidence the person needs to reach directly.

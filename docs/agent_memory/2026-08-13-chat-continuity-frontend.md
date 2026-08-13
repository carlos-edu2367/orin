# Chat continuity frontend

- `ChatPage` keeps autoscroll opt-in via `pinnedRef`: when the reader scrolls away from the bottom, incoming message-content identities and activity event IDs are counted instead of moving the viewport.
- The count deliberately retains seen identities for the active conversation, preventing duplicate SSE delivery and durable snapshot reconciliation from inflating the return-to-latest action. `assistant.delta` is counted through its visible message update only, never again as a raw activity event.
- Project-context overview navigation derives its base only from React Router's `projectId` route param, so `/projects/:projectId/chats/:conversationId[/overview]` never falls back to `/chats/:conversationId`.
- `ProjectNavigation` uses `NavLink` so both base chat paths and their `/overview` descendants expose the active link state and `aria-current`.

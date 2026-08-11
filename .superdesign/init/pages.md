# Page dependency trees

## `/chats/:conversationId`

Entry: `frontend/src/features/conversations/ChatPage.tsx`

- `ChatPage.tsx`
  - `api/client.ts`
  - `api/conversations.ts`
  - `components/CommandPalette.tsx`
  - `features/conversations/Composer.tsx`
  - `features/conversations/MarkdownMessage.tsx`
  - `features/conversations/TurnTimeline.tsx`
  - `features/conversations/ActivityStream.tsx`
  - `features/conversations/AgentPulse.tsx`
  - `features/conversations/activityReducer.ts`
  - `features/overview/OverviewPanel.tsx`

The route uses `frontend/src/styles/agentos.css` for its shell, message, composer, and overview styling.
